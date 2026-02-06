"""
JSONL recorder for trading signals.

Records all signal data for analysis and backtesting.
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import asdict

from gates import GateResult
from microstructure import MicrostructureMetrics
from state import TemporalState
from scorer import ScoreResult
from decision import Decision, Action


def build_signal_row(
    # Timestamps
    ts_ms: int,
    market: str,

    # Window info
    window_start: int,
    time_remaining_s: float,

    # Raw probability
    prob_up: float,

    # Gate results
    gates: GateResult,

    # Microstructure
    micro: MicrostructureMetrics,

    # State
    state: TemporalState,

    # Score
    score_result: ScoreResult,

    # Decision
    decision: Decision,

    # Binance data (optional)
    binance_data: dict | None = None,
) -> dict:
    """
    Build a complete signal row for JSONL output.

    Args:
        ts_ms: Timestamp in milliseconds
        market: Market name (e.g., "BTC15m")
        window_start: Window start timestamp
        time_remaining_s: Seconds remaining in window
        prob_up: Probability of UP outcome
        gates: Gate evaluation results
        micro: Microstructure metrics
        state: Temporal state
        score_result: Score calculation results
        decision: Final decision
        binance_data: Raw Binance data (optional)

    Returns:
        Dict ready for JSON serialization
    """
    # Determine underdog
    underdog_side = "DOWN" if prob_up > 0.5 else "UP"
    underdog_price = min(prob_up, 1 - prob_up)

    # Extract Binance indicators
    binance_indicators = {}
    if binance_data:
        vol_data = binance_data.get("volatility", {}) or {}
        class_data = binance_data.get("classification", {}) or {}
        sentiment = binance_data.get("sentiment", {}) or {}

        binance_indicators = {
            "rv_5m": vol_data.get("rv_5m"),
            "rv_1h": vol_data.get("rv_1h"),
            "atr_norm": vol_data.get("atr_norm"),
            "regime": class_data.get("cluster"),
            "funding_rate": sentiment.get("funding_rate"),
            "taker_ratio": sentiment.get("taker_buy_sell_ratio"),
            "long_short_ratio": sentiment.get("long_short_ratio"),
        }

    row = {
        # Version and timestamps
        "v": 1,
        "ts_ms": ts_ms,
        "ts_iso": datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat(),

        # Market info
        "market": market,
        "window_start": window_start,
        "time_remaining_s": round(time_remaining_s, 1),

        # Gates
        "gates": {
            "time": gates.time_gate,
            "liquidity": gates.liquidity_gate,
            "spread": gates.spread_gate,
            "stability": gates.stability_gate,
            "latency": gates.latency_gate,
            "all_passed": gates.all_passed,
            "failure_reason": gates.reason,
        },

        # Microstructure indicators
        "microstructure": {
            "mid": round(micro.mid, 4),
            "microprice": round(micro.microprice, 4),
            "microprice_vs_mid": round(micro.microprice_vs_mid, 5),
            "imbalance": round(micro.imbalance, 4),
            "imbalance_delta": round(micro.imbalance_delta, 4) if micro.imbalance_delta else None,
            "spread": round(micro.spread, 4),
            "spread_pct": round(micro.spread_pct, 4),
            "impact_buy_100": round(micro.impact_buy_100, 5),
            "impact_sell_100": round(micro.impact_sell_100, 5),
            "bid_concentration": round(micro.bid_concentration, 3),
            "ask_concentration": round(micro.ask_concentration, 3),
            "depth_ratio": round(micro.depth_ratio, 3),
        },

        # Binance indicators
        "binance": binance_indicators,

        # Probability
        "probability": {
            "prob_up": round(prob_up, 4),
            "prob_down": round(1 - prob_up, 4),
            "underdog": underdog_side,
            "underdog_price": round(underdog_price, 4),
            "zone": decision.zone,
        },

        # State
        "state": {
            "persistence_s": round(state.persistence_s, 1),
            "ticks_in_window": state.ticks_in_window,
            "prob_zscore": round(state.prob_stats.z_score, 2) if state.prob_stats and state.prob_stats.z_score else None,
            "imbalance_zscore": round(state.imbalance_stats.z_score, 2) if state.imbalance_stats and state.imbalance_stats.z_score else None,
            "prev_window_outcome": state.prev_window_outcome,
        },

        # Score
        "score": {
            "value": round(score_result.score, 3),
            "components": {k: round(v, 4) for k, v in score_result.components.items()},
        },

        # Decision
        "decision": {
            "action": decision.action.value,
            "side": decision.side.value if decision.side else None,
            "confidence": decision.confidence.value if decision.confidence else None,
            "reason": decision.reason,
        },
    }

    return row


class SignalWriter:
    """
    Writes signal records to JSONL files with daily rotation.
    """

    def __init__(self, output_dir: str | Path, prefix: str = "signals"):
        """
        Initialize writer.

        Args:
            output_dir: Directory to write files to
            prefix: File prefix (e.g., "signals" -> "signals_2026-02-06.jsonl")
        """
        self.output_dir = Path(output_dir)
        self.prefix = prefix
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self._current_file: Path | None = None
        self._current_date: str | None = None
        self._file_handle = None

    def _get_file_for_date(self, date_str: str) -> Path:
        """Get file path for a given date."""
        return self.output_dir / f"{self.prefix}_{date_str}.jsonl"

    def _ensure_file(self, ts_ms: int):
        """Ensure we have the correct file open for the timestamp."""
        date_str = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")

        if date_str != self._current_date:
            # Close old file
            if self._file_handle:
                self._file_handle.close()

            # Open new file
            self._current_date = date_str
            self._current_file = self._get_file_for_date(date_str)
            self._file_handle = open(self._current_file, "a", encoding="utf-8")

    def write(self, row: dict):
        """
        Write a signal row to the appropriate file.

        Args:
            row: Signal data dict
        """
        ts_ms = row.get("ts_ms", int(time.time() * 1000))
        self._ensure_file(ts_ms)

        line = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        self._file_handle.write(line + "\n")
        self._file_handle.flush()

    def close(self):
        """Close the current file."""
        if self._file_handle:
            self._file_handle.close()
            self._file_handle = None
            self._current_file = None
            self._current_date = None
