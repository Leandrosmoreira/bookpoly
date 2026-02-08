"""
Polymarket trading client.

Handles order placement, cancellation, and position queries.
"""

import asyncio
import time
import hmac
import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

import aiohttp

from config import BotConfig


class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(Enum):
    PENDING = "pending"
    OPEN = "open"
    FILLED = "filled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FAILED = "failed"


@dataclass
class Order:
    """Order representation."""
    order_id: str
    token_id: str
    side: OrderSide
    size: float
    price: float
    status: OrderStatus
    filled_size: float = 0.0
    avg_fill_price: float | None = None
    created_at: int = 0
    updated_at: int = 0


@dataclass
class Position:
    """Position representation."""
    token_id: str
    size: float
    avg_price: float
    unrealized_pnl: float


class PolymarketTrader:
    """
    Polymarket CLOB trading client.

    Handles authentication, order management, and position queries.
    """

    BASE_URL = "https://clob.polymarket.com"

    def __init__(self, config: BotConfig):
        """
        Initialize trader.

        Args:
            config: Bot configuration with API credentials
        """
        self.config = config
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Content-Type": "application/json"}
            )
        return self._session

    async def close(self):
        """Close HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()

    def _sign_request(self, method: str, path: str, body: str = "") -> dict:
        """
        Sign a request with HMAC.

        Args:
            method: HTTP method
            path: Request path
            body: Request body (for POST)

        Returns:
            Headers with signature
        """
        timestamp = str(int(time.time() * 1000))
        message = f"{timestamp}{method}{path}{body}"

        signature = hmac.new(
            self.config.api_secret.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()

        return {
            "POLY_API_KEY": self.config.api_key,
            "POLY_TIMESTAMP": timestamp,
            "POLY_SIGNATURE": signature,
        }

    async def _request(
        self,
        method: str,
        path: str,
        data: dict | None = None,
        signed: bool = True,
    ) -> dict:
        """
        Make an API request.

        Args:
            method: HTTP method
            path: API path
            data: Request data (for POST)
            signed: Whether to sign the request

        Returns:
            Response JSON
        """
        session = await self._get_session()
        url = f"{self.BASE_URL}{path}"

        headers = {}
        body = ""

        if data:
            body = json.dumps(data)

        if signed:
            headers.update(self._sign_request(method, path, body))

        async with session.request(
            method,
            url,
            data=body if body else None,
            headers=headers,
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise Exception(f"API error {resp.status}: {text}")

            return await resp.json()

    async def get_balance(self) -> float:
        """
        Get account USDC balance.

        Returns:
            Balance in USDC
        """
        if self.config.dry_run:
            return 1000.0  # Simulated balance

        # TODO: Implement actual balance query
        # This requires querying the blockchain or Polymarket API
        return 0.0

    async def get_positions(self) -> list[Position]:
        """
        Get current positions.

        Returns:
            List of Position objects
        """
        if self.config.dry_run:
            return []  # No positions in dry run

        # TODO: Implement actual position query
        return []

    async def place_order(
        self,
        token_id: str,
        side: OrderSide,
        size: float,
        price: float,
    ) -> Order | None:
        """
        Place a POST ONLY limit order.

        POST ONLY means:
        - Order can only be maker (added to order book)
        - If it would match immediately, it is cancelled
        - Guarantees maker fee rebate, never pays taker fee

        Args:
            token_id: Token ID to trade
            side: BUY or SELL
            size: Number of shares
            price: Limit price

        Returns:
            Order object or None if failed
        """
        if self.config.dry_run:
            # Simulate order
            return Order(
                order_id=f"dry_run_{int(time.time() * 1000)}",
                token_id=token_id,
                side=side,
                size=size,
                price=price,
                status=OrderStatus.FILLED,
                filled_size=size,
                avg_fill_price=price,
                created_at=int(time.time() * 1000),
                updated_at=int(time.time() * 1000),
            )

        if self.config.paper_trading:
            # Paper trading - log but don't execute
            print(f"[PAPER] Would place {side.value} order: {size} @ {price}")
            return Order(
                order_id=f"paper_{int(time.time() * 1000)}",
                token_id=token_id,
                side=side,
                size=size,
                price=price,
                status=OrderStatus.FILLED,
                filled_size=size,
                avg_fill_price=price,
                created_at=int(time.time() * 1000),
                updated_at=int(time.time() * 1000),
            )

        # Live trading
        try:
            data = {
                "tokenID": token_id,
                "side": side.value,
                "size": str(size),
                "price": str(price),
                "orderType": "LIMIT",
                "postOnly": True,  # POST ONLY: só pode ser maker, nunca taker
            }

            result = await self._request("POST", "/order", data=data)

            return Order(
                order_id=result.get("orderID", ""),
                token_id=token_id,
                side=side,
                size=size,
                price=price,
                status=OrderStatus.PENDING,
                created_at=int(time.time() * 1000),
                updated_at=int(time.time() * 1000),
            )
        except Exception as e:
            print(f"Error placing order: {e}")
            return None

    async def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an order.

        Args:
            order_id: Order ID to cancel

        Returns:
            True if cancelled successfully
        """
        if self.config.dry_run or self.config.paper_trading:
            return True

        try:
            await self._request("DELETE", f"/order/{order_id}")
            return True
        except Exception as e:
            print(f"Error cancelling order: {e}")
            return False

    async def get_order(self, order_id: str) -> Order | None:
        """
        Get order status.

        Args:
            order_id: Order ID to query

        Returns:
            Order object or None if not found
        """
        if self.config.dry_run or self.config.paper_trading:
            return None

        try:
            result = await self._request("GET", f"/order/{order_id}", signed=True)

            return Order(
                order_id=result.get("orderID", ""),
                token_id=result.get("tokenID", ""),
                side=OrderSide(result.get("side", "BUY")),
                size=float(result.get("size", 0)),
                price=float(result.get("price", 0)),
                status=OrderStatus(result.get("status", "pending")),
                filled_size=float(result.get("filledSize", 0)),
                avg_fill_price=float(result.get("avgFillPrice", 0)) if result.get("avgFillPrice") else None,
            )
        except Exception as e:
            print(f"Error getting order: {e}")
            return None

    async def wait_for_fill(
        self,
        order_id: str,
        timeout_s: float = 5.0,
        poll_interval: float = 0.5,
    ) -> Order | None:
        """
        Wait for an order to fill.

        Args:
            order_id: Order ID to wait for
            timeout_s: Maximum time to wait
            poll_interval: Polling interval

        Returns:
            Final order status or None if timeout
        """
        if self.config.dry_run or self.config.paper_trading:
            return None

        start = time.time()
        while time.time() - start < timeout_s:
            order = await self.get_order(order_id)

            if order and order.status in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.FAILED):
                return order

            await asyncio.sleep(poll_interval)

        # Timeout - try to cancel
        await self.cancel_order(order_id)
        return await self.get_order(order_id)
