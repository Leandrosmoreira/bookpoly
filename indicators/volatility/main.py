"""
Real-time volatility indicator recorder (1Hz).

Usage:
    python -m indicators.volatility.main
"""

import sys
import os
import time
import asyncio
import signal
import logging
from collections import deque

# Add parent dirs to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp
from config import VolatilityConfig
from binance_client import BinanceClient
from calculator import compute_metrics
from classifier import VolatilityClassifier
from recorder import build_row, build_error_row
from writer import Writer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("volatility")

# Graceful shutdown
shutdown_event = asyncio.Event()


def _signal_handler():
    log.info("Shutdown signal received")
    shutdown_event.set()


async def warmup_buffers(
    client: BinanceClient,
    session: aiohttp.ClientSession,
    symbols: list[str],
    buffer_size: int,
) -> dict[str, deque]:
    """Fetch initial klines to fill buffers."""
    buffers = {}

    for symbol in symbols:
        log.info(f"Warming up {symbol} buffer ({buffer_size} klines)...")
        klines = await client.fetch_klines(session, symbol, "1m", buffer_size)
        if klines:
            buffers[symbol] = deque(klines, maxlen=buffer_size)
            log.info(f"  {symbol}: {len(klines)} klines loaded")
        else:
            buffers[symbol] = deque(maxlen=buffer_size)
            log.warning(f"  {symbol}: no klines fetched")

    return buffers


async def fetch_symbol_update(
    client: BinanceClient,
    session: aiohttp.ClientSession,
    symbol: str,
) -> dict:
    """Fetch latest data for a symbol."""
    return await client.fetch_all_metrics(session, symbol, kline_limit=5)


async def run():
    config = VolatilityConfig()
    log.info(f"Config: symbols={config.symbols}, hz={config.poll_hz}")

    writer = Writer(config.out_dir)
    client = BinanceClient(config)
    classifier = VolatilityClassifier()

    connector = aiohttp.TCPConnector(limit=20)
    async with aiohttp.ClientSession(connector=connector) as session:
        # Warmup: fill kline buffers
        buffers = await warmup_buffers(client, session, config.symbols, config.rv_window_long)

        seq = 0
        log.info("Starting 1Hz loop...")

        while not shutdown_event.is_set():
            t0 = time.monotonic()
            ts_system = time.time()

            # Fetch updates for all symbols in parallel
            tasks = {
                symbol: fetch_symbol_update(client, session, symbol)
                for symbol in config.symbols
            }
            results = await asyncio.gather(*tasks.values(), return_exceptions=True)

            latency_ms = (time.monotonic() - t0) * 1000

            # Process each symbol
            for symbol, result in zip(tasks.keys(), results):
                if isinstance(result, Exception):
                    log.error(f"Error fetching {symbol}: {result}")
                    row = build_error_row(symbol, seq, ts_system, str(result))
                    writer.write(symbol, row)
                    continue

                # Update buffer with new klines
                if result.get("klines"):
                    for kline in result["klines"]:
                        # Only add if newer than last kline in buffer
                        if not buffers[symbol] or kline["close_time"] > buffers[symbol][-1]["close_time"]:
                            buffers[symbol].append(kline)

                # Compute metrics
                metrics = compute_metrics(list(buffers[symbol]), result)

                if not metrics:
                    row = build_error_row(symbol, seq, ts_system, "no_metrics")
                    writer.write(symbol, row)
                    continue

                # Classify
                cvi = metrics.get("volatility", {}).get("cvi", 0)
                cluster, percentile = classifier.classify(symbol, cvi)

                # Build and write row
                row = build_row(symbol, metrics, cluster, percentile, seq, ts_system, latency_ms)
                writer.write(symbol, row)

                # Log summary
                vol = metrics.get("volatility", {})
                log.info(
                    f"[{symbol}] seq={seq} "
                    f"cvi={vol.get('cvi', 0):.3f} "
                    f"cluster={cluster} "
                    f"rv_1h={vol.get('rv_1h', 0):.2%} "
                    f"latency={latency_ms:.0f}ms"
                )

            seq += 1
            await _sleep_until_next(t0, config.poll_hz)

    # Cleanup
    writer.close_all()
    log.info(f"Shutdown complete. {seq} ticks recorded.")


async def _sleep_until_next(t0: float, hz: int):
    """Sleep until the next tick, compensating for elapsed time."""
    elapsed = time.monotonic() - t0
    sleep_time = (1.0 / hz) - elapsed
    if sleep_time > 0:
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=sleep_time)
        except asyncio.TimeoutError:
            pass


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
