"""
Scanner v2 — reutiliza claim.scanner + verifica balance on-chain.
"""
import logging
import time
from typing import Optional

from web3 import Web3

from .config import ClaimV2Config, CTF_ABI

log = logging.getLogger(__name__)


# Reutilizar scanner e dataclass do claim v1
from claim.scanner import PositionScanner, RedeemablePosition  # noqa: E402


class ScannerV2:
    """Scanner com verificação on-chain de saldo antes de listar posições."""

    def __init__(self, config: ClaimV2Config):
        self.config = config
        self.scanner = PositionScanner(config)
        self._w3: Optional[Web3] = None
        self._contract = None

    def _get_w3(self) -> Web3:
        if self._w3 is not None:
            return self._w3
        for rpc_url in self.config.rpc_urls:
            try:
                request_kwargs = {"timeout": 10}
                try:
                    from polygon_rpc import get_request_kwargs_for_rpc
                    request_kwargs.update(get_request_kwargs_for_rpc(rpc_url, timeout=10))
                except ImportError:
                    pass
                w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs=request_kwargs))
                if w3.is_connected():
                    self._w3 = w3
                    self._contract = w3.eth.contract(
                        address=Web3.to_checksum_address(self.config.ctf_address),
                        abi=CTF_ABI
                    )
                    return w3
            except Exception:
                continue
        raise RuntimeError("Cannot connect to any Polygon RPC")

    def _has_balance(self, token_id: str) -> bool:
        """Verifica on-chain se o wallet tem saldo deste token."""
        for attempt in range(3):
            try:
                w3 = self._get_w3()
                balance = self._contract.functions.balanceOf(
                    Web3.to_checksum_address(self.config.wallet_address),
                    int(token_id)
                ).call()
                return balance > 0
            except Exception as e:
                err = str(e).lower()
                if "rate limit" in err or "too many requests" in err:
                    self._w3 = None  # Force reconnect
                    self._contract = None
                    time.sleep(3)
                    continue
                log.warning(f"  Erro ao verificar balance: {e}")
                return True  # Em dúvida, tenta resgatar
        return True

    def scan(self) -> list[RedeemablePosition]:
        """Scan + filtrar posições com saldo on-chain > 0."""
        positions = self.scanner.scan()
        if not positions:
            return []

        filtered = []
        for pos in positions:
            if self._has_balance(pos.token_id):
                filtered.append(pos)
            else:
                log.info(f"  Skip (saldo on-chain=0): {pos.market_slug}")

        return filtered

    def close(self):
        self.scanner.close()
