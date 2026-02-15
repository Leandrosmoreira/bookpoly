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
import logging
import sys
import time

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
    executor = ClaimExecutor(config)

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
            run_loop(scanner, executor)
        else:
            run_once(scanner, executor)
    except KeyboardInterrupt:
        log.info("Interrompido pelo usuário")
    finally:
        scanner.close()


def run_once(scanner: ScannerV2, executor: ClaimExecutor):
    """Um ciclo de scan + redeem."""
    positions = scanner.scan()

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


def run_loop(scanner: ScannerV2, executor: ClaimExecutor):
    """Loop contínuo — scan a cada 5 minutos."""
    log.info("Modo loop (Ctrl+C para parar)")
    while True:
        try:
            run_once(scanner, executor)
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
