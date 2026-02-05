import time
import logging
import aiohttp
from config import Config

log = logging.getLogger(__name__)


def normalize_book(raw: dict, depth: int) -> dict:
    """Sort and truncate bids/asks, convert strings to floats."""
    bids = sorted(raw.get("bids", []), key=lambda x: float(x["price"]), reverse=True)[:depth]
    asks = sorted(raw.get("asks", []), key=lambda x: float(x["price"]))[:depth]
    return {
        "bids": [{"p": float(b["price"]), "s": float(b["size"])} for b in bids],
        "asks": [{"p": float(a["price"]), "s": float(a["size"])} for a in asks],
    }


class ClobClient:
    def __init__(self, config: Config):
        self.base = config.clob_base.rstrip("/")
        self.timeout = aiohttp.ClientTimeout(total=config.request_timeout)
        self.depth = config.depth_levels
        self.max_retries = config.max_retries

    async def get_time_offset(self, session: aiohttp.ClientSession) -> float:
        """Get offset between local clock and server clock in seconds.
        Returns offset such that: server_time = local_time + offset
        """
        url = f"{self.base}/time"
        try:
            t0 = time.time()
            async with session.get(url, timeout=self.timeout) as resp:
                # /time returns plain text (epoch seconds), not JSON
                text = await resp.text()
                t1 = time.time()
                rtt = t1 - t0
                server_time = float(text.strip())
                local_mid = t0 + rtt / 2
                offset = server_time - local_mid
                log.info(f"Clock offset: {offset:.3f}s (RTT: {rtt*1000:.0f}ms)")
                return offset
        except Exception as e:
            log.warning(f"Failed to get server time, using offset=0: {e}")
            return 0.0

    async def fetch_book(self, session: aiohttp.ClientSession, token_id: str) -> dict | None:
        """Fetch order book for a single token. Returns normalized book or None."""
        url = f"{self.base}/book"
        params = {"token_id": token_id}
        for attempt in range(1, self.max_retries + 1):
            try:
                async with session.get(url, params=params, timeout=self.timeout) as resp:
                    if resp.status == 404:
                        log.warning(f"Book not found for token {token_id[:16]}...")
                        return None
                    resp.raise_for_status()
                    raw = await resp.json()
                    return normalize_book(raw, self.depth)
            except Exception as e:
                if attempt == self.max_retries:
                    log.error(f"fetch_book failed for {token_id[:16]}... after {attempt} attempts: {e}")
                    return None
                log.debug(f"fetch_book attempt {attempt} failed: {e}")
        return None

    async def fetch_books_batch(
        self, session: aiohttp.ClientSession, token_ids: list[str]
    ) -> dict[str, dict]:
        """Fetch books for multiple tokens in parallel.
        Returns dict[token_id -> normalized book].
        """
        import asyncio

        async def _fetch(tid: str):
            return tid, await self.fetch_book(session, tid)

        tasks = [_fetch(tid) for tid in token_ids]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        results = {}
        for item in raw_results:
            if isinstance(item, Exception):
                log.error(f"fetch_book exception: {item}")
            else:
                tid, book = item
                results[tid] = book
        return results
