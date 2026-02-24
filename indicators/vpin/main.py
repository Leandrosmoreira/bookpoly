"""
Real-time VPIN (Volume-synchronized Probability of Informed Trading) recorder.

Connects to Binance Futures aggTrade WebSocket, computes VPIN in real time,
and emits JSONL rows at 1Hz.

Usage:
    python -m indicators.vpin.main
"""

import sys
import os
import time
import asyncio
import signal
import logging
import statistics

# Add parent dirs to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp

from config import VpinConfig
from ws_client import AggTradeStream
from calculator import VpinCalculator
from recorder import build_row, build_error_row
from writer import Writer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("vpin")

# Graceful shutdown
shutdown_event = asyncio.Event()


def _signal_handler():
    log.info("Shutdown signal received")
    shutdown_event.set()


async def fetch_klines(
    session: aiohttp.ClientSession,
    rest_base: str,
    symbol: str,
    interval: str = "1m",
    limit: int = 60,
) -> list[dict]:
    """Fetch kline data from Binance REST API for warmup."""
    url = f"{rest_base}/fapi/v1/klines"
    params = {"symbol": symbol.upper(), "interval": interval, "limit": limit}
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return [
                {
                    "volume": float(k[5]),
                    "taker_buy_base": float(k[9]),
                }
                for k in data
            ]
    except Exception as e:
        log.error(f"Failed to fetch klines for {symbol}: {e}")
        return []


async def warmup_bucket_volumes(
    config: VpinConfig,
    session: aiohttp.ClientSession,
) -> dict[str, float]:
    """
    Compute bucket volume for each symbol.

    If config.bucket_volume is "auto", uses median 1m volume * bucket_volume_pct.
    Otherwise uses the fixed value for all symbols.
    """
    if config.bucket_volume != "auto":
        fixed = float(config.bucket_volume)
        log.info(f"Using fixed bucket volume: {fixed}")
        return {s: fixed for s in config.symbols}

    log.info(f"Auto-computing bucket volumes from {config.warmup_klines} klines...")
    volumes = {}

    for symbol in config.symbols:
        klines = await fetch_klines(
            session, config.rest_base, symbol, "1m", config.warmup_klines,
        )
        if not klines:
            # Fallback: use a reasonable default
            volumes[symbol] = 100.0
            log.warning(f"  {symbol}: no klines, using default bucket_volume=100")
            continue

        vols = [k["volume"] for k in klines if k["volume"] > 0]
        if not vols:
            volumes[symbol] = 100.0
            log.warning(f"  {symbol}: zero volumes, using default bucket_volume=100")
            continue

        median_vol = statistics.median(vols)
        bucket_vol = round(median_vol * config.bucket_volume_pct, 4)
        bucket_vol = max(bucket_vol, 0.001)  # Safety floor
        volumes[symbol] = bucket_vol
        log.info(f"  {symbol}: median_1m_vol={median_vol:.2f}, bucket_volume={bucket_vol:.4f}")

    return volumes


async def trade_consumer(
    stream: AggTradeStream,
    calculators: dict[str, VpinCalculator],
):
    """Consume trades from WebSocket and feed into calculators."""
    while not shutdown_event.is_set():
        trade = await stream.get_trade(timeout=0.5)
        if trade is None:
            continue

        symbol = trade["symbol"]
        calc = calculators.get(symbol)
        if calc:
            calc.add_trade(
                ts_ms=trade["ts_ms"],
                price=trade["price"],
                qty=trade["qty"],
                is_buy=trade["is_buy"],
            )


async def emit_loop(
    config: VpinConfig,
    calculators: dict[str, VpinCalculator],
    writer: Writer,
):
    """Emit JSONL rows at configured interval."""
    seq = 0

    while not shutdown_event.is_set():
        t0 = time.monotonic()
        ts_system = time.time()

        for symbol, calc in calculators.items():
            metrics = calc.get_metrics()
            row = build_row(symbol, calc, seq, ts_system, 0)
            writer.write(symbol, row)

            # Console log
            vpin_str = f"{metrics.vpin:.3f}" if metrics.vpin is not None else "warmup"
            ema_str = f"{metrics.vpin_ema:.3f}" if metrics.vpin_ema is not None else "---"
            log.info(
                f"[{symbol.upper()}] "
                f"vpin={vpin_str} "
                f"ema={ema_str} "
                f"tox={metrics.flow_toxicity} "
                f"buckets={metrics.completed_buckets}/{calc.num_buckets} "
                f"trades={metrics.trades_total}"
            )

        seq += 1

        # Sleep until next emit
        elapsed = time.monotonic() - t0
        sleep_time = config.emit_interval_s - elapsed
        if sleep_time > 0:
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=sleep_time)
            except asyncio.TimeoutError:
                pass


async def run():
    config = VpinConfig()
    log.info(f"VPIN Config: symbols={config.symbols}, buckets={config.num_buckets}")

    writer = Writer(config.out_dir)

    connector = aiohttp.TCPConnector(limit=20)
    async with aiohttp.ClientSession(connector=connector) as session:
        # 1. Warmup: compute bucket volumes
        bucket_volumes = await warmup_bucket_volumes(config, session)

        # 2. Create calculators
        calculators = {
            sym: VpinCalculator(bv, config.num_buckets)
            for sym, bv in bucket_volumes.items()
        }

        # 3. Create WebSocket stream
        stream = AggTradeStream(
            symbols=config.symbols,
            ws_base=config.ws_base,
            reconnect_delay=config.ws_reconnect_delay,
            max_reconnect_delay=config.ws_max_reconnect_delay,
        )

        # 4. Run WebSocket + consumer + emitter concurrently
        ws_task = asyncio.create_task(stream.start(session))
        consumer_task = asyncio.create_task(trade_consumer(stream, calculators))
        emit_task = asyncio.create_task(emit_loop(config, calculators, writer))

        log.info("VPIN recorder started. Press Ctrl+C to stop.")

        # Wait for shutdown
        await shutdown_event.wait()

        # Cleanup
        log.info("Shutting down...")
        await stream.stop()
        ws_task.cancel()
        consumer_task.cancel()
        emit_task.cancel()

        try:
            await asyncio.gather(ws_task, consumer_task, emit_task, return_exceptions=True)
        except Exception:
            pass

    # Final summary
    writer.close_all()
    total_trades = sum(c._trades_total for c in calculators.values())
    total_buckets = sum(len(c._completed) for c in calculators.values())
    log.info(f"Shutdown complete. trades={total_trades} buckets={total_buckets}")


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
