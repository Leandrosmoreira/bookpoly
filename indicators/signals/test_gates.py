"""
Unit tests for gate functions.

Usage:
    python -m indicators.signals.test_gates
"""

import sys
import os

# Fix encoding for Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import SignalConfig
from gates import (
    time_gate,
    liquidity_gate,
    spread_gate,
    stability_gate,
    latency_gate,
    get_probability_zone,
    evaluate_gates,
)


def test_time_gate():
    """Test time gate with different elapsed times."""
    config = SignalConfig()
    window_start = 1000000  # Arbitrary start time

    test_cases = [
        # (elapsed_s, expected_pass, description)
        (0, False, "Window just started"),
        (300, False, "5 min elapsed - too early"),
        (600, False, "10 min elapsed - still too early"),
        (660, True, "11 min elapsed - entry window starts"),
        (750, True, "12.5 min elapsed - middle of entry window"),
        (870, True, "14.5 min elapsed - end of entry window"),
        (871, False, "14.5+ min - too late (last 30s)"),
        (900, False, "Window ended"),
    ]

    print("\n=== TIME GATE TESTS ===")
    for elapsed, expected, desc in test_cases:
        now_ts = window_start + elapsed
        passed, remaining = time_gate(window_start, now_ts, config)
        status = "✓" if passed == expected else "✗"
        print(f"  {status} {desc}: elapsed={elapsed}s, passed={passed}, remaining={remaining:.0f}s")

    print()


def test_liquidity_gate():
    """Test liquidity gate with different depths."""
    config = SignalConfig()

    test_cases = [
        # (bid_depth, ask_depth, expected_pass, description)
        (0, 0, False, "Zero liquidity"),
        (100, 100, False, "Low liquidity (200 total)"),
        (150, 150, True, "Exactly at threshold (300)"),
        (500, 500, True, "High liquidity (1000 total)"),
        (35000, 40000, True, "Very high liquidity"),
    ]

    print("=== LIQUIDITY GATE TESTS ===")
    for bid, ask, expected, desc in test_cases:
        passed = liquidity_gate(bid, ask, config)
        status = "✓" if passed == expected else "✗"
        print(f"  {status} {desc}: bid={bid}, ask={ask}, total={bid+ask}, passed={passed}")

    print()


def test_spread_gate():
    """Test spread gate with different spreads."""
    config = SignalConfig()

    test_cases = [
        # (spread, mid, expected_pass, description)
        (0.01, 0.50, True, "Normal spread (2%)"),
        (0.01, 0.75, True, "Tight spread (1.3%)"),
        (0.02, 0.50, False, "Wide spread (4%)"),
        (0.05, 0.50, False, "Very wide spread (10%)"),
        (0.01, 0.01, False, "Edge case: low mid price"),
        (0.00, 0.50, True, "Zero spread"),
    ]

    print("=== SPREAD GATE TESTS ===")
    for spread, mid, expected, desc in test_cases:
        passed = spread_gate(spread, mid, config)
        spread_pct = (spread / mid * 100) if mid > 0 else 0
        status = "✓" if passed == expected else "✗"
        print(f"  {status} {desc}: spread={spread}, mid={mid}, pct={spread_pct:.1f}%, passed={passed}")

    print()


def test_stability_gate():
    """Test stability gate with different volatility values."""
    config = SignalConfig()

    test_cases = [
        # (rv_5m, regime, expected_pass, description)
        (None, None, True, "No volatility data"),
        (0.20, None, True, "Low volatility (20%)"),
        (0.50, None, True, "At threshold (50%)"),
        (0.51, None, False, "Above threshold (51%)"),
        (0.30, "normal", True, "Normal regime"),
        (0.30, "alta", True, "High regime (but vol OK)"),
        (0.30, "muito_alta", False, "Very high regime"),
        (0.60, "baixa", False, "Low regime but high vol"),
    ]

    print("=== STABILITY GATE TESTS ===")
    for rv, regime, expected, desc in test_cases:
        passed = stability_gate(rv, regime, config)
        status = "✓" if passed == expected else "✗"
        print(f"  {status} {desc}: rv_5m={rv}, regime={regime}, passed={passed}")

    print()


