import json
import time
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import aiohttp
from config import Config

log = logging.getLogger(__name__)

WINDOW_SECONDS_15M = 900    # 15 minutes
WINDOW_SECONDS_5M = 300    # 5 minutes
WINDOW_SECONDS_1H = 3600   # 1 hour
WINDOW_SECONDS_4H = 14400  # 4 hours

# Nome completo do ativo para o slug 1h (Polymarket usa ex.: bitcoin-up-or-down-february-22-2pm-et)
COIN_FULL_NAME_1H = {"btc": "bitcoin", "eth": "ethereum", "sol": "solana", "xrp": "xrp"}
# Nome completo para o slug 1d (ex.: bitcoin-up-or-down-on-february-23), inclui hype
COIN_FULL_NAME_1D = {"btc": "bitcoin", "eth": "ethereum", "sol": "solana", "xrp": "xrp", "hype": "hyperliquid"}

ET = ZoneInfo("America/New_York")


def current_window_ts(server_time_s: float, interval: str = "15m") -> int:
    """Round down to the nearest window boundary (15m, 5m, 1h, 4h or 1d)."""
    if interval == "1d":
        # Mercado diário: resolve ao meio-dia ET; "atual" = hoje se antes do meio-dia ET, senão amanhã
        dt_et = datetime.fromtimestamp(server_time_s, tz=ET)
        if dt_et.hour < 12:
            resolution_date = dt_et.date()
        else:
            resolution_date = (dt_et + timedelta(days=1)).date()
        noon_et = datetime(resolution_date.year, resolution_date.month, resolution_date.day, 12, 0, 0, tzinfo=ET)
        return int(noon_et.timestamp())
    if interval == "4h":
        # Mercados 4h da Polymarket usam janelas em ET (12PM, 4PM, 8PM, 12AM, 4AM, 8AM ET)
        dt_et = datetime.fromtimestamp(server_time_s, tz=ET)
        window_hour = (dt_et.hour // 4) * 4
        window_start_et = datetime(
            dt_et.year, dt_et.month, dt_et.day, window_hour, 0, 0, tzinfo=ET
        )
        return int(window_start_et.timestamp())
    if interval == "5m":
        sec = WINDOW_SECONDS_5M
    elif interval == "1h":
        sec = WINDOW_SECONDS_1H
    else:
        sec = WINDOW_SECONDS_15M
    return int(server_time_s // sec) * sec


def _slug_1h(coin: str, window_ts: int) -> str:
    """Build Gamma slug for 1h market: e.g. bitcoin-up-or-down-february-22-2pm-et."""
    name = COIN_FULL_NAME_1H.get(coin.lower(), coin.lower())
    dt_utc = datetime.fromtimestamp(window_ts, tz=timezone.utc)
    dt_et = dt_utc.astimezone(ET)
    month = dt_et.strftime("%B").lower()
    day = dt_et.day
    hour_12 = dt_et.hour % 12 or 12
    am_pm = "am" if dt_et.hour < 12 else "pm"
    return f"{name}-up-or-down-{month}-{day}-{hour_12}{am_pm}-et"


def _slug_1d(coin: str, window_ts: int) -> str:
    """Build Gamma slug for daily market: e.g. bitcoin-up-or-down-on-february-23."""
    name = COIN_FULL_NAME_1D.get(coin.lower(), coin.lower())
    dt_utc = datetime.fromtimestamp(window_ts, tz=timezone.utc)
    dt_et = dt_utc.astimezone(ET)
    month = dt_et.strftime("%B").lower()
    day = dt_et.day
    return f"{name}-up-or-down-on-{month}-{day}"


def make_slug(coin: str, window_ts: int, interval: str = "15m") -> str:
    """Build the Gamma API slug for an updown market (15m, 5m, 1h, 4h or 1d)."""
    if interval == "1h":
        return _slug_1h(coin, window_ts)
    if interval == "1d":
        return _slug_1d(coin, window_ts)
    if interval == "4h":
        return f"{coin.lower()}-updown-4h-{window_ts}"
    if interval == "5m":
        suffix = "5m"
    else:
        suffix = "15m"
    return f"{coin.lower()}-updown-{suffix}-{window_ts}"


def _market_label(coin: str, interval: str) -> str:
    """e.g. BTC15m, BTC5m, BTC1h, BTC4h, BTC1d, HYPE1d."""
    suffix_map = {"5m": "5m", "1h": "1h", "4h": "4h", "1d": "1d"}
    suffix = suffix_map.get(interval, "15m")
    return f"{coin.upper()}{suffix}"


class MarketDiscovery:
    def __init__(self, config: Config):
        self.gamma_base = config.gamma_base.rstrip("/")
        self.timeout = aiohttp.ClientTimeout(total=config.request_timeout)
        self.coins = config.coins
        self.coins_5m = getattr(config, "coins_5m", []) or []
        self.coins_1h = getattr(config, "coins_1h", []) or []
        self.coins_4h = getattr(config, "coins_4h", []) or []
        self.coins_1d = getattr(config, "coins_1d", []) or []
        self.max_retries = config.max_retries
        # Cache: (coin, interval) -> {condition_id, yes_token, no_token, window_ts, market_label, ...}
        self._cache: dict[tuple[str, str], dict] = {}
        # List of (coin, interval) to discover
        self._specs: list[tuple[str, str]] = [(c, "15m") for c in self.coins]
        if self.coins_5m:
            self._specs += [(c, "5m") for c in self.coins_5m]
        if self.coins_1h:
            self._specs += [(c, "1h") for c in self.coins_1h]
        if self.coins_4h:
            self._specs += [(c, "4h") for c in self.coins_4h]
        if self.coins_1d:
            self._specs += [(c, "1d") for c in self.coins_1d]

    def _market_label(self, coin: str, interval: str) -> str:
        return _market_label(coin, interval)

    def clear_cache_for_interval(self, interval: str):
        """Clear cache for an interval when its window boundary changed."""
        to_drop = [k for k in self._cache if k[1] == interval]
        for k in to_drop:
            del self._cache[k]

    async def discover_one(
        self,
        session: aiohttp.ClientSession,
        coin: str,
        server_time_s: float,
        interval: str = "15m",
    ) -> dict | None:
        """Discover the current market for a coin/interval via Gamma slug lookup."""
        window_ts = current_window_ts(server_time_s, interval)
        slug = make_slug(coin, window_ts, interval)

        # Check cache
        cache_key = (coin, interval)
        cached = self._cache.get(cache_key)
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
                "market_label": self._market_label(coin, interval),
                "slug": slug,
            }
            self._cache[cache_key] = info
            log.info(f"Discovered {info['market_label']}: {slug} | YES={clob_tokens[0][:16]}... NO={clob_tokens[1][:16]}...")
            return info

        return None

    async def discover_all(
        self,
        session: aiohttp.ClientSession,
        server_time_s: float,
    ) -> dict[str, dict]:
        """Discover all configured markets (15m + 5m) in parallel.
        Returns dict[market_label -> market_info]. Missing markets are excluded.
        """
        tasks = {
            (coin, interval): self.discover_one(session, coin, server_time_s, interval)
            for coin, interval in self._specs
        }
        results_list = await asyncio.gather(*tasks.values(), return_exceptions=True)
        markets = {}
        for (coin, interval), result in zip(tasks.keys(), results_list):
            label = self._market_label(coin, interval)
            if isinstance(result, Exception):
                log.error(f"Discovery exception for {label}: {result}")
            elif result is not None:
                markets[label] = result
            else:
                log.warning(f"Could not discover market for {label}")
        return markets

    def needs_rediscovery(self, current_time_s: float) -> bool:
        """Check if we've crossed a window boundary since last discovery."""
        for coin, interval in self._specs:
            current_window = current_window_ts(current_time_s, interval)
            cached = self._cache.get((coin, interval))
            if cached is None or cached["window_ts"] != current_window:
                return True
        return len(self._cache) < len(self._specs)
