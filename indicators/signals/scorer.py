"""
Score calculation for trading signals.

Combines all indicators into a single composite score
that determines entry strength.
"""

from dataclasses import dataclass


@dataclass
class ScoreWeights:
    """Configurable weights for score calculation."""
    # Positive factors (higher = better)
    imbalance: float = 0.25
    microprice_edge: float = 0.15
    imbalance_delta: float = 0.10
    momentum: float = 0.10  # taker_buy_sell_ratio
    persistence: float = 0.05

    # Negative factors (higher = worse)
    volatility: float = -0.20
    spread: float = -0.10
    impact: float = -0.05


@dataclass
class NormalizedIndicators:
    """Normalized indicators (0-1 range)."""
    imbalance: float
    microprice_edge: float
    imbalance_delta: float
    momentum: float
    persistence: float
    volatility: float
    spread: float
    impact: float


@dataclass
class ScoreResult:
    """Result of score calculation."""
    score: float  # Combined score
    normalized: NormalizedIndicators
    components: dict[str, float]  # Individual weighted contributions


def normalize(value: float, min_val: float, max_val: float, clip: bool = True) -> float:
    """
    Normalize a value to 0-1 range.

    Args:
        value: Raw value
        min_val: Minimum expected value
        max_val: Maximum expected value
        clip: Whether to clip to [0, 1]

    Returns:
        Normalized value
    """
    if max_val == min_val:
        return 0.5

    normalized = (value - min_val) / (max_val - min_val)

    if clip:
        return max(0.0, min(1.0, normalized))

    return normalized


def normalize_symmetric(value: float, max_abs: float) -> float:
    """
    Normalize a symmetric value (e.g., -0.5 to 0.5) to 0-1.

    Maps: -max_abs -> 0, 0 -> 0.5, +max_abs -> 1

    Args:
        value: Raw value (can be negative or positive)
        max_abs: Maximum absolute value expected

    Returns:
        Normalized value (0-1)
    """
    if max_abs == 0:
        return 0.5

    normalized = (value + max_abs) / (2 * max_abs)
    return max(0.0, min(1.0, normalized))


def compute_score(
    # Microstructure indicators
    imbalance: float,
    microprice_edge: float,
    imbalance_delta: float | None,
    impact_buy: float,
    impact_sell: float,
    spread_pct: float,

    # Binance indicators
    rv_5m: float | None,
    taker_ratio: float | None,

    # State indicators
    persistence_s: float,

    # Weights (optional override)
    weights: ScoreWeights | None = None,
) -> ScoreResult:
    """
    Compute composite score from all indicators.

    Args:
        imbalance: Order book imbalance (-1 to 1)
        microprice_edge: Microprice - mid
        imbalance_delta: Change in imbalance from previous tick
        impact_buy: Price impact for buying
        impact_sell: Price impact for selling
        spread_pct: Spread as percentage of mid
        rv_5m: 5-minute realized volatility
        taker_ratio: Binance taker buy/sell ratio
        persistence_s: Seconds gates have been satisfied
        weights: Optional custom weights

    Returns:
        ScoreResult with score and breakdown
    """
    if weights is None:
        weights = ScoreWeights()

    # Normalize all indicators to 0-1 range

    # Imbalance: -0.5 to 0.5 -> 0 to 1
    # Positive imbalance (more bids) = higher score
    imbalance_norm = normalize_symmetric(imbalance, max_abs=0.5)

    # Microprice edge: -0.02 to 0.02 -> 0 to 1
    # Positive edge (microprice > mid) = buy pressure = higher score
    microprice_edge_norm = normalize_symmetric(microprice_edge, max_abs=0.02)

    # Imbalance delta: -0.2 to 0.2 -> 0 to 1
    # Positive delta = increasing buy pressure = higher score
    if imbalance_delta is not None:
        imbalance_delta_norm = normalize_symmetric(imbalance_delta, max_abs=0.2)
    else:
        imbalance_delta_norm = 0.5  # Neutral if no data

    # Momentum (taker ratio): 0.4 to 0.6 -> 0 to 1
    # > 0.5 means more taker buys = bullish
    if taker_ratio is not None:
        momentum_norm = normalize(taker_ratio, min_val=0.4, max_val=0.6)
    else:
        momentum_norm = 0.5  # Neutral if no data

    # Persistence: 0 to 120s -> 0 to 1
    # More persistence = more confidence
    persistence_norm = normalize(persistence_s, min_val=0, max_val=120)

    # Volatility: 0 to 1.0 (100%) -> 0 to 1
    # Lower volatility = better (so we invert for score)
    if rv_5m is not None:
        vol_norm = normalize(rv_5m, min_val=0, max_val=1.0)
    else:
        vol_norm = 0.3  # Assume moderate if no data

    # Spread: 0 to 3% -> 0 to 1
    # Lower spread = better (so we invert for score)
    spread_norm = normalize(spread_pct, min_val=0, max_val=0.03)

    # Impact: average of buy/sell impact, 0 to 0.02 -> 0 to 1
    # Lower impact = better (so we invert for score)
    avg_impact = (abs(impact_buy) + abs(impact_sell)) / 2
    impact_norm = normalize(avg_impact, min_val=0, max_val=0.02)

    # Build normalized indicators
    normalized = NormalizedIndicators(
        imbalance=imbalance_norm,
        microprice_edge=microprice_edge_norm,
        imbalance_delta=imbalance_delta_norm,
        momentum=momentum_norm,
        persistence=persistence_norm,
        volatility=vol_norm,
        spread=spread_norm,
        impact=impact_norm,
    )

    # Compute weighted components
    components = {
        "imbalance": weights.imbalance * imbalance_norm,
        "microprice_edge": weights.microprice_edge * microprice_edge_norm,
        "imbalance_delta": weights.imbalance_delta * imbalance_delta_norm,
        "momentum": weights.momentum * momentum_norm,
        "persistence": weights.persistence * persistence_norm,
        # Negative weights: higher normalized value = worse score
        "volatility": weights.volatility * vol_norm,
        "spread": weights.spread * spread_norm,
        "impact": weights.impact * impact_norm,
    }

    # Sum all components
    raw_score = sum(components.values())

    # Normalize final score to 0-1 range
    # With current weights: min possible = -0.35, max possible = 0.65
    # So we normalize from -0.35 to 0.65 -> 0 to 1
    final_score = normalize(raw_score, min_val=-0.35, max_val=0.65)

    return ScoreResult(
        score=final_score,
        normalized=normalized,
        components=components,
    )


def get_score_interpretation(score: float) -> str:
    """
    Get human-readable interpretation of score.

    Args:
        score: Normalized score (0-1)

    Returns:
        Interpretation string
    """
    if score >= 0.8:
        return "very_strong"
    elif score >= 0.65:
        return "strong"
    elif score >= 0.5:
        return "moderate"
    elif score >= 0.35:
        return "weak"
    else:
        return "very_weak"


def format_score_breakdown(result: ScoreResult) -> str:
    """Format score breakdown for logging."""
    parts = [f"score={result.score:.2f}"]

    # Show top contributors
    sorted_components = sorted(
        result.components.items(),
        key=lambda x: abs(x[1]),
        reverse=True
    )

    contrib_strs = []
    for name, value in sorted_components[:3]:
        sign = "+" if value >= 0 else ""
        contrib_strs.append(f"{name}={sign}{value:.2f}")

    parts.append(f"[{', '.join(contrib_strs)}]")

    return " ".join(parts)
