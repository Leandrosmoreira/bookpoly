"""
Main bot execution loop.

Reads signals and executes trades based on decisions.

Usage:
    python -m bot.main
    python -m bot.main --paper  # Paper trading mode
    python -m bot.main --dry    # Dry run (no orders)

⚠️ WARNING: This bot can execute real trades with real money.
Always test thoroughly with --dry and --paper modes first.
"""

import sys
import os
import time
import asyncio
import signal
import logging
import json
from pathlib import Path
from datetime import datetime, timezone

# Add project paths
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "indicators" / "signals"))

from bot.config import BotConfig
from bot.trader import PolymarketTrader, OrderSide
from bot.position import PositionManager
from bot.risk import RiskManager, RiskLimits

from indicators.signals.config import SignalConfig
from indicators.signals.gates import evaluate_gates, get_probability_zone
from indicators.signals.microstructure import compute_microstructure
from indicators.signals.state import StateTracker
from indicators.signals.scorer import compute_score
from indicators.signals.decision import decide, Action, DecisionConfig
from indicators.signals.defense import (
    DefenseAction,
    DefenseConfig,
    format_defense_result,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bot")

# Graceful shutdown
shutdown_event = asyncio.Event()


def _signal_handler():
    log.info("Shutdown signal received")
    shutdown_event.set()


def get_latest_jsonl_row(directory: Path, pattern: str) -> dict | None:
    """Read the last line of the most recent JSONL file."""
    if not directory.exists():
        return None

    files = list(directory.glob(pattern))
    if not files:
        return None

    latest_file = max(files, key=lambda f: f.stat().st_mtime)

    try:
        with open(latest_file, "rb") as f:
            f.seek(0, 2)
            file_size = f.tell()
            if file_size == 0:
                return None

            pos = file_size - 1
            while pos > 0:
                f.seek(pos)
                char = f.read(1)
                if char == b'\n' and pos < file_size - 1:
                    break
                pos -= 1

            if pos > 0:
                f.seek(pos + 1)
            else:
                f.seek(0)

            last_line = f.readline().decode("utf-8").strip()
            if last_line:
                return json.loads(last_line)
    except Exception as e:
        log.error(f"Error reading {latest_file}: {e}")

    return None


async def run_bot():
    """Main bot loop."""
    # Load configurations
    bot_config = BotConfig()
    signal_config = SignalConfig()
    decision_config = DecisionConfig()
    risk_limits = RiskLimits()

    # Validate config
    errors = bot_config.validate()
    if errors:
        for error in errors:
            log.error(f"Config error: {error}")
        return

    log.info(f"Bot config: {bot_config}")
    log.info(f"Mode: {'DRY_RUN' if bot_config.dry_run else ('PAPER' if bot_config.paper_trading else 'LIVE')}")

    if not bot_config.dry_run and not bot_config.paper_trading:
        log.warning("=" * 50)
        log.warning("⚠️  LIVE TRADING MODE - REAL MONEY AT RISK ⚠️")
        log.warning("=" * 50)
        await asyncio.sleep(5)  # Give time to cancel

    # Initialize components
    trader = PolymarketTrader(bot_config)
    position_manager = PositionManager(bot_config, initial_bankroll=1000.0)
    risk_manager = RiskManager(bot_config, risk_limits)
    state_tracker = StateTracker(window_size=300)
    defense_config = DefenseConfig()

    log.info(f"Defense mode: {'ENABLED' if defense_config.enabled else 'DISABLED'}")

    # Data directories
    project_root = Path(__file__).parent.parent
    polymarket_dir = project_root / "data" / "raw" / "books"
    binance_dir = project_root / "data" / "raw" / "volatility"

    log.info(f"Polymarket data: {polymarket_dir}")
    log.info(f"Binance data: {binance_dir}")
    log.info("Starting bot loop...")

    seq = 0

    try:
        while not shutdown_event.is_set():
            t0 = time.monotonic()
            now_ts = time.time()

            for coin in signal_config.coins:
                market = f"{coin.upper()}15m"

                # Get latest Polymarket data
                poly_pattern = f"{coin.upper()}15m_*.jsonl"
                poly_data = get_latest_jsonl_row(polymarket_dir, poly_pattern)

                if not poly_data:
                    continue

                # Check data freshness
                poly_ts = poly_data.get("ts_ms", 0) / 1000.0
                data_age = now_ts - poly_ts
                if data_age > 10:
                    log.warning(f"[{market}] Data too stale ({data_age:.1f}s)")
                    continue

                # Get Binance data
                symbol = f"{coin.upper()}USDT"
                binance_pattern = f"{symbol}_volatility_*.jsonl"
                binance_data = get_latest_jsonl_row(binance_dir, binance_pattern)

                # Evaluate gates
                gate_result = evaluate_gates(poly_data, binance_data, signal_config)

                # Get probability
                yes_data = poly_data.get("yes", {}) or {}
                prob_up = yes_data.get("mid", 0.5)
                zone = get_probability_zone(prob_up)

                # Compute microstructure
                prev_imbalance = state_tracker.get_prev_imbalance(coin)
                micro = compute_microstructure(poly_data, prev_imbalance)

                # Extract Binance indicators (moved earlier for state update)
                rv_5m = None
                taker_ratio = None
                regime = None
                if binance_data:
                    vol_data = binance_data.get("volatility", {}) or {}
                    rv_5m = vol_data.get("rv_5m")
                    sentiment = binance_data.get("sentiment", {}) or {}
                    taker_ratio = sentiment.get("taker_buy_sell_ratio")
                    class_data = binance_data.get("classification", {}) or {}
                    regime = class_data.get("cluster")

                # Update state (now includes defense indicators)
                window_start = poly_data.get("window_start", 0)
                state = state_tracker.update(
                    coin=coin,
                    gates_passed=gate_result.all_passed,
                    prob=prob_up,
                    imbalance=micro.imbalance,
                    spread_pct=micro.spread_pct,
                    microprice_edge=micro.microprice_vs_mid,
                    window_start=window_start,
                    now_ts=now_ts,
                    rv_5m=rv_5m,
                    taker_ratio=taker_ratio,
                )

                # === DEFENSE MODE: Check open positions ===
                position = position_manager.get_position_for_market(market)
                if position is not None and defense_config.enabled:
                    # Update defense state
                    position_manager.update_defense_state(
                        token_id=position.token_id,
                        imbalance=micro.imbalance,
                        microprice_vs_mid=micro.microprice_vs_mid,
                        rv_5m=rv_5m or 0.0,
                        taker_ratio=taker_ratio or 1.0,
                    )

                    # Get z-score from state
                    z_score = None
                    if state.prob_stats and state.prob_stats.z_score is not None:
                        z_score = state.prob_stats.z_score

                    # Get imbalance delta from state
                    imbalance_delta = state_tracker.get_imbalance_delta_30s(coin)

                    # Check defense
                    defense_result = position_manager.check_defense(
                        token_id=position.token_id,
                        remaining_s=gate_result.time_remaining_s,
                        prob_up=prob_up,
                        imbalance=micro.imbalance,
                        imbalance_delta=imbalance_delta,
                        microprice_vs_mid=micro.microprice_vs_mid,
                        taker_ratio=taker_ratio or 1.0,
                        rv_5m=rv_5m or 0.0,
                        regime=regime,
                        z_score=z_score,
                    )

                    # Log defense status if score is concerning
                    if defense_result.score >= defense_config.alert_threshold:
                        log.warning(f"[{market}] DEFENSE: {format_defense_result(defense_result)}")

                    # Execute defense action
                    if defense_result.action == DefenseAction.EXIT_EMERGENCY:
                        log.warning(f"[{market}] EMERGENCY EXIT: {defense_result.reason}")
                        current_price = prob_up if position.side == "UP" else (1 - prob_up)
                        closed = position_manager.exit_early(
                            position.token_id, current_price, defense_result.reason
                        )
                        if closed:
                            log.info(f"[{market}] Closed early: P&L=${closed.pnl:.2f}")
                            # TODO: Execute actual sell order via trader
                        continue  # Skip entry logic for this market

                    elif defense_result.action == DefenseAction.EXIT_TACTICAL:
                        log.warning(f"[{market}] TACTICAL EXIT: {defense_result.reason}")
                        current_price = prob_up if position.side == "UP" else (1 - prob_up)
                        closed = position_manager.exit_early(
                            position.token_id, current_price, defense_result.reason
                        )
                        if closed:
                            log.info(f"[{market}] Closed tactically: P&L=${closed.pnl:.2f}")
                        continue

                    elif defense_result.action == DefenseAction.EXIT_TIME:
                        log.warning(f"[{market}] TIME EXIT: {defense_result.reason}")
                        current_price = prob_up if position.side == "UP" else (1 - prob_up)
                        closed = position_manager.exit_early(
                            position.token_id, current_price, defense_result.reason
                        )
                        if closed:
                            log.info(f"[{market}] Time exit: P&L=${closed.pnl:.2f}")
                        continue

                    elif defense_result.action == DefenseAction.FLIP:
                        log.warning(f"[{market}] FLIP: {defense_result.reason}")
                        new_side = "DOWN" if position.side == "UP" else "UP"
                        current_price = prob_up if position.side == "UP" else (1 - prob_up)
                        closed, new_trade = position_manager.flip_position(
                            position.token_id, current_price, new_side, defense_result.reason
                        )
                        if closed:
                            log.info(f"[{market}] Flipped from {position.side} to {new_side}")
                            log.info(f"[{market}] Closed: P&L=${closed.pnl:.2f}")
                            if new_trade:
                                log.info(f"[{market}] New position: {new_trade.size:.2f} @ {new_trade.entry_price:.4f}")
                        continue

                    # If HOLD, continue monitoring (no action needed)

                # Compute score
                score_result = compute_score(
                    imbalance=micro.imbalance,
                    microprice_edge=micro.microprice_vs_mid,
                    imbalance_delta=micro.imbalance_delta,
                    impact_buy=micro.impact_buy_100,
                    impact_sell=micro.impact_sell_100,
                    spread_pct=micro.spread_pct,
                    rv_5m=rv_5m,
                    taker_ratio=taker_ratio,
                    persistence_s=state.persistence_s,
                )

                # Make decision
                decision = decide(
                    all_gates_passed=gate_result.all_passed,
                    gate_failure_reason=gate_result.reason,
                    prob_up=prob_up,
                    zone=zone,
                    persistence_s=state.persistence_s,
                    score=score_result.score,
                    regime=regime,
                    remaining_s=gate_result.time_remaining_s,  # Para forced entry
                    config=decision_config,
                )

                # Check if we should enter
                if decision.action == Action.ENTER:
                    # Risk checks
                    can_trade, reason = risk_manager.can_trade(
                        market=market,
                        volatility=rv_5m,
                        regime=regime,
                        liquidity=micro.mid * (yes_data.get("bid_depth", 0) + yes_data.get("ask_depth", 0)),
                        spread_pct=micro.spread_pct,
                    )

                    if not can_trade:
                        log.info(f"[{market}] ENTRY blocked: {reason}")
                        continue

                    # Position check
                    can_pos, pos_reason = position_manager.can_trade()
                    if not can_pos:
                        log.info(f"[{market}] ENTRY blocked: {pos_reason}")
                        continue

                    # Calculate position size
                    entry_price = prob_up if decision.side.value == "UP" else (1 - prob_up)
                    size = position_manager.calculate_position_size(
                        entry_price=entry_price,
                        score=score_result.score,
                        confidence=decision.confidence.value,
                    )

                    # Apply risk limit
                    max_size = risk_manager.calculate_max_size(
                        position_manager.current_bankroll,
                        entry_price,
                    )
                    size = min(size, max_size)

                    if size < bot_config.min_position_size:
                        log.info(f"[{market}] Size too small: {size}")
                        continue

                    # Get token ID
                    token_id = yes_data.get("token_id", "")
                    if decision.side.value == "DOWN":
                        no_data = poly_data.get("no", {}) or {}
                        token_id = no_data.get("token_id", "")

                    if not token_id:
                        log.error(f"[{market}] No token_id found")
                        continue

                    # Execute trade
                    log.info(
                        f"[{market}] ★ EXECUTING TRADE ★ "
                        f"{decision.side.value} {size:.2f} @ {entry_price:.4f} "
                        f"score={score_result.score:.2f} conf={decision.confidence.value}"
                    )

                    order = await trader.place_order(
                        token_id=token_id,
                        side=OrderSide.BUY,
                        size=size,
                        price=entry_price,
                    )

                    if order:
                        # Record position
                        position_manager.open_position(
                            market=market,
                            token_id=token_id,
                            side=decision.side.value,
                            size=size,
                            entry_price=entry_price,
                        )
                        risk_manager.open_position(market)

                        log.info(f"[{market}] Order placed: {order.order_id}")
                    else:
                        log.error(f"[{market}] Failed to place order")

            # Log status periodically
            if seq % 60 == 0:
                log.info(f"Status: {position_manager.get_daily_summary()}")
                log.info(f"Risk: {risk_manager.format_status()}")

            seq += 1

            # Sleep until next tick
            elapsed = time.monotonic() - t0
            sleep_time = 1.0 - elapsed
            if sleep_time > 0:
                try:
                    await asyncio.wait_for(shutdown_event.wait(), timeout=sleep_time)
                except asyncio.TimeoutError:
                    pass

    finally:
        await trader.close()
        log.info(f"Bot stopped. Final stats: {position_manager.get_stats()}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Run trading bot")
    parser.add_argument("--paper", action="store_true", help="Paper trading mode")
    parser.add_argument("--dry", action="store_true", help="Dry run (no orders)")
    parser.add_argument("--live", action="store_true", help="Live trading (CAUTION!)")

    args = parser.parse_args()

    # Set mode via environment
    if args.dry:
        os.environ["BOT_DRY_RUN"] = "true"
    elif args.paper:
        os.environ["BOT_DRY_RUN"] = "false"
        os.environ["BOT_PAPER_TRADING"] = "true"
    elif args.live:
        os.environ["BOT_DRY_RUN"] = "false"
        os.environ["BOT_PAPER_TRADING"] = "false"

    loop = asyncio.new_event_loop()

    if sys.platform != "win32":
        loop.add_signal_handler(signal.SIGINT, _signal_handler)
        loop.add_signal_handler(signal.SIGTERM, _signal_handler)
    else:
        signal.signal(signal.SIGINT, lambda s, f: _signal_handler())
        signal.signal(signal.SIGTERM, lambda s, f: _signal_handler())

    try:
        loop.run_until_complete(run_bot())
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt, shutting down...")
        shutdown_event.set()
    finally:
        loop.close()


if __name__ == "__main__":
    main()
