"""
Claim Executor - Redeems winning positions using polymarket-apis.

Uses PolymarketGaslessWeb3Client which sends transactions through
Polymarket's relayer - NO GAS FEES required!

For Magic email accounts (signature_type=1), this is the recommended approach.
"""
import logging
import time
from typing import Optional, Union

from claims.config import ClaimConfig
from claims.models import ClaimItem, ClaimResult

log = logging.getLogger(__name__)

# Import polymarket-apis for gasless redemption
GASLESS_AVAILABLE = False
ONCHAIN_AVAILABLE = False

try:
    from polymarket_apis import PolymarketGaslessWeb3Client
    GASLESS_AVAILABLE = True
except ImportError as e:
    log.warning(f"PolymarketGaslessWeb3Client not available: {e}")

try:
    from polymarket_apis import PolymarketWeb3Client
    ONCHAIN_AVAILABLE = True
except ImportError as e:
    log.warning(f"PolymarketWeb3Client not available: {e}")

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

    Methods available (in order of preference):
    1. Gasless redeem (primary): Uses Polymarket relayer - NO GAS NEEDED!
    2. On-chain redeem (fallback): Direct on-chain, requires POL for gas
    3. SELL@0.99 (last resort): Only works if orderbook still open

    For Magic email accounts (signature_type=1), uses gasless client.
    """

    def __init__(self, config: ClaimConfig):
        self.config = config
        self.gasless_client = None
        self.web3_client = None
        self.clob_client = None
        self._gasless_initialized = False
        self._web3_initialized = False
        self._clob_initialized = False

    def _ensure_gasless_initialized(self) -> bool:
        """Initialize gasless Web3 client (uses Polymarket relayer - no gas!)."""
        if self._gasless_initialized:
            return True

        if not GASLESS_AVAILABLE:
            log.warning("PolymarketGaslessWeb3Client not available")
            return False

        if not self.config.private_key:
            log.error("POLYMARKET_PRIVATE_KEY not set")
            return False

        try:
            private_key = self.config.private_key
            if private_key.startswith("0x"):
                private_key = private_key[2:]

            # Initialize gasless client with Magic email signature type (1)
            self.gasless_client = PolymarketGaslessWeb3Client(
                private_key=private_key,
                signature_type=self.config.signature_type,  # 1 = Magic/Email
                chain_id=self.config.chain_id,
            )

            self._gasless_initialized = True
            log.info("PolymarketGaslessWeb3Client initialized - NO GAS FEES!")
            return True

        except Exception as e:
            log.error(f"Failed to initialize PolymarketGaslessWeb3Client: {e}")
            return False

    def _ensure_web3_initialized(self) -> bool:
        """Initialize on-chain Web3 client (requires POL for gas)."""
        if self._web3_initialized:
            return True

        if not ONCHAIN_AVAILABLE:
            log.warning("PolymarketWeb3Client not available")
            return False

        if not self.config.private_key:
            log.error("POLYMARKET_PRIVATE_KEY not set")
            return False

        try:
            private_key = self.config.private_key
            if private_key.startswith("0x"):
                private_key = private_key[2:]

            self.web3_client = PolymarketWeb3Client(
                private_key=private_key,
                signature_type=self.config.signature_type,
                chain_id=self.config.chain_id,
            )

            self._web3_initialized = True
            log.info("PolymarketWeb3Client initialized (requires POL for gas)")

            # Check POL balance
            try:
                pol_balance = self.web3_client.get_pol_balance()
                log.info(f"POL balance: {pol_balance}")
                if pol_balance < 0.01:
                    log.warning("Low POL balance for on-chain transactions")
            except Exception as e:
                log.warning(f"Could not check POL balance: {e}")

            return True

        except Exception as e:
            log.error(f"Failed to initialize PolymarketWeb3Client: {e}")
            return False

    def _ensure_clob_initialized(self) -> bool:
        """Initialize py-clob-client for SELL workaround."""
        if self._clob_initialized:
            return True

        if not PY_CLOB_AVAILABLE:
            return False

        if not self.config.private_key or not self.config.funder:
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
            log.info("ClobClient initialized (SELL@0.99 fallback)")
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

        Strategy (in order):
        1. Try gasless redeem via Polymarket relayer (FREE!)
        2. Try on-chain redeem (requires POL for gas)
        3. Try SELL@0.99 workaround (if orderbook still open)
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
                amount_received=item.shares,
                fee_paid=0.0,
                dry_run=True,
                started_at=started_at,
            )

        # 1. Try gasless redeem first (NO GAS FEES!)
        result = await self._try_gasless_redeem(item, started_at)
        if result.success:
            return result

        # 2. Try on-chain redeem if gasless failed
        if "gasless" in str(result.error).lower() or "relayer" in str(result.error).lower():
            log.info("Gasless failed, trying on-chain redeem...")
            onchain_result = await self._try_onchain_redeem(item, started_at)
            if onchain_result.success:
                return onchain_result
            result = onchain_result

        # 3. Try SELL@0.99 as last resort
        if "orderbook" not in str(result.error).lower():
            log.info("Trying SELL@0.99 fallback...")
            sell_result = await self._try_sell_workaround(item, started_at)
            if sell_result.success:
                return sell_result

        return result

    async def _try_gasless_redeem(self, item: ClaimItem, started_at: int) -> ClaimResult:
        """Try to redeem using Polymarket's gasless relayer (FREE!)."""
        if not self._ensure_gasless_initialized():
            return ClaimResult(
                success=False,
                claim_id=item.claim_id,
                error="Failed to initialize gasless client",
                retryable=True,
                started_at=started_at,
            )

        try:
            log.info(f"Gasless redeem: {item.shares:.4f} shares of {item.market_slug}")

            condition_id = item.market_id

            # Build amounts array based on outcome_index
            if item.outcome_index == 0:
                amounts = [item.shares, 0.0]
            else:
                amounts = [0.0, item.shares]

            log.info(f"Calling gasless redeem_position: condition={condition_id[:16]}..., amounts={amounts}")

            # Call redeem_position via gasless client
            tx_receipt = self.gasless_client.redeem_position(
                condition_id=condition_id,
                amounts=amounts,
                neg_risk=item.neg_risk,
            )

            tx_hash = tx_receipt.transaction_hash if hasattr(tx_receipt, 'transaction_hash') else str(tx_receipt)
            log.info(f"Gasless redemption successful! TX: {tx_hash}")

            return ClaimResult(
                success=True,
                claim_id=item.claim_id,
                order_id=str(tx_hash),
                amount_received=item.shares,
                fee_paid=0.0,  # NO GAS FEES!
                started_at=started_at,
            )

        except Exception as e:
            error_msg = str(e)
            log.error(f"Gasless redeem failed: {error_msg}")

            if "already redeemed" in error_msg.lower() or "nothing to redeem" in error_msg.lower():
                return ClaimResult(
                    success=True,
                    claim_id=item.claim_id,
                    error="Already redeemed",
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

    async def _try_onchain_redeem(self, item: ClaimItem, started_at: int) -> ClaimResult:
        """Try on-chain redeem (requires POL for gas)."""
        if not self._ensure_web3_initialized():
            return ClaimResult(
                success=False,
                claim_id=item.claim_id,
                error="Failed to initialize Web3 client",
                retryable=True,
                started_at=started_at,
            )

        try:
            log.info(f"On-chain redeem: {item.shares:.4f} shares of {item.market_slug}")

            condition_id = item.market_id

            if item.outcome_index == 0:
                amounts = [item.shares, 0.0]
            else:
                amounts = [0.0, item.shares]

            log.info(f"Calling on-chain redeem_position: condition={condition_id[:16]}...")

            tx_receipt = self.web3_client.redeem_position(
                condition_id=condition_id,
                amounts=amounts,
                neg_risk=item.neg_risk,
            )

            tx_hash = tx_receipt.transaction_hash if hasattr(tx_receipt, 'transaction_hash') else str(tx_receipt)
            log.info(f"On-chain redemption successful! TX: {tx_hash}")

            return ClaimResult(
                success=True,
                claim_id=item.claim_id,
                order_id=str(tx_hash),
                amount_received=item.shares,
                fee_paid=0.0,
                started_at=started_at,
            )

        except Exception as e:
            error_msg = str(e)
            log.error(f"On-chain redeem failed: {error_msg}")

            if "already redeemed" in error_msg.lower() or "nothing to redeem" in error_msg.lower():
                return ClaimResult(
                    success=True,
                    claim_id=item.claim_id,
                    error="Already redeemed",
                    retryable=False,
                    started_at=started_at,
                )

            if "insufficient" in error_msg.lower() and "funds" in error_msg.lower():
                return ClaimResult(
                    success=False,
                    claim_id=item.claim_id,
                    error="Insufficient POL for gas",
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
                error="CLOB client not available",
                retryable=False,
                started_at=started_at,
            )

        try:
            log.info(f"SELL@0.99: {item.shares:.4f} shares @ ${self.config.sell_price}")

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
