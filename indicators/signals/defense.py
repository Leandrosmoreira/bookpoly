"""
Defense module for managing open positions.

Detects reversals and decides when to exit or flip positions.
This is the "Modo Defesa" - active only when we have an open position.

Key indicators:
1. Imbalance Delta - sudden flow change
2. Microprice vs Mid - book pressure
3. Imbalance MA 30s - persistent pressure
4. Volatility Spike - RV acceleration
5. Taker Ratio - aggressive traders
6. Momentum - price direction
7. Z-Score - extreme price moves
"""

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from collections import deque
import time


class DefenseAction(Enum):
    """Possible defense actions."""
    HOLD = "HOLD"  # Keep position, no action
    EXIT_EMERGENCY = "EXIT_EMERGENCY"  # Close immediately (market order)
    EXIT_TACTICAL = "EXIT_TACTICAL"  # Close and wait before re-entry
    EXIT_TIME = "EXIT_TIME"  # Close due to time running out
    FLIP = "FLIP"  # Close and open opposite position


@dataclass
class DefenseConfig:
    """Configuration for defense thresholds."""

    # === EMERGENCY EXIT ===
    # Triggers immediate position close
    # IMPORTANTE: Thresholds ajustados apos backtest para reduzir false positives
    max_imb_delta: float = float(os.getenv("DEFENSE_MAX_IMB_DELTA", "0.35"))  # Increased from 0.25 (too many FPs)
    microprice_persist_s: int = int(os.getenv("DEFENSE_MICROPRICE_PERSIST_S", "60"))  # Increased from 45
    taker_threshold_low: float = float(os.getenv("DEFENSE_TAKER_LOW", "0.80"))  # Decreased from 0.85
    taker_threshold_high: float = float(os.getenv("DEFENSE_TAKER_HIGH", "1.20"))  # Increased from 1.15
    taker_persist_s: int = int(os.getenv("DEFENSE_TAKER_PERSIST_S", "60"))  # Increased from 45
    z_threshold: float = float(os.getenv("DEFENSE_Z_THRESHOLD", "99.0"))  # DISABLED - 65 FPs per loss saved
    blocked_regimes: tuple = ("muito_alta",)

    # Require multiple signals to exit (reduces false positives)
    require_multiple_signals: bool = os.getenv("DEFENSE_REQUIRE_MULTIPLE", "true").lower() == "true"
    min_signals_for_exit: int = int(os.getenv("DEFENSE_MIN_SIGNALS_EXIT", "2"))

    # === TACTICAL EXIT ===
    # Close when volatility spikes
    vol_spike_threshold: float = float(os.getenv("DEFENSE_VOL_SPIKE", "0.20"))
    vol_spike_window_s: int = int(os.getenv("DEFENSE_VOL_SPIKE_WINDOW_S", "60"))
    tactical_regimes: tuple = ("alta", "muito_alta")

    # === TIME EXIT ===
    # Close in the last minute if signals are mixed
    min_remaining_for_hold: int = int(os.getenv("DEFENSE_MIN_REMAINING_S", "60"))
    min_prob_to_hold: float = float(os.getenv("DEFENSE_MIN_PROB_HOLD", "0.90"))

    # === FLIP CONDITIONS ===
    # Only flip if multiple signals agree
    min_imb_for_flip: float = float(os.getenv("DEFENSE_MIN_IMB_FLIP", "0.15"))
    min_time_to_flip: int = int(os.getenv("DEFENSE_MIN_TIME_FLIP", "90"))
    max_vol_for_flip: float = float(os.getenv("DEFENSE_MAX_VOL_FLIP", "0.45"))
    flip_stake_pct: float = float(os.getenv("DEFENSE_FLIP_STAKE_PCT", "0.50"))
    min_signals_for_flip: int = int(os.getenv("DEFENSE_MIN_SIGNALS_FLIP", "4"))

    # === GENERAL ===
    enabled: bool = os.getenv("DEFENSE_ENABLED", "true").lower() == "true"
    alert_threshold: float = float(os.getenv("DEFENSE_ALERT_THRESHOLD", "0.50"))


