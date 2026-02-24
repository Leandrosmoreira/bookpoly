"""
Binance Futures aggTrade WebSocket client.

Connects to the multi-stream endpoint for real-time trade data
with automatic reconnection and backoff.
"""

import asyncio
import json
import logging
import time

import aiohttp

log = logging.getLogger(__name__)


class AggTradeStream:
    """Binance Futures aggTrade WebSocket multi-stream client."""

    def __init__(
        self,
        symbols: list[str],
        ws_base: str = "wss://fstream.binance.com",
        reconnect_delay: float = 3.0,
        max_reconnect_delay: float = 60.0,
    ):
        streams = "/".join(f"{s.lower()}@aggTrade" for s in symbols)
        self.url = f"{ws_base}/stream?streams={streams}"
        self.symbols = [s.lower() for s in symbols]
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_delay = max_reconnect_delay
        self._running = True
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._trade_queue: asyncio.Queue = asyncio.Queue(maxsize=10_000)

    async def start(self, session: aiohttp.ClientSession):
        """Start the WebSocket connection loop."""
        self._session = session
        self._running = True
        await self._connect_loop()

    async def stop(self):
        """Stop the WebSocket connection."""
        self._running = False
        if self._ws and not self._ws.closed:
            await self._ws.close()

    async def _connect_loop(self):
        """Connect with exponential backoff on failure."""
        delay = self.reconnect_delay

        while self._running:
            try:
                log.info(f"Connecting to {self.url[:80]}...")
                self._ws = await self._session.ws_connect(
                    self.url,
                    heartbeat=20,
                    receive_timeout=30,
                )
                log.info(f"WebSocket connected ({len(self.symbols)} streams)")
                delay = self.reconnect_delay  # Reset backoff

                await self._read_loop()

            except asyncio.CancelledError:
                break
            except Exception as e:
                if not self._running:
                    break
                log.warning(f"WebSocket error: {e}, reconnecting in {delay:.0f}s")
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.max_reconnect_delay)

    async def _read_loop(self):
        """Read messages from WebSocket and enqueue parsed trades."""
        async for msg in self._ws:
            if not self._running:
                break

            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    payload = json.loads(msg.data)
                    trade = self._parse_trade(payload)
                    if trade:
                        try:
                            self._trade_queue.put_nowait(trade)
                        except asyncio.QueueFull:
                            # Drop oldest to avoid memory buildup
                            try:
                                self._trade_queue.get_nowait()
                            except asyncio.QueueEmpty:
                                pass
                            self._trade_queue.put_nowait(trade)
                except (json.JSONDecodeError, KeyError) as e:
                    log.debug(f"Parse error: {e}")

            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                log.warning(f"WebSocket closed: {msg.type}")
                break

    def _parse_trade(self, payload: dict) -> dict | None:
        """Parse multi-stream aggTrade message."""
        data = payload.get("data")
        if not data or data.get("e") != "aggTrade":
            return None

        # Extract symbol from stream name (e.g., "btcusdt@aggTrade" -> "btcusdt")
        stream = payload.get("stream", "")
        symbol = stream.split("@")[0] if "@" in stream else data.get("s", "").lower()

        return {
            "symbol": symbol,
            "ts_ms": data["T"],
            "price": float(data["p"]),
            "qty": float(data["q"]),
            "is_buy": not data["m"],  # m=True means buyer is maker -> sell-initiated
        }

    async def get_trade(self, timeout: float = 1.0) -> dict | None:
        """Get next trade from queue with timeout."""
        try:
            return await asyncio.wait_for(self._trade_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def queue_size(self) -> int:
        """Current number of trades waiting in queue."""
        return self._trade_queue.qsize()
