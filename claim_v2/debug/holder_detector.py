"""Detecta holder real do token (EOA/Proxy/Safe) via balanceOf no CTF."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from eth_account import Account
from web3 import Web3

from ..config import ClaimV2Config, CTF_ABI
from .relayer_raw_logger import RelayerRawLogger


@dataclass
class HolderProbe:
    token_id: str
    eoa: str
    proxy: str
    safe: str
    eoa_balance: int
    proxy_balance: int
    safe_balance: int
    holder_role: str
    holder_address: str


class HolderDetector:
    """Sonda balances de ERC1155 e escolhe holder por prioridade."""

    def __init__(self, config: ClaimV2Config, logs_dir: Path, run_id: str, enabled: bool = False):
        self.config = config
        self.enabled = enabled
        self.raw = RelayerRawLogger(logs_dir, run_id, enabled=enabled)
        self._w3: Optional[Web3] = None
        self._contract = None
        self.eoa = Account.from_key(config.private_key).address
        self.proxy = config.wallet_address
        self.safe = (getattr(config, "safe_address", "") or "").strip()

    def _get_w3(self) -> Web3:
        if self._w3 is not None:
            return self._w3
        for rpc_url in self.config.rpc_urls:
            try:
                request_kwargs = {"timeout": 10}
                try:
                    from polygon_rpc import get_request_kwargs_for_rpc  # type: ignore

                    request_kwargs.update(get_request_kwargs_for_rpc(rpc_url, timeout=10))
                except ImportError:
                    pass
                w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs=request_kwargs))
                if w3.is_connected():
                    self._w3 = w3
                    self._contract = w3.eth.contract(
                        address=Web3.to_checksum_address(self.config.ctf_address),
                        abi=CTF_ABI,
                    )
                    return w3
            except Exception:
                continue
        raise RuntimeError("Cannot connect to any Polygon RPC for holder detection")

    def _balance(self, owner: str, token_id: str) -> int:
        if not owner:
            return 0
        try:
            w3 = self._get_w3()
            return int(
                self._contract.functions.balanceOf(
                    Web3.to_checksum_address(owner),
                    int(token_id),
                ).call()
            )
        except Exception:
            return 0

    def probe(self, token_id: str) -> HolderProbe:
        eoa_bal = self._balance(self.eoa, token_id)
        proxy_bal = self._balance(self.proxy, token_id)
        safe_bal = self._balance(self.safe, token_id) if self.safe else 0

        if safe_bal > 0:
            role, holder = "safe", self.safe
        elif proxy_bal > 0:
            role, holder = "proxy", self.proxy
        elif eoa_bal > 0:
            role, holder = "eoa", self.eoa
        else:
            role, holder = "unknown", ""

        probe = HolderProbe(
            token_id=token_id,
            eoa=self.eoa,
            proxy=self.proxy,
            safe=self.safe,
            eoa_balance=eoa_bal,
            proxy_balance=proxy_bal,
            safe_balance=safe_bal,
            holder_role=role,
            holder_address=holder,
        )
        self.raw.log("holder_probe", probe.__dict__)
        return probe