@dataclass
class DefenseState:
    """Tracks state for defense evaluation."""

    # Position info
    side: str = ""  # "UP" or "DOWN"
    entry_price: float = 0.0
    entry_ts: float = 0.0

    # Tracking counters (for persistence checks)
    microprice_against_since: Optional[float] = None
    taker_against_since: Optional[float] = None

    # Historical values for spike detection
    rv_5m_history: deque = field(default_factory=lambda: deque(maxlen=60))
    imbalance_history: deque = field(default_factory=lambda: deque(maxlen=30))
    microprice_edge_history: deque = field(default_factory=lambda: deque(maxlen=30))

    # Timestamps
    last_update_ts: float = 0.0

    def reset(self):
        """Reset state when position is closed."""
        self.side = ""
        self.entry_price = 0.0
        self.entry_ts = 0.0
        self.microprice_against_since = None
        self.taker_against_since = None
        self.rv_5m_history.clear()
        self.imbalance_history.clear()
        self.microprice_edge_history.clear()
        self.last_update_ts = 0.0

    def start_position(self, side: str, entry_price: float):
        """Start tracking a new position."""
        self.reset()
        self.side = side
        self.entry_price = entry_price
        self.entry_ts = time.time()

    def update(
        self,
        imbalance: float,
        microprice_vs_mid: float,
        rv_5m: float,
        taker_ratio: float,
        now_ts: Optional[float] = None,
    ):
        """Update state with new tick data."""
        if now_ts is None:
            now_ts = time.time()

        self.last_update_ts = now_ts

        # Update histories
        self.imbalance_history.append((now_ts, imbalance))
        self.microprice_edge_history.append((now_ts, microprice_vs_mid))
        self.rv_5m_history.append((now_ts, rv_5m))

        # Track microprice persistence
        is_microprice_against = self._is_microprice_against(microprice_vs_mid)
        if is_microprice_against:
            if self.microprice_against_since is None:
                self.microprice_against_since = now_ts
        else:
            self.microprice_against_since = None

        # Track taker persistence
        is_taker_against = self._is_taker_against(taker_ratio)
        if is_taker_against:
            if self.taker_against_since is None:
                self.taker_against_since = now_ts
        else:
            self.taker_against_since = None

    def _is_microprice_against(self, microprice_vs_mid: float) -> bool:
        """Check if microprice is against our position."""
        if self.side == "UP":
            return microprice_vs_mid < 0  # Negative = pressure to go down
        elif self.side == "DOWN":
            return microprice_vs_mid > 0  # Positive = pressure to go up
        return False

    def _is_taker_against(self, taker_ratio: float, config: Optional[DefenseConfig] = None) -> bool:
        """Check if taker flow is against our position."""
        if config is None:
            config = DefenseConfig()

        if self.side == "UP":
            return taker_ratio < config.taker_threshold_low  # More sellers
        elif self.side == "DOWN":
            return taker_ratio > config.taker_threshold_high  # More buyers
        return False

    def get_microprice_against_duration(self, now_ts: Optional[float] = None) -> float:
        """Get how long microprice has been against us."""
        if self.microprice_against_since is None:
            return 0.0
        if now_ts is None:
            now_ts = time.time()
        return now_ts - self.microprice_against_since

    def get_taker_against_duration(self, now_ts: Optional[float] = None) -> float:
        """Get how long taker flow has been against us."""
        if self.taker_against_since is None:
            return 0.0
        if now_ts is None:
            now_ts = time.time()
        return now_ts - self.taker_against_since

    def get_imbalance_ma_30s(self) -> Optional[float]:
        """Get 30-second moving average of imbalance."""
        if len(self.imbalance_history) < 10:  # Need at least 10 samples
            return None
        values = [v for _, v in self.imbalance_history]
        return sum(values) / len(values)

    def get_rv_spike(self, window_s: int = 60) -> Optional[float]:
        """Get volatility spike (% change in RV over window)."""
        if len(self.rv_5m_history) < 2:
            return None

        now_ts = self.last_update_ts
        cutoff = now_ts - window_s

        # Get oldest value within window
        old_val = None
        for ts, val in self.rv_5m_history:
            if ts >= cutoff:
                old_val = val
                break

        if old_val is None or old_val == 0:
            return None

        current_val = self.rv_5m_history[-1][1]
        return (current_val / old_val) - 1.0


@dataclass
class DefenseResult:
    """Result of defense evaluation."""
    action: DefenseAction
    reason: str
    score: float  # 0-1, higher = more danger

    # Diagnostic info
    imbalance_delta: Optional[float] = None
    imbalance_ma_30s: Optional[float] = None
    microprice_against_s: float = 0.0
    taker_against_s: float = 0.0
    rv_spike: Optional[float] = None
    z_score: Optional[float] = None

    # Flip info (only if action == FLIP)
    flip_signals: int = 0
    flip_conditions: dict = field(default_factory=dict)


