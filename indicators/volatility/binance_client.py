import asyncio
import logging
import aiohttp
from typing import Any
from config import VolatilityConfig

log = logging.getLogger(__name__)


class BinanceClient:
    """Async client for Binance Futures API."""

    def __init__(self, config: VolatilityConfig):
        self.base = config.binance_base.rstrip("/")
        self.timeout = aiohttp.ClientTimeout(total=config.request_timeout)
        self.max_retries = config.max_retries

    async def _request(
        self,
        session: aiohttp.ClientSession,
        endpoint: str,
        params: dict = None,
    ) -> Any:
        """Make a GET request with retry logic."""
        url = f"{self.base}{endpoint}"
        for attempt in range(1, self.max_retries + 1):
            try:
                async with session.get(url, params=params, timeout=self.timeout) as resp:
                    if resp.status == 429:
                        # Rate limited, wait and retry
                        wait = int(resp.headers.get("Retry-After", 5))
                        log.warning(f"Rate limited, waiting {wait}s")
                        await asyncio.sleep(wait)
                        continue
                    resp.raise_for_status()
                    return await resp.json()
            except asyncio.TimeoutError:
                log.warning(f"Timeout on {endpoint} (attempt {attempt})")
                if attempt == self.max_retries:
                    raise
            except aiohttp.ClientError as e:
                log.warning(f"Client error on {endpoint}: {e} (attempt {attempt})")
                if attempt == self.max_retries:
                    raise
            await asyncio.sleep(0.5 * attempt)
        return None

    async def fetch_klines(
        self,
        session: aiohttp.ClientSession,
        symbol: str,
        interval: str = "1m",
        limit: int = 500,
        start_time: int = None,
        end_time: int = None,
    ) -> list[dict]:
        """Fetch kline/candlestick data.

        Returns list of dicts with: open_time, open, high, low, close, volume, close_time, etc.
        """
        params = {"symbol": symbol, "interval": interval, "limit": min(limit, 1500)}
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time

        data = await self._request(session, "/fapi/v1/klines", params)
        if not data:
            return []

        # Parse kline array into dict
        klines = []
        for k in data:
            klines.append({
                "open_time": k[0],
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "close_time": k[6],
                "quote_volume": float(k[7]),
                "trades": k[8],
                "taker_buy_base": float(k[9]),
                "taker_buy_quote": float(k[10]),
            })
        return klines

    async def fetch_ticker_24h(
        self,
        session: aiohttp.ClientSession,
        symbol: str,
    ) -> dict | None:
        """Fetch 24hr ticker stats."""
        params = {"symbol": symbol}
        data = await self._request(session, "/fapi/v1/ticker/24hr", params)
        if not data:
            return None
        return {
            "price": float(data.get("lastPrice", 0)),
            "high_24h": float(data.get("highPrice", 0)),
            "low_24h": float(data.get("lowPrice", 0)),
            "change_pct": float(data.get("priceChangePercent", 0)),
            "volume_24h": float(data.get("volume", 0)),
            "quote_volume_24h": float(data.get("quoteVolume", 0)),
        }

    async def fetch_funding_rate(
        self,
        session: aiohttp.ClientSession,
        symbol: str,
        limit: int = 1,
    ) -> list[dict]:
        """Fetch funding rate history."""
        params = {"symbol": symbol, "limit": limit}
        data = await self._request(session, "/fapi/v1/fundingRate", params)
        if not data:
            return []
        return [
            {
                "funding_time": d["fundingTime"],
                "funding_rate": float(d["fundingRate"]),
                "mark_price": float(d.get("markPrice", 0)),
            }
            for d in data
        ]

    async def fetch_open_interest(
        self,
        session: aiohttp.ClientSession,
        symbol: str,
    ) -> dict | None:
        """Fetch current open interest."""
        params = {"symbol": symbol}
        data = await self._request(session, "/fapi/v1/openInterest", params)
        if not data:
            return None
        return {
            "open_interest": float(data.get("openInterest", 0)),
            "time": data.get("time", 0),
        }

    async def fetch_open_interest_hist(
        self,
        session: aiohttp.ClientSession,
        symbol: str,
        period: str = "5m",
        limit: int = 30,
        start_time: int = None,
        end_time: int = None,
    ) -> list[dict]:
        """Fetch open interest history (last 30 days only)."""
        params = {"symbol": symbol, "period": period, "limit": min(limit, 500)}
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time

        data = await self._request(session, "/futures/data/openInterestHist", params)
        if not data:
            return []
        return [
            {
                "timestamp": d["timestamp"],
                "open_interest": float(d["sumOpenInterest"]),
                "open_interest_value": float(d["sumOpenInterestValue"]),
            }
            for d in data
        ]

    async def fetch_long_short_ratio(
        self,
        session: aiohttp.ClientSession,
        symbol: str,
        period: str = "5m",
        limit: int = 1,
    ) -> list[dict]:
        """Fetch global long/short account ratio."""
        params = {"symbol": symbol, "period": period, "limit": limit}
        data = await self._request(session, "/futures/data/globalLongShortAccountRatio", params)
        if not data:
            return []
        return [
            {
                "timestamp": d["timestamp"],
                "long_short_ratio": float(d["longShortRatio"]),
                "long_account": float(d["longAccount"]),
                "short_account": float(d["shortAccount"]),
            }
            for d in data
        ]

    async def fetch_top_trader_ls_ratio(
        self,
        session: aiohttp.ClientSession,
        symbol: str,
        period: str = "5m",
        limit: int = 1,
    ) -> list[dict]:
        """Fetch top trader long/short position ratio."""
        params = {"symbol": symbol, "period": period, "limit": limit}
        data = await self._request(session, "/futures/data/topLongShortPositionRatio", params)
        if not data:
            return []
        return [
            {
                "timestamp": d["timestamp"],
                "long_short_ratio": float(d["longShortRatio"]),
                "long_account": float(d["longAccount"]),
                "short_account": float(d["shortAccount"]),
            }
            for d in data
        ]

    async def fetch_taker_buy_sell_ratio(
        self,
        session: aiohttp.ClientSession,
        symbol: str,
        period: str = "5m",
        limit: int = 1,
    ) -> list[dict]:
        """Fetch taker buy/sell volume ratio."""
        params = {"symbol": symbol, "period": period, "limit": limit}
        data = await self._request(session, "/futures/data/takerlongshortRatio", params)
        if not data:
            return []
        return [
            {
                "timestamp": d["timestamp"],
                "buy_sell_ratio": float(d["buySellRatio"]),
                "buy_vol": float(d["buyVol"]),
                "sell_vol": float(d["sellVol"]),
            }
            for d in data
        ]

    async def fetch_all_metrics(
        self,
        session: aiohttp.ClientSession,
        symbol: str,
        kline_limit: int = 360,
    ) -> dict:
        """Fetch all metrics for a symbol in parallel."""
        tasks = {
            "klines": self.fetch_klines(session, symbol, "1m", kline_limit),
            "ticker": self.fetch_ticker_24h(session, symbol),
            "funding": self.fetch_funding_rate(session, symbol, 1),
            "oi": self.fetch_open_interest(session, symbol),
            "ls_ratio": self.fetch_long_short_ratio(session, symbol, "5m", 1),
            "top_ls_ratio": self.fetch_top_trader_ls_ratio(session, symbol, "5m", 1),
            "taker_ratio": self.fetch_taker_buy_sell_ratio(session, symbol, "5m", 1),
        }

        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        data = {}
        for key, result in zip(tasks.keys(), results):
            if isinstance(result, Exception):
                log.error(f"Error fetching {key} for {symbol}: {result}")
                data[key] = None
            else:
                data[key] = result

        return data
