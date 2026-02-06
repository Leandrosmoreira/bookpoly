"""
Backfill historical volatility data from Binance.

Usage:
    python -m indicators.volatility.backfill
    python -m indicators.volatility.backfill --symbol BTCUSDT
    python -m indicators.volatility.backfill --start 2026-02-01 --end 2026-02-05
"""

import sys
import os
import time
import asyncio
import argparse
import logging
from datetime import datetime, timezone, timedelta

# Add parent dirs to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp
from config import VolatilityConfig
from binance_client import BinanceClient
from calculator import compute_metrics
from classifier import VolatilityClassifier
from recorder import build_row
from writer import Writer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("backfill")


async def fetch_klines_range(
    client: BinanceClient,
    session: aiohttp.ClientSession,
    symbol: str,
    start_ms: int,
    end_ms: int,
    interval: str = "1m",
    batch_size: int = 1500,
) -> list[dict]:
    """Fetch klines for a time range, handling pagination."""
    all_klines = []
    current_start = start_ms

    while current_start < end_ms:
        klines = await client.fetch_klines(
            session,
            symbol,
            interval,
            limit=batch_size,
            start_time=current_start,
            end_time=end_ms,
        )

        if not klines:
            break

        all_klines.extend(klines)
        # Move start to after last kline
        current_start = klines[-1]["close_time"] + 1

        log.info(f"  {symbol}: fetched {len(klines)} klines, total {len(all_klines)}")

        # Small delay to avoid rate limits
        await asyncio.sleep(0.1)

    return all_klines


async def backfill_symbol(
    client: BinanceClient,
    session: aiohttp.ClientSession,
    writer: Writer,
    classifier: VolatilityClassifier,
    symbol: str,
    start_date: datetime,
    end_date: datetime,
    config: VolatilityConfig,
):
    """Backfill data for a single symbol."""
    log.info(f"Backfilling {symbol} from {start_date.date()} to {end_date.date()}")

    start_ms = int(start_date.timestamp() * 1000)
    end_ms = int(end_date.timestamp() * 1000)

    # Fetch all klines for the period
    klines = await fetch_klines_range(
        client, session, symbol, start_ms, end_ms, config.kline_interval, config.backfill_batch_size
    )

    if not klines:
        log.warning(f"No klines found for {symbol}")
        return 0

    log.info(f"  {symbol}: processing {len(klines)} klines...")

    # Process klines and write to JSONL
    # We'll create a row for each minute (each kline)
    rows_written = 0
    buffer = []
    buffer_size = config.rv_window_long  # Need this many klines for calculations

    for i, kline in enumerate(klines):
        buffer.append(kline)
        if len(buffer) > buffer_size:
            buffer.pop(0)

        # Skip until we have enough data
        if len(buffer) < 60:
            continue

        # Compute metrics using buffer
        # For backfill, we don't have real-time sentiment data, so use empty dict
        sentiment = {"ticker": None, "funding": [], "oi": None, "ls_ratio": [], "top_ls_ratio": [], "taker_ratio": []}
        metrics = compute_metrics(buffer, sentiment)

        if not metrics:
            continue

        # Classify
        cvi = metrics.get("volatility", {}).get("cvi", 0)
        cluster, percentile = classifier.classify(symbol, cvi)

        # Build row
        ts_system = kline["close_time"] / 1000.0
        row = build_row(symbol, metrics, cluster, percentile, i, ts_system, 0)

        # Write to appropriate day's file
        date_str = datetime.fromtimestamp(ts_system, tz=timezone.utc).strftime("%Y-%m-%d")
        writer.write(symbol, row, date_str)
        rows_written += 1

        if rows_written % 10000 == 0:
            log.info(f"  {symbol}: {rows_written} rows written...")

    log.info(f"  {symbol}: completed, {rows_written} rows written")
    return rows_written


async def backfill_all(config: VolatilityConfig, symbols: list[str] = None, start_str: str = None, end_str: str = None):
    """Backfill all configured symbols."""
    if symbols is None:
        symbols = config.symbols

    if start_str:
        start_date = datetime.strptime(start_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    else:
        start_date = datetime.strptime(config.backfill_start, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    if end_str:
        end_date = datetime.strptime(end_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    else:
        end_date = datetime.now(timezone.utc)

    log.info(f"Starting backfill: {symbols}")
    log.info(f"  Date range: {start_date.date()} to {end_date.date()}")

    writer = Writer(config.out_dir)
    client = BinanceClient(config)
    classifier = VolatilityClassifier()

    connector = aiohttp.TCPConnector(limit=10)
    async with aiohttp.ClientSession(connector=connector) as session:
        total_rows = 0

        for symbol in symbols:
            try:
                rows = await backfill_symbol(
                    client, session, writer, classifier, symbol, start_date, end_date, config
                )
                total_rows += rows
            except Exception as e:
                log.error(f"Error backfilling {symbol}: {e}")
                import traceback
                traceback.print_exc()

    writer.close_all()
    log.info(f"Backfill complete. Total rows: {total_rows}")
    return total_rows


def main():
    parser = argparse.ArgumentParser(description="Backfill volatility data from Binance")
    parser.add_argument("--symbol", "-s", help="Single symbol to backfill (default: all)")
    parser.add_argument("--start", help="Start date YYYY-MM-DD (default: from config)")
    parser.add_argument("--end", help="End date YYYY-MM-DD (default: now)")
    args = parser.parse_args()

    config = VolatilityConfig()

    symbols = [args.symbol.upper()] if args.symbol else None

    asyncio.run(backfill_all(config, symbols, args.start, args.end))


if __name__ == "__main__":
    main()
