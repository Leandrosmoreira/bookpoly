"""
Binance WebSocket Client for real-time data.

Connects to Binance Futures WebSocket and streams kline data
for reversal detection.
"""

import asyncio
import json
import logging
import time
from typing import Callable, Optional
from collections import defaultdict

try:
    import websockets
except ImportError:
    websockets = None

from .config import BinanceRealtimeConfig
from .reversal_detector import ReversalDetector

log = logging.getLogger("binance_ws")


class BinanceWebSocketClient:
    """
    WebSocket client for Binance Futures data.

    Streams kline (candlestick) data and maintains reversal detectors
    for each symbol.
    """

    def __init__(self, config: Optional[BinanceRealtimeConfig] = None):
        if websockets is None:
            raise ImportError(
                "websockets library required. Install with: pip install websockets"
            )

        self.config = config or BinanceRealtimeConfig()
        self.detectors: dict[str, ReversalDetector] = {}
        self.ws = None
        self.running = False
        self._callbacks: list[Callable] = []
        self._last_prices: dict[str, float] = {}

        # Initialize detectors for each symbol
        for symbol in self.config.symbols:
            self.detectors[symbol] = ReversalDetector(self.config)

    def add_callback(self, callback: Callable):
        """Add callback to be called on each update."""
        self._callbacks.append(callback)

    def get_detector(self, symbol: str) -> Optional[ReversalDetector]:
        """Get reversal detector for a symbol."""
        return self.detectors.get(symbol.upper())

    def get_last_price(self, symbol: str) -> Optional[float]:
        """Get last known price for a symbol."""
        return self._last_prices.get(symbol.upper())

    def _build_stream_url(self) -> str:
        """Build WebSocket URL with all streams."""
        streams = []
        for symbol in self.config.symbols:
            s = symbol.lower()
            streams.append(f"{s}@kline_1m")

        streams_str = "/".join(streams)
        return f"{self.config.ws_url}?streams={streams_str}"

    async def _handle_message(self, message: str):
        """Handle incoming WebSocket message."""
        try:
            data = json.loads(message)

            # Combined stream format
            if "stream" in data:
                stream = data["stream"]
                payload = data["data"]
            else:
                payload = data

            # Handle kline data
            if "k" in payload:
                kline = payload["k"]
                symbol = kline["s"]  # e.g., "BTCUSDT"

                open_ = float(kline["o"])
                high = float(kline["h"])
                low = float(kline["l"])
                close = float(kline["c"])
                volume = float(kline["v"])
                timestamp = kline["t"]
                is_closed = kline["x"]

                # Update price
                self._last_prices[symbol] = close

                # Update detector
                detector = self.detectors.get(symbol)
                if detector:
                    detector.update_candle(
                        open_=open_,
                        high=high,
                        low=low,
                        close=close,
                        volume=volume,
                        timestamp=timestamp,
                        is_closed=is_closed,
                    )

                    # Call callbacks
                    for callback in self._callbacks:
                        try:
                            await callback(symbol, detector, is_closed)
                        except Exception as e:
                            log.error(f"Callback error: {e}")

        except json.JSONDecodeError as e:
            log.error(f"JSON decode error: {e}")
        except Exception as e:
            log.error(f"Message handling error: {e}")

    async def connect(self):
        """Connect to WebSocket and start streaming."""
        url = self._build_stream_url()
        log.info(f"Connecting to {url}")

        self.running = True

        while self.running:
            try:
                async with websockets.connect(
                    url,
                    ping_interval=self.config.ping_interval,
                    ping_timeout=10,
                ) as ws:
                    self.ws = ws
                    log.info("WebSocket connected")

                    async for message in ws:
                        if not self.running:
                            break
                        await self._handle_message(message)

            except websockets.ConnectionClosed as e:
                log.warning(f"WebSocket closed: {e}")
                if self.running:
                    log.info(f"Reconnecting in {self.config.reconnect_delay}s...")
                    await asyncio.sleep(self.config.reconnect_delay)

            except Exception as e:
                log.error(f"WebSocket error: {e}")
                if self.running:
                    await asyncio.sleep(self.config.reconnect_delay)

    async def disconnect(self):
        """Disconnect from WebSocket."""
        self.running = False
        if self.ws:
            await self.ws.close()

    def check_reversal(self, symbol: str, bet_side: str) -> dict:
        """
        Check for reversal signal for a symbol.

        Args:
            symbol: e.g., "BTCUSDT"
            bet_side: "UP" or "DOWN"

        Returns:
            Dict with reversal detection result
        """
        detector = self.detectors.get(symbol.upper())
        if not detector:
            return {"error": f"No detector for {symbol}"}

        if not detector.has_enough_data:
            return {"error": "Not enough data yet"}

        result = detector.detect(bet_side)
        return detector.to_dict()


class SimpleReversalChecker:
    """
    Simple reversal checker that uses REST API instead of WebSocket.

    This is a fallback for environments where WebSocket is not available
    or for simpler integration with existing code.
    """

    def __init__(self, config: Optional[BinanceRealtimeConfig] = None):
        self.config = config or BinanceRealtimeConfig()
        self.detectors: dict[str, ReversalDetector] = {}

        for symbol in self.config.symbols:
            self.detectors[symbol] = ReversalDetector(self.config)

    async def update_from_klines(self, symbol: str, klines: list[dict]):
        """
        Update detector from kline data (from REST API).

        Args:
            symbol: e.g., "BTCUSDT"
            klines: List of kline dicts with open, high, low, close, volume
        """
        detector = self.detectors.get(symbol.upper())
        if not detector:
            return

        for k in klines:
            detector.update_candle(
                open_=k["open"],
                high=k["high"],
                low=k["low"],
                close=k["close"],
                volume=k["volume"],
                timestamp=k.get("timestamp", int(time.time() * 1000)),
                is_closed=True,
            )

    def check_reversal(self, symbol: str, bet_side: str) -> dict:
        """Check for reversal signal."""
        detector = self.detectors.get(symbol.upper())
        if not detector:
            return {"error": f"No detector for {symbol}"}

        if not detector.has_enough_data:
            return {"error": "Not enough data"}

        result = detector.detect(bet_side)
        return detector.to_dict()

    def get_detector(self, symbol: str) -> Optional[ReversalDetector]:
        """Get detector for symbol."""
        return self.detectors.get(symbol.upper())
