"""
Executor — Orquestra: gasless (Relayer) → fallback on-chain.
"""
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from .config import ClaimV2Config
from .debug.effect_verifier import EffectVerifier
from .debug.holder_detector import HolderDetector
from .debug.relayer_raw_logger import RelayerRawLogger
from .gasless_redeemer import GaslessRedeemer
from .onchain_redeemer import OnchainRedeemer

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

    def __init__(
        self,
        config: ClaimV2Config,
        *,
        debug_holder: bool = False,
        debug_verify: bool = False,
        debug_raw_relayer: bool = False,
        logs_dir: Path | None = None,
        run_id: str = "run",
    ):
        self.config = config
        self.gasless: GaslessRedeemer | None = None
        self.onchain = OnchainRedeemer(config)
        self.logs_dir = logs_dir or (Path(__file__).parent.parent / "logs")
        self.run_id = run_id

        self.raw_logger = RelayerRawLogger(self.logs_dir, run_id, enabled=debug_raw_relayer)
        self.holder_detector = HolderDetector(config, self.logs_dir, run_id, enabled=debug_holder)
        self.effect_verifier = EffectVerifier(config, self.logs_dir, run_id, enabled=debug_verify) if debug_verify else None

        if config.has_builder_keys:
            self.gasless = GaslessRedeemer(config, raw_logger=self.raw_logger)
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
        # Detectar holder real (EOA/Proxy/Safe)
        probe = self.holder_detector.probe(position.token_id)
        log.info(
            "  Holder probe: holder=%s role=%s | eoa=%s proxy=%s safe=%s",
            probe.holder_address or "UNKNOWN",
            probe.holder_role,
            probe.eoa_balance,
            probe.proxy_balance,
            probe.safe_balance,
        )
        self.raw_logger.log(
            "redeem_route_probe",
            {
                "market_slug": position.market_slug,
                "token_id": position.token_id,
                "condition_id": position.condition_id,
                "holder_role": probe.holder_role,
                "holder_address": probe.holder_address,
                "balances": {
                    "eoa": probe.eoa_balance,
                    "proxy": probe.proxy_balance,
                    "safe": probe.safe_balance,
                },
            },
        )

        if probe.holder_role == "unknown":
            return {"success": False, "method": "failed", "tx_hash": "", "error": "holder_unknown"}

        before = None
        if self.effect_verifier and probe.holder_address:
            before = self.effect_verifier.snapshot(probe.holder_address, position.token_id)

        # Rota A: holder proxy/safe -> preferir gasless
        if probe.holder_role in ("proxy", "safe"):
            if self.gasless:
                gasless_result = self.gasless.redeem(position)
                if gasless_result.success:
                    if self.effect_verifier and before:
                        after = self.effect_verifier.snapshot(probe.holder_address, position.token_id)
                        effect = self.effect_verifier.classify(before, after)
                        if effect.get("result") == "SUCCESS_EFFECT":
                            return {
                                "success": True,
                                "method": "gasless",
                                "tx_hash": gasless_result.tx_hash or gasless_result.tx_id,
                                "error": "",
                            }
                        return {
                            "success": False,
                            "method": "failed",
                            "tx_hash": gasless_result.tx_hash or gasless_result.tx_id,
                            "error": f"gasless_{effect.get('result', 'NO_EFFECT').lower()}",
                        }
                    return {
                        "success": True,
                        "method": "gasless",
                        "tx_hash": gasless_result.tx_hash or gasless_result.tx_id,
                        "error": "",
                    }
                # Proxy/Safe não pode ser resgatado por EOA direto
                return {
                    "success": False,
                    "method": "failed",
                    "tx_hash": "",
                    "error": f"gasless_failed_for_{probe.holder_role}_holder:{gasless_result.error}",
                }
            return {
                "success": False,
                "method": "failed",
                "tx_hash": "",
                "error": f"{probe.holder_role}_holder_requires_gasless",
            }

        # Rota B: holder EOA -> on-chain direto
        onchain_result = self.onchain.redeem(position)
        if onchain_result.success:
            if self.effect_verifier and before:
                after = self.effect_verifier.snapshot(probe.holder_address, position.token_id)
                effect = self.effect_verifier.classify(before, after)
                if effect.get("result") != "SUCCESS_EFFECT":
                    return {
                        "success": False,
                        "method": "failed",
                        "tx_hash": onchain_result.tx_hash,
                        "error": f"onchain_{effect.get('result', 'NO_EFFECT').lower()}",
                    }
            return {
                "success": True,
                "method": "onchain",
                "tx_hash": onchain_result.tx_hash,
                "error": "",
            }

        if onchain_result.error == "already_redeemed":
            return {
                "success": False,
                "method": "already_redeemed",
                "tx_hash": onchain_result.tx_hash,
                "error": "already_redeemed",
            }
        return {"success": False, "method": "failed", "tx_hash": "", "error": onchain_result.error}

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
