"""
Technical indicators for reversal detection.

Focused on detecting price reversals in real-time using Binance data.
"""

from typing import Optional
from collections import deque
import math


def calc_ema(values: list[float], period: int) -> list[float]:
    """Calculate Exponential Moving Average."""
    if len(values) < period:
        return []

    multiplier = 2 / (period + 1)
    ema = [sum(values[:period]) / period]  # First EMA is SMA

    for price in values[period:]:
        ema.append((price - ema[-1]) * multiplier + ema[-1])

    return ema


def calc_rsi(closes: list[float], period: int = 14) -> Optional[float]:
    """
    Calculate RSI (Relative Strength Index).

    Args:
        closes: List of closing prices
        period: RSI period (default 14)

    Returns:
        RSI value (0-100) or None if not enough data

    Interpretation:
        - RSI < 30: Oversold, possible reversal UP
        - RSI > 70: Overbought, possible reversal DOWN
    """
    if len(closes) < period + 1:
        return None

    # Calculate price changes
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

    # Separate gains and losses
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]

    # Use recent data
    recent_gains = gains[-period:]
    recent_losses = losses[-period:]

    avg_gain = sum(recent_gains) / period
    avg_loss = sum(recent_losses) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    return round(rsi, 2)


def calc_macd(
    closes: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Optional[dict]:
    """
    Calculate MACD (Moving Average Convergence Divergence).

    Args:
        closes: List of closing prices
        fast: Fast EMA period (default 12)
        slow: Slow EMA period (default 26)
        signal: Signal line period (default 9)

    Returns:
        Dict with macd, signal, histogram, crossover or None

    Interpretation:
        - Bullish crossover: MACD crosses above signal line
        - Bearish crossover: MACD crosses below signal line
        - Histogram > 0 and growing: Bullish momentum
        - Histogram < 0 and shrinking: Bearish momentum
    """
    if len(closes) < slow + signal:
        return None

    ema_fast = calc_ema(closes, fast)
    ema_slow = calc_ema(closes, slow)

    if not ema_fast or not ema_slow:
        return None

    # Align EMAs (slow EMA starts later)
    offset = slow - fast
    macd_line = [
        ema_fast[i + offset] - ema_slow[i]
        for i in range(len(ema_slow))
    ]

    if len(macd_line) < signal:
        return None

    signal_line = calc_ema(macd_line, signal)
    if not signal_line:
        return None

    # Get histogram
    hist_offset = signal - 1
    histogram = [
        macd_line[i + hist_offset] - signal_line[i]
        for i in range(len(signal_line))
    ]

    if len(histogram) < 2:
        return None

    # Detect crossover
    prev_hist = histogram[-2]
    curr_hist = histogram[-1]

    crossover = "none"
    if prev_hist < 0 and curr_hist > 0:
        crossover = "bullish"
    elif prev_hist > 0 and curr_hist < 0:
        crossover = "bearish"

    return {
        "macd": round(macd_line[-1], 4),
        "signal": round(signal_line[-1], 4),
        "histogram": round(curr_hist, 4),
        "prev_histogram": round(prev_hist, 4),
        "crossover": crossover,
        "momentum_increasing": abs(curr_hist) > abs(prev_hist),
    }


def calc_momentum(closes: list[float], period: int = 5) -> Optional[dict]:
    """
    Calculate price momentum (rate of change).

    Args:
        closes: List of closing prices
        period: Lookback period

    Returns:
        Dict with momentum info or None

    This is the KEY indicator for your strategy!
    - Positive momentum: Price going UP
    - Negative momentum: Price going DOWN
    - Strong momentum against your bet = DON'T ENTER
    """
    if len(closes) < period + 1:
        return None

    current = closes[-1]
    past = closes[-(period + 1)]

    if past == 0:
        return None

    # Percentage change
    pct_change = (current - past) / past

    # Absolute price change
    abs_change = current - past

    # Direction
    if pct_change > 0.001:  # > 0.1%
        direction = "up"
    elif pct_change < -0.001:  # < -0.1%
        direction = "down"
    else:
        direction = "flat"

    # Strength (normalized)
    # 0.3% move in 5 candles is considered "strong"
    strength = min(abs(pct_change) / 0.003, 1.0)

    return {
        "pct_change": round(pct_change, 6),
        "abs_change": round(abs_change, 2),
        "direction": direction,
        "strength": round(strength, 2),
        "is_strong": strength > 0.5,
    }


def calc_volume_spike(volumes: list[float], period: int = 20) -> Optional[dict]:
    """
    Detect volume spikes that often precede reversals.

    Args:
        volumes: List of volumes
        period: Averaging period

    Returns:
        Dict with volume analysis or None
    """
    if len(volumes) < period + 1:
        return None

    current_vol = volumes[-1]
    avg_vol = sum(volumes[-period - 1:-1]) / period

    if avg_vol == 0:
        return None

    ratio = current_vol / avg_vol
    is_spike = ratio > 2.0

    return {
        "current": round(current_vol, 2),
        "average": round(avg_vol, 2),
        "ratio": round(ratio, 2),
        "is_spike": is_spike,
    }


def calc_price_action(closes: list[float], highs: list[float], lows: list[float]) -> Optional[dict]:
    """
    Analyze recent price action for reversal signals.

    Args:
        closes: Closing prices
        highs: High prices
        lows: Low prices

    Returns:
        Dict with price action analysis
    """
    if len(closes) < 5 or len(highs) < 5 or len(lows) < 5:
        return None

    # Check for higher highs / lower lows
    recent_highs = highs[-5:]
    recent_lows = lows[-5:]

    higher_highs = all(recent_highs[i] >= recent_highs[i - 1] for i in range(1, len(recent_highs)))
    lower_lows = all(recent_lows[i] <= recent_lows[i - 1] for i in range(1, len(recent_lows)))
    lower_highs = all(recent_highs[i] <= recent_highs[i - 1] for i in range(1, len(recent_highs)))
    higher_lows = all(recent_lows[i] >= recent_lows[i - 1] for i in range(1, len(recent_lows)))

    # Trend detection
    if higher_highs and higher_lows:
        trend = "uptrend"
    elif lower_highs and lower_lows:
        trend = "downtrend"
    else:
        trend = "mixed"

    # Check for potential reversal patterns
    # Bearish reversal: was making higher highs, now making lower high
    bearish_reversal = (
        highs[-3] > highs[-4] and  # Was going up
        highs[-2] > highs[-3] and
        highs[-1] < highs[-2]  # Now going down
    )

    # Bullish reversal: was making lower lows, now making higher low
    bullish_reversal = (
        lows[-3] < lows[-4] and  # Was going down
        lows[-2] < lows[-3] and
        lows[-1] > lows[-2]  # Now going up
    )

    return {
        "trend": trend,
        "higher_highs": higher_highs,
        "lower_lows": lower_lows,
        "bearish_reversal_pattern": bearish_reversal,
        "bullish_reversal_pattern": bullish_reversal,
    }


class CandleBuffer:
    """
    Maintains a buffer of recent candles for indicator calculation.

    Uses deques for efficient memory management.
    """

    def __init__(self, max_size: int = 100):
        self.max_size = max_size
        self.opens = deque(maxlen=max_size)
        self.highs = deque(maxlen=max_size)
        self.lows = deque(maxlen=max_size)
        self.closes = deque(maxlen=max_size)
        self.volumes = deque(maxlen=max_size)
        self.timestamps = deque(maxlen=max_size)

    def add_candle(
        self,
        open_: float,
        high: float,
        low: float,
        close: float,
        volume: float,
        timestamp: int,
    ):
        """Add a new candle to the buffer."""
        self.opens.append(open_)
        self.highs.append(high)
        self.lows.append(low)
        self.closes.append(close)
        self.volumes.append(volume)
        self.timestamps.append(timestamp)

    def update_current(self, high: float, low: float, close: float, volume: float):
        """Update the current (last) candle with new data."""
        if not self.closes:
            return

        # Update high/low if exceeded
        if high > self.highs[-1]:
            self.highs[-1] = high
        if low < self.lows[-1]:
            self.lows[-1] = low

        self.closes[-1] = close
        self.volumes[-1] = volume

    def get_closes(self) -> list[float]:
        return list(self.closes)

    def get_highs(self) -> list[float]:
        return list(self.highs)

    def get_lows(self) -> list[float]:
        return list(self.lows)

    def get_volumes(self) -> list[float]:
        return list(self.volumes)

    def __len__(self):
        return len(self.closes)

    @property
    def last_price(self) -> Optional[float]:
        return self.closes[-1] if self.closes else None

    @property
    def last_timestamp(self) -> Optional[int]:
        return self.timestamps[-1] if self.timestamps else None
