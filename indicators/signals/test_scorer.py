"""
Unit tests for scorer and decision modules.

Usage:
    python -m indicators.signals.test_scorer
"""

import sys
import os

# Fix encoding for Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scorer import (
    normalize,
    normalize_symmetric,
    compute_score,
    get_score_interpretation,
    format_score_breakdown,
    ScoreWeights,
)
from decision import (
    decide,
    format_decision,
    get_entry_price,
    get_risk_reward,
    Action,
    Side,
    Confidence,
    DecisionConfig,
)


def test_normalize():
    """Test normalization functions."""
    print("\n=== NORMALIZE TESTS ===")

    test_cases = [
        (0.5, 0, 1, 0.5, "Middle value"),
        (0, 0, 1, 0.0, "Min value"),
        (1, 0, 1, 1.0, "Max value"),
        (-0.5, 0, 1, 0.0, "Below min (clipped)"),
        (1.5, 0, 1, 1.0, "Above max (clipped)"),
        (0.25, 0, 1, 0.25, "Quarter value"),
    ]

    for value, min_val, max_val, expected, desc in test_cases:
        result = normalize(value, min_val, max_val)
        status = "Y" if abs(result - expected) < 0.001 else "N"
        print(f"  {status} {desc}: value={value}, result={result:.3f}")

    print()


def test_normalize_symmetric():
    """Test symmetric normalization."""
    print("=== NORMALIZE SYMMETRIC TESTS ===")

    test_cases = [
        (0.0, 0.5, 0.5, "Zero -> 0.5"),
        (0.5, 0.5, 1.0, "Max positive -> 1.0"),
        (-0.5, 0.5, 0.0, "Max negative -> 0.0"),
        (0.25, 0.5, 0.75, "Quarter positive -> 0.75"),
        (-0.25, 0.5, 0.25, "Quarter negative -> 0.25"),
    ]

    for value, max_abs, expected, desc in test_cases:
        result = normalize_symmetric(value, max_abs)
        status = "Y" if abs(result - expected) < 0.001 else "N"
        print(f"  {status} {desc}: value={value}, max_abs={max_abs}, result={result:.3f}")

    print()


def test_compute_score():
    """Test score computation."""
    print("=== COMPUTE SCORE TESTS ===")

    # Bullish scenario (all positive)
    result = compute_score(
        imbalance=0.3,  # Strong buy pressure
        microprice_edge=0.01,  # Microprice above mid
        imbalance_delta=0.1,  # Increasing buy pressure
        impact_buy=0.005,  # Low impact
        impact_sell=0.005,
        spread_pct=0.01,  # Tight spread
        rv_5m=0.2,  # Low volatility
        taker_ratio=0.55,  # More taker buys
        persistence_s=60,  # Good persistence
    )
    print(f"  Bullish scenario: score={result.score:.3f}")
    print(f"    Interpretation: {get_score_interpretation(result.score)}")
    print(f"    Breakdown: {format_score_breakdown(result)}")

    # Bearish scenario (all negative)
    result = compute_score(
        imbalance=-0.3,  # Strong sell pressure
        microprice_edge=-0.01,  # Microprice below mid
        imbalance_delta=-0.1,  # Increasing sell pressure
        impact_buy=0.015,  # High impact
        impact_sell=0.015,
        spread_pct=0.025,  # Wide spread
        rv_5m=0.7,  # High volatility
        taker_ratio=0.45,  # More taker sells
        persistence_s=5,  # Low persistence
    )
    print(f"  Bearish scenario: score={result.score:.3f}")
    print(f"    Interpretation: {get_score_interpretation(result.score)}")

    # Neutral scenario
    result = compute_score(
        imbalance=0.0,
        microprice_edge=0.0,
        imbalance_delta=0.0,
        impact_buy=0.01,
        impact_sell=0.01,
        spread_pct=0.015,
        rv_5m=0.4,
        taker_ratio=0.5,
        persistence_s=30,
    )
    print(f"  Neutral scenario: score={result.score:.3f}")
    print(f"    Interpretation: {get_score_interpretation(result.score)}")

    print()


