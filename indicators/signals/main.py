"""
Real-time gate evaluation loop (1Hz).

Reads the latest Polymarket and Binance JSONL files to evaluate gates.

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
    gates.append(f"T:{'✓' if result.time_gate else '✗'}")
    gates.append(f"L:{'✓' if result.liquidity_gate else '✗'}")
    gates.append(f"S:{'✓' if result.spread_gate else '✗'}")
    gates.append(f"V:{'✓' if result.stability_gate else '✗'}")
    gates.append(f"N:{'✓' if result.latency_gate else '✗'}")

    all_str = "ALL:✓" if result.all_passed else f"ALL:✗({result.reason})"

    return f"[{' '.join(gates)}] {all_str} | prob={prob_up:.1%} zone={zone} remaining={result.time_remaining_s:.0f}s"


async def run():
    config = SignalConfig()

    # Data directories (relative to project root)
    project_root = Path(__file__).parent.parent.parent
    polymarket_dir = project_root / "data" / "raw" / "books"
    binance_dir = project_root / "data" / "raw" / "volatility"

    log.info(f"Config: coins={config.coins}")
    log.info(f"Polymarket data: {polymarket_dir}")
    log.info(f"Binance data: {binance_dir}")
    log.info(f"Time window: {config.time_window_start_s}s - {config.time_window_end_s}s")
    log.info(f"Min depth: ${config.min_depth}, Max spread: {config.max_spread_pct:.1%}")
    log.info(f"Max volatility: {config.max_volatility:.0%}, Max latency: {config.max_latency_ms}ms")

    # State tracking
    persistence_start: dict[str, float] = {}  # coin -> timestamp when gates first passed

    seq = 0
    log.info("Starting 1Hz gate evaluation loop...")

    while not shutdown_event.is_set():
        t0 = time.monotonic()
        now_ts = time.time()

        for coin in config.coins:
            # Get latest Polymarket data
            # Format: BTC15m_2026-02-05.jsonl
            poly_pattern = f"{coin.upper()}15m_*.jsonl"
            poly_data = get_latest_jsonl_row(str(polymarket_dir), poly_pattern)

            if not poly_data:
                log.warning(f"[{coin.upper()}] No Polymarket data found")
                continue

            # Check data freshness (should be < 5 seconds old)
            # Note: ts_ms is in milliseconds
            poly_ts = poly_data.get("ts_ms", 0) / 1000.0
            data_age = now_ts - poly_ts
            if data_age > 5:
                log.warning(f"[{coin.upper()}] Polymarket data stale ({data_age:.1f}s old)")

            # Get latest Binance data
            symbol = f"{coin.upper()}USDT"
            binance_pattern = f"{symbol}_volatility_*.jsonl"
            binance_data = get_latest_jsonl_row(str(binance_dir), binance_pattern)

            if binance_data:
                binance_ts = binance_data.get("ts_system", 0)
                binance_age = now_ts - binance_ts
                if binance_age > 5:
                    log.debug(f"[{coin.upper()}] Binance data slightly stale ({binance_age:.1f}s old)")

            # Evaluate gates
            result = evaluate_gates(poly_data, binance_data, config)

            # Get probability and zone
            # Note: mid price IS the probability (0.0 to 1.0)
            yes_data = poly_data.get("yes", {}) or {}
            prob_up = yes_data.get("mid", 0.5)
            zone = get_probability_zone(prob_up)

            # Track persistence
            if result.all_passed:
                if coin not in persistence_start:
                    persistence_start[coin] = now_ts
                    log.info(f"[{coin.upper()}] Gates passed, starting persistence timer")

                persistence_s = now_ts - persistence_start[coin]
                persistence_ok = persistence_s >= config.min_persistence_s

                # Check entry conditions
                can_enter = (
                    result.all_passed and
                    zone != "danger" and
                    persistence_ok
                )

                if can_enter:
                    log.info(
                        f"[{coin.upper()}] ★ ENTRY SIGNAL ★ "
                        f"zone={zone} persistence={persistence_s:.0f}s prob={prob_up:.1%}"
                    )
                else:
                    extra = f"persist={persistence_s:.0f}s/{config.min_persistence_s}s"
                    log.info(f"[{coin.upper()}] {format_gate_result(result, prob_up, zone)} {extra}")
            else:
                # Reset persistence if gates fail
                if coin in persistence_start:
                    log.info(f"[{coin.upper()}] Gates failed, resetting persistence")
                    del persistence_start[coin]

                log.info(f"[{coin.upper()}] {format_gate_result(result, prob_up, zone)}")

        seq += 1

        # Sleep until next tick
        elapsed = time.monotonic() - t0
        sleep_time = 1.0 - elapsed
        if sleep_time > 0:
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=sleep_time)
            except asyncio.TimeoutError:
                pass

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
