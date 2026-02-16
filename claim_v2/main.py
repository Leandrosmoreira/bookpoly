#!/usr/bin/env python3
"""
Claim v2 — Gasless via Polymarket Relayer + fallback on-chain.

USO:
    python -m claim_v2.main           # Single run
    python -m claim_v2.main --loop    # Contínuo (5min)

REQUER:
    - POLYMARKET_PRIVATE_KEY e POLYMARKET_FUNDER no .env
    - Para gasless: POLY_BUILDER_API_KEY, POLY_BUILDER_SECRET, POLY_BUILDER_PASSPHRASE
    - Para on-chain fallback: POL (MATIC) na wallet para gas
"""
import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import ClaimV2Config
from .scanner import ScannerV2
from .executor import ClaimExecutor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def main():
    parser = argparse.ArgumentParser(description="Claim v2 — Gasless + On-chain fallback")
    parser.add_argument("--loop", action="store_true", help="Rodar em loop (scan a cada 5 minutos)")
    parser.add_argument("--debug-holder", action="store_true", help="Loga holder detector (EOA/Proxy/Safe)")
    parser.add_argument("--debug-verify", action="store_true", help="Verifica efeito real (delta token/USDC)")
    parser.add_argument("--debug-raw-relayer", action="store_true", help="Salva payload bruto de execute/wait do relayer")
    args = parser.parse_args()

    print("=" * 64)
    print("  CLAIM v2 — GASLESS VIA RELAYER + FALLBACK ON-CHAIN")
    print("=" * 64)
    print()

    config = ClaimV2Config()
    errors = config.validate()
    if errors:
        log.error("Erros de configuração:")
        for e in errors:
            log.error(f"  - {e}")
        sys.exit(1)

    if config.has_builder_keys:
        log.info("Builder keys configuradas → Gasless habilitado")
    else:
        log.warning("Builder keys NÃO configuradas → Apenas on-chain (paga POL)")
        log.warning("Para gasless, configure: POLY_BUILDER_API_KEY, POLY_BUILDER_SECRET, POLY_BUILDER_PASSPHRASE")
    print()

    # Inicializar
    scanner = ScannerV2(config)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logs_dir = Path(__file__).parent.parent / "logs"
    executor = ClaimExecutor(
        config,
        debug_holder=args.debug_holder,
        debug_verify=args.debug_verify,
        debug_raw_relayer=args.debug_raw_relayer,
        logs_dir=logs_dir,
        run_id=run_id,
    )

    if not executor.initialize():
        log.error("Falha ao inicializar executor")
        sys.exit(1)

    # Mostrar info
    pol_balance = executor.onchain.get_pol_balance()
    log.info(f"Wallet: {config.wallet_address}")
    log.info(f"POL balance: {pol_balance:.4f}")
    print()

    try:
        if args.loop:
            run_loop(scanner, executor, logs_dir=logs_dir, run_id=run_id, debug_mode=(args.debug_holder or args.debug_verify or args.debug_raw_relayer))
        else:
            run_once(scanner, executor, logs_dir=logs_dir, run_id=run_id, debug_mode=(args.debug_holder or args.debug_verify or args.debug_raw_relayer))
    except KeyboardInterrupt:
        log.info("Interrompido pelo usuário")
    finally:
        scanner.close()


def run_once(scanner: ScannerV2, executor: ClaimExecutor, *, logs_dir: Path, run_id: str, debug_mode: bool):
    """Um ciclo de scan + redeem."""
    positions = scanner.scan()
    if debug_mode:
        dump_path = logs_dir / f"claim_v2_redeemables_{run_id}.json"
        payload = [
            {
                "condition_id": p.condition_id,
                "token_id": p.token_id,
                "outcome": p.outcome,
                "outcome_index": p.outcome_index,
                "shares": p.shares,
                "market_slug": p.market_slug,
                "title": p.title,
            }
            for p in positions
        ]
        dump_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        log.info(f"Dump redeemables: {dump_path}")

    if not positions:
        log.info("Nenhuma posição para resgatar.")
        return

    total_shares = sum(p.shares for p in positions)
    log.info(f"Posições encontradas: {len(positions)} — ~${total_shares:.2f} USDC")
    for p in positions:
        log.info(f"  • {p.market_slug}: {p.shares:.0f} shares ({p.outcome})")
    print()

    stats = executor.redeem_all(positions)

    print()
    log.info("─── RESUMO ───")
    log.info(f"  Gasless OK: {stats.gasless_ok}")
    log.info(f"  On-chain OK: {stats.onchain_ok}")
    log.info(f"  Já resgatados: {stats.already_redeemed}")
    log.info(f"  Falhas: {stats.failed}")
    log.info(f"  Total: {stats.total}")
    if debug_mode:
        report_path = logs_dir / "CLAIM_V2_DEBUG_REPORT.md"
        report = "\n".join(
            [
                "# CLAIM V2 DEBUG REPORT",
                "",
                f"- Run ID: `{run_id}`",
                f"- Gasless OK: `{stats.gasless_ok}`",
                f"- On-chain OK: `{stats.onchain_ok}`",
                f"- Already Redeemed: `{stats.already_redeemed}`",
                f"- Failed: `{stats.failed}`",
                "",
                "## Arquivos gerados",
                f"- `logs/claim_v2_redeemables_{run_id}.json`",
                f"- `logs/relayer_raw_{run_id}.jsonl` (se `--debug-raw-relayer`)",
                "",
                "## Nota",
                "- Se houver falha com holder `proxy/safe`, o fallback on-chain via EOA nao resolve.",
                "- Verifique eventos `holder_probe` e `effect_result` para confirmar holder mismatch.",
                "",
            ]
        )
        report_path.write_text(report, encoding="utf-8")
        log.info(f"Debug report: {report_path}")


def run_loop(scanner: ScannerV2, executor: ClaimExecutor, *, logs_dir: Path, run_id: str, debug_mode: bool):
    """Loop contínuo — scan a cada 5 minutos."""
    log.info("Modo loop (Ctrl+C para parar)")
    while True:
        try:
            run_once(scanner, executor, logs_dir=logs_dir, run_id=run_id, debug_mode=debug_mode)
            log.info("Próximo scan em 5 minutos...")
            time.sleep(300)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            log.error(f"Erro no loop: {e}")
            log.info("Retentando em 1 minuto...")
            time.sleep(60)


if __name__ == "__main__":
    main()