def test_decision():
    """Test decision logic."""
    print("=== DECISION TESTS ===")

    config = DecisionConfig()

    # Test 1: All conditions met - should ENTER
    decision = decide(
        all_gates_passed=True,
        gate_failure_reason=None,
        prob_up=0.85,  # UP is favorite
        zone="safe",
        persistence_s=30,
        score=0.65,
        regime="normal",
        config=config,
    )
    status = "Y" if decision.action == Action.ENTER else "N"
    print(f"  {status} All conditions met: {format_decision(decision)}")

    # Test 2: Gates failed
    decision = decide(
        all_gates_passed=False,
        gate_failure_reason="time_gate_failed",
        prob_up=0.85,
        zone="safe",
        persistence_s=30,
        score=0.65,
        regime="normal",
        config=config,
    )
    status = "Y" if decision.action == Action.NO_ENTER else "N"
    print(f"  {status} Gates failed: {decision.action.value} - {decision.reason}")

    # Test 3: Danger zone
    decision = decide(
        all_gates_passed=True,
        gate_failure_reason=None,
        prob_up=0.99,  # Underdog at 1%
        zone="danger",
        persistence_s=30,
        score=0.65,
        regime="normal",
        config=config,
    )
    status = "Y" if decision.action == Action.NO_ENTER else "N"
    print(f"  {status} Danger zone: {decision.action.value} - {decision.reason}")

    # Test 4: High volatility regime
    decision = decide(
        all_gates_passed=True,
        gate_failure_reason=None,
        prob_up=0.85,
        zone="safe",
        persistence_s=30,
        score=0.65,
        regime="muito_alta",
        config=config,
    )
    status = "Y" if decision.action == Action.NO_ENTER else "N"
    print(f"  {status} High vol regime: {decision.action.value} - {decision.reason}")

    # Test 5: Low persistence
    decision = decide(
        all_gates_passed=True,
        gate_failure_reason=None,
        prob_up=0.85,
        zone="safe",
        persistence_s=10,  # Below threshold
        score=0.65,
        regime="normal",
        config=config,
    )
    status = "Y" if decision.action == Action.NO_ENTER else "N"
    print(f"  {status} Low persistence: {decision.action.value} - {decision.reason}")

    # Test 6: Low score
    decision = decide(
        all_gates_passed=True,
        gate_failure_reason=None,
        prob_up=0.85,
        zone="safe",
        persistence_s=30,
        score=0.25,  # Below threshold
        regime="normal",
        config=config,
    )
    status = "Y" if decision.action == Action.NO_ENTER else "N"
    print(f"  {status} Low score: {decision.action.value} - {decision.reason}")

    # Test 7: DOWN side entry
    decision = decide(
        all_gates_passed=True,
        gate_failure_reason=None,
        prob_up=0.15,  # DOWN is favorite
        zone="safe",
        persistence_s=30,
        score=0.65,
        regime="normal",
        config=config,
    )
    status = "Y" if decision.side == Side.DOWN else "N"
    print(f"  {status} DOWN entry: side={decision.side.value if decision.side else 'None'}")

    print()


def test_entry_calculations():
    """Test entry price and risk/reward calculations."""
    print("=== ENTRY CALCULATIONS TESTS ===")

    # UP side entry
    entry_price = get_entry_price(0.85, Side.UP)
    risk_reward = get_risk_reward(entry_price)
    print(f"  UP entry at prob=85%: entry_price={entry_price:.2f}, risk/reward={risk_reward:.2f}")

    # DOWN side entry
    entry_price = get_entry_price(0.15, Side.DOWN)
    risk_reward = get_risk_reward(entry_price)
    print(f"  DOWN entry at prob=15%: entry_price={entry_price:.2f}, risk/reward={risk_reward:.2f}")

    # Sweet spot entry
    entry_price = get_entry_price(0.90, Side.UP)
    risk_reward = get_risk_reward(entry_price)
    print(f"  UP entry at prob=90%: entry_price={entry_price:.2f}, risk/reward={risk_reward:.2f}")

    print()


if __name__ == "__main__":
    test_normalize()
    test_normalize_symmetric()
    test_compute_score()
    test_decision()
    test_entry_calculations()

    print("=== ALL PHASE 4 TESTS COMPLETE ===")
