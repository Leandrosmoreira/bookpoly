"""
Claim Executor - Executes claims via SELL@0.99 workaround.

Since Polymarket doesn't have an official claim API, we use the workaround
discovered by the community: create a SELL order at price 0.99 for resolved
positions. The order is filled instantly and we receive $0.99 per share.
"""
import asyncio
import hmac
import hashlib
import httpx
import json
import logging
import time
from typing import Optional
from claims.config import ClaimConfig
from claims.models import ClaimItem, ClaimResult

log = logging.getLogger(__name__)


class ClaimExecutor:
    """
    Executes claims via SELL@0.99 workaround.

    How it works:
    1. After market resolves, winning shares are worth $1.00
    2. We create a SELL order at $0.99 (max allowed by API)
    3. Order is filled instantly by the system
    4. We receive $0.99 per share (1% "fee" for the workaround)
    """

    def __init__(self, config: ClaimConfig):
        self.config = config
        self.client = httpx.AsyncClient(timeout=30.0)

    async def close(self):
        """Close HTTP client."""
        await self.client.aclose()

    async def claim(self, item: ClaimItem) -> ClaimResult:
        """
        Execute a claim for the given item.

        For winning positions: SELL at 0.99
        For losing positions: We could skip or SELL at 0.01 (minimal recovery)
        """
        started_at = int(time.time())

        # Skip losing positions (nothing to claim)
        if not item.won:
            log.info(f"Skipping losing position {item.claim_id} ({item.shares} shares)")
            return ClaimResult(
                success=True,
                claim_id=item.claim_id,
                order_id="",
                amount_received=0.0,
                fee_paid=0.0,
                dry_run=self.config.dry_run,
            )

        # Dry run mode
        if self.config.dry_run:
            log.info(
                f"[DRY-RUN] Would claim {item.shares:.4f} shares of {item.market_slug} "
                f"@ ${self.config.sell_price:.2f} = ${item.shares * self.config.sell_price:.2f}"
            )
            return ClaimResult(
                success=True,
                claim_id=item.claim_id,
                order_id="DRY_RUN",
                amount_received=item.shares * self.config.sell_price,
                fee_paid=item.shares * (1.0 - self.config.sell_price),
                dry_run=True,
                started_at=started_at,
            )

        try:
            # Create SELL order at max price (0.99)
            order_result = await self._create_sell_order(item)

            if not order_result:
                return ClaimResult(
                    success=False,
                    claim_id=item.claim_id,
                    error="Failed to create order",
                    retryable=True,
                    started_at=started_at,
                )

            order_id = order_result.get("order_id") or order_result.get("id", "")

            # Wait for fill (should be instant for resolved markets)
            fill_result = await self._wait_for_fill(order_id, timeout=30)

            if fill_result.get("filled"):
                amount_received = float(fill_result.get("amount", item.shares * self.config.sell_price))
                fee_paid = item.shares - amount_received

                log.info(
                    f"Successfully claimed {item.shares:.4f} shares "
                    f"for ${amount_received:.2f} (fee: ${fee_paid:.4f})"
                )

                return ClaimResult(
                    success=True,
                    claim_id=item.claim_id,
                    order_id=order_id,
                    amount_received=amount_received,
                    fee_paid=fee_paid,
                    raw_response=fill_result,
                    started_at=started_at,
                )
            else:
                error = fill_result.get("error", "Order not filled")
                log.warning(f"Claim not filled: {error}")

                # Check if already claimed (API returns error for 0 balance)
                if "insufficient" in error.lower() or "balance" in error.lower():
                    return ClaimResult(
                        success=True,  # Consider it success if already claimed
                        claim_id=item.claim_id,
                        order_id=order_id,
                        error="Already claimed (insufficient balance)",
                        retryable=False,
                        started_at=started_at,
                    )

                return ClaimResult(
                    success=False,
                    claim_id=item.claim_id,
                    order_id=order_id,
                    error=error,
                    retryable=True,
                    started_at=started_at,
                )

        except Exception as e:
            log.error(f"Error claiming {item.claim_id}: {e}")
            return ClaimResult(
                success=False,
                claim_id=item.claim_id,
                error=str(e),
                retryable=True,
                started_at=started_at,
            )

    async def _create_sell_order(self, item: ClaimItem) -> Optional[dict]:
        """
        Create a SELL order at the configured price.
        """
        try:
            url = f"{self.config.clob_base_url}/order"

            # Order payload (must use camelCase like bot/trader.py)
            # Note: funder is NOT included in body (it's in headers/auth)
            order_data = {
                "tokenID": item.token_id,
                "side": "SELL",
                "price": str(self.config.sell_price),
                "size": str(item.shares),
                "orderType": "LIMIT",
            }

            # Use same JSON format as bot/trader.py (no spaces, consistent)
            body = json.dumps(order_data, separators=(',', ':'))
            headers = self._get_auth_headers("POST", "/order", body)
            headers["Content-Type"] = "application/json"

            log.debug(f"Creating SELL order: {order_data}")

            resp = await self.client.post(url, content=body, headers=headers)

            if resp.status_code in (200, 201):
                result = resp.json()
                log.info(f"Order created: {result.get('order_id', result.get('id'))}")
                return result

            log.error(f"Order creation failed: {resp.status_code} - {resp.text}")
            return None

        except Exception as e:
            log.error(f"Error creating SELL order: {e}")
            return None

    async def _wait_for_fill(self, order_id: str, timeout: int = 30) -> dict:
        """
        Wait for an order to be filled.

        For resolved markets, this should be nearly instant.
        """
        if not order_id or order_id == "DRY_RUN":
            return {"filled": True, "dry_run": True}

        start = time.time()
        poll_interval = 1.0  # Check every second

        while time.time() - start < timeout:
            try:
                url = f"{self.config.clob_base_url}/order/{order_id}"
                headers = self._get_auth_headers("GET", f"/order/{order_id}")

                resp = await self.client.get(url, headers=headers)

                if resp.status_code == 200:
                    order = resp.json()
                    status = order.get("status", "").lower()

                    if status in ("filled", "matched", "completed"):
                        return {
                            "filled": True,
                            "amount": order.get("filled_size") or order.get("size"),
                            "price": order.get("avg_price") or order.get("price"),
                        }

                    if status in ("cancelled", "rejected", "expired"):
                        return {
                            "filled": False,
                            "error": f"Order {status}",
                        }

                    # Still open, keep waiting
                    log.debug(f"Order {order_id} status: {status}")

                elif resp.status_code == 404:
                    # Order might have been filled and removed
                    return {"filled": True, "note": "Order not found (possibly filled)"}

                await asyncio.sleep(poll_interval)

            except Exception as e:
                log.warning(f"Error checking order status: {e}")
                await asyncio.sleep(poll_interval)

        return {"filled": False, "error": "Timeout waiting for fill"}

    async def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an unfilled order.
        """
        try:
            url = f"{self.config.clob_base_url}/order/{order_id}"
            headers = self._get_auth_headers("DELETE", f"/order/{order_id}")

            resp = await self.client.delete(url, headers=headers)

            if resp.status_code in (200, 204):
                log.info(f"Order {order_id} cancelled")
                return True

            log.warning(f"Failed to cancel order: {resp.status_code}")
            return False

        except Exception as e:
            log.error(f"Error cancelling order: {e}")
            return False

    def _get_auth_headers(self, method: str, path: str, body: str = "") -> dict:
        """Generate authentication headers for CLOB API."""
        if not self.config.api_key or not self.config.api_secret:
            log.warning("API credentials not configured")
            return {}

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
