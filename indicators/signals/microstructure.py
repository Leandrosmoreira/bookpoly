"""
Microstructure indicators for trading signals.

These indicators analyze the order book structure to identify
trading opportunities and market conditions.
"""

from dataclasses import dataclass


@dataclass
class MicrostructureMetrics:
    """Computed microstructure metrics from order book."""
    # Microprice (VWAP-weighted price)
    microprice: float
    microprice_vs_mid: float  # microprice - mid (positive = buy pressure)

    # Imbalance metrics
    imbalance: float  # (bid - ask) / (bid + ask)
    imbalance_delta: float | None  # Change from previous tick

    # Price impact
    impact_buy_100: float  # Slippage to buy 100 shares
    impact_sell_100: float  # Slippage to sell 100 shares
    impact_buy_500: float  # Slippage to buy 500 shares
    impact_sell_500: float  # Slippage to sell 500 shares

    # Book shape
    bid_concentration: float  # % of depth in top 5 levels
    ask_concentration: float  # % of depth in top 5 levels
    depth_ratio: float  # bid_depth / ask_depth

    # Spread metrics
    spread: float
    spread_pct: float
    mid: float


def compute_microprice(bids: list[dict], asks: list[dict], levels: int = 3) -> float:
    """
    Compute microprice as VWAP of top N levels.

    Microprice gives more weight to the side with more liquidity,
    providing a better estimate of "fair value" than simple mid.

    Args:
        bids: List of {"p": price, "s": size} sorted by price descending
        asks: List of {"p": price, "s": size} sorted by price ascending
        levels: Number of levels to include (default 3)

    Returns:
        Microprice (VWAP of top levels)
    """
    if not bids or not asks:
        return 0.0

    # Get top N levels
    top_bids = bids[:levels]
    top_asks = asks[:levels]

    # Compute weighted prices
    bid_value = sum(b["p"] * b["s"] for b in top_bids)
    bid_size = sum(b["s"] for b in top_bids)

    ask_value = sum(a["p"] * a["s"] for a in top_asks)
    ask_size = sum(a["s"] for a in top_asks)

    total_value = bid_value + ask_value
    total_size = bid_size + ask_size

    if total_size == 0:
        return 0.0

    return total_value / total_size


def compute_simple_microprice(best_bid: float | None, best_ask: float | None,
                               bid_size: float, ask_size: float) -> float:
    """
    Compute simple microprice using only best bid/ask.

    Formula: (best_bid * ask_size + best_ask * bid_size) / (bid_size + ask_size)

    This weights the price toward the side with MORE liquidity,
    since that side is "stronger" and less likely to move.
    """
    # Handle None values
    if best_bid is None and best_ask is None:
        return 0.0
    if best_bid is None:
        return best_ask if best_ask is not None else 0.0
    if best_ask is None:
        return best_bid
    
    total_size = bid_size + ask_size
    if total_size == 0:
        return (best_bid + best_ask) / 2

    return (best_bid * ask_size + best_ask * bid_size) / total_size


def compute_imbalance(bid_depth: float, ask_depth: float) -> float:
    """
    Compute order book imbalance.

    Formula: (bid_depth - ask_depth) / (bid_depth + ask_depth)

    Returns:
        Value between -1 and 1
        Positive = more bids (buy pressure)
        Negative = more asks (sell pressure)
    """
    total = bid_depth + ask_depth
    if total == 0:
        return 0.0
    return (bid_depth - ask_depth) / total


def compute_price_impact(orders: list[dict], size: float, is_buy: bool) -> float:
    """
    Compute price impact (slippage) for a given order size.

    Args:
        orders: List of {"p": price, "s": size} (bids for sell, asks for buy)
        size: Number of shares to trade
        is_buy: True if buying (walk up asks), False if selling (walk down bids)

    Returns:
        Average execution price (or 0 if insufficient liquidity)
    """
    if not orders or size <= 0:
        return 0.0

    remaining = size
    total_cost = 0.0

    for order in orders:
        fill_size = min(remaining, order["s"])
        total_cost += fill_size * order["p"]
        remaining -= fill_size

        if remaining <= 0:
            break

    if remaining > 0:
        # Not enough liquidity
        return 0.0

    return total_cost / size


