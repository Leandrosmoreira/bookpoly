"""
Gate functions for trading signals.

Gates are binary filters (True/False) that must ALL pass before evaluating score.
If any gate fails, we don't trade.
"""

import time
from dataclasses import dataclass
from config import SignalConfig


@dataclass
class GateResult:
    """Result of evaluating all gates."""
    time_gate: bool
    liquidity_gate: bool
    spread_gate: bool
    stability_gate: bool
    latency_gate: bool
    all_passed: bool
    time_remaining_s: float
    reason: str | None  # Why gates failed (if any)


def time_gate(
    window_start: int,
    now_ts: float,
    config: SignalConfig,
    window_duration_s: int | None = None,
    entry_window_length_s: int | None = None,
    entry_window_max_remaining_s: int | None = None,
    entry_window_min_remaining_s: int | None = None,
) -> tuple[bool, float]:
    """
    Check if we're in the trading window.

    Args:
        window_start: Unix timestamp of window start
        now_ts: Current timestamp
        config: Signal configuration
        window_duration_s: If set (e.g. 3600 for 1h), use custom entry window.
        entry_window_length_s: "Últimos N s" - janela = [duration-N, duration-30]. Usado se max/min remaining não forem passados.
        entry_window_max_remaining_s: "Até X s restantes" - início da janela (ex: 900 = pode entrar quando há até 15 min).
        entry_window_min_remaining_s: "Pelo menos Y s restantes" - fim da janela (ex: 300 = não entrar nos últimos 5 min).

    Exemplo "15 min restantes a 5 min restantes": max_remaining=900, min_remaining=300.
    """
    elapsed = now_ts - window_start
    duration = window_duration_s if window_duration_s is not None else config.window_duration_s
    remaining = duration - elapsed

    if window_duration_s is not None:
        if entry_window_max_remaining_s is not None and entry_window_min_remaining_s is not None:
            # Janela por restante: [min_remaining, max_remaining] → elapsed em [duration-max_remaining, duration-min_remaining]
            time_start = window_duration_s - entry_window_max_remaining_s
            time_end = window_duration_s - entry_window_min_remaining_s
        else:
            # Últimos N s, menos últimos 30s
            window_len = entry_window_length_s if entry_window_length_s is not None else 240
            time_start = window_duration_s - window_len
            time_end = window_duration_s - 30
    else:
        time_start = config.time_window_start_s
        time_end = config.time_window_end_s

    in_window = time_start <= elapsed <= time_end

    return in_window, remaining


def liquidity_gate(
    bid_depth: float,
    ask_depth: float,
    config: SignalConfig,
) -> bool:
    """
    Check if there's enough liquidity to trade.

    Args:
        bid_depth: Total bid depth in shares
        ask_depth: Total ask depth in shares
        config: Signal configuration

    Returns:
        True if liquidity is sufficient
    """
    total_depth = bid_depth + ask_depth
    return total_depth >= config.min_depth


def spread_gate(
    spread: float | None,
    mid: float,
    config: SignalConfig,
) -> bool:
    """
    Check if spread is acceptable (not too wide).

    Args:
        spread: Bid-ask spread (ask - bid), can be None
        mid: Mid price
        config: Signal configuration

    Returns:
        True if spread is acceptable
    """
    if mid <= 0:
        return False
    
    if spread is None or spread <= 0:
        return False

    spread_pct = spread / mid
    return spread_pct <= config.max_spread_pct


def stability_gate(
    rv_5m: float | None,
    regime: str | None,
    config: SignalConfig,
) -> bool:
    """
    Check if volatility is acceptable (not too high).

    Args:
        rv_5m: 5-minute realized volatility (annualized)
        regime: Volatility regime from classifier (muito_baixa, baixa, normal, alta, muito_alta)
        config: Signal configuration

    Returns:
        True if volatility is acceptable
    """
    # Check regime first if configured
    if config.block_high_vol_regime and regime:
        if regime == "muito_alta":
            return False

    # Check raw volatility
    if rv_5m is not None:
        if rv_5m > config.max_volatility:
            return False

    return True


def latency_gate(
    latency_ms: float,
    config: SignalConfig,
) -> bool:
    """
    Check if network latency is acceptable.

    Args:
        latency_ms: Network latency in milliseconds
        config: Signal configuration

    Returns:
        True if latency is acceptable
    """
    return latency_ms <= config.max_latency_ms


