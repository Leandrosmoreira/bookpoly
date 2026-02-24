"""
VPIN (Volume-synchronized Probability of Informed Trading) calculator.

Uses volume-bucketed trade classification to measure the probability
of informed trading in real time.
"""

import math
import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class VpinBucket:
    """A single volume bucket for VPIN calculation."""
    volume: float = 0.0
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    trade_count: int = 0
    ts_start: int = 0
    ts_end: int = 0
    price_sum: float = 0.0  # For weighted average price


@dataclass
class VpinMetrics:
    """Computed VPIN metrics."""
    vpin: float | None = None
    vpin_ema: float | None = None
    flow_toxicity: str = "unknown"
    buy_pct_last_5: float | None = None
    bucket_fill_pct: float = 0.0
    avg_bucket_duration_s: float = 0.0
    completed_buckets: int = 0
    bucket_volume: float = 0.0
    trades_total: int = 0


class VpinCalculator:
    """
    Volume-bucketed VPIN calculator.

    Trades are accumulated into fixed-volume buckets.
    When a bucket fills, it is closed and a new one starts.
    VPIN is computed as the average order imbalance across
    the last N completed buckets.
    """

    def __init__(self, bucket_volume: float, num_buckets: int = 50, ema_span: int = 10):
        self.bucket_volume = bucket_volume
        self.num_buckets = num_buckets
        self._ema_alpha = 2.0 / (ema_span + 1)

        self._current = VpinBucket()
        self._completed: deque[VpinBucket] = deque(maxlen=num_buckets)
        self._vpin_ema: float | None = None
        self._trades_total = 0

    def add_trade(self, ts_ms: int, price: float, qty: float, is_buy: bool) -> float | None:
        """
        Add a trade to the current bucket.

        If the trade causes the bucket to fill (or overflow), the bucket
        is completed and a new one starts. Overflow volume is split
        proportionally into the new bucket.

        Returns:
            Updated VPIN if a bucket was completed, else None.
        """
        self._trades_total += 1
        remaining_qty = qty
        vpin = None

        while remaining_qty > 0:
            space = self.bucket_volume - self._current.volume

            if remaining_qty <= space:
                # Fits entirely in current bucket
                self._add_to_current(ts_ms, price, remaining_qty, is_buy)
                remaining_qty = 0
            else:
                # Fills current bucket, overflow goes to next
                self._add_to_current(ts_ms, price, space, is_buy)
                remaining_qty -= space

                # Complete current bucket
                self._complete_bucket()
                vpin = self.compute_vpin()

                # Start new bucket
                self._current = VpinBucket()

        return vpin

    def _add_to_current(self, ts_ms: int, price: float, qty: float, is_buy: bool):
        """Add volume to current bucket."""
        if self._current.trade_count == 0:
            self._current.ts_start = ts_ms

        self._current.volume += qty
        self._current.price_sum += price * qty
        self._current.trade_count += 1
        self._current.ts_end = ts_ms

        if is_buy:
            self._current.buy_volume += qty
        else:
            self._current.sell_volume += qty

    def _complete_bucket(self):
        """Close current bucket and add to completed deque."""
        self._completed.append(self._current)

        # Update EMA
        bucket_oi = abs(self._current.buy_volume - self._current.sell_volume)
        bucket_vpin = bucket_oi / self._current.volume if self._current.volume > 0 else 0

        if self._vpin_ema is None:
            self._vpin_ema = bucket_vpin
        else:
            self._vpin_ema = self._ema_alpha * bucket_vpin + (1 - self._ema_alpha) * self._vpin_ema

    def compute_vpin(self) -> float | None:
        """Compute VPIN from completed buckets."""
        if len(self._completed) < 2:
            return None

        total_oi = sum(
            abs(b.buy_volume - b.sell_volume)
            for b in self._completed
        )
        total_vol = sum(b.volume for b in self._completed)

        if total_vol <= 0:
            return None

        return total_oi / total_vol

    def get_metrics(self) -> VpinMetrics:
        """Get all VPIN metrics for the current state."""
        vpin = self.compute_vpin()

        # Buy percentage of last 5 buckets
        buy_pct_5 = None
        if len(self._completed) >= 5:
            recent = list(self._completed)[-5:]
            total_buy = sum(b.buy_volume for b in recent)
            total_vol = sum(b.volume for b in recent)
            if total_vol > 0:
                buy_pct_5 = total_buy / total_vol

        # Bucket fill percentage
        fill_pct = self._current.volume / self.bucket_volume if self.bucket_volume > 0 else 0

        # Average bucket duration
        avg_duration = 0.0
        if len(self._completed) >= 2:
            durations = [
                (b.ts_end - b.ts_start) / 1000.0
                for b in self._completed
                if b.ts_end > b.ts_start
            ]
            if durations:
                avg_duration = sum(durations) / len(durations)

        # Flow toxicity classification
        toxicity = self._classify_toxicity(vpin)

        return VpinMetrics(
            vpin=vpin,
            vpin_ema=self._vpin_ema,
            flow_toxicity=toxicity,
            buy_pct_last_5=buy_pct_5,
            bucket_fill_pct=fill_pct,
            avg_bucket_duration_s=avg_duration,
            completed_buckets=len(self._completed),
            bucket_volume=self.bucket_volume,
            trades_total=self._trades_total,
        )

    @staticmethod
    def _classify_toxicity(vpin: float | None) -> str:
        """Classify flow toxicity based on VPIN value."""
        if vpin is None:
            return "unknown"
        if vpin < 0.3:
            return "low"
        if vpin < 0.5:
            return "medium"
        if vpin < 0.7:
            return "high"
        return "extreme"
