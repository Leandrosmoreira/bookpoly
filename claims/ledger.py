"""
Claim Ledger - SQLite storage for idempotent claim tracking.

Prevents double-claiming and tracks history of all claim attempts.
"""
import sqlite3
import time
import os
from pathlib import Path
from typing import Optional
from claims.models import ClaimItem, ClaimResult, ClaimStatus


class ClaimLedger:
    """
    SQLite-based ledger for tracking claims.

    Ensures idempotency by tracking claim_id and status.
    """

    def __init__(self, db_path: str = "data/claims.db"):
        self.db_path = db_path

        # Ensure directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        """Initialize database schema."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS claims (
                claim_id TEXT PRIMARY KEY,
                market_id TEXT NOT NULL,
                market_slug TEXT,
                token_id TEXT NOT NULL,
                side TEXT,
                shares REAL NOT NULL,
                entry_price REAL,
                won INTEGER,
                payout REAL,
                fee REAL DEFAULT 0,
                status TEXT DEFAULT 'PENDING',
                order_id TEXT,
                first_seen_ts INTEGER NOT NULL,
                last_attempt_ts INTEGER,
                completed_ts INTEGER,
                attempt_count INTEGER DEFAULT 0,
                error_msg TEXT,
                raw_response TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_claims_status ON claims(status);
            CREATE INDEX IF NOT EXISTS idx_claims_market ON claims(market_id);
            CREATE INDEX IF NOT EXISTS idx_claims_first_seen ON claims(first_seen_ts);

            -- Stats table for tracking daily totals
            CREATE TABLE IF NOT EXISTS claim_stats (
                date TEXT PRIMARY KEY,
                total_claims INTEGER DEFAULT 0,
                successful_claims INTEGER DEFAULT 0,
                failed_claims INTEGER DEFAULT 0,
                total_payout REAL DEFAULT 0,
                total_fees REAL DEFAULT 0,
                updated_at INTEGER
            );
        """)
        self.conn.commit()

    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()

    def is_already_claimed(self, claim_id: str) -> bool:
        """Check if a claim has already been successfully processed."""
        row = self.conn.execute(
            "SELECT status FROM claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        return row is not None and row["status"] == ClaimStatus.SUCCESS.value

    def is_pending_or_retrying(self, claim_id: str) -> bool:
        """Check if a claim is currently being processed."""
        row = self.conn.execute(
            "SELECT status, last_attempt_ts FROM claims WHERE claim_id = ?",
            (claim_id,)
        ).fetchone()

        if not row:
            return False

        status = row["status"]
        last_attempt = row["last_attempt_ts"] or 0

        # If in progress and recent (< 5 min), consider it pending
        if status == ClaimStatus.IN_PROGRESS.value:
            if time.time() - last_attempt < 300:
                return True

        return status == ClaimStatus.RETRYING.value

    def should_retry(self, claim_id: str, max_retries: int = 5) -> bool:
        """Check if a failed claim should be retried."""
        row = self.conn.execute(
            "SELECT status, attempt_count FROM claims WHERE claim_id = ?",
            (claim_id,)
        ).fetchone()

        if not row:
            return True  # Never attempted

        status = row["status"]
        attempts = row["attempt_count"] or 0

        # Don't retry successful claims
        if status == ClaimStatus.SUCCESS.value:
            return False

        # Don't retry skipped claims
        if status == ClaimStatus.SKIPPED.value:
            return False

        # Retry if under max attempts
        return attempts < max_retries

    def get_retry_delay(self, claim_id: str, backoff_base: int = 10) -> int:
        """Get exponential backoff delay for retry."""
        row = self.conn.execute(
            "SELECT attempt_count FROM claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()

        if not row:
            return 0

        attempts = row["attempt_count"] or 0
        # Exponential backoff: 10s, 20s, 40s, 80s, 160s
        return backoff_base * (2 ** attempts)

    def register_claim(self, item: ClaimItem):
        """Register a new claimable item."""
        now = int(time.time())

        self.conn.execute("""
            INSERT OR IGNORE INTO claims (
                claim_id, market_id, market_slug, token_id, side,
                shares, entry_price, won, payout, status, first_seen_ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            item.claim_id,
            item.market_id,
            item.market_slug,
            item.token_id,
            item.side,
            item.shares,
            item.entry_price,
            1 if item.won else 0,
            item.total_payout,
            ClaimStatus.PENDING.value,
            now,
        ))
        self.conn.commit()

    def mark_in_progress(self, claim_id: str):
        """Mark a claim as in progress."""
        now = int(time.time())
        self.conn.execute("""
            UPDATE claims
            SET status = ?, last_attempt_ts = ?, attempt_count = attempt_count + 1
            WHERE claim_id = ?
        """, (ClaimStatus.IN_PROGRESS.value, now, claim_id))
        self.conn.commit()

    def mark_success(self, claim_id: str, result: ClaimResult):
        """Mark a claim as successful."""
        now = int(time.time())
        self.conn.execute("""
            UPDATE claims
            SET status = ?, order_id = ?, payout = ?, fee = ?,
                completed_ts = ?, last_attempt_ts = ?, raw_response = ?
            WHERE claim_id = ?
        """, (
            ClaimStatus.SUCCESS.value,
            result.order_id,
            result.amount_received,
            result.fee_paid,
            now,
            now,
            str(result.raw_response) if result.raw_response else None,
            claim_id,
        ))
        self.conn.commit()

        # Update daily stats
        self._update_daily_stats(1, 0, result.amount_received, result.fee_paid)

    def mark_failed(self, claim_id: str, error: str, retryable: bool = True):
        """Mark a claim as failed."""
        now = int(time.time())
        status = ClaimStatus.RETRYING.value if retryable else ClaimStatus.FAILED.value

        self.conn.execute("""
            UPDATE claims
            SET status = ?, error_msg = ?, last_attempt_ts = ?
            WHERE claim_id = ?
        """, (status, error, now, claim_id))
        self.conn.commit()

        if not retryable:
            self._update_daily_stats(0, 1, 0, 0)

    def mark_skipped(self, claim_id: str, reason: str):
        """Mark a claim as skipped (e.g., already claimed externally)."""
        now = int(time.time())
        self.conn.execute("""
            UPDATE claims
            SET status = ?, error_msg = ?, last_attempt_ts = ?
            WHERE claim_id = ?
        """, (ClaimStatus.SKIPPED.value, reason, now, claim_id))
        self.conn.commit()

    def _update_daily_stats(self, success: int, failed: int, payout: float, fees: float):
        """Update daily statistics."""
        today = time.strftime("%Y-%m-%d")
        now = int(time.time())

        self.conn.execute("""
            INSERT INTO claim_stats (date, total_claims, successful_claims, failed_claims,
                                     total_payout, total_fees, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                total_claims = total_claims + 1,
                successful_claims = successful_claims + ?,
                failed_claims = failed_claims + ?,
                total_payout = total_payout + ?,
                total_fees = total_fees + ?,
                updated_at = ?
        """, (today, 1, success, failed, payout, fees, now,
              success, failed, payout, fees, now))
        self.conn.commit()

    def get_pending_claims(self, max_retries: int = 5) -> list[dict]:
        """Get all pending/retrying claims that should be processed."""
        rows = self.conn.execute("""
            SELECT * FROM claims
            WHERE status IN (?, ?) AND attempt_count < ?
            ORDER BY first_seen_ts ASC
        """, (ClaimStatus.PENDING.value, ClaimStatus.RETRYING.value, max_retries)).fetchall()

        return [dict(row) for row in rows]

    def get_recent_claims(self, limit: int = 20) -> list[dict]:
        """Get recent claim attempts."""
        rows = self.conn.execute("""
            SELECT * FROM claims
            ORDER BY COALESCE(completed_ts, last_attempt_ts, first_seen_ts) DESC
            LIMIT ?
        """, (limit,)).fetchall()

        return [dict(row) for row in rows]

    def get_daily_stats(self, date: Optional[str] = None) -> Optional[dict]:
        """Get stats for a specific date."""
        if date is None:
            date = time.strftime("%Y-%m-%d")

        row = self.conn.execute(
            "SELECT * FROM claim_stats WHERE date = ?", (date,)
        ).fetchone()

        return dict(row) if row else None

    def get_stats_summary(self) -> dict:
        """Get overall statistics."""
        row = self.conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status = 'SUCCESS' THEN 1 ELSE 0 END) as success,
                SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) as failed,
                SUM(CASE WHEN status = 'PENDING' THEN 1 ELSE 0 END) as pending,
                SUM(CASE WHEN status = 'RETRYING' THEN 1 ELSE 0 END) as retrying,
                SUM(CASE WHEN status = 'SUCCESS' THEN payout ELSE 0 END) as total_payout,
                SUM(CASE WHEN status = 'SUCCESS' THEN fee ELSE 0 END) as total_fees
            FROM claims
        """).fetchone()

        return dict(row) if row else {}
