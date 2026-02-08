"""
Reversal Detector - Core logic for detecting price reversals.

This module combines multiple indicators to generate a reversal score
that can be used to block entries when the market is likely to reverse.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional
import time

from .indicators import (
    CandleBuffer,
    calc_rsi,
    calc_macd,
    calc_momentum,
    calc_volume_spike,
    calc_price_action,
)
from .config import BinanceRealtimeConfig


class ReversalDirection(Enum):
    """Direction of potential reversal."""
    UP = "up"  # Likely to reverse UP (was going down)
    DOWN = "down"  # Likely to reverse DOWN (was going up)
    NONE = "none"


class ReversalSignal(Enum):
    """Reversal signal strength."""
    NONE = "none"  # No reversal detected
    WEAK = "weak"  # Some signs, not conclusive
    MODERATE = "moderate"  # Moderate confidence
    STRONG = "strong"  # High confidence reversal incoming
    EXTREME = "extreme"  # Very high confidence, BLOCK entry


@dataclass
class ReversalResult:
    """Result of reversal detection."""
    score: float  # 0-1, higher = more likely reversal
    direction: ReversalDirection
    signal: ReversalSignal
    should_block: bool  # True if should block entry
    reason: str  # Human-readable explanation

    # Component scores
    rsi_score: float
    macd_score: float
    momentum_score: float
    volume_score: float
    price_action_score: float

    # Raw indicator values
    rsi: Optional[float]
    macd_crossover: Optional[str]
    momentum_pct: Optional[float]
    momentum_direction: Optional[str]
    volume_ratio: Optional[float]

    timestamp: float


class ReversalDetector:
    """
    Detects potential price reversals using multiple indicators.

    Usage:
        detector = ReversalDetector()
        detector.update_candle(open, high, low, close, volume, timestamp)
        result = detector.detect(bet_side="UP")

        if result.should_block:
            print("DON'T ENTER! Reversal detected!")
    """

    def __init__(self, config: Optional[BinanceRealtimeConfig] = None):
        self.config = config or BinanceRealtimeConfig()
        self.buffer = CandleBuffer(max_size=self.config.max_candles)
        self.last_result: Optional[ReversalResult] = None

    def update_candle(
        self,
        open_: float,
        high: float,
        low: float,
        close: float,
        volume: float,
        timestamp: int,
        is_closed: bool = True,
    ):
        """
        Update with new candle data.

        Args:
            open_, high, low, close, volume: OHLCV data
            timestamp: Candle timestamp (ms)
            is_closed: True if candle is closed, False if still forming
        """
        if is_closed:
            self.buffer.add_candle(open_, high, low, close, volume, timestamp)
        else:
            # Update current forming candle
            self.buffer.update_current(high, low, close, volume)

    def detect(self, bet_side: str = "UP") -> ReversalResult:
        """
        Detect potential reversal.

        Args:
            bet_side: "UP" or "DOWN" - the side we want to bet on

        Returns:
            ReversalResult with score and recommendation
        """
        closes = self.buffer.get_closes()
        highs = self.buffer.get_highs()
        lows = self.buffer.get_lows()
        volumes = self.buffer.get_volumes()

        # Initialize scores
        rsi_score = 0.0
        macd_score = 0.0
        momentum_score = 0.0
        volume_score = 0.0
        price_action_score = 0.0

        # Raw values for logging
        rsi_val = None
        macd_crossover = None
        momentum_pct = None
        momentum_dir = None
        volume_ratio = None

        reasons = []
        reversal_direction = ReversalDirection.NONE

        # === 1. RSI Analysis ===
        rsi = calc_rsi(closes, self.config.rsi_period)
        if rsi is not None:
            rsi_val = rsi

            if rsi < self.config.rsi_oversold:
                # Oversold - might reverse UP
                rsi_score = (self.config.rsi_oversold - rsi) / self.config.rsi_oversold
                if bet_side == "DOWN":
                    # We bet DOWN but market might go UP
                    reasons.append(f"RSI oversold ({rsi:.0f})")
                    reversal_direction = ReversalDirection.UP

            elif rsi > self.config.rsi_overbought:
                # Overbought - might reverse DOWN
                rsi_score = (rsi - self.config.rsi_overbought) / (100 - self.config.rsi_overbought)
                if bet_side == "UP":
                    # We bet UP but market might go DOWN
                    reasons.append(f"RSI overbought ({rsi:.0f})")
                    reversal_direction = ReversalDirection.DOWN

        # === 2. MACD Analysis ===
        macd = calc_macd(closes, self.config.macd_fast, self.config.macd_slow, self.config.macd_signal)
        if macd:
            macd_crossover = macd["crossover"]

            if macd["crossover"] == "bearish":
                macd_score = 0.8
                if bet_side == "UP":
                    reasons.append("MACD bearish crossover")
                    reversal_direction = ReversalDirection.DOWN

            elif macd["crossover"] == "bullish":
                macd_score = 0.8
                if bet_side == "DOWN":
                    reasons.append("MACD bullish crossover")
                    reversal_direction = ReversalDirection.UP

            # Histogram momentum
            if not macd["momentum_increasing"]:
                macd_score = max(macd_score, 0.3)

        # === 3. Momentum Analysis (CRITICAL for your strategy) ===
        momentum = calc_momentum(closes, self.config.momentum_period)
        if momentum:
            momentum_pct = momentum["pct_change"]
            momentum_dir = momentum["direction"]

            # KEY: If momentum is AGAINST our bet, high score
            if bet_side == "UP" and momentum["direction"] == "down":
                momentum_score = momentum["strength"]
                if momentum["is_strong"]:
                    reasons.append(f"Strong DOWN momentum ({momentum['pct_change']*100:.2f}%)")
                    reversal_direction = ReversalDirection.DOWN

            elif bet_side == "DOWN" and momentum["direction"] == "up":
                momentum_score = momentum["strength"]
                if momentum["is_strong"]:
                    reasons.append(f"Strong UP momentum ({momentum['pct_change']*100:.2f}%)")
                    reversal_direction = ReversalDirection.UP

        # === 4. Volume Spike Analysis ===
        vol_spike = calc_volume_spike(volumes, self.config.volume_avg_period)
        if vol_spike:
            volume_ratio = vol_spike["ratio"]

            if vol_spike["is_spike"]:
                volume_score = min((vol_spike["ratio"] - 1.0) / 2.0, 1.0)
                reasons.append(f"Volume spike ({vol_spike['ratio']:.1f}x)")

        # === 5. Price Action Analysis ===
        price_action = calc_price_action(closes, highs, lows)
        if price_action:
            if bet_side == "UP" and price_action["bearish_reversal_pattern"]:
                price_action_score = 0.7
                reasons.append("Bearish reversal pattern")
                reversal_direction = ReversalDirection.DOWN

            elif bet_side == "DOWN" and price_action["bullish_reversal_pattern"]:
                price_action_score = 0.7
                reasons.append("Bullish reversal pattern")
                reversal_direction = ReversalDirection.UP

        # === Calculate Final Score ===
        score = (
            self.config.weight_rsi * rsi_score +
            self.config.weight_macd * macd_score +
            self.config.weight_momentum * momentum_score +
            self.config.weight_volume * volume_score +
            self.config.weight_imbalance * price_action_score
        )
        score = min(score, 1.0)

        # Determine signal level
        if score >= 0.80:
            signal = ReversalSignal.EXTREME
        elif score >= 0.60:
            signal = ReversalSignal.STRONG
        elif score >= 0.40:
            signal = ReversalSignal.MODERATE
        elif score >= 0.20:
            signal = ReversalSignal.WEAK
        else:
            signal = ReversalSignal.NONE

        # Should we block entry?
        should_block = score >= self.config.reversal_block

        # Build reason string
        if reasons:
            reason = "; ".join(reasons)
        else:
            reason = "No reversal signals"

        result = ReversalResult(
            score=round(score, 3),
            direction=reversal_direction,
            signal=signal,
            should_block=should_block,
            reason=reason,
            rsi_score=round(rsi_score, 3),
            macd_score=round(macd_score, 3),
            momentum_score=round(momentum_score, 3),
            volume_score=round(volume_score, 3),
            price_action_score=round(price_action_score, 3),
            rsi=rsi_val,
            macd_crossover=macd_crossover,
            momentum_pct=momentum_pct,
            momentum_direction=momentum_dir,
            volume_ratio=volume_ratio,
            timestamp=time.time(),
        )

        self.last_result = result
        return result

    def get_quick_momentum_check(self, bet_side: str) -> tuple[bool, str]:
        """
        Quick check if momentum is against our bet.

        This is a fast check that can be used without full detection.

        Args:
            bet_side: "UP" or "DOWN"

        Returns:
            (should_block, reason)
        """
        closes = self.buffer.get_closes()
        if len(closes) < 6:
            return False, "Not enough data"

        momentum = calc_momentum(closes, 5)
        if not momentum:
            return False, "Could not calculate momentum"

        # Check if momentum is strongly against our bet
        if bet_side == "UP" and momentum["direction"] == "down" and momentum["is_strong"]:
            return True, f"Strong DOWN momentum ({momentum['pct_change']*100:.2f}%)"

        if bet_side == "DOWN" and momentum["direction"] == "up" and momentum["is_strong"]:
            return True, f"Strong UP momentum ({momentum['pct_change']*100:.2f}%)"

        return False, "Momentum OK"

    @property
    def has_enough_data(self) -> bool:
        """Check if we have enough candles for reliable detection."""
        return len(self.buffer) >= 30

    @property
    def current_price(self) -> Optional[float]:
        """Get current price."""
        return self.buffer.last_price

    def to_dict(self) -> dict:
        """Export current state as dictionary."""
        if not self.last_result:
            return {}

        r = self.last_result
        return {
            "reversal": {
                "score": r.score,
                "direction": r.direction.value,
                "signal": r.signal.value,
                "should_block": r.should_block,
                "reason": r.reason,
            },
            "components": {
                "rsi": r.rsi_score,
                "macd": r.macd_score,
                "momentum": r.momentum_score,
                "volume": r.volume_score,
                "price_action": r.price_action_score,
            },
            "indicators": {
                "rsi": r.rsi,
                "macd_crossover": r.macd_crossover,
                "momentum_pct": r.momentum_pct,
                "momentum_direction": r.momentum_direction,
                "volume_ratio": r.volume_ratio,
            },
            "meta": {
                "timestamp": r.timestamp,
                "candles": len(self.buffer),
            },
        }
