"""
Secure Redeemer - Direct blockchain calls using web3.py.

SECURITY:
- Uses ONLY official Ethereum libraries (web3.py, eth-account)
- Calls smart contract DIRECTLY on Polygon
- NO third-party servers or relayers
- Private key NEVER leaves your machine

REQUIREMENTS:
- POL (MATIC) in wallet for gas fees (~0.01-0.05 POL per tx)
"""
import logging
from dataclasses import dataclass
from typing import Optional

from web3 import Web3
from eth_account import Account

from .config import ClaimConfig, CTF_ABI
from .scanner import RedeemablePosition

log = logging.getLogger(__name__)

# Zero bytes32 (parent collection ID is always zero for Polymarket)
ZERO_BYTES32 = "0x" + "0" * 64


@dataclass
class RedeemResult:
    """Result of a redeem operation."""

    success: bool
    tx_hash: str = ""
    error: str = ""
    gas_used: int = 0
    position: Optional[RedeemablePosition] = None


class SecureRedeemer:
    """
    Redeems positions directly on blockchain.

    SECURITY GUARANTEES:
    1. Uses only web3.py (official Ethereum library)
    2. Transactions signed locally with eth-account
    3. Sent directly to Polygon RPC
    4. No third-party servers involved
    5. Private key never transmitted anywhere
    """

    def __init__(self, config: ClaimConfig):
        self.config = config
        self.w3: Optional[Web3] = None
        self.account: Optional[Account] = None
        self.contract = None
        self._initialized = False

    def initialize(self) -> bool:
        """Initialize Web3 connection."""
        if self._initialized:
            return True

        try:
            # Connect to Polygon RPC
            self.w3 = Web3(Web3.HTTPProvider(self.config.rpc_url))

            if not self.w3.is_connected():
                log.error(f"Failed to connect to {self.config.rpc_url}")
                return False

            log.info(f"Connected to Polygon (chain {self.w3.eth.chain_id})")

            # Load account from private key
            self.account = Account.from_key(self.config.private_key)
            log.info(f"Wallet: {self.account.address}")

            # Check POL balance for gas
            balance_wei = self.w3.eth.get_balance(self.account.address)
            balance_pol = balance_wei / 10**18
            log.info(f"POL balance: {balance_pol:.4f}")

            if balance_pol < 0.01:
                log.warning("Low POL balance! Need POL for gas fees.")
                log.warning(f"Send POL to: {self.account.address}")

            # Load CTF contract
            self.contract = self.w3.eth.contract(
                address=Web3.to_checksum_address(self.config.ctf_address),
                abi=CTF_ABI
            )

            self._initialized = True
            return True

        except Exception as e:
            log.error(f"Failed to initialize: {e}")
            return False

    def redeem(self, position: RedeemablePosition) -> RedeemResult:
        """
        Redeem a winning position.

        This calls redeemPositions() directly on the CTF contract.
        """
        if not self._initialized:
            if not self.initialize():
                return RedeemResult(
                    success=False,
                    error="Failed to initialize",
                    position=position
                )

        # Dry run mode
        if self.config.dry_run:
            log.info(f"[DRY-RUN] Would redeem {position.shares:.2f} shares of {position.market_slug}")
            return RedeemResult(
                success=True,
                tx_hash="DRY_RUN",
                position=position
            )

        try:
            log.info(f"Redeeming {position.shares:.2f} shares of {position.market_slug}...")

            # Prepare condition_id as bytes32
            condition_id = position.condition_id
            if condition_id.startswith("0x"):
                condition_id_bytes = bytes.fromhex(condition_id[2:])
            else:
                condition_id_bytes = bytes.fromhex(condition_id)

            # Index sets: [1] for outcome 0 (Yes/Up), [2] for outcome 1 (No/Down)
            # The index set is a bitmap where bit i represents outcome i
            index_sets = [1 << position.outcome_index]

            log.info(f"  condition_id: {position.condition_id[:20]}...")
            log.info(f"  index_sets: {index_sets}")

            # Build transaction
            tx = self.contract.functions.redeemPositions(
                Web3.to_checksum_address(self.config.usdc_address),  # collateralToken
                bytes.fromhex(ZERO_BYTES32[2:]),  # parentCollectionId (always zero)
                condition_id_bytes,  # conditionId
                index_sets  # indexSets
            ).build_transaction({
                "from": self.account.address,
                "nonce": self.w3.eth.get_transaction_count(self.account.address),
                "gas": self.config.gas_limit,
                "gasPrice": self.w3.eth.gas_price,
                "chainId": self.config.chain_id,
            })

            # Sign transaction locally (key never leaves machine)
            signed_tx = self.w3.eth.account.sign_transaction(tx, self.config.private_key)

            # Send to blockchain
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            log.info(f"  TX submitted: {tx_hash.hex()}")

            # Wait for confirmation
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

            if receipt.status == 1:
                log.info(f"  SUCCESS! Block {receipt.blockNumber}, gas used: {receipt.gasUsed}")
                return RedeemResult(
                    success=True,
                    tx_hash=tx_hash.hex(),
                    gas_used=receipt.gasUsed,
                    position=position
                )
            else:
                log.error(f"  Transaction reverted!")
                return RedeemResult(
                    success=False,
                    tx_hash=tx_hash.hex(),
                    error="Transaction reverted",
                    position=position
                )

        except Exception as e:
            error_msg = str(e)
            log.error(f"  Redeem failed: {error_msg}")

            # Check common errors
            if "insufficient funds" in error_msg.lower():
                log.error(f"  Need more POL for gas! Send to: {self.account.address}")

            return RedeemResult(
                success=False,
                error=error_msg,
                position=position
            )

    def get_pol_balance(self) -> float:
        """Get POL balance for gas."""
        if not self._initialized:
            self.initialize()

        if self.w3 and self.account:
            balance_wei = self.w3.eth.get_balance(self.account.address)
            return balance_wei / 10**18

        return 0.0
