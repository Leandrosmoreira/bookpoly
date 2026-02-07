"""
Complete signal generation loop (1Hz).

Integrates all components:
- Gates (Phase 1)
- Microstructure (Phase 2)
- State tracking (Phase 3)
- Scorer and Decision (Phase 4)

Usage:
    python -m indicators.signals.main
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

# Add parent dirs to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import SignalConfig
from gates import evaluate_gates, get_probability_zone, GateResult
from microstructure import compute_microstructure, MicrostructureMetrics
from state import StateTracker, format_state_summary
from scorer import compute_score, ScoreResult, format_score_breakdown
from decision import decide, Decision, Action, format_decision, DecisionConfig
from recorder import build_signal_row, SignalWriter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("signals")

# Graceful shutdown
shutdown_event = asyncio.Event()


def _signal_handler():
    log.info("Shutdown signal received")
    shutdown_event.set()


def get_latest_jsonl_row(directory: str, pattern: str) -> dict | None:
    """
    Read the last line of the most recent JSONL file matching the pattern.

    Args:
        directory: Directory to search in
        pattern: Glob pattern for filenames (e.g., "*_book_*.jsonl")

    Returns:
        Parsed JSON dict or None if not found
    """
    path = Path(directory)
    if not path.exists():
        return None

    # Find matching files
    files = list(path.glob(pattern))
    if not files:
        return None

    # Get most recent file by modification time
    latest_file = max(files, key=lambda f: f.stat().st_mtime)

    # Read last line
    try:
        with open(latest_file, "rb") as f:
            # Seek to end, then back to find last newline
            f.seek(0, 2)  # End of file
            file_size = f.tell()

            if file_size == 0:
                return None

            # Read backwards to find last complete line
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


def format_gate_result(result: GateResult, prob_up: float, zone: str) -> str:
    """Format gate result for logging."""
    gates = []
    gates.append(f"T:{'Y' if result.time_gate else 'N'}")
    gates.append(f"L:{'Y' if result.liquidity_gate else 'N'}")
    gates.append(f"S:{'Y' if result.spread_gate else 'N'}")
    gates.append(f"V:{'Y' if result.stability_gate else 'N'}")
    gates.append(f"N:{'Y' if result.latency_gate else 'N'}")

    all_str = "ALL:Y" if result.all_passed else f"ALL:N({result.reason})"

    return f"[{' '.join(gates)}] {all_str} | prob={prob_up:.1%} zone={zone} remaining={result.time_remaining_s:.0f}s"


async def run():
    config = SignalConfig()
    decision_config = DecisionConfig()

    # Data directories (relative to project root)
    project_root = Path(__file__).parent.parent.parent
    polymarket_dir = project_root / "data" / "raw" / "books"
    binance_dir = project_root / "data" / "raw" / "volatility"
    signals_dir = project_root / "data" / "raw" / "signals"

    log.info(f"Config: coins={config.coins}")
    log.info(f"Polymarket data: {polymarket_dir}")
    log.info(f"Binance data: {binance_dir}")
    log.info(f"Signals output: {signals_dir}")
    log.info(f"Time window: {config.time_window_start_s}s - {config.time_window_end_s}s")
    log.info(f"Min depth: ${config.min_depth}, Max spread: {config.max_spread_pct:.1%}")
    log.info(f"Max volatility: {config.max_volatility:.0%}, Max latency: {config.max_latency_ms}ms")

    # State tracker (maintains history per coin)
    state_tracker = StateTracker(window_size=300)  # 5 min at 1Hz

    # Signal writer
    writer = SignalWriter(output_dir=signals_dir, prefix="signals")

    seq = 0
    log.info("Starting 1Hz signal generation loop...")

    try:
        while not shutdown_event.is_set():
            t0 = time.monotonic()
            now_ts = time.time()

            for coin in config.coins:
                market = f"{coin.upper()}15m"

                # Get latest Polymarket data
                poly_pattern = f"{coin.upper()}15m_*.jsonl"
                poly_data = get_latest_jsonl_row(str(polymarket_dir), poly_pattern)

                if not poly_data:
                    log.warning(f"[{market}] No Polymarket data found")
                    continue

                # Check data freshness (should be < 5 seconds old)
                poly_ts = poly_data.get("ts_ms", 0) / 1000.0
                data_age = now_ts - poly_ts
                if data_age > 5:
                    log.warning(f"[{market}] Polymarket data stale ({data_age:.1f}s old)")

                # Get latest Binance data
                symbol = f"{coin.upper()}USDT"
                binance_pattern = f"{symbol}_volatility_*.jsonl"
                binance_data = get_latest_jsonl_row(str(binance_dir), binance_pattern)

                if binance_data:
                    binance_ts = binance_data.get("ts_system", 0)
                    binance_age = now_ts - binance_ts
                    if binance_age > 5:
                        log.debug(f"[{market}] Binance data slightly stale ({binance_age:.1f}s old)")

                # === PHASE 1: GATES ===
                gate_result = evaluate_gates(poly_data, binance_data, config)

                # Get probability and zone
                yes_data = poly_data.get("yes", {}) or {}
                prob_up = yes_data.get("mid", 0.5)
                zone = get_probability_zone(prob_up)

                # === PHASE 2: MICROSTRUCTURE ===
                prev_imbalance = state_tracker.get_prev_imbalance(coin)
                micro = compute_microstructure(poly_data, prev_imbalance)

                # === PHASE 3: STATE ===
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
                )

                # === PHASE 4: SCORER ===
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

                # === PHASE 4: DECISION ===
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

                # === LOGGING ===
                if decision.action == Action.ENTER:
                    log.info(f"[{market}] {format_decision(decision)}")
                elif gate_result.all_passed:
                    # Gates passed but not entering (persistence or score)
                    log.info(
                        f"[{market}] {format_gate_result(gate_result, prob_up, zone)} | "
                        f"{format_score_breakdown(score_result)} | "
                        f"persist={state.persistence_s:.0f}s"
                    )
                else:
                    # Gates failed - only log every 10 ticks to reduce noise
                    if seq % 10 == 0:
                        log.debug(f"[{market}] {format_gate_result(gate_result, prob_up, zone)}")

                # === RECORD ===
                ts_ms = poly_data.get("ts_ms", int(now_ts * 1000))
                signal_row = build_signal_row(
                    ts_ms=ts_ms,
                    market=market,
                    window_start=window_start,
                    time_remaining_s=gate_result.time_remaining_s,
                    prob_up=prob_up,
                    gates=gate_result,
                    micro=micro,
                    state=state,
                    score_result=score_result,
                    decision=decision,
                    binance_data=binance_data,
                )
                writer.write(signal_row)

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
        writer.close()
        log.info(f"Shutdown complete. {seq} ticks processed.")


def main():
    loop = asyncio.new_event_loop()

    # Register signal handlers
    if sys.platform != "win32":
        loop.add_signal_handler(signal.SIGINT, _signal_handler)
        loop.add_signal_handler(signal.SIGTERM, _signal_handler)
    else:
        signal.signal(signal.SIGINT, lambda s, f: _signal_handler())
        signal.signal(signal.SIGTERM, lambda s, f: _signal_handler())

    try:
        loop.run_until_complete(run())
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt, shutting down...")
        shutdown_event.set()
    finally:
        loop.close()


if __name__ == "__main__":
    main()
