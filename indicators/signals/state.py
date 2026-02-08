"""
Temporal state tracking for trading signals.

Tracks persistence, rolling statistics, and historical context
to make better trading decisions.
"""

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque


@dataclass
class RollingStats:
    """Rolling window statistics."""
    mean: float
    std: float
    min: float
    max: float
    count: int
    z_score: float | None  # (current - mean) / std


@dataclass
class TemporalState:
    """Temporal state for a single coin."""
    # Gate persistence
    gates_passed_since: float | None = None  # Timestamp when gates first passed
    persistence_s: float = 0.0  # Seconds gates have been passing

    # Rolling statistics (last 5 minutes = 300 ticks at 1Hz)
    prob_stats: RollingStats | None = None
    imbalance_stats: RollingStats | None = None
    spread_stats: RollingStats | None = None
    microprice_edge_stats: RollingStats | None = None

    # Previous window result
    prev_window_outcome: str | None = None  # "up", "down", or None
    prev_window_prob: float | None = None  # Final probability of prev window

    # Current window tracking
    current_window_start: int = 0
    ticks_in_window: int = 0


class StateTracker:
    """
    Tracks temporal state for multiple coins.

    Maintains rolling windows and persistence tracking
    for making entry decisions.
    """

    def __init__(self, window_size: int = 300):
        """
        Initialize state tracker.

        Args:
            window_size: Number of ticks to keep in rolling window (default 300 = 5 min at 1Hz)
        """
        self.window_size = window_size

        # Per-coin state
        self._states: dict[str, TemporalState] = {}

        # Rolling windows per coin
        self._prob_history: dict[str, Deque[float]] = {}
        self._imbalance_history: dict[str, Deque[float]] = {}
        self._spread_history: dict[str, Deque[float]] = {}
        self._microprice_edge_history: dict[str, Deque[float]] = {}

        # Defense-specific histories (with timestamps for 30s window)
        self._imbalance_ts_history: dict[str, Deque[tuple[float, float]]] = {}
        self._microprice_ts_history: dict[str, Deque[tuple[float, float]]] = {}
        self._rv_5m_history: dict[str, Deque[tuple[float, float]]] = {}
        self._taker_ratio_history: dict[str, Deque[tuple[float, float]]] = {}

    def _get_state(self, coin: str) -> TemporalState:
        """Get or create state for a coin."""
        if coin not in self._states:
            self._states[coin] = TemporalState()
            self._prob_history[coin] = deque(maxlen=self.window_size)
            self._imbalance_history[coin] = deque(maxlen=self.window_size)
            self._spread_history[coin] = deque(maxlen=self.window_size)
            self._microprice_edge_history[coin] = deque(maxlen=self.window_size)
            # Defense histories (60 ticks = 60 seconds at 1Hz)
            self._imbalance_ts_history[coin] = deque(maxlen=60)
            self._microprice_ts_history[coin] = deque(maxlen=60)
            self._rv_5m_history[coin] = deque(maxlen=60)
            self._taker_ratio_history[coin] = deque(maxlen=60)
        return self._states[coin]

    def _compute_rolling_stats(self, history: Deque[float], current: float) -> RollingStats:
        """Compute rolling statistics from history."""
        if len(history) < 2:
            return RollingStats(
                mean=current,
                std=0.0,
                min=current,
                max=current,
                count=len(history),
                z_score=None,
            )

        values = list(history)
        n = len(values)
        mean = sum(values) / n
        variance = sum((x - mean) ** 2 for x in values) / n
        std = variance ** 0.5

        z_score = (current - mean) / std if std > 0 else 0.0

        return RollingStats(
            mean=mean,
            std=std,
            min=min(values),
            max=max(values),
            count=n,
            z_score=z_score,
        )

    def update(
        self,
        coin: str,
        gates_passed: bool,
        prob: float,
        imbalance: float,
        spread_pct: float,
        microprice_edge: float,
        window_start: int,
        now_ts: float | None = None,
        rv_5m: float | None = None,
        taker_ratio: float | None = None,
    ) -> TemporalState:
        """
        Update state for a coin with new tick data.

        Args:
            coin: Coin symbol (e.g., "btc")
            gates_passed: Whether all gates are currently passing
            prob: Current probability (mid price)
            imbalance: Current order book imbalance
            spread_pct: Current spread as percentage
            microprice_edge: Microprice - mid
            window_start: Current window start timestamp
            now_ts: Current timestamp (default: time.time())

        Returns:
            Updated TemporalState
        """
        if now_ts is None:
            now_ts = time.time()

        state = self._get_state(coin)

        # Check for window change
        if window_start != state.current_window_start:
            # New window started
            if state.current_window_start > 0:
                # Save previous window info
                state.prev_window_prob = prob  # Will be updated with actual outcome
            state.current_window_start = window_start
            state.ticks_in_window = 0

            # Clear rolling history for new window
            self._prob_history[coin].clear()
            self._imbalance_history[coin].clear()
            self._spread_history[coin].clear()
            self._microprice_edge_history[coin].clear()

        state.ticks_in_window += 1

        # Update rolling histories
        self._prob_history[coin].append(prob)
        self._imbalance_history[coin].append(imbalance)
        self._spread_history[coin].append(spread_pct)
        self._microprice_edge_history[coin].append(microprice_edge)

        # Update defense histories (with timestamps)
        self._imbalance_ts_history[coin].append((now_ts, imbalance))
        self._microprice_ts_history[coin].append((now_ts, microprice_edge))
        if rv_5m is not None:
            self._rv_5m_history[coin].append((now_ts, rv_5m))
        if taker_ratio is not None:
            self._taker_ratio_history[coin].append((now_ts, taker_ratio))

        # Compute rolling stats
        state.prob_stats = self._compute_rolling_stats(self._prob_history[coin], prob)
        state.imbalance_stats = self._compute_rolling_stats(self._imbalance_history[coin], imbalance)
        state.spread_stats = self._compute_rolling_stats(self._spread_history[coin], spread_pct)
        state.microprice_edge_stats = self._compute_rolling_stats(
            self._microprice_edge_history[coin], microprice_edge
        )

        # Update persistence
        if gates_passed:
            if state.gates_passed_since is None:
                state.gates_passed_since = now_ts
            state.persistence_s = now_ts - state.gates_passed_since
        else:
            state.gates_passed_since = None
            state.persistence_s = 0.0

        return state

    def set_window_outcome(self, coin: str, outcome: str, final_prob: float):
        """
        Record the outcome of a completed window.

        Args:
            coin: Coin symbol
            outcome: "up" or "down"
            final_prob: Final probability at window close
        """
        state = self._get_state(coin)
        state.prev_window_outcome = outcome
        state.prev_window_prob = final_prob

    def get_state(self, coin: str) -> TemporalState:
        """Get current state for a coin."""
        return self._get_state(coin)

    def get_prev_imbalance(self, coin: str) -> float | None:
        """Get previous imbalance for delta calculation."""
        history = self._imbalance_history.get(coin)
        if history and len(history) > 0:
            return history[-1]
        return None

    def get_imbalance_ma(self, coin: str, periods: int = 30) -> float | None:
        """
        Get moving average of imbalance.

        Args:
            coin: Coin symbol
            periods: Number of periods for MA (default 30 = 30 seconds)

        Returns:
            Moving average or None if not enough data
        """
        history = self._imbalance_history.get(coin)
        if not history or len(history) < periods:
            return None

        recent = list(history)[-periods:]
        return sum(recent) / len(recent)

    def get_prob_momentum(self, coin: str, periods: int = 60) -> float | None:
        """
        Get probability momentum (change over N periods).

        Args:
            coin: Coin symbol
            periods: Number of periods to look back

        Returns:
            Change in probability or None if not enough data
        """
        history = self._prob_history.get(coin)
        if not history or len(history) < periods:
            return None

        return history[-1] - history[-periods]

    # === DEFENSE METHODS ===

    def get_imbalance_delta_30s(self, coin: str) -> float | None:
        """
        Get change in imbalance over last 30 seconds.

        Returns:
            imbalance_now - imbalance_30s_ago, or None if not enough data
        """
        history = self._imbalance_ts_history.get(coin)
        if not history or len(history) < 10:
            return None

        now_ts = history[-1][0]
        current_imb = history[-1][1]

        # Find value from ~30s ago
        target_ts = now_ts - 30
        old_imb = None
        for ts, imb in history:
            if ts >= target_ts:
                old_imb = imb
                break

        if old_imb is None:
            old_imb = history[0][1]  # Use oldest available

        return current_imb - old_imb

    def get_microprice_edge_ma_30s(self, coin: str) -> float | None:
        """
        Get 30-second moving average of microprice edge.

        Returns:
            Average microprice_vs_mid over last 30s, or None if not enough data
        """
        history = self._microprice_ts_history.get(coin)
        if not history or len(history) < 10:
            return None

        now_ts = history[-1][0]
        cutoff = now_ts - 30

        values = [v for ts, v in history if ts >= cutoff]
        if not values:
            return None

        return sum(values) / len(values)

    def get_rv_spike(self, coin: str, window_s: int = 60) -> float | None:
        """
        Get volatility spike (% change in RV over window).

        Returns:
            (rv_now / rv_old) - 1, or None if not enough data
        """
        history = self._rv_5m_history.get(coin)
        if not history or len(history) < 2:
            return None

        now_ts = history[-1][0]
        current_rv = history[-1][1]
        cutoff = now_ts - window_s

        # Find oldest value within window
        old_rv = None
        for ts, rv in history:
            if ts >= cutoff:
                old_rv = rv
                break

        if old_rv is None or old_rv <= 0:
            return None

        return (current_rv / old_rv) - 1.0

    def get_taker_ratio_ma_30s(self, coin: str) -> float | None:
        """
        Get 30-second moving average of taker ratio.

        Returns:
            Average taker_ratio over last 30s, or None if not enough data
        """
        history = self._taker_ratio_history.get(coin)
        if not history or len(history) < 5:
            return None

        now_ts = history[-1][0]
        cutoff = now_ts - 30

        values = [v for ts, v in history if ts >= cutoff]
        if not values:
            return None

        return sum(values) / len(values)

    def is_microprice_against(self, coin: str, side: str, persist_s: int = 30) -> bool:
        """
        Check if microprice has been against position for persist_s seconds.

        Args:
            coin: Coin symbol
            side: Position side ("UP" or "DOWN")
            persist_s: Required persistence in seconds

        Returns:
            True if microprice is persistently against our position
        """
        history = self._microprice_ts_history.get(coin)
        if not history or len(history) < persist_s:
            return False

        now_ts = history[-1][0]
        cutoff = now_ts - persist_s

        # Check if all values in the window are against us
        for ts, edge in history:
            if ts >= cutoff:
                if side == "UP" and edge >= 0:
                    return False  # Not all against
                if side == "DOWN" and edge <= 0:
                    return False  # Not all against

        return True

    def is_taker_against(
        self, coin: str, side: str, persist_s: int = 30, threshold: float = 0.90
    ) -> bool:
        """
        Check if taker ratio has been against position for persist_s seconds.

        Args:
            coin: Coin symbol
            side: Position side ("UP" or "DOWN")
            persist_s: Required persistence in seconds
            threshold: Taker ratio threshold (< threshold for UP, > 1/threshold for DOWN)

        Returns:
            True if taker flow is persistently against our position
        """
        history = self._taker_ratio_history.get(coin)
        if not history or len(history) < persist_s:
            return False

        now_ts = history[-1][0]
        cutoff = now_ts - persist_s

        high_threshold = 1.0 / threshold  # e.g., 1.11 if threshold = 0.90

        # Check if all values in the window are against us
        for ts, ratio in history:
            if ts >= cutoff:
                if side == "UP" and ratio >= threshold:
                    return False  # Not all against
                if side == "DOWN" and ratio <= high_threshold:
                    return False  # Not all against

        return True


def format_state_summary(state: TemporalState) -> str:
    """Format state for logging."""
    parts = []

    # Persistence
    if state.persistence_s > 0:
        parts.append(f"persist={state.persistence_s:.0f}s")

    # Z-scores
    if state.prob_stats and state.prob_stats.z_score is not None:
        parts.append(f"prob_z={state.prob_stats.z_score:+.2f}")

    if state.imbalance_stats and state.imbalance_stats.z_score is not None:
        parts.append(f"imb_z={state.imbalance_stats.z_score:+.2f}")

    # Previous window
    if state.prev_window_outcome:
        parts.append(f"prev={state.prev_window_outcome}")

    return " | ".join(parts) if parts else "no_state"
