"""
On-chain Redeemer — Fallback: redeem direto na blockchain pagando gas em POL.

Reutiliza a lógica do claim/redeemer.py (SecureRedeemer) de forma simplificada.
"""
import logging
import time
from dataclasses import dataclass
from typing import Optional

from web3 import Web3
from eth_account import Account

from .config import ClaimV2Config, CTF_ABI

log = logging.getLogger(__name__)

ZERO_BYTES32 = "0x" + "0" * 64


@dataclass
class OnchainResult:
    success: bool
    tx_hash: str = ""
    error: str = ""
    gas_used: int = 0
    method: str = "onchain"


class OnchainRedeemer:
    """Redeem direto na blockchain (paga gas em POL). Fallback do gasless."""

    def __init__(self, config: ClaimV2Config):
        self.config = config
        self.w3: Optional[Web3] = None
        self.account: Optional[Account] = None
        self.contract = None
        self._current_rpc: Optional[str] = None
        self._initialized = False

    def _connect(self, rpc_url: str) -> bool:
        try:
            request_kwargs = {"timeout": 15}
            try:
                from polygon_rpc import get_request_kwargs_for_rpc
                request_kwargs.update(get_request_kwargs_for_rpc(rpc_url, timeout=15))
            except ImportError:
                pass
            self.w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs=request_kwargs))
            if not self.w3.is_connected():
                return False
            self._current_rpc = rpc_url
            self.contract = self.w3.eth.contract(
                address=Web3.to_checksum_address(self.config.ctf_address),
                abi=CTF_ABI
            )
            return True
        except Exception:
            return False

    def _reconnect(self) -> bool:
        current = (self._current_rpc or "").rstrip("/")
        for url in self.config.rpc_urls:
            if url.rstrip("/") == current:
                continue
            if self._connect(url):
                return True
        return False

    def initialize(self) -> bool:
        if self._initialized:
            return True
        for url in self.config.rpc_urls:
            if self._connect(url):
                break
        else:
            log.error("Falha ao conectar a RPCs Polygon")
            return False

        self.account = Account.from_key(self.config.private_key)
        log.info(f"On-chain redeemer: {self.account.address}")

        balance = self.w3.eth.get_balance(self.account.address) / 10**18
        log.info(f"POL balance: {balance:.4f}")
        if balance < 0.01:
            log.warning(f"Saldo POL baixo! Envie POL para: {self.account.address}")

        self._initialized = True
        return True

    def redeem(self, position) -> OnchainResult:
        if not self._initialized and not self.initialize():
            return OnchainResult(success=False, error="Falha ao inicializar")

        try:
            log.info(f"  Redeem on-chain: {position.market_slug} ({position.outcome})...")

            condition_id = position.condition_id
            if condition_id.startswith("0x"):
                condition_bytes = bytes.fromhex(condition_id[2:])
            else:
                condition_bytes = bytes.fromhex(condition_id)

            index_sets = [1 << position.outcome_index]

            tx = self.contract.functions.redeemPositions(
                Web3.to_checksum_address(self.config.usdc_address),
                bytes.fromhex(ZERO_BYTES32[2:]),
                condition_bytes,
                index_sets
            ).build_transaction({
                "from": self.account.address,
                "nonce": self.w3.eth.get_transaction_count(self.account.address),
                "gas": self.config.gas_limit,
                "gasPrice": self.w3.eth.gas_price,
                "chainId": self.config.chain_id,
            })

            signed = self.w3.eth.account.sign_transaction(tx, self.config.private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            log.info(f"  TX: {tx_hash.hex()[:20]}...")

            # Aguardar receipt com retry
            receipt = None
            for attempt in range(6):
                try:
                    receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
                    break
                except Exception as e:
                    err = str(e).lower()
                    if "rate limit" in err or "too many requests" in err:
                        if self._reconnect():
                            time.sleep(2)
                            continue
                        time.sleep(10)
                        continue
                    raise

            if receipt and receipt.status == 1:
                log.info(f"  OK — bloco {receipt.blockNumber}")
                return OnchainResult(
                    success=True,
                    tx_hash=tx_hash.hex(),
                    gas_used=receipt.gasUsed,
                )
            elif receipt:
                return OnchainResult(
                    success=False,
                    tx_hash=tx_hash.hex(),
                    error="already_redeemed",
                )
            else:
                return OnchainResult(
                    success=True,
                    tx_hash=tx_hash.hex(),
                )

        except Exception as e:
            error_msg = str(e)
            log.error(f"  On-chain falhou: {error_msg}")
            if "insufficient funds" in error_msg.lower():
                log.error(f"  Precisa de POL! Envie para: {self.account.address}")
            return OnchainResult(success=False, error=error_msg)

    def get_pol_balance(self) -> float:
        if not self._initialized:
            self.initialize()
        if not self.w3 or not self.account:
            return 0.0
        try:
            return self.w3.eth.get_balance(self.account.address) / 10**18
        except Exception:
            return 0.0