def get_probability_zone(prob_up: float) -> str:
    """
    Classify the current probability into a risk zone.

    Args:
        prob_up: Probability of UP outcome (0.0 to 1.0)

    Returns:
        Zone name: "danger", "caution", "safe", or "neutral"
    """
    # Underdog is the one with lower probability
    underdog_prob = min(prob_up, 1.0 - prob_up)

    if underdog_prob < 0.02:
        return "danger"  # Too risky, underdog < 2%
    elif underdog_prob < 0.05:
        return "caution"  # Be careful, underdog 2-5%
    elif underdog_prob < 0.15:
        return "safe"  # Good zone, underdog 5-15%
    else:
        return "neutral"  # No clear edge, underdog > 15%


def evaluate_gates(
    polymarket_data: dict,
    binance_data: dict | None,
    config: SignalConfig,
    now_ts: float | None = None,
    window_duration_s: int | None = None,
    entry_window_length_s: int | None = None,
    entry_window_max_remaining_s: int | None = None,
    entry_window_min_remaining_s: int | None = None,
) -> GateResult:
    """
    Evaluate all gates and return combined result.

    Args:
        polymarket_data: Row from Polymarket book recorder
        binance_data: Row from Binance volatility recorder (optional)
        config: Signal configuration
        now_ts: Override timestamp for backtesting (default: current time)
        window_duration_s: If set (e.g. 3600 for 1h), time gate uses custom window.
        entry_window_length_s: Length in seconds for "last N s" window (default 240).
        entry_window_max_remaining_s: Entrar quando restar até X s (ex: 900 = 15 min).
        entry_window_min_remaining_s: Não entrar quando restar menos de Y s (ex: 300 = 5 min).

    Returns:
        GateResult with all gate evaluations
    """
    # Use provided timestamp or data timestamp or current time
    if now_ts is None:
        # Use data timestamp (ts_ms in milliseconds)
        now_ts = polymarket_data.get("ts_ms", 0) / 1000.0
        if now_ts == 0:
            now_ts = time.time()

    # Extract Polymarket data
    window_start = polymarket_data.get("window_start", 0)
    yes_data = polymarket_data.get("yes", {}) or {}
    latency = polymarket_data.get("fetch", {}).get("latency_ms", 0)

    bid_depth = yes_data.get("bid_depth", 0)
    ask_depth = yes_data.get("ask_depth", 0)
    spread = yes_data.get("spread", 0)
    mid = yes_data.get("mid")
    if mid is None:
        mid = (polymarket_data.get("derived") or {}).get("prob_up")
    mid = mid if mid is not None else 0

    # Extract Binance data
    rv_5m = None
    regime = None
    if binance_data:
        vol_data = binance_data.get("volatility", {}) or {}
        rv_5m = vol_data.get("rv_5m")
        class_data = binance_data.get("classification", {}) or {}
        regime = class_data.get("cluster")

    # Evaluate each gate
    time_ok, time_remaining = time_gate(
        window_start, now_ts, config, window_duration_s,
        entry_window_length_s=entry_window_length_s,
        entry_window_max_remaining_s=entry_window_max_remaining_s,
        entry_window_min_remaining_s=entry_window_min_remaining_s,
    )
    liquidity_ok = liquidity_gate(bid_depth, ask_depth, config)
    spread_ok = spread_gate(spread, mid, config)
    stability_ok = stability_gate(rv_5m, regime, config)
    latency_ok = latency_gate(latency, config)

    # All gates must pass
    all_passed = time_ok and liquidity_ok and spread_ok and stability_ok and latency_ok

    # Determine failure reason
    reason = None
    if not all_passed:
        if not time_ok:
            reason = "time_gate_failed"
        elif not liquidity_ok:
            reason = "liquidity_gate_failed"
        elif not spread_ok:
            reason = "spread_gate_failed"
        elif not stability_ok:
            reason = "stability_gate_failed"
        elif not latency_ok:
            reason = "latency_gate_failed"

    return GateResult(
        time_gate=time_ok,
        liquidity_gate=liquidity_ok,
        spread_gate=spread_ok,
        stability_gate=stability_ok,
        latency_gate=latency_ok,
        all_passed=all_passed,
        time_remaining_s=time_remaining,
        reason=reason,
    )
