"""
Tests for the defense module.

Tests the reversal detection and exit/flip logic.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from indicators.signals.defense import (
    DefenseConfig,
    DefenseState,
    DefenseAction,
    DefenseResult,
    evaluate_defense,
)


def test_hold_when_all_good():
    """Test that we HOLD when conditions are favorable."""
    config = DefenseConfig()
    state = DefenseState()
    state.start_position("UP", 0.95)

    result = evaluate_defense(
        side="UP",
        entry_price=0.95,
        remaining_s=120,
        prob_up=0.96,  # Still winning
        imbalance=0.15,  # Positive = favors UP
        imbalance_delta=0.01,  # Small change
        microprice_vs_mid=0.001,  # Slightly positive
        taker_ratio=1.05,  # More buyers
        rv_5m=0.30,  # Normal volatility
        regime="normal",
        z_score=0.5,  # Normal
        state=state,
        config=config,
    )

    assert result.action == DefenseAction.HOLD
    assert result.score < 0.5  # Low danger
    print(f"[OK] HOLD test: {result.reason}")


def test_emergency_exit_regime():
    """Test emergency exit when regime is muito_alta."""
    config = DefenseConfig()
    state = DefenseState()
    state.start_position("UP", 0.95)

    result = evaluate_defense(
        side="UP",
        entry_price=0.95,
        remaining_s=120,
        prob_up=0.96,
        imbalance=0.15,
        imbalance_delta=0.01,
        microprice_vs_mid=0.001,
        taker_ratio=1.05,
        rv_5m=0.80,
        regime="muito_alta",  # BLOCKED REGIME
        z_score=0.5,
        state=state,
        config=config,
    )

    assert result.action == DefenseAction.EXIT_EMERGENCY
    assert "regime" in result.reason.lower()
    print(f"[OK] Emergency exit (regime): {result.reason}")


def test_emergency_exit_imbalance_flip():
    """Test emergency exit when imbalance flips violently."""
    config = DefenseConfig()
    state = DefenseState()
    state.start_position("UP", 0.95)

    result = evaluate_defense(
        side="UP",
        entry_price=0.95,
        remaining_s=120,
        prob_up=0.90,  # Starting to drop
        imbalance=-0.10,  # Now negative
        imbalance_delta=-0.25,  # Violent flip DOWN (> 0.20 threshold)
        microprice_vs_mid=-0.01,
        taker_ratio=0.85,
        rv_5m=0.40,
        regime="alta",
        z_score=1.0,
        state=state,
        config=config,
    )

    assert result.action == DefenseAction.EXIT_EMERGENCY
    assert "imbalance" in result.reason.lower()
    print(f"[OK] Emergency exit (imbalance flip): {result.reason}")


def test_emergency_exit_zscore():
    """Test emergency exit when z-score is extreme."""
    config = DefenseConfig()
    state = DefenseState()
    state.start_position("UP", 0.95)

    result = evaluate_defense(
        side="UP",
        entry_price=0.95,
        remaining_s=120,
        prob_up=0.92,
        imbalance=0.05,
        imbalance_delta=-0.05,
        microprice_vs_mid=-0.005,
        taker_ratio=0.95,
        rv_5m=0.40,
        regime="normal",
        z_score=-2.5,  # Extreme negative z-score
        state=state,
        config=config,
    )

    assert result.action == DefenseAction.EXIT_EMERGENCY
    assert "zscore" in result.reason.lower()
    print(f"[OK] Emergency exit (z-score): {result.reason}")


def test_time_exit():
    """Test time exit in the last minute with mixed signals."""
    config = DefenseConfig()
    state = DefenseState()
    state.start_position("UP", 0.95)

    result = evaluate_defense(
        side="UP",
        entry_price=0.95,
        remaining_s=45,  # Less than 60s
        prob_up=0.85,  # Prob dropped, not clearly winning
        imbalance=0.05,
        imbalance_delta=-0.02,
        microprice_vs_mid=-0.002,
        taker_ratio=0.95,
        rv_5m=0.35,
        regime="normal",
        z_score=-0.5,
        state=state,
        config=config,
    )

    assert result.action == DefenseAction.EXIT_TIME
    assert "time" in result.reason.lower() or "remaining" in result.reason.lower()
    print(f"[OK] Time exit: {result.reason}")


def test_defense_disabled():
    """Test that defense is disabled when config says so."""
    config = DefenseConfig()
    config.enabled = False
    state = DefenseState()
    state.start_position("UP", 0.95)

    result = evaluate_defense(
        side="UP",
        entry_price=0.95,
        remaining_s=120,
        prob_up=0.60,  # Bad
        imbalance=-0.30,  # Bad
        imbalance_delta=-0.25,  # Very bad
        microprice_vs_mid=-0.05,  # Bad
        taker_ratio=0.70,  # Bad
        rv_5m=0.80,  # Bad
        regime="muito_alta",  # Bad
        z_score=-2.5,  # Bad
        state=state,
        config=config,
    )

    assert result.action == DefenseAction.HOLD
    assert "disabled" in result.reason.lower()
    print(f"[OK] Defense disabled: {result.reason}")


def test_down_position_imbalance():
    """Test DOWN position with positive imbalance flip (against us)."""
    config = DefenseConfig()
    state = DefenseState()
    state.start_position("DOWN", 0.95)

    result = evaluate_defense(
        side="DOWN",
        entry_price=0.95,
        remaining_s=120,
        prob_up=0.10,  # We bet DOWN, prob_up is low (good)
        imbalance=0.20,  # Positive = against DOWN
        imbalance_delta=0.25,  # Flip to positive (against us)
        microprice_vs_mid=0.01,
        taker_ratio=1.10,
        rv_5m=0.40,
        regime="normal",
        z_score=1.0,
        state=state,
        config=config,
    )

    assert result.action == DefenseAction.EXIT_EMERGENCY
    assert "imbalance" in result.reason.lower()
    print(f"[OK] DOWN position emergency exit: {result.reason}")


def test_state_tracking():
    """Test that DefenseState tracks history correctly."""
    import time

    state = DefenseState()
    state.start_position("UP", 0.95)

    # Simulate 10 ticks
    for i in range(10):
        state.update(
            imbalance=0.10 - i * 0.02,  # Decreasing
            microprice_vs_mid=-0.001 * i,  # Going negative
            rv_5m=0.30 + i * 0.01,  # Increasing
            taker_ratio=1.0 - i * 0.02,  # Decreasing
            now_ts=time.time() + i,
        )

    # Check histories are populated
    assert len(state.imbalance_history) == 10
    assert len(state.microprice_edge_history) == 10
    assert len(state.rv_5m_history) == 10

    # Check imbalance MA
    imb_ma = state.get_imbalance_ma_30s()
    assert imb_ma is not None
    print(f"[OK] State tracking: imbalance_ma={imb_ma:.4f}")


def run_all_tests():
    """Run all defense tests."""
    print("=" * 60)
    print("        DEFENSE MODULE TESTS")
    print("=" * 60)
    print()

    tests = [
        test_hold_when_all_good,
        test_emergency_exit_regime,
        test_emergency_exit_imbalance_flip,
        test_emergency_exit_zscore,
        test_time_exit,
        test_defense_disabled,
        test_down_position_imbalance,
        test_state_tracking,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"[FAIL] {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"[ERROR] {test.__name__}: {e}")
            failed += 1

    print()
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    exit(0 if success else 1)
