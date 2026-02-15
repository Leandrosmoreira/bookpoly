#!/usr/bin/env python3
"""
Secure Claim Module - Main entry point.

Sempre LIVE: executa resgates de verdade na blockchain.

USAGE:
    python -m claim.main
    python -m claim.main --loop   # scan a cada 5 min

REQUIREMENTS:
    - POL (MATIC) na wallet para gas
    - POLYMARKET_PRIVATE_KEY e POLYMARKET_FUNDER no .env
"""
import argparse
import logging
import sys
import time

from .config import ClaimConfig
from .scanner import PositionScanner
from .redeemer import SecureRedeemer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)
# Não mostrar log de HTTP do httpx (só dados atuais no nosso log)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def main():
    parser = argparse.ArgumentParser(
        description="Secure Polymarket Claim - Resgate direto na blockchain (sempre LIVE)"
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Rodar em loop (scan a cada 5 minutos)"
    )
    args = parser.parse_args()

    print("=" * 60)
    print(" SECURE CLAIM MODULE (LIVE)")
    print(" Resgates executados na blockchain")
    print("=" * 60)
    print()

    config = ClaimConfig()

    errors = config.validate()
    if errors:
        log.error("Erros de configuração:")
        for e in errors:
            log.error(f"  - {e}")
        sys.exit(1)

    log.warning("LIVE MODE - Transações serão executadas!")
    print()

    # Initialize
    scanner = PositionScanner(config)
    redeemer = SecureRedeemer(config)

    # Initialize redeemer to show wallet info
    if not redeemer.initialize():
        log.error("Failed to initialize redeemer")
        sys.exit(1)

    print()

    try:
        if args.loop:
            run_loop(scanner, redeemer, config)
        else:
            run_once(scanner, redeemer, config)
    except KeyboardInterrupt:
        log.info("Interrupted by user")
    finally:
        scanner.close()


def run_once(scanner: PositionScanner, redeemer: SecureRedeemer, config: ClaimConfig):
    """Run a single scan and redeem cycle. Só mostra posições atuais ainda não resgatadas."""
    positions = scanner.scan()

    if not positions:
        log.info("Nenhuma posição para resgatar no momento.")
        return

    total_shares = sum(p.shares for p in positions)
    log.info(f"Posições que a API indica para resgatar: {len(positions)} — ~${total_shares:.2f} USDC")
    log.info("(Se a API estiver atrasada, posições já resgatadas serão ignoradas ao tentar.)")
    for p in positions:
        log.info(f"  • {p.market_slug}: {p.shares:.0f} shares ({p.outcome})")
    print()

    # Check POL balance
    pol_balance = redeemer.get_pol_balance()
    if pol_balance < 0.01:
        log.error(f"Insufficient POL for gas! Balance: {pol_balance:.4f}")
        log.error(f"Send POL to: {redeemer.account.address}")
        return

    # Redeem each position
    success_count = 0
    skipped_count = 0  # já resgatados (API desatualizada)
    fail_count = 0

    for pos in positions:
        result = redeemer.redeem(pos)

        if result.success:
            success_count += 1
        elif getattr(result, "error", "") == "already_redeemed":
            skipped_count += 1
        else:
            fail_count += 1

        # Delay between transactions (evita rate limit do RPC)
        time.sleep(12)

    print()
    if skipped_count:
        log.info(f"Resumo: {success_count} resgatados, {skipped_count} já estavam resgatados (API atrasada), {fail_count} falhas")
    else:
        log.info(f"Resumo: {success_count} resgatados, {fail_count} falhas")


def run_loop(scanner: PositionScanner, redeemer: SecureRedeemer, config: ClaimConfig):
    """Run continuously, scanning every 5 minutes."""
    log.info("Running in loop mode (Ctrl+C to stop)")

    while True:
        try:
            run_once(scanner, redeemer, config)
            log.info("Sleeping 5 minutes...")
            time.sleep(300)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            log.error(f"Error in loop: {e}")
            log.info("Retrying in 1 minute...")
            time.sleep(60)


if __name__ == "__main__":
    main()
