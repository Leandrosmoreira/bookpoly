"""
Claim Executor - Executes claims via SELL@0.99 workaround using py-clob-client.

Since Polymarket doesn't have an official claim API, we use the workaround
discovered by the community: create a SELL order at price 0.99 for resolved
positions. The order is filled instantly and we receive $0.99 per share.

IMPORTANT: Orders must be cryptographically signed using py-clob-client.
Simple REST calls don't work - the API requires signed order objects.
"""
import logging
import time
from typing import Optional

from claims.config import ClaimConfig
from claims.models import ClaimItem, ClaimResult

log = logging.getLogger(__name__)

# Import py-clob-client
try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
    from py_clob_client.constants import SELL
    PY_CLOB_AVAILABLE = True
except ImportError:
    PY_CLOB_AVAILABLE = False
    log.warning("py-clob-client not installed. Run: pip install py-clob-client")


class ClaimExecutor:
    """
    Executes claims via SELL@0.99 workaround using py-clob-client.

    How it works:
    1. After market resolves, winning shares are worth $1.00
    2. We create a SELL order at $0.99 (max allowed by API)
    3. Order is filled instantly by the system
    4. We receive $0.99 per share (1% "fee" for the workaround)

    IMPORTANT: Uses py-clob-client for proper order signing.
    """

    def __init__(self, config: ClaimConfig):
        self.config = config
        self.clob_client: Optional[ClobClient] = None
        self._initialized = False

    def _ensure_initialized(self) -> bool:
        """Initialize py-clob-client if not already done."""
        if self._initialized:
            return True

        if not PY_CLOB_AVAILABLE:
            log.error("py-clob-client not available. Install with: pip install py-clob-client")
            return False

        if not self.config.private_key:
            log.error("POLYMARKET_PRIVATE_KEY not set")
            return False

        if not self.config.funder:
            log.error("POLYMARKET_FUNDER not set")
            return False

        try:
            # Remove 0x prefix if present
            private_key = self.config.private_key
            if private_key.startswith("0x"):
                private_key = private_key[2:]

            # Initialize ClobClient with Magic email signature type
            self.clob_client = ClobClient(
                host=self.config.clob_base_url,
                key=private_key,
                chain_id=self.config.chain_id,
                signature_type=self.config.signature_type,  # 1 = Magic email
                funder=self.config.funder,
            )

            # Set API credentials if available
            if self.config.api_key and self.config.api_secret:
                creds = ApiCreds(
                    api_key=self.config.api_key,
                    api_secret=self.config.api_secret,
                    api_passphrase=self.config.api_passphrase or "",
                )
                self.clob_client.set_api_creds(creds)
                log.info("Using provided API credentials")
            else:
                # Derive API credentials from private key
                log.info("Deriving API credentials from private key...")
                try:
                    creds = self.clob_client.derive_api_key()
                    self.clob_client.set_api_creds(creds)
                    log.info(f"Derived API key: {creds.api_key[:8]}...")
                except Exception:
                    # Try to create new credentials
                    log.info("Creating new API credentials...")
                    creds = self.clob_client.create_api_key()
                    self.clob_client.set_api_creds(creds)
                    log.info(f"Created API key: {creds.api_key[:8]}...")

            self._initialized = True
            log.info("ClobClient initialized successfully")
            return True

        except Exception as e:
            log.error(f"Failed to initialize ClobClient: {e}")
            return False

    async def close(self):
        """Close resources."""
        # py-clob-client doesn't need explicit closing
        pass

    async def claim(self, item: ClaimItem) -> ClaimResult:
        """
        Execute a claim for the given item.

        For winning positions: SELL at 0.99
        For losing positions: Skip (nothing to claim)
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

        # Ensure client is initialized
        if not self._ensure_initialized():
            return ClaimResult(
                success=False,
                claim_id=item.claim_id,
                error="Failed to initialize ClobClient",
                retryable=True,
                started_at=started_at,
            )

        try:
            log.info(f"Creating SELL order for {item.shares:.4f} shares @ ${self.config.sell_price}")

            # Create order using py-clob-client
            order_args = OrderArgs(
                token_id=item.token_id,
                price=self.config.sell_price,
                size=item.shares,
                side=SELL,
            )

            # Create and sign the order
            signed_order = self.clob_client.create_order(order_args)

            # Post the order
            result = self.clob_client.post_order(signed_order, OrderType.GTC)

            order_id = result.get("orderID", "") or result.get("order_id", "")

            if result.get("success") or order_id:
                amount_received = item.shares * self.config.sell_price
                fee_paid = item.shares * (1.0 - self.config.sell_price)

                log.info(
                    f"Successfully created SELL order {order_id}: "
                    f"{item.shares:.4f} shares @ ${self.config.sell_price:.2f} = ${amount_received:.2f}"
                )

                return ClaimResult(
                    success=True,
                    claim_id=item.claim_id,
                    order_id=order_id,
                    amount_received=amount_received,
                    fee_paid=fee_paid,
                    raw_response=result,
                    started_at=started_at,
                )
            else:
                error = result.get("error", result.get("errorMsg", "Unknown error"))
                log.error(f"Order creation failed: {error}")

                # Check if already claimed
                error_lower = str(error).lower()
                if "insufficient" in error_lower or "balance" in error_lower:
                    return ClaimResult(
                        success=True,  # Consider it success if already claimed
                        claim_id=item.claim_id,
                        error="Already claimed (insufficient balance)",
                        retryable=False,
                        started_at=started_at,
                    )

                return ClaimResult(
                    success=False,
                    claim_id=item.claim_id,
                    error=str(error),
                    retryable=True,
                    started_at=started_at,
                )

        except Exception as e:
            error_msg = str(e)
            log.error(f"Error claiming {item.claim_id}: {error_msg}")

            # Check for specific errors
            error_lower = error_msg.lower()
            if "insufficient" in error_lower or "balance" in error_lower:
                return ClaimResult(
                    success=True,
                    claim_id=item.claim_id,
                    error="Already claimed (insufficient balance)",
                    retryable=False,
                    started_at=started_at,
                )

            return ClaimResult(
                success=False,
                claim_id=item.claim_id,
                error=error_msg,
                retryable=True,
                started_at=started_at,
            )