def compute_book_concentration(orders: list[dict], levels: int = 5) -> float:
    """
    Compute concentration of liquidity in top N levels.

    Higher concentration = liquidity is clustered near best price
    Lower concentration = liquidity is spread across the book

    Args:
        orders: Full order book side
        levels: Number of top levels to consider

    Returns:
        Percentage of total depth in top N levels (0.0 to 1.0)
    """
    if not orders:
        return 0.0

    total_depth = sum(o["s"] for o in orders)
    if total_depth == 0:
        return 0.0

    top_depth = sum(o["s"] for o in orders[:levels])
    return top_depth / total_depth


def compute_microstructure(
    polymarket_data: dict,
    prev_imbalance: float | None = None,
) -> MicrostructureMetrics:
    """
    Compute all microstructure metrics from Polymarket data.

    Args:
        polymarket_data: Row from Polymarket book recorder
        prev_imbalance: Previous tick's imbalance (for delta calculation)

    Returns:
        MicrostructureMetrics with all computed values
    """
    yes_data = polymarket_data.get("yes", {}) or {}

    # Extract basic data
    mid = yes_data.get("mid", 0) or 0
    spread = yes_data.get("spread", 0) or 0
    bid_depth = yes_data.get("bid_depth", 0) or 0
    ask_depth = yes_data.get("ask_depth", 0) or 0
    best_bid = yes_data.get("best_bid") or 0
    best_ask = yes_data.get("best_ask") or 0

    # Get order book levels
    bids = yes_data.get("bids", []) or []
    asks = yes_data.get("asks", []) or []

    # Compute microprice
    if bids and asks:
        microprice = compute_microprice(bids, asks, levels=3)
    else:
        # Fall back to simple microprice using best bid/ask
        bid_top_size = bids[0]["s"] if bids else 0
        ask_top_size = asks[0]["s"] if asks else 0
        microprice = compute_simple_microprice(best_bid, best_ask, bid_top_size, ask_top_size)

    microprice_vs_mid = microprice - mid if mid > 0 else 0

    # Compute imbalance
    imbalance = compute_imbalance(bid_depth, ask_depth)
    imbalance_delta = imbalance - prev_imbalance if prev_imbalance is not None else None

    # Compute price impact
    impact_buy_100 = compute_price_impact(asks, 100, is_buy=True)
    impact_sell_100 = compute_price_impact(bids, 100, is_buy=False)
    impact_buy_500 = compute_price_impact(asks, 500, is_buy=True)
    impact_sell_500 = compute_price_impact(bids, 500, is_buy=False)

    # Convert to slippage (difference from mid)
    if impact_buy_100 > 0:
        impact_buy_100 = impact_buy_100 - mid
    if impact_sell_100 > 0:
        impact_sell_100 = mid - impact_sell_100
    if impact_buy_500 > 0:
        impact_buy_500 = impact_buy_500 - mid
    if impact_sell_500 > 0:
        impact_sell_500 = mid - impact_sell_500

    # Compute book concentration
    bid_concentration = compute_book_concentration(bids, levels=5)
    ask_concentration = compute_book_concentration(asks, levels=5)

    # Depth ratio
    depth_ratio = bid_depth / ask_depth if ask_depth > 0 else 0

    # Spread percentage
    spread_pct = spread / mid if mid > 0 else 0

    return MicrostructureMetrics(
        microprice=microprice,
        microprice_vs_mid=microprice_vs_mid,
        imbalance=imbalance,
        imbalance_delta=imbalance_delta,
        impact_buy_100=impact_buy_100,
        impact_sell_100=impact_sell_100,
        impact_buy_500=impact_buy_500,
        impact_sell_500=impact_sell_500,
        bid_concentration=bid_concentration,
        ask_concentration=ask_concentration,
        depth_ratio=depth_ratio,
        spread=spread,
        spread_pct=spread_pct,
        mid=mid,
    )


def normalize_metric(value: float, min_val: float, max_val: float,
                     clip: bool = True) -> float:
    """
    Normalize a metric to 0-1 range.

    Args:
        value: Raw metric value
        min_val: Minimum expected value
        max_val: Maximum expected value
        clip: If True, clip to [0, 1] range

    Returns:
        Normalized value
    """
    if max_val == min_val:
        return 0.5

    normalized = (value - min_val) / (max_val - min_val)

    if clip:
        return max(0.0, min(1.0, normalized))

    return normalized
