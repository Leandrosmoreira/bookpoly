#!/usr/bin/env python3
"""
Secure Claim Module - Main entry point.

SECURITY:
- Uses ONLY official libraries (web3.py, eth-account, httpx)
- Direct blockchain calls, no third-party servers
- Private key never transmitted anywhere

USAGE:
    # Dry run (see positions without redeeming)
    python -m claim.main --dry-run

    # Execute redeems
    python -m claim.main

REQUIREMENTS:
    - POL (MATIC) in wallet for gas
    - POLYMARKET_PRIVATE_KEY in .env
    - POLYMARKET_FUNDER in .env
"""
import argparse
import logging
import sys
import time

from .config import ClaimConfig
from .scanner import PositionScanner
from .redeemer import SecureRedeemer

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="Secure Polymarket Claim - Direct blockchain redemption"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan positions without executing redeems"
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Run continuously (scan every 5 minutes)"
    )
    args = parser.parse_args()

    # Banner
    print("=" * 60)
    print(" SECURE CLAIM MODULE")
    print(" Direct blockchain redemption using web3.py")
    print("=" * 60)
    print()

    # Load config
    config = ClaimConfig(dry_run=args.dry_run)

    # Validate
    errors = config.validate()
    if errors:
        log.error("Configuration errors:")
        for e in errors:
            log.error(f"  - {e}")
        sys.exit(1)

    if config.dry_run:
        log.info("DRY-RUN MODE - No transactions will be executed")
    else:
        log.warning("LIVE MODE - Transactions will be executed!")

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
    """Run a single scan and redeem cycle."""
    log.info("Scanning for redeemable positions...")
    positions = scanner.scan()

    if not positions:
        log.info("No redeemable positions found")
        return

    print()
    log.info(f"Found {len(positions)} positions to redeem:")

    total_shares = sum(p.shares for p in positions)
    log.info(f"Total: ~${total_shares:.2f} USDC")

    print()

    # Check POL balance
    pol_balance = redeemer.get_pol_balance()
    if pol_balance < 0.01 and not config.dry_run:
        log.error(f"Insufficient POL for gas! Balance: {pol_balance:.4f}")
        log.error(f"Send POL to: {redeemer.account.address}")
        return

    # Redeem each position
    success_count = 0
    fail_count = 0

    for pos in positions:
        result = redeemer.redeem(pos)

        if result.success:
            success_count += 1
        else:
            fail_count += 1

        # Small delay between transactions
        if not config.dry_run:
            time.sleep(2)

    print()
    log.info(f"Completed: {success_count} success, {fail_count} failed")


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
