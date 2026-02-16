"""
Executor — Orquestra: gasless (Relayer) → fallback on-chain.
"""
import logging
import time
from dataclasses import dataclass
from typing import Optional

from web3 import Web3

from .config import ClaimV2Config, CTF_ABI
from .gasless_redeemer import GaslessRedeemer, GaslessResult
from .onchain_redeemer import OnchainRedeemer, OnchainResult

log = logging.getLogger(__name__)


@dataclass
class RedeemStats:
    gasless_ok: int = 0
    onchain_ok: int = 0
    already_redeemed: int = 0
    failed: int = 0
    total: int = 0


class ClaimExecutor:
    """Orquestra redeem: tenta gasless primeiro, fallback on-chain."""

    def __init__(self, config: ClaimV2Config):
        self.config = config
        self.gasless: GaslessRedeemer | None = None
        self.onchain = OnchainRedeemer(config)
        self._w3: Optional[Web3] = None
        self._contract = None

        if config.has_builder_keys:
            self.gasless = GaslessRedeemer(config)
            log.info("Gasless redeemer habilitado (Builder keys configuradas)")
        else:
            log.warning("Builder keys não configuradas — apenas on-chain (paga POL)")

    def initialize(self) -> bool:
        """Inicializa redeemers."""
        # Inicializar gasless (Safe deploy)
        if self.gasless:
            try:
                self.gasless.ensure_safe_deployed()
            except Exception as e:
                log.warning(f"Gasless init falhou: {e} — usando on-chain")
                self.gasless = None

        # Inicializar on-chain (fallback)
        if not self.onchain.initialize():
            log.error("On-chain redeemer falhou ao inicializar")
            return False

        return True

    # ─── Verificação on-chain após redeem ───────────────────────────────────

    def _get_w3(self) -> Web3:
        """Conecta a um RPC para verificar saldo de token CTF."""
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
        raise RuntimeError("Cannot connect to any Polygon RPC for verify")

    def _has_balance_onchain(self, token_id: str) -> bool:
        """Retorna True se ainda houver saldo desse token na wallet."""
        try:
            w3 = self._get_w3()
            if not self._contract:
                return True
            balance = self._contract.functions.balanceOf(
                Web3.to_checksum_address(self.config.wallet_address),
                int(token_id),
            ).call()
            return balance > 0
        except Exception as e:
            log.warning(f"  Erro ao verificar saldo on-chain pós-redeem: {e}")
            # Em dúvida, melhor considerar que ainda há saldo (não confiar cegamente)
            return True

    def redeem(self, position) -> dict:
        """Resgata uma posição. Tenta gasless → fallback on-chain."""
        result = {"success": False, "method": "none", "tx_hash": "", "error": ""}

        # 1. Tentar gasless
        if self.gasless:
            gasless_result = self.gasless.redeem(position)
            if gasless_result.success:
                return {
                    "success": True,
                    "method": "gasless",
                    "tx_hash": gasless_result.tx_hash or gasless_result.tx_id,
                    "error": "",
                }
            log.info(f"  Gasless falhou, tentando on-chain...")

        # 2. Fallback on-chain
        onchain_result = self.onchain.redeem(position)
        if onchain_result.success:
            # Verificar se o token foi realmente queimado
            if self._has_balance_onchain(position.token_id):
                return {
                    "success": False,
                    "method": "failed",
                    "tx_hash": onchain_result.tx_hash,
                    "error": "onchain_redeem_no_burn",
                }
            return {
                "success": True,
                "method": "onchain",
                "tx_hash": onchain_result.tx_hash,
                "error": "",
            }

        # Verificar se é "already_redeemed"
        if onchain_result.error == "already_redeemed":
            return {
                "success": False,
                "method": "already_redeemed",
                "tx_hash": onchain_result.tx_hash,
                "error": "already_redeemed",
            }

        return {
            "success": False,
            "method": "failed",
            "tx_hash": "",
            "error": onchain_result.error,
        }

    def redeem_all(self, positions: list) -> RedeemStats:
        """Resgata todas as posições com delay entre cada uma."""
        stats = RedeemStats(total=len(positions))

        for i, pos in enumerate(positions):
            log.info(f"[{i+1}/{len(positions)}] {pos.market_slug}: {pos.shares:.0f} shares ({pos.outcome})")
            result = self.redeem(pos)

            if result["success"]:
                if result["method"] == "gasless":
                    stats.gasless_ok += 1
                    log.info(f"  ✓ Gasless OK — {result['tx_hash'][:20]}...")
                else:
                    stats.onchain_ok += 1
                    log.info(f"  ✓ On-chain OK — {result['tx_hash'][:20]}...")
            elif result["error"] == "already_redeemed":
                stats.already_redeemed += 1
                log.info(f"  ○ Já resgatado")
            else:
                stats.failed += 1
                log.error(f"  ✗ Falhou: {result['error'][:80]}")

            # Delay entre redeems
            if i < len(positions) - 1:
                time.sleep(5)

        return stats
