"""
Trade simulator for backtesting.

Replays historical data through the signal pipeline and simulates trades.
"""

import sys
import os
from dataclasses import dataclass
from pathlib import Path

# Add signals module to path
sys.path.insert(0, str(Path(__file__).parent.parent / "indicators" / "signals"))

from config import SignalConfig
from gates import evaluate_gates, get_probability_zone
from microstructure import compute_microstructure
from state import StateTracker
from scorer import compute_score
from decision import decide, Action, Side, DecisionConfig


@dataclass
class SimulatedTrade:
    """A simulated trade."""
    window_start: int
    entry_ts: int
    entry_price: float
    side: str  # "UP" or "DOWN"
    confidence: str
    score: float
    outcome: str | None  # "UP" or "DOWN" - actual result
    pnl: float | None  # Profit/loss
    won: bool | None


@dataclass
class WindowResult:
    """Result of simulating a window."""
    window_start: int
    num_ticks: int
    entry_signals: int  # How many ENTER signals
    trade: SimulatedTrade | None  # First entry (if any)
    final_prob_up: float | None
    outcome: str | None
    market: str | None = None  # e.g. BTC1h (set by runner for multi-market)


class Simulator:
    """
    Simulates trading decisions on historical data.
    """

    def __init__(
        self,
        signal_config: SignalConfig | None = None,
        decision_config: DecisionConfig | None = None,
    ):
        """
        Initialize simulator.

        Args:
            signal_config: Gate configuration
            decision_config: Decision thresholds
        """
        self.signal_config = signal_config or SignalConfig()
        self.decision_config = decision_config or DecisionConfig()
        self.state_tracker = StateTracker(window_size=300)

    def reset_state(self):
        """Reset state tracker for new simulation."""
        self.state_tracker = StateTracker(window_size=300)

    def simulate_tick(
        self,
        poly_data: dict,
        binance_data: dict | None,
        coin: str = "btc",
        window_duration_s: int | None = None,
        entry_window_length_s: int | None = None,
        entry_window_max_remaining_s: int | None = None,
        entry_window_min_remaining_s: int | None = None,
    ) -> dict:
        """
        Simulate a single tick and return the decision.

        Args:
            poly_data: Polymarket book data
            binance_data: Binance volatility data
            coin: Coin symbol
            window_duration_s: If set (e.g. 3600 for 1h), time gate uses last 4 min of window.

        Returns:
            Dict with decision details
        """
        # Evaluate gates (pass window_duration_s and entry_window_length_s for 1h/4h/1d)
        gate_result = evaluate_gates(
            poly_data, binance_data, self.signal_config,
            window_duration_s=window_duration_s,
            entry_window_length_s=entry_window_length_s,
            entry_window_max_remaining_s=entry_window_max_remaining_s,
            entry_window_min_remaining_s=entry_window_min_remaining_s,
        )

        # Get probability (prefer derived.prob_up to align with loader outcome)
        yes_data = poly_data.get("yes", {}) or {}
        derived = poly_data.get("derived", {}) or {}
        prob_up = derived.get("prob_up")
        if prob_up is None:
            prob_up = yes_data.get("mid", 0.5)
        if prob_up is None:
            prob_up = 0.5
        zone = get_probability_zone(prob_up)

        # Compute microstructure
        prev_imbalance = self.state_tracker.get_prev_imbalance(coin)
        micro = compute_microstructure(poly_data, prev_imbalance)

        # Update state
        window_start = poly_data.get("window_start", 0)
        ts_ms = poly_data.get("ts_ms", 0)
        now_ts = ts_ms / 1000.0

        state = self.state_tracker.update(
            coin=coin,
            gates_passed=gate_result.all_passed,
            prob=prob_up,
            imbalance=micro.imbalance,
            spread_pct=micro.spread_pct,
            microprice_edge=micro.microprice_vs_mid,
            window_start=window_start,
            now_ts=now_ts,
        )

        # Extract Binance indicators
        rv_5m = None
        taker_ratio = None
        regime = None
        if binance_data:
            vol_data = binance_data.get("volatility", {}) or {}
            rv_5m = vol_data.get("rv_5m")
            sentiment = binance_data.get("sentiment", {}) or {}
            taker_ratio = sentiment.get("taker_buy_sell_ratio")
            class_data = binance_data.get("classification", {}) or {}
            regime = class_data.get("cluster")

        # Compute score
        score_result = compute_score(
            imbalance=micro.imbalance,
            microprice_edge=micro.microprice_vs_mid,
            imbalance_delta=micro.imbalance_delta,
            impact_buy=micro.impact_buy_100,
            impact_sell=micro.impact_sell_100,
            spread_pct=micro.spread_pct,
            rv_5m=rv_5m,
            taker_ratio=taker_ratio,
            persistence_s=state.persistence_s,
        )

        # Make decision
        decision = decide(
            all_gates_passed=gate_result.all_passed,
            gate_failure_reason=gate_result.reason,
            prob_up=prob_up,
            zone=zone,
            persistence_s=state.persistence_s,
            score=score_result.score,
            regime=regime,
            remaining_s=gate_result.time_remaining_s,
            config=self.decision_config,
        )

        return {
            "ts_ms": ts_ms,
            "prob_up": prob_up,
            "zone": zone,
            "gates_passed": gate_result.all_passed,
            "persistence_s": state.persistence_s,
            "score": score_result.score,
            "action": decision.action,
            "side": decision.side,
            "confidence": decision.confidence,
            "reason": decision.reason,
        }

    def simulate_window(
        self,
        ticks: list[dict],
        outcome: str | None,
        coin: str = "btc",
        window_duration_s: int | None = None,
        entry_window_length_s: int | None = None,
        entry_window_max_remaining_s: int | None = None,
        entry_window_min_remaining_s: int | None = None,
    ) -> WindowResult:
        """
        Simulate a complete window.

        Args:
            ticks: List of ticks in the window
            outcome: Actual outcome ("UP" or "DOWN")
            coin: Coin symbol
            window_duration_s: If set (e.g. 3600 for 1h), time gate uses last N min of window.
            entry_window_length_s: If set (e.g. 900 for 15 min), entry window length in seconds.

        Returns:
            WindowResult with trade details
        """
        if not ticks:
            return WindowResult(
                window_start=0,
                num_ticks=0,
                entry_signals=0,
                trade=None,
                final_prob_up=None,
                outcome=outcome,
            )

        window_start = ticks[0].get("window_start", 0)
        entry_signals = 0
        first_entry = None

        # Reset state for new window
        self.state_tracker._get_state(coin).current_window_start = 0

        for tick in ticks:
            # Extract Binance data if available
            binance_data = tick.get("volatility_data") or tick.get("binance")

            result = self.simulate_tick(
                tick, binance_data, coin,
                window_duration_s=window_duration_s,
                entry_window_length_s=entry_window_length_s,
                entry_window_max_remaining_s=entry_window_max_remaining_s,
                entry_window_min_remaining_s=entry_window_min_remaining_s,
            )

            if result["action"] == Action.ENTER:
                entry_signals += 1

                if first_entry is None:
                    # Record first entry
                    entry_price = result["prob_up"] if result["side"] == Side.UP else (1 - result["prob_up"])

                    first_entry = SimulatedTrade(
                        window_start=window_start,
                        entry_ts=result["ts_ms"],
                        entry_price=entry_price,
                        side=result["side"].value if result["side"] else "UP",
                        confidence=result["confidence"].value if result["confidence"] else "medium",
                        score=result["score"],
                        outcome=outcome,
                        pnl=None,
                        won=None,
                    )

        # Calculate P&L for the trade
        if first_entry and outcome:
            won = (first_entry.side == outcome)
            if won:
                pnl = 1.0 - first_entry.entry_price  # Win: get $1, paid entry_price
            else:
                pnl = -first_entry.entry_price  # Lose: lose entry_price

            first_entry.pnl = pnl
            first_entry.won = won

        # Get final probability
        final_prob_up = None
        if ticks:
            last = ticks[-1]
            if "probability" in last:
                final_prob_up = last["probability"].get("prob_up")
            elif "yes" in last:
                final_prob_up = last["yes"].get("mid")

        return WindowResult(
            window_start=window_start,
            num_ticks=len(ticks),
            entry_signals=entry_signals,
            trade=first_entry,
            final_prob_up=final_prob_up,
            outcome=outcome,
        )

    def simulate_windows(
        self,
        windows: list[tuple[list[dict], str | None]],
        coin: str = "btc",
    ) -> list[WindowResult]:
        """
        Simulate multiple windows.

        Args:
            windows: List of (ticks, outcome) tuples
            coin: Coin symbol

        Returns:
            List of WindowResults
        """
        results = []

        for ticks, outcome in windows:
            result = self.simulate_window(ticks, outcome, coin)
            results.append(result)

        return results
