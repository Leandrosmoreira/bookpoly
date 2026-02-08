"""
Claim Loop - Main loop for the Claim Sweeper.

Runs every 2 minutes (with jitter) to scan for and execute claims.
Uses file locking to prevent concurrent execution.
"""
import asyncio
import fcntl
import json
import logging
import os
import random
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from claims.config import ClaimConfig
from claims.models import ClaimEvent, ClaimStats
from claims.ledger import ClaimLedger
from claims.scanner import ClaimScanner
from claims.executor import ClaimExecutor

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("claims")


class ClaimLoop:
    """
    Main claim sweeper loop.

    Features:
    - Runs every poll_seconds + random jitter
    - File lock to prevent concurrent execution
    - Respects max_per_cycle limit
    - Logs events to JSONL files
    - Graceful shutdown on SIGTERM/SIGINT
    """

    def __init__(self, config: ClaimConfig = None):
        self.config = config or ClaimConfig()
        self.ledger = ClaimLedger(self.config.db_path)
        self.scanner = ClaimScanner(self.config)
        self.executor = ClaimExecutor(self.config)

        self.running = True
        self.lock_fd = None

        # Ensure log directory exists
        Path(self.config.log_dir).mkdir(parents=True, exist_ok=True)

    async def run(self):
        """Main loop."""
        log.info("=" * 60)
        log.info("Claim Sweeper starting...")
        log.info(f"  Mode: {'DRY RUN' if self.config.dry_run else 'LIVE'}")
        log.info(f"  Poll interval: {self.config.poll_seconds}s + jitter")
        log.info(f"  Max per cycle: {self.config.max_per_cycle}")
        log.info(f"  Sell price: ${self.config.sell_price:.2f}")
        log.info("=" * 60)

        # Validate configuration
        errors = self.config.validate()
        if errors:
            for err in errors:
                log.error(f"Config error: {err}")
            if not self.config.dry_run:
                log.error("Cannot run in LIVE mode with config errors")
                return

        # Setup signal handlers
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        while self.running:
            try:
                await self._run_cycle()
            except Exception as e:
                log.error(f"Error in cycle: {e}")

            if not self.running:
                break

            # Sleep with jitter
            jitter = random.uniform(0, self.config.jitter_seconds)
            sleep_time = self.config.poll_seconds + jitter
            log.info(f"Sleeping {sleep_time:.1f}s until next cycle...")

            # Sleep in small chunks to allow graceful shutdown
            for _ in range(int(sleep_time)):
                if not self.running:
                    break
                await asyncio.sleep(1)

        await self._cleanup()
        log.info("Claim Sweeper stopped")

    async def _run_cycle(self):
        """Execute one claim cycle."""
        # Acquire lock
        if not self._acquire_lock():
            log.warning("Could not acquire lock, another instance running?")
            return

        try:
            stats = ClaimStats()
            self._log_event(ClaimEvent(
                ts=int(time.time()),
                event="scan_start",
                dry_run=self.config.dry_run,
            ))

            # Scan for claimables
            claimables = await self.scanner.scan_claimables()
            stats.scanned = len(claimables)

            # Filter already claimed
            new_claimables = []
            for item in claimables:
                if self.ledger.is_already_claimed(item.claim_id):
                    stats.skipped += 1
                    continue
                if self.ledger.is_pending_or_retrying(item.claim_id):
                    continue
                new_claimables.append(item)

            stats.claimable = len(new_claimables)
            log.info(f"Cycle: {stats.scanned} scanned, {stats.claimable} claimable, {stats.skipped} skipped")

            # Process up to max_per_cycle
            to_process = new_claimables[:self.config.max_per_cycle]

            for item in to_process:
                if not self.running:
                    break

                # Check retry delay
                if not self.ledger.should_retry(item.claim_id, self.config.max_retries):
                    log.debug(f"Skipping {item.claim_id}: max retries reached")
                    continue

                delay = self.ledger.get_retry_delay(item.claim_id, self.config.backoff_base)
                if delay > 0:
                    log.debug(f"Waiting {delay}s before retry")
                    await asyncio.sleep(delay)

                # Register and mark in progress
                self.ledger.register_claim(item)
                self.ledger.mark_in_progress(item.claim_id)
                stats.attempted += 1

                # Log attempt
                self._log_event(ClaimEvent(
                    ts=int(time.time()),
                    event="claim_attempt",
                    claim_id=item.claim_id,
                    market_id=item.market_id,
                    market_slug=item.market_slug,
                    token_id=item.token_id,
                    shares=item.shares,
                    amount=item.total_payout,
                    dry_run=self.config.dry_run,
                ))

                # Execute claim
                result = await self.executor.claim(item)

                if result.success:
                    self.ledger.mark_success(item.claim_id, result)
                    stats.success += 1
                    stats.total_payout += result.amount_received
                    stats.total_fees += result.fee_paid

                    self._log_event(ClaimEvent(
                        ts=int(time.time()),
                        event="claim_success",
                        claim_id=item.claim_id,
                        market_id=item.market_id,
                        market_slug=item.market_slug,
                        shares=item.shares,
                        amount=result.amount_received,
                        fee=result.fee_paid,
                        order_id=result.order_id,
                        dry_run=self.config.dry_run,
                    ))
                else:
                    self.ledger.mark_failed(item.claim_id, result.error, result.retryable)
                    stats.failed += 1

                    self._log_event(ClaimEvent(
                        ts=int(time.time()),
                        event="claim_failed",
                        claim_id=item.claim_id,
                        market_id=item.market_id,
                        market_slug=item.market_slug,
                        error=result.error,
                        dry_run=self.config.dry_run,
                    ))

            # Log cycle end
            stats.cycle_end = int(time.time())
            self._log_event(ClaimEvent(
                ts=stats.cycle_end,
                event="scan_end",
                dry_run=self.config.dry_run,
            ))

            # Log summary
            log.info(
                f"Cycle complete: {stats.attempted} attempted, "
                f"{stats.success} success, {stats.failed} failed | "
                f"Payout: ${stats.total_payout:.2f} (fees: ${stats.total_fees:.2f})"
            )

        finally:
            self._release_lock()

    def _acquire_lock(self) -> bool:
        """Acquire exclusive file lock."""
        try:
            lock_path = Path(self.config.lock_path)
            lock_path.parent.mkdir(parents=True, exist_ok=True)

            self.lock_fd = open(lock_path, "w")
            fcntl.flock(self.lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (IOError, OSError):
            if self.lock_fd:
                self.lock_fd.close()
                self.lock_fd = None
            return False

    def _release_lock(self):
        """Release file lock."""
        if self.lock_fd:
            try:
                fcntl.flock(self.lock_fd.fileno(), fcntl.LOCK_UN)
                self.lock_fd.close()
            except:
                pass
            self.lock_fd = None

    def _log_event(self, event: ClaimEvent):
        """Log event to JSONL file."""
        try:
            date = datetime.now().strftime("%Y-%m-%d")
            log_file = Path(self.config.log_dir) / f"claims_{date}.jsonl"

            with open(log_file, "a") as f:
                f.write(json.dumps(event.to_dict()) + "\n")
        except Exception as e:
            log.warning(f"Failed to log event: {e}")

    def _handle_signal(self, signum, frame):
        """Handle shutdown signals."""
        log.info(f"Received signal {signum}, shutting down...")
        self.running = False

    async def _cleanup(self):
        """Cleanup resources."""
        await self.scanner.close()
        await self.executor.close()
        self.ledger.close()
        self._release_lock()


async def main():
    """Entry point."""
    config = ClaimConfig()

    # Check if enabled
    if not config.enabled:
        log.warning("Claim Sweeper is disabled (CLAIM_ENABLED=false)")
        return

    loop = ClaimLoop(config)
    await loop.run()


if __name__ == "__main__":
    asyncio.run(main())
