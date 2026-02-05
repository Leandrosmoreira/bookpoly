import json
import time
import asyncio
import logging
import aiohttp
from config import Config

log = logging.getLogger(__name__)

WINDOW_SECONDS = 900  # 15 minutes


def current_window_ts(server_time_s: float) -> int:
    """Round down to the nearest 15-minute boundary."""
    return int(server_time_s // WINDOW_SECONDS) * WINDOW_SECONDS


def make_slug(coin: str, window_ts: int) -> str:
    """Build the Gamma API slug for a 15-min updown market."""
    return f"{coin.lower()}-updown-15m-{window_ts}"


class MarketDiscovery:
    def __init__(self, config: Config):
        self.gamma_base = config.gamma_base.rstrip("/")
        self.timeout = aiohttp.ClientTimeout(total=config.request_timeout)
        self.coins = config.coins
        self.max_retries = config.max_retries
        # Cache: coin -> {condition_id, yes_token, no_token, window_ts, market_label}
        self._cache: dict[str, dict] = {}

    def _market_label(self, coin: str) -> str:
        return f"{coin.upper()}15m"

    async def discover_one(
        self,
        session: aiohttp.ClientSession,
        coin: str,
        server_time_s: float,
    ) -> dict | None:
        """Discover the current 15-min market for a coin via Gamma slug lookup."""
        window_ts = current_window_ts(server_time_s)
        slug = make_slug(coin, window_ts)

        # Check cache
        cached = self._cache.get(coin)
        if cached and cached["window_ts"] == window_ts:
            return cached

        url = f"{self.gamma_base}/events/slug/{slug}"
        last_err = None

        for attempt in range(1, self.max_retries + 2):  # extra attempt for transition
            try:
                async with session.get(url, timeout=self.timeout) as resp:
                    if resp.status == 404:
                        # Market may not exist yet during transition
                        log.warning(f"Market not found: {slug} (attempt {attempt})")
                        last_err = "market_not_found"
                        if attempt <= self.max_retries:
                            await asyncio.sleep(min(0.5 * attempt, 3.0))
                            continue
                        return None
                    resp.raise_for_status()
                    event = await resp.json()
            except Exception as e:
                log.error(f"Gamma fetch error for {slug}: {e}")
                last_err = str(e)
                if attempt <= self.max_retries:
                    await asyncio.sleep(min(0.5 * attempt, 3.0))
                    continue
                return None

            # Extract market info from event
            markets = event.get("markets", [])
            if not markets:
                log.warning(f"No markets in event for {slug}")
                return None

            market = markets[0]
            raw_tokens = market.get("clobTokenIds", [])
            # Gamma API may return clobTokenIds as a JSON string or a list
            if isinstance(raw_tokens, str):
                try:
                    clob_tokens = json.loads(raw_tokens)
                except json.JSONDecodeError:
                    log.error(f"Cannot parse clobTokenIds for {slug}: {raw_tokens[:100]}")
                    return None
            else:
                clob_tokens = raw_tokens
            if len(clob_tokens) < 2:
                log.error(f"Missing clobTokenIds for {slug}: {clob_tokens}")
                return None

            info = {
                "condition_id": market.get("conditionId", ""),
                "yes_token": clob_tokens[0],
                "no_token": clob_tokens[1],
                "window_ts": window_ts,
                "market_label": self._market_label(coin),
                "slug": slug,
            }
            self._cache[coin] = info
            log.info(f"Discovered {info['market_label']}: {slug} | YES={clob_tokens[0][:16]}... NO={clob_tokens[1][:16]}...")
            return info

        return None

    async def discover_all(
        self,
        session: aiohttp.ClientSession,
        server_time_s: float,
    ) -> dict[str, dict]:
        """Discover all configured markets in parallel.
        Returns dict[coin -> market_info]. Missing markets are excluded.
        """
        tasks = {
            coin: self.discover_one(session, coin, server_time_s)
            for coin in self.coins
        }
        results_list = await asyncio.gather(*tasks.values(), return_exceptions=True)
        markets = {}
        for coin, result in zip(tasks.keys(), results_list):
            if isinstance(result, Exception):
                log.error(f"Discovery exception for {coin}: {result}")
            elif result is not None:
                markets[coin] = result
            else:
                log.warning(f"Could not discover market for {coin}")
        return markets

    def needs_rediscovery(self, current_time_s: float) -> bool:
        """Check if we've crossed a 15-min boundary since last discovery."""
        current_window = current_window_ts(current_time_s)
        for coin, info in self._cache.items():
            if info["window_ts"] != current_window:
                return True
        return len(self._cache) < len(self.coins)
