"""Verifica efeito do redeem por delta de USDC + saldo token CTF."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from web3 import Web3

from ..config import ClaimV2Config, CTF_ABI
from .relayer_raw_logger import RelayerRawLogger


USDC_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    }
]


@dataclass
class EffectSnapshot:
    holder: str
    token_id: str
    usdc: int
    token_balance: int


class EffectVerifier:
    """Snapshot antes/depois e classificação de efeito."""

    def __init__(self, config: ClaimV2Config, logs_dir: Path, run_id: str, enabled: bool = False):
        self.config = config
        self.enabled = enabled
        self.raw = RelayerRawLogger(logs_dir, run_id, enabled=enabled)
        self._w3: Optional[Web3] = None
        self._ctf = None
        self._usdc = None

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
                    self._ctf = w3.eth.contract(
                        address=Web3.to_checksum_address(self.config.ctf_address),
                        abi=CTF_ABI,
                    )
                    self._usdc = w3.eth.contract(
                        address=Web3.to_checksum_address(self.config.usdc_address),
                        abi=USDC_ABI,
                    )
                    return w3
            except Exception:
                continue
        raise RuntimeError("Cannot connect to any Polygon RPC for effect verify")

    def snapshot(self, holder: str, token_id: str) -> EffectSnapshot:
        if not holder:
            return EffectSnapshot(holder="", token_id=token_id, usdc=0, token_balance=0)
        try:
            w3 = self._get_w3()
            checksum = Web3.to_checksum_address(holder)
            usdc = int(self._usdc.functions.balanceOf(checksum).call())
            token = int(self._ctf.functions.balanceOf(checksum, int(token_id)).call())
            snap = EffectSnapshot(holder=holder, token_id=token_id, usdc=usdc, token_balance=token)
            self.raw.log("effect_snapshot", snap.__dict__)
            return snap
        except Exception as e:
            self.raw.log("effect_snapshot_error", {"holder": holder, "token_id": token_id, "error": str(e)})
            return EffectSnapshot(holder=holder, token_id=token_id, usdc=0, token_balance=0)

    def classify(self, before: EffectSnapshot, after: EffectSnapshot) -> dict:
        delta_usdc = after.usdc - before.usdc
        delta_token = after.token_balance - before.token_balance
        if delta_token < 0 and delta_usdc >= 0:
            result = "SUCCESS_EFFECT"
        elif delta_token == 0 and delta_usdc == 0:
            result = "NO_EFFECT"
        else:
            result = "PARTIAL_EFFECT"
        row = {
            "holder": before.holder,
            "token_id": before.token_id,
            "before_usdc": before.usdc,
            "after_usdc": after.usdc,
            "delta_usdc": delta_usdc,
            "before_token": before.token_balance,
            "after_token": after.token_balance,
            "delta_token": delta_token,
            "result": result,
        }
        self.raw.log("effect_result", row)
        return row