def evaluate_defense(
    # Position info
    side: str,
    entry_price: float,
    remaining_s: float,

    # Current indicators
    prob_up: float,
    imbalance: float,
    imbalance_delta: Optional[float],
    microprice_vs_mid: float,
    taker_ratio: float,
    rv_5m: float,
    regime: Optional[str],
    z_score: Optional[float],

    # State
    state: DefenseState,

    # Config
    config: Optional[DefenseConfig] = None,
) -> DefenseResult:
    """
    Evaluate whether to hold, exit, or flip position.

    Args:
        side: Current position side ("UP" or "DOWN")
        entry_price: Price we entered at
        remaining_s: Seconds remaining in window
        prob_up: Current probability of UP
        imbalance: Current order book imbalance
        imbalance_delta: Change in imbalance from previous tick
        microprice_vs_mid: Microprice minus mid
        taker_ratio: Taker buy/sell ratio
        rv_5m: 5-minute realized volatility
        regime: Volatility regime
        z_score: Price z-score
        state: Defense state tracker
        config: Defense configuration

    Returns:
        DefenseResult with action and reasoning
    """
    if config is None:
        config = DefenseConfig()

    if not config.enabled:
        return DefenseResult(
            action=DefenseAction.HOLD,
            reason="defense_disabled",
            score=0.0,
        )

    # Calculate derived metrics (use last_update_ts for backtest compatibility)
    now_ts = state.last_update_ts if state.last_update_ts > 0 else None
    microprice_against_s = state.get_microprice_against_duration(now_ts)
    taker_against_s = state.get_taker_against_duration(now_ts)
    imbalance_ma_30s = state.get_imbalance_ma_30s()
    rv_spike = state.get_rv_spike(config.vol_spike_window_s)

    # Calculate current prob for our side
    prob_our_side = prob_up if side == "UP" else (1 - prob_up)

    reasons = []
    danger_score = 0.0

    # === CHECK 1: EMERGENCY EXIT ===

    # 1a. Blocked regime (always exit, no multi-signal required)
    if regime and regime in config.blocked_regimes:
        return DefenseResult(
            action=DefenseAction.EXIT_EMERGENCY,
            reason=f"regime_blocked:{regime}",
            score=1.0,
            imbalance_delta=imbalance_delta,
            imbalance_ma_30s=imbalance_ma_30s,
            microprice_against_s=microprice_against_s,
            taker_against_s=taker_against_s,
            rv_spike=rv_spike,
            z_score=z_score,
        )

    # Count danger signals (requires multiple to exit)
    exit_signals = []

    # 1b. Violent imbalance flip
    if imbalance_delta is not None:
        imb_against = (
            (side == "UP" and imbalance_delta <= -config.max_imb_delta) or
            (side == "DOWN" and imbalance_delta >= config.max_imb_delta)
        )
        if imb_against:
            exit_signals.append(f"imbalance_flip:{imbalance_delta:+.3f}")

    # 1c. Microprice persistent against
    if microprice_against_s >= config.microprice_persist_s:
        exit_signals.append(f"microprice_against:{microprice_against_s:.0f}s")

    # 1d. Taker ratio persistent against
    if taker_against_s >= config.taker_persist_s:
        exit_signals.append(f"taker_against:{taker_against_s:.0f}s")

    # 1e. Extreme z-score (disabled by default - too many false positives)
    if z_score is not None and abs(z_score) > config.z_threshold:
        exit_signals.append(f"extreme_zscore:{z_score:+.2f}")

    # 1f. Probability dropping fast (new: prob < 90% when we bet on favorite)
    if prob_our_side < 0.90:
        exit_signals.append(f"prob_dropping:{prob_our_side:.0%}")

    # Check if we have enough signals to exit
    if config.require_multiple_signals:
        if len(exit_signals) >= config.min_signals_for_exit:
            return DefenseResult(
                action=DefenseAction.EXIT_EMERGENCY,
                reason=f"multi_signal({len(exit_signals)}):" + "+".join(exit_signals[:2]),
                score=1.0,
                imbalance_delta=imbalance_delta,
                imbalance_ma_30s=imbalance_ma_30s,
                microprice_against_s=microprice_against_s,
                taker_against_s=taker_against_s,
                rv_spike=rv_spike,
                z_score=z_score,
            )
    else:
        # Legacy mode: exit on single signal
        if exit_signals:
            return DefenseResult(
                action=DefenseAction.EXIT_EMERGENCY,
                reason=exit_signals[0],
                score=1.0,
                imbalance_delta=imbalance_delta,
                imbalance_ma_30s=imbalance_ma_30s,
                microprice_against_s=microprice_against_s,
                taker_against_s=taker_against_s,
                rv_spike=rv_spike,
                z_score=z_score,
            )

    # === CHECK 2: TACTICAL EXIT ===

    if (
        rv_spike is not None and
        rv_spike > config.vol_spike_threshold and
        regime in config.tactical_regimes and
        remaining_s >= 90  # Still time to re-enter
    ):
        return DefenseResult(
            action=DefenseAction.EXIT_TACTICAL,
            reason=f"vol_spike:{rv_spike*100:.0f}%_regime:{regime}",
            score=0.7,
            imbalance_delta=imbalance_delta,
            imbalance_ma_30s=imbalance_ma_30s,
            microprice_against_s=microprice_against_s,
            taker_against_s=taker_against_s,
            rv_spike=rv_spike,
            z_score=z_score,
        )

    # === CHECK 3: TIME EXIT ===

    if remaining_s < config.min_remaining_for_hold:
        # In the last minute, only hold if clearly winning
        if prob_our_side < config.min_prob_to_hold:
            return DefenseResult(
                action=DefenseAction.EXIT_TIME,
                reason=f"time_exit:remaining={remaining_s:.0f}s_prob={prob_our_side:.0%}",
                score=0.5,
                imbalance_delta=imbalance_delta,
                imbalance_ma_30s=imbalance_ma_30s,
                microprice_against_s=microprice_against_s,
                taker_against_s=taker_against_s,
                rv_spike=rv_spike,
                z_score=z_score,
            )

    # === CHECK 4: FLIP OPPORTUNITY ===

    if remaining_s >= config.min_time_to_flip:
        flip_conditions = {}
        flip_signals = 0

        # [1] Imbalance favors underdog
        imb_favor_underdog = (
            (side == "UP" and imbalance < -config.min_imb_for_flip) or
            (side == "DOWN" and imbalance > config.min_imb_for_flip)
        )
        flip_conditions["imbalance_underdog"] = imb_favor_underdog
        if imb_favor_underdog:
            flip_signals += 1

        # [2] Microprice favors underdog for 30s
        microprice_underdog = microprice_against_s >= 30
        flip_conditions["microprice_underdog_30s"] = microprice_underdog
        if microprice_underdog:
            flip_signals += 1

        # [3] Taker ratio favors underdog
        taker_underdog = (
            (side == "UP" and taker_ratio < 0.85) or
            (side == "DOWN" and taker_ratio > 1.15)
        )
        flip_conditions["taker_underdog"] = taker_underdog
        if taker_underdog:
            flip_signals += 1

        # [4] Volatility is controlled
        vol_controlled = rv_5m < config.max_vol_for_flip
        flip_conditions["vol_controlled"] = vol_controlled
        if vol_controlled:
            flip_signals += 1

        # [5] Time is sufficient
        time_ok = remaining_s >= config.min_time_to_flip
        flip_conditions["time_sufficient"] = time_ok
        if time_ok:
            flip_signals += 1

        if flip_signals >= config.min_signals_for_flip:
            return DefenseResult(
                action=DefenseAction.FLIP,
                reason=f"flip:{flip_signals}/5_signals",
                score=0.6,
                imbalance_delta=imbalance_delta,
                imbalance_ma_30s=imbalance_ma_30s,
                microprice_against_s=microprice_against_s,
                taker_against_s=taker_against_s,
                rv_spike=rv_spike,
                z_score=z_score,
                flip_signals=flip_signals,
                flip_conditions=flip_conditions,
            )

    # === DEFAULT: HOLD ===

    # Calculate danger score for logging
    if microprice_against_s > 0:
        danger_score += min(microprice_against_s / config.microprice_persist_s, 0.3)
    if taker_against_s > 0:
        danger_score += min(taker_against_s / config.taker_persist_s, 0.3)
    if rv_spike is not None and rv_spike > 0:
        danger_score += min(rv_spike / config.vol_spike_threshold, 0.2)
    if z_score is not None:
        danger_score += min(abs(z_score) / config.z_threshold, 0.2)

    danger_score = min(danger_score, 1.0)

    return DefenseResult(
        action=DefenseAction.HOLD,
        reason=f"holding:prob={prob_our_side:.0%}_danger={danger_score:.2f}",
        score=danger_score,
        imbalance_delta=imbalance_delta,
        imbalance_ma_30s=imbalance_ma_30s,
        microprice_against_s=microprice_against_s,
        taker_against_s=taker_against_s,
        rv_spike=rv_spike,
        z_score=z_score,
    )


def format_defense_result(result: DefenseResult) -> str:
    """Format defense result for logging."""
    if result.action == DefenseAction.HOLD:
        return f"HOLD: {result.reason}"
    elif result.action == DefenseAction.FLIP:
        return f"FLIP! {result.reason} signals={result.flip_signals}"
    else:
        return f"{result.action.value}! {result.reason}"
