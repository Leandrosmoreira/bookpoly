"""
Claim Executor - Redeems winning positions using polymarket-apis.

For resolved markets, the orderbook is closed so we can't use SELL@0.99.
Instead, we use the on-chain redeem_position function via polymarket-apis
which supports Magic email accounts (signature_type=1).

This requires some POL (MATIC) in the wallet to pay for gas fees.
"""
import logging
import time
from typing import Optional

from claims.config import ClaimConfig
from claims.models import ClaimItem, ClaimResult

log = logging.getLogger(__name__)

# Import polymarket-apis for on-chain redemption
POLYMARKET_APIS_AVAILABLE = False
try:
    from polymarket_apis import PolymarketWeb3Client
    POLYMARKET_APIS_AVAILABLE = True
except ImportError as e:
    log.warning(f"polymarket-apis not installed: {e}. Run: pip install polymarket-apis")

# Import py-clob-client for SELL@0.99 workaround (backup method)
PY_CLOB_AVAILABLE = False
try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
    from py_clob_client.order_builder.constants import SELL
    PY_CLOB_AVAILABLE = True
except ImportError as e:
    log.warning(f"py-clob-client not available: {e}")


class ClaimExecutor:
    """
    Executes claims for winning positions in resolved markets.

    Two methods available:
    1. On-chain redeem (primary): Uses polymarket-apis to call redeemPositions
       on the CTF contract. This is the proper way for resolved markets.
       Requires POL for gas.

    2. SELL@0.99 (fallback): If orderbook is still active, sells at 0.99.
       Only works briefly after resolution before orderbook closes.

    For Magic email accounts (signature_type=1), uses PolymarketWeb3Client.
    """

    def __init__(self, config: ClaimConfig):
        self.config = config
        self.web3_client: Optional[PolymarketWeb3Client] = None
        self.clob_client: Optional[ClobClient] = None
        self._web3_initialized = False
        self._clob_initialized = False

    def _ensure_web3_initialized(self) -> bool:
        """Initialize polymarket-apis Web3 client if not already done."""
        if self._web3_initialized:
            return True

        if not POLYMARKET_APIS_AVAILABLE:
            log.warning("polymarket-apis not available")
            return False

        if not self.config.private_key:
            log.error("POLYMARKET_PRIVATE_KEY not set")
            return False

        try:
            private_key = self.config.private_key
            if private_key.startswith("0x"):
                private_key = private_key[2:]

            # Initialize with Magic email signature type (1)
            self.web3_client = PolymarketWeb3Client(
                private_key=private_key,
                signature_type=self.config.signature_type,  # 1 = Magic/Email
                chain_id=self.config.chain_id,  # 137 = Polygon mainnet
            )

            self._web3_initialized = True
            log.info("PolymarketWeb3Client initialized successfully")

            # Check POL balance for gas
            try:
                pol_balance = self.web3_client.get_pol_balance()
                log.info(f"POL balance: {pol_balance} (for gas fees)")
                if pol_balance < 0.01:
                    log.warning("Low POL balance! You need POL to pay gas for redemptions")
            except Exception as e:
                log.warning(f"Could not check POL balance: {e}")

            return True

        except Exception as e:
            log.error(f"Failed to initialize PolymarketWeb3Client: {e}")
            return False

    def _ensure_clob_initialized(self) -> bool:
        """Initialize py-clob-client if not already done."""
        if self._clob_initialized:
            return True

        if not PY_CLOB_AVAILABLE:
            return False

        if not self.config.private_key:
            return False

        if not self.config.funder:
            return False

        try:
            private_key = self.config.private_key
            if private_key.startswith("0x"):
                private_key = private_key[2:]

            self.clob_client = ClobClient(
                host=self.config.clob_base_url,
                key=private_key,
                chain_id=self.config.chain_id,
                signature_type=self.config.signature_type,
                funder=self.config.funder,
            )

            if self.config.api_key and self.config.api_secret:
                creds = ApiCreds(
                    api_key=self.config.api_key,
                    api_secret=self.config.api_secret,
                    api_passphrase=self.config.api_passphrase or "",
                )
                self.clob_client.set_api_creds(creds)

            self._clob_initialized = True
            log.info("ClobClient initialized (fallback for SELL@0.99)")
            return True

        except Exception as e:
            log.warning(f"Failed to initialize ClobClient: {e}")
            return False

    async def close(self):
        """Close resources."""
        pass

    async def claim(self, item: ClaimItem) -> ClaimResult:
        """
        Execute a claim for the given item.

        Strategy:
        1. Try on-chain redeem via polymarket-apis (proper method)
        2. If that fails, try SELL@0.99 workaround (might work if orderbook still open)
        """
        started_at = int(time.time())

        # Skip losing positions
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
                f"[DRY-RUN] Would redeem {item.shares:.4f} shares of {item.market_slug} "
                f"= ${item.shares:.2f}"
            )
            return ClaimResult(
                success=True,
                claim_id=item.claim_id,
                order_id="DRY_RUN",
                amount_received=item.shares,  # Full value on redeem
                fee_paid=0.0,  # No fee for on-chain redeem
                dry_run=True,
                started_at=started_at,
            )

        # Try on-chain redemption first
        result = await self._try_onchain_redeem(item, started_at)
        if result.success:
            return result

        # If on-chain failed with "orderbook does not exist", it means
        # the market is fully resolved - on-chain redeem should have worked
        # Try SELL@0.99 as fallback only if orderbook might still be open
        if "orderbook" not in str(result.error).lower():
            log.info("Trying SELL@0.99 fallback...")
            fallback_result = await self._try_sell_workaround(item, started_at)
            if fallback_result.success:
                return fallback_result

        return result

    async def _try_onchain_redeem(self, item: ClaimItem, started_at: int) -> ClaimResult:
        """Try to redeem position on-chain using polymarket-apis."""
        if not self._ensure_web3_initialized():
            return ClaimResult(
                success=False,
                claim_id=item.claim_id,
                error="Failed to initialize Web3 client (polymarket-apis)",
                retryable=True,
                started_at=started_at,
            )

        try:
            log.info(f"Redeeming on-chain: {item.shares:.4f} shares of {item.market_slug}")

            # Get condition_id from market_id
            condition_id = item.market_id

            # Build amounts array based on outcome_index
            # [x, y] where x is first outcome shares, y is second outcome shares
            if item.outcome_index == 0:
                amounts = [item.shares, 0.0]
            else:
                amounts = [0.0, item.shares]

            log.info(f"Calling redeem_position: condition={condition_id[:16]}..., amounts={amounts}, neg_risk={item.neg_risk}")

            # Call redeem_position
            tx_receipt = self.web3_client.redeem_position(
                condition_id=condition_id,
                amounts=amounts,
                neg_risk=item.neg_risk,
            )

            tx_hash = tx_receipt.transaction_hash if hasattr(tx_receipt, 'transaction_hash') else str(tx_receipt)
            log.info(f"Redemption tx submitted: {tx_hash}")

            return ClaimResult(
                success=True,
                claim_id=item.claim_id,
                order_id=str(tx_hash),
                amount_received=item.shares,  # Full value
                fee_paid=0.0,  # Gas paid separately in POL
                started_at=started_at,
            )

        except Exception as e:
            error_msg = str(e)
            log.error(f"On-chain redeem failed: {error_msg}")

            # Check if already redeemed
            if "already redeemed" in error_msg.lower() or "nothing to redeem" in error_msg.lower():
                return ClaimResult(
                    success=True,
                    claim_id=item.claim_id,
                    error="Already redeemed",
                    retryable=False,
                    started_at=started_at,
                )

            # Check for insufficient gas
            if "insufficient" in error_msg.lower() and "funds" in error_msg.lower():
                return ClaimResult(
                    success=False,
                    claim_id=item.claim_id,
                    error="Insufficient POL for gas. Send POL to your wallet.",
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

    async def _try_sell_workaround(self, item: ClaimItem, started_at: int) -> ClaimResult:
        """Try SELL@0.99 workaround (only works if orderbook still open)."""
        if not self._ensure_clob_initialized():
            return ClaimResult(
                success=False,
                claim_id=item.claim_id,
                error="CLOB client not available for SELL workaround",
                retryable=False,
                started_at=started_at,
            )

        try:
            log.info(f"Creating SELL order: {item.shares:.4f} shares @ ${self.config.sell_price}")

            order_args = OrderArgs(
                token_id=item.token_id,
                price=self.config.sell_price,
                size=item.shares,
                side=SELL,
            )

            signed_order = self.clob_client.create_order(order_args)
            result = self.clob_client.post_order(signed_order, OrderType.GTC)

            order_id = result.get("orderID", "") or result.get("order_id", "")

            if result.get("success") or order_id:
                amount_received = item.shares * self.config.sell_price
                fee_paid = item.shares * (1.0 - self.config.sell_price)

                log.info(f"SELL order created: {order_id} = ${amount_received:.2f}")

                return ClaimResult(
                    success=True,
                    claim_id=item.claim_id,
                    order_id=order_id,
                    amount_received=amount_received,
                    fee_paid=fee_paid,
                    raw_response=result,
                    started_at=started_at,
                )

            error = result.get("error", result.get("errorMsg", "Unknown error"))
            return ClaimResult(
                success=False,
                claim_id=item.claim_id,
                error=str(error),
                retryable="orderbook" not in str(error).lower(),
                started_at=started_at,
            )

        except Exception as e:
            return ClaimResult(
                success=False,
                claim_id=item.claim_id,
                error=str(e),
                retryable=False,
                started_at=started_at,
            )
