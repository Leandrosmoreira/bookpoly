"""
Executor — Orquestra: gasless (Relayer) → fallback on-chain.
"""
import logging
import time
from dataclasses import dataclass

from .config import ClaimV2Config
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
