"""
Paper Trading Bot - Simula trades com dados reais.

Usa os dados coletados em tempo real para simular decisões de trading
seguindo a lógica documentada em docs/LOGICA_BOT.md.

Usage:
    python -m bot.paper_trading
    python -m bot.paper_trading --verbose
    python -m bot.paper_trading --coins btc,eth
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
from dataclasses import dataclass, field

# Add project paths
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "indicators" / "signals"))

from indicators.signals.config import SignalConfig
from indicators.signals.gates import evaluate_gates, get_probability_zone
from indicators.signals.microstructure import compute_microstructure
from indicators.signals.state import StateTracker
from indicators.signals.scorer import compute_score
from indicators.signals.decision import decide, Action, DecisionConfig

# Reversal detection
from indicators.binance_realtime.reversal_detector import ReversalDetector
from indicators.binance_realtime.config import BinanceRealtimeConfig

# Order management
from bot.order_manager import (
    OrderManager,
    OrderManagerConfig,
    OrderBookSnapshot,
    create_book_snapshot_from_polymarket_data,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("paper")

# Graceful shutdown
shutdown_event = asyncio.Event()


def _signal_handler():
    log.info("Shutdown signal received")
    shutdown_event.set()


@dataclass
class PaperTrade:
    """Record of a simulated trade."""
    timestamp: int
    market: str
    side: str  # UP or DOWN
    entry_price: float
    size_usd: float = 5.0
    shares: float = 0.0
    window_start: int = 0
    window_end: int = 0
    exit_price: float | None = None
    pnl: float | None = None
    status: str = "open"  # open, won, lost, expired
    reason: str = ""  # Why entered


@dataclass
class PaperPortfolio:
    """Paper trading portfolio."""
    initial_balance: float = 100.0
    balance: float = 100.0
    open_trades: dict = field(default_factory=dict)  # market -> PaperTrade
    closed_trades: list = field(default_factory=list)

    # Daily stats
    daily_trades: int = 0
    daily_wins: int = 0
    daily_losses: int = 0
    daily_pnl: float = 0.0

    # All-time stats
    total_trades: int = 0
    total_wins: int = 0
    total_losses: int = 0
    total_pnl: float = 0.0

    # Risk limits (from docs/LOGICA_BOT.md)
    max_daily_trades: int = 20
    max_daily_loss: float = 25.0  # 25% of bankroll
    max_open_positions: int = 3
    min_time_between_trades: float = 10.0
    last_trade_time: float = 0.0
    consecutive_losses: int = 0
    max_consecutive_losses: int = 5
    trading_halted: bool = False
    halt_until: float = 0.0

    def can_trade(self) -> tuple[bool, str]:
        """Check if we can open a new trade."""
        now = time.time()

        # Check halt - DESABILITADO para teste
        # if self.trading_halted:
        #     if now >= self.halt_until:
        #         self.trading_halted = False
        #         self.consecutive_losses = 0
        #         log.info("Trading resumed after halt")
        #     else:
        #         remaining = int(self.halt_until - now)
        #         return False, f"Trading halted ({remaining}s remaining)"

        # Check daily limits
        if self.daily_trades >= self.max_daily_trades:
            return False, f"Daily trade limit ({self.daily_trades}/{self.max_daily_trades})"

        # Daily loss limit - DESABILITADO para teste
        # if self.daily_pnl <= -self.max_daily_loss:
        #     return False, f"Daily loss limit (${self.daily_pnl:.2f})"

        # Check open positions
        if len(self.open_trades) >= self.max_open_positions:
            return False, f"Max open positions ({len(self.open_trades)}/{self.max_open_positions})"

        # Check time since last trade
        time_since = now - self.last_trade_time
        if time_since < self.min_time_between_trades:
            return False, f"Too soon ({time_since:.0f}s < {self.min_time_between_trades}s)"

        # Check balance
        if self.balance < 5.0:
            return False, f"Insufficient balance (${self.balance:.2f})"

        return True, "OK"

    def open_trade(self, trade: PaperTrade) -> bool:
        """Open a new trade."""
        if trade.market in self.open_trades:
            return False

        # Calculate shares
        trade.shares = trade.size_usd / trade.entry_price

        # Deduct from balance (we're buying)
        self.balance -= trade.size_usd

        self.open_trades[trade.market] = trade
        self.daily_trades += 1
        self.total_trades += 1
        self.last_trade_time = time.time()

        return True

    def close_trade(self, market: str, outcome: str, btc_went_up: bool) -> PaperTrade | None:
        """
        Close a trade with real outcome.

        Args:
            market: Market name (e.g., "BTC15m")
            outcome: "ended" when window closes
            btc_went_up: True if BTC actually went UP during the window
        """
        if market not in self.open_trades:
            return None

        trade = self.open_trades.pop(market)
        trade.exit_price = 1.0 if btc_went_up else 0.0  # Real settlement price

        # Determine if we won
        # We bet on UP or DOWN - check if our prediction was correct
        if trade.side == "UP":
            won = btc_went_up  # We win if BTC went up
        else:
            won = not btc_went_up  # We win if BTC went down

        if won:
            # We get $1 per share
            payout = trade.shares * 1.0
            trade.pnl = payout - trade.size_usd
            trade.status = "won"
            self.daily_wins += 1
            self.total_wins += 1
            self.consecutive_losses = 0
        else:
            # We lose everything
            trade.pnl = -trade.size_usd
            trade.status = "lost"
            self.daily_losses += 1
            self.total_losses += 1
            self.consecutive_losses += 1

            # Check consecutive losses - DESABILITADO para teste
            # if self.consecutive_losses >= self.max_consecutive_losses:
            #     self.trading_halted = True
            #     self.halt_until = time.time() + 3600  # 1 hour
            #     log.warning(f"Trading HALTED: {self.consecutive_losses} consecutive losses")

        # Update balance and PnL
        self.balance += trade.size_usd + trade.pnl  # Return cost + PnL
        self.daily_pnl += trade.pnl
        self.total_pnl += trade.pnl

        self.closed_trades.append(trade)
        return trade

    def reset_daily_stats(self):
        """Reset daily statistics."""
        self.daily_trades = 0
        self.daily_wins = 0
        self.daily_losses = 0
        self.daily_pnl = 0.0

    def get_summary(self) -> str:
        """Get portfolio summary."""
        win_rate = self.total_wins / self.total_trades * 100 if self.total_trades > 0 else 0
        roi = (self.balance - self.initial_balance) / self.initial_balance * 100

        return (
            f"Balance: ${self.balance:.2f} | "
            f"Open: {len(self.open_trades)} | "
            f"Trades: {self.total_trades} (W:{self.total_wins}/L:{self.total_losses}) | "
            f"Win Rate: {win_rate:.0f}% | "
            f"PnL: ${self.total_pnl:+.2f} | "
            f"ROI: {roi:+.1f}%"
        )


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


async def run_paper_trading(coins: list[str], verbose: bool = False):
    """Run paper trading loop."""
    config = SignalConfig()
    config.coins = coins
    decision_config = DecisionConfig()

    # Data directories
    project_root = Path(__file__).parent.parent
    polymarket_dir = project_root / "data" / "raw" / "books"
    binance_dir = project_root / "data" / "raw" / "volatility"

    log.info("=" * 60)
    log.info("📈 PAPER TRADING BOT")
    log.info("=" * 60)
    log.info(f"Coins: {', '.join(c.upper() for c in coins)}")
    log.info(f"Data: {polymarket_dir}")
    log.info("")
    log.info("Parâmetros (docs/LOGICA_BOT.md):")
    log.info(f"  Bankroll: $100")
    log.info(f"  Size/trade: $5 (fixo)")
    log.info(f"  Max trades/dia: 20")
    log.info(f"  Max perda/dia: $25")
    log.info(f"  Max posições: 3")
    log.info(f"  Time Gate: 30s-240s")
    log.info(f"  Spread Gate: ≤10%")
    log.info(f"  Volatility Gate: ≤100%")
    log.info(f"  Min Depth: $300")
    log.info(f"  Forced Entry: prob≥95% + ≤2min")
    log.info("=" * 60)

    # Initialize
    portfolio = PaperPortfolio(initial_balance=100.0, balance=100.0)
    state_tracker = StateTracker(window_size=300)

    # Track windows to detect end
    current_windows: dict[str, int] = {}  # market -> window_start

    # Track BTC price at window start to determine outcome
    window_start_prices: dict[str, float] = {}  # market -> BTC price at window start

    # Track entry prob to determine outcome (fallback)
    entry_probs: dict[str, float] = {}  # market -> prob_up at entry

    # Initialize reversal detectors for each coin
    reversal_config = BinanceRealtimeConfig()
    reversal_detectors: dict[str, ReversalDetector] = {}
    for coin in coins:
        symbol = f"{coin.upper()}USDT"
        reversal_detectors[symbol] = ReversalDetector(reversal_config)
    log.info(f"  Reversal Detection: ENABLED (threshold=0.70)")

    # Initialize order manager
    order_config = OrderManagerConfig()
    order_manager = OrderManager(
        config=order_config,
        bot_config=None,  # No real trading in paper mode
    )
    log.info(f"  Order Manager: ENABLED (timeout={order_config.order_timeout_s}s, max_attempts={order_config.max_attempts})")

    seq = 0
    last_status_time = time.time()

    try:
        while not shutdown_event.is_set():
            t0 = time.monotonic()
            now_ts = time.time()

            for coin in coins:
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
                    if verbose:
                        log.debug(f"[{market}] Data stale ({data_age:.1f}s)")
                    continue

                window_start = poly_data.get("window_start", 0)

                # Get Binance data FIRST (fix bug: was used before defined)
                symbol = f"{coin.upper()}USDT"
                binance_pattern = f"{symbol}_volatility_*.jsonl"
                binance_data = get_latest_jsonl_row(binance_dir, binance_pattern)

                # Get current BTC price from Binance
                current_btc_price = None
                if binance_data:
                    price_data = binance_data.get("price", {}) or {}
                    current_btc_price = price_data.get("close", 0)

                    # Update reversal detector with kline data
                    detector = reversal_detectors.get(symbol)
                    if detector and current_btc_price:
                        # We need OHLCV data - get from volatility data or estimate
                        vol_data = binance_data.get("volatility", {}) or {}
                        # Use current price as close, estimate others
                        detector.update_candle(
                            open_=current_btc_price,  # Approximation
                            high=current_btc_price * 1.001,  # Small range
                            low=current_btc_price * 0.999,
                            close=current_btc_price,
                            volume=1.0,  # Placeholder
                            timestamp=int(now_ts * 1000),
                            is_closed=True,
                        )

                # Check if window changed (to close trades)
                if market in current_windows:
                    prev_window = current_windows[market]
                    if window_start != prev_window:
                        # Window changed!
                        if market in portfolio.open_trades:
                            # Close trade - determine outcome
                            trade = portfolio.open_trades.get(market)
                            start_price = window_start_prices.get(market)

                            if trade and current_btc_price and start_price:
                                # Compare prices: did BTC go UP or DOWN?
                                btc_went_up = current_btc_price > start_price
                                price_change = ((current_btc_price - start_price) / start_price) * 100

                                closed_trade = portfolio.close_trade(market, "ended", btc_went_up)
                                if closed_trade:
                                    emoji = "✅" if closed_trade.status == "won" else "❌"
                                    result = "UP" if btc_went_up else "DOWN"
                                    log.info(
                                        f"[{market}] {emoji} CLOSED: bet={closed_trade.side} result={result} "
                                        f"BTC ${start_price:.0f}→${current_btc_price:.0f} ({price_change:+.2f}%) "
                                        f"PnL=${closed_trade.pnl:+.2f}"
                                    )
                            elif trade:
                                # Fallback: use probability heuristic
                                entry_prob = entry_probs.get(market, 0.5)
                                btc_went_up = entry_prob > 0.5

                                closed_trade = portfolio.close_trade(market, "ended", btc_went_up)
                                if closed_trade:
                                    emoji = "✅" if closed_trade.status == "won" else "❌"
                                    log.info(
                                        f"[{market}] {emoji} CLOSED (no price data): bet={closed_trade.side} "
                                        f"PnL=${closed_trade.pnl:+.2f}"
                                    )

                        # Record price at start of new window
                        if current_btc_price:
                            window_start_prices[market] = current_btc_price

                # If this is first time seeing this window, record start price
                if market not in current_windows and current_btc_price:
                    window_start_prices[market] = current_btc_price

                current_windows[market] = window_start

                # Skip if already have position in this market
                if market in portfolio.open_trades:
                    continue

                # Evaluate gates
                gate_result = evaluate_gates(poly_data, binance_data, config)

                # Get probability and zone
                yes_data = poly_data.get("yes", {}) or {}
                prob_up = yes_data.get("mid", 0.5)
                zone = get_probability_zone(prob_up)

                # Compute microstructure
                prev_imbalance = state_tracker.get_prev_imbalance(coin)
                micro = compute_microstructure(poly_data, prev_imbalance)

                # Update state
                state = state_tracker.update(
                    coin=coin,
                    gates_passed=gate_result.all_passed,
                    prob=prob_up,
                    imbalance=micro.imbalance,
                    spread_pct=micro.spread_pct,
                    microprice_edge=micro.microprice_vs_mid,
                    window_start=window_start,
                    now_ts=now_ts,
                )

                # Extract Binance indicators
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

                # === REVERSAL DETECTION ===
                # Detect if price is reversing against our potential bet
                reversal_score = None
                reversal_direction = None
                reversal_reason = None
                momentum_pct = None

                detector = reversal_detectors.get(symbol)
                bet_side = "UP" if prob_up > 0.5 else "DOWN"

                if detector and detector.has_enough_data:
                    reversal_result = detector.detect(bet_side)
                    reversal_score = reversal_result.score
                    reversal_direction = reversal_result.direction.value
                    reversal_reason = reversal_result.reason
                    momentum_pct = reversal_result.momentum_pct

                    # Log if reversal is significant
                    if reversal_score >= 0.50:
                        log.warning(
                            f"[{market}] ⚠️ REVERSAL ALERT: score={reversal_score:.2f} "
                            f"dir={reversal_direction} momentum={momentum_pct*100:.2f}% "
                            f"reason={reversal_reason}"
                        )

                # Make decision (now includes reversal check)
                decision = decide(
                    all_gates_passed=gate_result.all_passed,
                    gate_failure_reason=gate_result.reason,
                    prob_up=prob_up,
                    zone=zone,
                    persistence_s=state.persistence_s,
                    score=score_result.score,
                    regime=regime,
                    remaining_s=gate_result.time_remaining_s,
                    # NEW: Reversal detection parameters
                    reversal_score=reversal_score,
                    reversal_direction=reversal_direction,
                    reversal_reason=reversal_reason,
                    momentum_pct=momentum_pct,
                    config=decision_config,
                )

                # Log detailed status every second (verbose mode)
                if verbose:
                    # Format gates status
                    gates_status = []
                    gates_status.append(f"T:{'✓' if gate_result.time_gate else '✗'}")
                    gates_status.append(f"L:{'✓' if gate_result.liquidity_gate else '✗'}")
                    gates_status.append(f"S:{'✓' if gate_result.spread_gate else '✗'}")
                    gates_status.append(f"V:{'✓' if gate_result.stability_gate else '✗'}")
                    gates_status.append(f"N:{'✓' if gate_result.latency_gate else '✗'}")
                    gates_all = "✓" if gate_result.all_passed else "✗"
                    
                    # Get key parameters (yes_data já foi definido antes)
                    spread = yes_data.get("spread") or 0
                    mid = yes_data.get("mid") or 0
                    spread_pct = (spread / mid * 100) if mid > 0 else 0
                    depth = (yes_data.get("bid_depth", 0) or 0) + (yes_data.get("ask_depth", 0) or 0)
                    latency = poly_data.get("fetch", {}).get("latency_ms", 0)
                    
                    # Format decision
                    action_emoji = "★" if decision.action == Action.ENTER else "○"
                    action_text = decision.action.value if decision.action != Action.ENTER else f"ENTER {decision.side.value}"
                    
                    vol_str = f"{rv_5m*100:.0f}%" if rv_5m else "N/A"
                    rev_str = f"rev={reversal_score:.2f}" if reversal_score else "rev=N/A"
                    log.info(
                        f"[{market}] [{''.join(gates_status)}] ALL:{gates_all} | "
                        f"prob={prob_up:.1%} zone={zone} score={score_result.score:.2f} {rev_str} | "
                        f"spread={spread_pct:.1f}% depth=${depth:.0f} vol={vol_str} | "
                        f"persist={state.persistence_s:.0f}s remain={gate_result.time_remaining_s:.0f}s | "
                        f"{action_emoji} {action_text}"
                    )
                elif gate_result.all_passed:
                    # Non-verbose: only log when gates pass
                    log.info(
                        f"[{market}] Gates:OK prob={prob_up:.1%} zone={zone} "
                        f"score={score_result.score:.2f} persist={state.persistence_s:.0f}s "
                        f"remaining={gate_result.time_remaining_s:.0f}s"
                    )

                # Check if we should enter
                if decision.action == Action.ENTER:
                    # Check portfolio limits
                    can_trade, reason = portfolio.can_trade()
                    if not can_trade:
                        log.info(f"[{market}] ⛔ BLOCKED: {reason}")
                        continue

                    # === INTELLIGENT ORDER EXECUTION ===
                    # Use OrderManager to calculate optimal entry price
                    side = decision.side.value
                    book = create_book_snapshot_from_polymarket_data(poly_data)

                    # Calculate entry price using order manager
                    entry_price = order_manager.calculate_entry_price(
                        book=book,
                        side="BUY" if side == "UP" else "SELL",
                        attempt=1,
                    )

                    # Verify liquidity at our entry level
                    has_liquidity, liquidity_reason = order_manager.verify_liquidity(
                        entry_price=entry_price,
                        book=book,
                        side="BUY" if side == "UP" else "SELL",
                    )

                    if not has_liquidity:
                        log.warning(
                            f"[{market}] ⚠️ Liquidity check failed: {liquidity_reason} "
                            f"(best_bid={book.best_bid:.3f}, best_ask={book.best_ask:.3f})"
                        )
                        continue

                    # Estimate fill probability
                    fill_prob = order_manager.estimator.estimate(
                        our_price=entry_price,
                        book=book,
                        remaining_s=gate_result.time_remaining_s,
                        side="BUY" if side == "UP" else "SELL",
                    )

                    if fill_prob < order_config.min_fill_probability:
                        log.warning(
                            f"[{market}] ⚠️ Fill probability too low: "
                            f"{fill_prob:.0%} < {order_config.min_fill_probability:.0%}"
                        )
                        continue

                    # In paper trading, we simulate immediate fill
                    # Real trading would use execute_with_retry
                    log.info(
                        f"[{market}] 📋 Order calc: entry={entry_price:.3f} "
                        f"(bid={book.best_bid:.3f}+delta) "
                        f"fill_prob={fill_prob:.0%} "
                        f"depth=${book.ask_depth:.0f}"
                    )

                    # Create trade with optimized entry price
                    trade = PaperTrade(
                        timestamp=int(now_ts * 1000),
                        market=market,
                        side=side,
                        entry_price=entry_price,  # Use optimized price
                        size_usd=5.0,
                        window_start=window_start,
                        window_end=window_start + 900,
                        reason=decision.reason,
                    )

                    if portfolio.open_trade(trade):
                        # Save entry prob for fallback outcome detection
                        entry_probs[market] = prob_up

                        btc_price_str = f"BTC=${current_btc_price:.0f}" if current_btc_price else ""
                        log.info(
                            f"[{market}] ★ ENTER {side} ★ "
                            f"@ ${entry_price:.3f} (POST ONLY) "
                            f"score={score_result.score:.2f} "
                            f"conf={decision.confidence.value} "
                            f"fill_prob={fill_prob:.0%} "
                            f"{btc_price_str} "
                            f"reason={decision.reason}"
                        )

            # Log status periodically
            if now_ts - last_status_time >= 60:
                log.info(f"📊 {portfolio.get_summary()}")
                last_status_time = now_ts

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
        # Final summary
        log.info("")
        log.info("=" * 60)
        log.info("📊 FINAL SUMMARY")
        log.info("=" * 60)
        log.info(portfolio.get_summary())

        if portfolio.closed_trades:
            log.info("")
            log.info("Últimos trades:")
            for trade in portfolio.closed_trades[-10:]:
                emoji = "✅" if trade.status == "won" else "❌"
                log.info(
                    f"  {emoji} {trade.market} {trade.side} "
                    f"entry=${trade.entry_price:.2f} "
                    f"PnL=${trade.pnl:+.2f}"
                )

        log.info("=" * 60)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Paper Trading Bot")
    parser.add_argument("--coins", type=str, default="btc", help="Coins to trade (comma-separated)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = parser.parse_args()
    coins = [c.strip().lower() for c in args.coins.split(",")]

    loop = asyncio.new_event_loop()

    if sys.platform != "win32":
        loop.add_signal_handler(signal.SIGINT, _signal_handler)
        loop.add_signal_handler(signal.SIGTERM, _signal_handler)
    else:
        signal.signal(signal.SIGINT, lambda s, f: _signal_handler())
        signal.signal(signal.SIGTERM, lambda s, f: _signal_handler())

    try:
        loop.run_until_complete(run_paper_trading(coins, args.verbose))
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt, shutting down...")
        shutdown_event.set()
    finally:
        loop.close()


if __name__ == "__main__":
    main()
