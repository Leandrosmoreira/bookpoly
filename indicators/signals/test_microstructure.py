"""
Unit tests for microstructure indicators.

Usage:
    python -m indicators.signals.test_microstructure
"""

import sys
import os

# Fix encoding for Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from microstructure import (
    compute_microprice,
    compute_simple_microprice,
    compute_imbalance,
    compute_price_impact,
    compute_book_concentration,
    compute_microstructure,
    normalize_metric,
)


def test_microprice():
    """Test microprice calculation."""
    print("\n=== MICROPRICE TESTS ===")

    # Balanced book
    bids = [{"p": 0.74, "s": 100}, {"p": 0.73, "s": 100}, {"p": 0.72, "s": 100}]
    asks = [{"p": 0.76, "s": 100}, {"p": 0.77, "s": 100}, {"p": 0.78, "s": 100}]

    microprice = compute_microprice(bids, asks, levels=3)
    print(f"  Balanced book: microprice={microprice:.4f} (expect ~0.75)")

    # Heavy bid side (buy pressure)
    bids_heavy = [{"p": 0.74, "s": 500}, {"p": 0.73, "s": 500}, {"p": 0.72, "s": 500}]
    microprice = compute_microprice(bids_heavy, asks, levels=3)
    print(f"  Heavy bids: microprice={microprice:.4f} (expect > 0.75)")

    # Heavy ask side (sell pressure)
    asks_heavy = [{"p": 0.76, "s": 500}, {"p": 0.77, "s": 500}, {"p": 0.78, "s": 500}]
    microprice = compute_microprice(bids, asks_heavy, levels=3)
    print(f"  Heavy asks: microprice={microprice:.4f} (expect < 0.75)")

    # Empty book
    microprice = compute_microprice([], [], levels=3)
    print(f"  Empty book: microprice={microprice} (expect 0)")

    print()


def test_simple_microprice():
    """Test simple microprice calculation."""
    print("=== SIMPLE MICROPRICE TESTS ===")

    # Equal sizes - should be mid
    mp = compute_simple_microprice(0.74, 0.76, 100, 100)
    mid = (0.74 + 0.76) / 2
    print(f"  Equal sizes: microprice={mp:.4f}, mid={mid:.4f}")

    # More bid size - microprice closer to ask
    mp = compute_simple_microprice(0.74, 0.76, 300, 100)
    print(f"  More bids (300 vs 100): microprice={mp:.4f} (expect closer to 0.76)")

    # More ask size - microprice closer to bid
    mp = compute_simple_microprice(0.74, 0.76, 100, 300)
    print(f"  More asks (100 vs 300): microprice={mp:.4f} (expect closer to 0.74)")

    print()


def test_imbalance():
    """Test imbalance calculation."""
    print("=== IMBALANCE TESTS ===")

    test_cases = [
        (100, 100, 0.0, "Equal depth"),
        (150, 50, 0.5, "More bids"),
        (50, 150, -0.5, "More asks"),
        (200, 0, 1.0, "Only bids"),
        (0, 200, -1.0, "Only asks"),
        (0, 0, 0.0, "Empty book"),
    ]

    for bid_depth, ask_depth, expected, desc in test_cases:
        imbalance = compute_imbalance(bid_depth, ask_depth)
        status = "✓" if abs(imbalance - expected) < 0.001 else "✗"
        print(f"  {status} {desc}: bid={bid_depth}, ask={ask_depth}, imbalance={imbalance:.3f}")

    print()


def test_price_impact():
    """Test price impact calculation."""
    print("=== PRICE IMPACT TESTS ===")

    # Simple asks for buy orders
    asks = [
        {"p": 0.76, "s": 100},
        {"p": 0.77, "s": 100},
        {"p": 0.78, "s": 100},
    ]

    # Buy 50 shares - fills at 0.76
    impact = compute_price_impact(asks, 50, is_buy=True)
    print(f"  Buy 50 shares: avg_price={impact:.4f} (expect 0.76)")

    # Buy 100 shares - fills at 0.76
    impact = compute_price_impact(asks, 100, is_buy=True)
    print(f"  Buy 100 shares: avg_price={impact:.4f} (expect 0.76)")

    # Buy 150 shares - fills 100 @ 0.76, 50 @ 0.77
    impact = compute_price_impact(asks, 150, is_buy=True)
    expected = (100 * 0.76 + 50 * 0.77) / 150
    print(f"  Buy 150 shares: avg_price={impact:.4f} (expect {expected:.4f})")

    # Buy 300 shares - fills all levels
    impact = compute_price_impact(asks, 300, is_buy=True)
    expected = (100 * 0.76 + 100 * 0.77 + 100 * 0.78) / 300
    print(f"  Buy 300 shares: avg_price={impact:.4f} (expect {expected:.4f})")

    # Buy 400 shares - not enough liquidity
    impact = compute_price_impact(asks, 400, is_buy=True)
    print(f"  Buy 400 shares: avg_price={impact} (expect 0, insufficient liquidity)")

    print()


