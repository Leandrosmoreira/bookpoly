from collections import deque
from typing import Optional
import bisect


# Fixed thresholds for cold-start (annualized volatility %)
FIXED_THRESHOLDS = {
    "BTCUSDT": {
        "muito_baixa": 0.15,
        "baixa": 0.25,
        "normal": 0.50,
        "alta": 0.80,
    },
    "ETHUSDT": {
        "muito_baixa": 0.20,
        "baixa": 0.35,
        "normal": 0.60,
        "alta": 1.00,
    },
    "SOLUSDT": {
        "muito_baixa": 0.30,
        "baixa": 0.50,
        "normal": 0.80,
        "alta": 1.20,
    },
    "XRPUSDT": {
        "muito_baixa": 0.25,
        "baixa": 0.40,
        "normal": 0.70,
        "alta": 1.10,
    },
}

# Default thresholds for unknown symbols
DEFAULT_THRESHOLDS = {
    "muito_baixa": 0.20,
    "baixa": 0.35,
    "normal": 0.60,
    "alta": 0.90,
}


class VolatilityClassifier:
    """Classifies volatility into clusters based on CVI percentiles."""

    def __init__(self, lookback_size: int = 7 * 24 * 60 * 60):
        """
        Args:
            lookback_size: Number of CVI values to keep for percentile calculation.
                           Default is 7 days of 1-second data.
        """
        self.lookback_size = lookback_size
        # History per symbol: deque of CVI values
        self._history: dict[str, deque] = {}

    def add_observation(self, symbol: str, cvi: float):
        """Add a CVI observation to the history."""
        if symbol not in self._history:
            self._history[symbol] = deque(maxlen=self.lookback_size)
        self._history[symbol].append(cvi)

    def get_percentile(self, symbol: str, cvi: float) -> float:
        """Get the percentile rank of a CVI value."""
        if symbol not in self._history or len(self._history[symbol]) < 100:
            return 50.0  # Not enough data, return median

        sorted_vals = sorted(self._history[symbol])
        pos = bisect.bisect_left(sorted_vals, cvi)
        percentile = (pos / len(sorted_vals)) * 100
        return round(percentile, 2)

    def classify(self, symbol: str, cvi: float) -> tuple[str, float]:
        """
        Classify CVI into a volatility cluster.

        Returns:
            (cluster_name, percentile)
        """
        # Add observation
        self.add_observation(symbol, cvi)

        # Check if we have enough history for percentile-based classification
        if symbol in self._history and len(self._history[symbol]) >= 1000:
            return self._classify_by_percentile(symbol, cvi)
        else:
            return self._classify_by_fixed(symbol, cvi)

    def _classify_by_percentile(self, symbol: str, cvi: float) -> tuple[str, float]:
        """Classify using dynamic percentiles."""
        percentile = self.get_percentile(symbol, cvi)

        if percentile <= 10:
            cluster = "muito_baixa"
        elif percentile <= 30:
            cluster = "baixa"
        elif percentile <= 70:
            cluster = "normal"
        elif percentile <= 90:
            cluster = "alta"
        else:
            cluster = "muito_alta"

        return cluster, percentile

    def _classify_by_fixed(self, symbol: str, cvi: float) -> tuple[str, float]:
        """Classify using fixed thresholds (for cold-start)."""
        thresholds = FIXED_THRESHOLDS.get(symbol, DEFAULT_THRESHOLDS)

        # CVI is already normalized to 0-1 scale, compare directly
        if cvi <= 0.10:
            cluster = "muito_baixa"
            percentile = cvi * 100  # Rough estimate
        elif cvi <= 0.30:
            cluster = "baixa"
            percentile = 10 + (cvi - 0.10) / 0.20 * 20
        elif cvi <= 0.70:
            cluster = "normal"
            percentile = 30 + (cvi - 0.30) / 0.40 * 40
        elif cvi <= 0.90:
            cluster = "alta"
            percentile = 70 + (cvi - 0.70) / 0.20 * 20
        else:
            cluster = "muito_alta"
            percentile = 90 + min((cvi - 0.90) / 0.10 * 10, 10)

        return cluster, round(percentile, 2)

    def get_stats(self, symbol: str) -> dict:
        """Get statistics for a symbol's CVI history."""
        if symbol not in self._history or not self._history[symbol]:
            return {}

        vals = list(self._history[symbol])
        sorted_vals = sorted(vals)
        n = len(vals)

        return {
            "count": n,
            "min": sorted_vals[0],
            "max": sorted_vals[-1],
            "mean": sum(vals) / n,
            "p10": sorted_vals[int(n * 0.10)] if n >= 10 else sorted_vals[0],
            "p50": sorted_vals[int(n * 0.50)],
            "p90": sorted_vals[int(n * 0.90)] if n >= 10 else sorted_vals[-1],
        }
