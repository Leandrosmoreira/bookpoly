import sys
import os
import time
import asyncio
import signal
import logging
import aiohttp

# Ensure src/ is on path when running as `python src/main.py`
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import Config
from clob_client import ClobClient
from market_discovery import MarketDiscovery, current_window_ts
from recorder import build_row, build_error_row
from writer import Writer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("main")

# --- Graceful shutdown ---
shutdown_event = asyncio.Event()


def _signal_handler():
    log.info("Shutdown signal received")
    shutdown_event.set()


async def run():
    config = Config()
    log.info(f"Config: coins={config.coins}, depth={config.depth_levels}, hz={config.poll_hz}")

    writer = Writer(config.out_dir)
    client = ClobClient(config)
    discovery = MarketDiscovery(config)

    connector = aiohttp.TCPConnector(limit=16)
    async with aiohttp.ClientSession(connector=connector) as session:
        # 1. Sync clock
        server_offset = await client.get_time_offset(session)

        # 2. Initial discovery
        server_time = time.time() + server_offset
        markets = await discovery.discover_all(session, server_time)

        if not markets:
            log.error("No markets discovered. Exiting.")
            return

        log.info(f"Discovered {len(markets)} markets: {list(markets.keys())}")

        seq = 0
        last_window = current_window_ts(server_time)

        while not shutdown_event.is_set():
            t0 = time.monotonic()
            ts_system = time.time()
            server_time = ts_system + server_offset

            # 3. Rediscover on window transition
            cur_window = current_window_ts(server_time)
            if cur_window != last_window:
                log.info(f"Window transition: {last_window} -> {cur_window}")
                last_window = cur_window
                # Clear cache to force rediscovery
                discovery._cache.clear()
                new_markets = await discovery.discover_all(session, server_time)
                if new_markets:
                    markets = new_markets
                else:
                    log.warning("Rediscovery failed, keeping previous markets")

            # 4. Collect all token IDs
            all_token_ids = []
            for info in markets.values():
                all_token_ids.append(info["yes_token"])
                all_token_ids.append(info["no_token"])

            # 5. Fetch all books
            try:
                books = await client.fetch_books_batch(session, all_token_ids)
                latency_ms = (time.monotonic() - t0) * 1000
            except Exception as e:
                log.error(f"Batch fetch failed: {e}")
                for coin, info in markets.items():
                    row = build_error_row(coin, info, seq, ts_system, str(e))
                    writer.write(info["market_label"], row)
                seq += 1
                await _sleep_until_next(t0, config.poll_hz)
                continue

            # 6. Build and write rows
            for coin, info in markets.items():
                yes_book = books.get(info["yes_token"])
                no_book = books.get(info["no_token"])

                if yes_book is None and no_book is None:
                    row = build_error_row(coin, info, seq, ts_system, "both_books_empty")
                else:
                    row = build_row(coin, info, yes_book, no_book, seq, ts_system, latency_ms)

                writer.write(info["market_label"], row)

                # Log summary
                d = row.get("derived")
                if d and d.get("prob_up") is not None:
                    log.info(
                        f"[{info['market_label']}] seq={seq} "
                        f"mid_up={d['mid_yes_cents']}¢ "
                        f"overround={d['overround']} "
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
            pass  # Normal: timeout means we should proceed to next tick


def main():
    loop = asyncio.new_event_loop()

    # Register signal handlers
    if sys.platform != "win32":
        loop.add_signal_handler(signal.SIGINT, _signal_handler)
        loop.add_signal_handler(signal.SIGTERM, _signal_handler)
    else:
        # Windows: use signal module directly
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
