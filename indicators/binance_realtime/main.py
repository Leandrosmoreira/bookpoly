"""
Main entry point for Binance real-time data capture.

Usage:
    python -m indicators.binance_realtime.main

This module:
1. Connects to Binance WebSocket
2. Streams kline data for BTC, ETH, SOL, XRP
3. Calculates reversal indicators in real-time
4. Saves data to JSONL files
"""

import asyncio
import logging
import signal
import sys
import json
import time
from pathlib import Path
from datetime import datetime, timezone

from .config import BinanceRealtimeConfig
from .websocket_client import BinanceWebSocketClient
from .reversal_detector import ReversalDetector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("binance_rt")

# Shutdown event
shutdown_event = asyncio.Event()


def _signal_handler():
    log.info("Shutdown signal received")
    shutdown_event.set()


class JsonlWriter:
    """Simple JSONL writer with daily rotation."""

    def __init__(self, base_dir: str):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.handles: dict[str, any] = {}
        self.current_date: str = ""

    def _get_handle(self, symbol: str):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Rotate if date changed
        if today != self.current_date:
            self.close_all()
            self.current_date = today

        key = f"{symbol}_{today}"
        if key not in self.handles:
            filepath = self.base_dir / f"{symbol}_{today}.jsonl"
            self.handles[key] = open(filepath, "a", encoding="utf-8")

        return self.handles[key]

    def write(self, symbol: str, data: dict):
        fh = self._get_handle(symbol)
        line = json.dumps(data, separators=(",", ":"), ensure_ascii=False) + "\n"
        fh.write(line)
        fh.flush()

    def close_all(self):
        for fh in self.handles.values():
            fh.close()
        self.handles.clear()


async def on_candle_update(symbol: str, detector: ReversalDetector, is_closed: bool):
    """Callback when candle data is received."""
    if not is_closed:
        return  # Only process closed candles

    if detector.has_enough_data:
        # Detect for both sides and log
        result_up = detector.detect("UP")
        result_down = detector.detect("DOWN")

        log.info(
            f"[{symbol}] price=${detector.current_price:.2f} | "
            f"RSI={result_up.rsi:.0f} | "
            f"momentum={result_up.momentum_pct*100:+.2f}% | "
            f"rev_score(UP)={result_up.score:.2f} rev_score(DOWN)={result_down.score:.2f}"
        )


async def run_capture():
    """Run the data capture loop."""
    config = BinanceRealtimeConfig()

    log.info("=" * 60)
    log.info("📡 BINANCE REAL-TIME DATA CAPTURE")
    log.info("=" * 60)
    log.info(f"Symbols: {', '.join(config.symbols)}")
    log.info(f"Output: {config.out_dir}")
    log.info(f"Reversal thresholds: alert={config.reversal_alert}, block={config.reversal_block}")
    log.info("=" * 60)

    # Initialize client
    client = BinanceWebSocketClient(config)
    client.add_callback(on_candle_update)

    # Run WebSocket
    try:
        await client.connect()
    except KeyboardInterrupt:
        log.info("Interrupted")
    finally:
        await client.disconnect()


def main():
    """Entry point."""
    loop = asyncio.new_event_loop()

    if sys.platform != "win32":
        loop.add_signal_handler(signal.SIGINT, _signal_handler)
        loop.add_signal_handler(signal.SIGTERM, _signal_handler)
    else:
        signal.signal(signal.SIGINT, lambda s, f: _signal_handler())
        signal.signal(signal.SIGTERM, lambda s, f: _signal_handler())

    try:
        loop.run_until_complete(run_capture())
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt")
    finally:
        loop.close()


if __name__ == "__main__":
    main()