def test_book_concentration():
    """Test book concentration calculation."""
    print("=== BOOK CONCENTRATION TESTS ===")

    # All liquidity at top
    orders_top_heavy = [
        {"p": 0.74, "s": 1000},
        {"p": 0.73, "s": 10},
        {"p": 0.72, "s": 10},
    ]
    conc = compute_book_concentration(orders_top_heavy, levels=1)
    print(f"  Top-heavy book (1 level): {conc:.2%}")

    conc = compute_book_concentration(orders_top_heavy, levels=3)
    print(f"  Top-heavy book (3 levels): {conc:.2%}")

    # Even distribution
    orders_even = [
        {"p": 0.74, "s": 100},
        {"p": 0.73, "s": 100},
        {"p": 0.72, "s": 100},
        {"p": 0.71, "s": 100},
        {"p": 0.70, "s": 100},
        {"p": 0.69, "s": 100},
        {"p": 0.68, "s": 100},
        {"p": 0.67, "s": 100},
        {"p": 0.66, "s": 100},
        {"p": 0.65, "s": 100},
    ]
    conc = compute_book_concentration(orders_even, levels=5)
    print(f"  Even distribution (5 of 10 levels): {conc:.2%} (expect 50%)")

    # Empty book
    conc = compute_book_concentration([], levels=5)
    print(f"  Empty book: {conc:.2%}")

    print()


def test_compute_microstructure():
    """Test full microstructure computation."""
    print("=== FULL MICROSTRUCTURE TEST ===")

    # Synthetic Polymarket data
    poly_data = {
        "yes": {
            "mid": 0.75,
            "spread": 0.02,
            "bid_depth": 500,
            "ask_depth": 400,
            "best_bid": 0.74,
            "best_ask": 0.76,
            "bids": [
                {"p": 0.74, "s": 200},
                {"p": 0.73, "s": 150},
                {"p": 0.72, "s": 100},
                {"p": 0.71, "s": 50},
            ],
            "asks": [
                {"p": 0.76, "s": 150},
                {"p": 0.77, "s": 100},
                {"p": 0.78, "s": 100},
                {"p": 0.79, "s": 50},
            ],
        }
    }

    metrics = compute_microstructure(poly_data, prev_imbalance=0.1)

    print(f"  Mid: {metrics.mid:.4f}")
    print(f"  Microprice: {metrics.microprice:.4f}")
    print(f"  Microprice vs Mid: {metrics.microprice_vs_mid:+.4f}")
    print(f"  Imbalance: {metrics.imbalance:+.3f}")
    print(f"  Imbalance Delta: {metrics.imbalance_delta:+.3f}")
    print(f"  Impact Buy 100: {metrics.impact_buy_100:.4f}")
    print(f"  Impact Sell 100: {metrics.impact_sell_100:.4f}")
    print(f"  Impact Buy 500: {metrics.impact_buy_500:.4f}")
    print(f"  Impact Sell 500: {metrics.impact_sell_500:.4f}")
    print(f"  Bid Concentration: {metrics.bid_concentration:.1%}")
    print(f"  Ask Concentration: {metrics.ask_concentration:.1%}")
    print(f"  Depth Ratio: {metrics.depth_ratio:.2f}")
    print(f"  Spread: {metrics.spread:.4f} ({metrics.spread_pct:.2%})")

    print()


def test_normalize():
    """Test metric normalization."""
    print("=== NORMALIZATION TESTS ===")

    test_cases = [
        (0.5, 0, 1, 0.5, "Middle value"),
        (0, 0, 1, 0.0, "Min value"),
        (1, 0, 1, 1.0, "Max value"),
        (-0.5, 0, 1, 0.0, "Below min (clipped)"),
        (1.5, 0, 1, 1.0, "Above max (clipped)"),
        (50, 0, 100, 0.5, "Scale 0-100"),
    ]

    for value, min_val, max_val, expected, desc in test_cases:
        normalized = normalize_metric(value, min_val, max_val)
        status = "✓" if abs(normalized - expected) < 0.001 else "✗"
        print(f"  {status} {desc}: value={value}, norm={normalized:.3f}")

    print()


if __name__ == "__main__":
    test_microprice()
    test_simple_microprice()
    test_imbalance()
    test_price_impact()
    test_book_concentration()
    test_compute_microstructure()
    test_normalize()

    print("=== ALL MICROSTRUCTURE TESTS COMPLETE ===")