def test_latency_gate():
    """Test latency gate with different latencies."""
    config = SignalConfig()

    test_cases = [
        # (latency_ms, expected_pass, description)
        (100, True, "Low latency"),
        (300, True, "Normal latency"),
        (500, True, "At threshold"),
        (501, False, "Just above threshold"),
        (1000, False, "High latency"),
    ]

    print("=== LATENCY GATE TESTS ===")
    for latency, expected, desc in test_cases:
        passed = latency_gate(latency, config)
        status = "✓" if passed == expected else "✗"
        print(f"  {status} {desc}: latency={latency}ms, passed={passed}")

    print()


def test_probability_zones():
    """Test probability zone classification."""
    test_cases = [
        # (prob_up, expected_zone, description)
        (0.99, "danger", "Strong favorite (99% up)"),
        (0.01, "danger", "Strong underdog (1% up)"),
        (0.97, "caution", "97% favorite"),
        (0.03, "caution", "3% underdog"),
        (0.90, "safe", "90% favorite"),
        (0.10, "safe", "10% underdog"),
        (0.75, "neutral", "75% favorite"),
        (0.50, "neutral", "50/50"),
        (0.25, "neutral", "25% underdog"),
    ]

    print("=== PROBABILITY ZONE TESTS ===")
    for prob, expected, desc in test_cases:
        zone = get_probability_zone(prob)
        underdog = min(prob, 1 - prob)
        status = "✓" if zone == expected else "✗"
        print(f"  {status} {desc}: prob={prob:.0%}, underdog={underdog:.0%}, zone={zone}")

    print()


def test_evaluate_gates():
    """Test full gate evaluation with synthetic data."""
    config = SignalConfig()

    # Synthetic Polymarket data - should pass all gates
    poly_data_good = {
        "ts_ms": 1000000 * 1000 + 750000,  # 750s elapsed (in entry window)
        "window_start": 1000000,
        "yes": {
            "mid": 0.75,
            "spread": 0.01,
            "bid_depth": 500,
            "ask_depth": 500,
        },
        "fetch": {
            "latency_ms": 200,
        },
    }

    # Synthetic Binance data
    binance_data_good = {
        "volatility": {
            "rv_5m": 0.25,
        },
        "classification": {
            "cluster": "normal",
        },
    }

    print("=== EVALUATE_GATES TEST (ALL SHOULD PASS) ===")
    result = evaluate_gates(poly_data_good, binance_data_good, config)
    print(f"  Time Gate: {result.time_gate}")
    print(f"  Liquidity Gate: {result.liquidity_gate}")
    print(f"  Spread Gate: {result.spread_gate}")
    print(f"  Stability Gate: {result.stability_gate}")
    print(f"  Latency Gate: {result.latency_gate}")
    print(f"  ALL PASSED: {result.all_passed}")
    print(f"  Time remaining: {result.time_remaining_s:.0f}s")
    print(f"  Reason: {result.reason}")

    # Test with bad data
    poly_data_bad = {
        "ts_ms": 1000000 * 1000 + 300000,  # 300s elapsed (too early)
        "window_start": 1000000,
        "yes": {
            "mid": 0.75,
            "spread": 0.05,  # Wide spread
            "bid_depth": 50,  # Low liquidity
            "ask_depth": 50,
        },
        "fetch": {
            "latency_ms": 800,  # High latency
        },
    }

    binance_data_bad = {
        "volatility": {
            "rv_5m": 0.80,  # High volatility
        },
        "classification": {
            "cluster": "muito_alta",
        },
    }

    print("\n=== EVALUATE_GATES TEST (ALL SHOULD FAIL) ===")
    result = evaluate_gates(poly_data_bad, binance_data_bad, config)
    print(f"  Time Gate: {result.time_gate}")
    print(f"  Liquidity Gate: {result.liquidity_gate}")
    print(f"  Spread Gate: {result.spread_gate}")
    print(f"  Stability Gate: {result.stability_gate}")
    print(f"  Latency Gate: {result.latency_gate}")
    print(f"  ALL PASSED: {result.all_passed}")
    print(f"  Reason: {result.reason}")

    print()


if __name__ == "__main__":
    test_time_gate()
    test_liquidity_gate()
    test_spread_gate()
    test_stability_gate()
    test_latency_gate()
    test_probability_zones()
    test_evaluate_gates()

    print("=== ALL TESTS COMPLETE ===")
