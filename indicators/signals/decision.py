"""
Decision logic for trading signals.

Combines gates, score, and context to make final ENTER/NO_ENTER decision.
Now includes reversal detection to prevent entering when market is reversing.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Action(Enum):
    """Possible trading actions."""
    ENTER = "ENTER"
    NO_ENTER = "NO_ENTER"


class Side(Enum):
    """Trading side (bet on UP or DOWN)."""
    UP = "UP"
    DOWN = "DOWN"


class Confidence(Enum):
    """Confidence level of the signal."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class DecisionConfig:
    """Configurable thresholds for decision logic."""
    # Minimum persistence required (seconds)
    min_persistence_s: float = 20.0

    # Score thresholds
    score_high: float = 0.70  # High confidence threshold
    score_medium: float = 0.50  # Medium confidence threshold
    score_low: float = 0.35  # Minimum to consider entry

    # Zones that block entry
    blocked_zones: tuple = ("danger",)

    # Regimes that block entry
    blocked_regimes: tuple = ("muito_alta",)

    # === FORCED ENTRY (override all filters) ===
    # Baseado na análise: Prob >=90% + <=180s tem melhor win rate com spread ok
    force_entry_enabled: bool = True
    force_entry_min_prob: float = 0.90  # 90% (antes: 95%) - melhor spread
    force_entry_max_remaining_s: float = 180.0  # 3 minutos (antes: 2min) - mais tempo

    # === REVERSAL DETECTION ===
    # Bloqueia entrada se detectar reversão contra nossa posição
    reversal_check_enabled: bool = True
    reversal_block_threshold: float = 0.70  # Score > 0.70 = bloqueia
    reversal_alert_threshold: float = 0.50  # Score > 0.50 = alerta (log)


@dataclass
class ReversalInfo:
    """Information about reversal detection."""
    score: float = 0.0
    direction: str = "none"  # "up", "down", "none"
    should_block: bool = False
    reason: str = ""
    momentum_pct: Optional[float] = None


@dataclass
class Decision:
    """Final trading decision."""
    action: Action
    side: Side | None  # Only set if ENTER
    confidence: Confidence | None  # Only set if ENTER
    reason: str  # Explanation of decision
    score: float
    persistence_s: float
    zone: str
    regime: str | None
    reversal: Optional[ReversalInfo] = None  # Reversal detection info


def decide(
    # Gate results
    all_gates_passed: bool,
    gate_failure_reason: str | None,

    # Probability info
    prob_up: float,
    zone: str,

    # State info
    persistence_s: float,

    # Score
    score: float,

    # Volatility regime
    regime: str | None,

    # Time remaining in window (for forced entry)
    remaining_s: float | None = None,

    # Reversal detection (NEW)
    reversal_score: float | None = None,
    reversal_direction: str | None = None,
    reversal_reason: str | None = None,
    momentum_pct: float | None = None,

    # Config
    config: DecisionConfig | None = None,
) -> Decision:
    """
    Make final trading decision based on all inputs.

    Args:
        all_gates_passed: Whether all gates are satisfied
        gate_failure_reason: Why gates failed (if applicable)
        prob_up: Current probability of UP outcome
        zone: Probability zone (danger, caution, safe, neutral)
        persistence_s: Seconds gates have been satisfied
        score: Composite score (0-1)
        regime: Volatility regime
        config: Decision thresholds

    Returns:
        Decision with action, side, confidence, and reason
    """
    if config is None:
        config = DecisionConfig()

    # Determine which side we're betting on
    # We bet on the FAVORITE (against the underdog)
    side = Side.UP if prob_up > 0.5 else Side.DOWN

    # Probabilidade do favorito (sempre > 0.5)
    prob_favorite = max(prob_up, 1 - prob_up)

    # Build reversal info
    reversal_info = ReversalInfo(
        score=reversal_score or 0.0,
        direction=reversal_direction or "none",
        should_block=False,
        reason=reversal_reason or "",
        momentum_pct=momentum_pct,
    )

    # === REVERSAL CHECK (NEW - CRITICAL FOR YOUR STRATEGY) ===
    # Bloqueia entrada se detectar reversão contra nossa posição
    if config.reversal_check_enabled and reversal_score is not None:
        # Check if reversal is against our bet
        reversal_against_bet = (
            (side == Side.UP and reversal_direction == "down") or
            (side == Side.DOWN and reversal_direction == "up")
        )

        if reversal_against_bet and reversal_score >= config.reversal_block_threshold:
            reversal_info.should_block = True
            return Decision(
                action=Action.NO_ENTER,
                side=None,
                confidence=None,
                reason=f"reversal_blocked:score={reversal_score:.2f}_dir={reversal_direction}_{reversal_reason}",
                score=score,
                persistence_s=persistence_s,
                zone=zone,
                regime=regime,
                reversal=reversal_info,
            )

    # === FORCED ENTRY CHECK ===
    # Se prob >= 90% e faltam <= 3min, entra (mas RESPEITA reversal check!)
    if config.force_entry_enabled and remaining_s is not None:
        if prob_favorite >= config.force_entry_min_prob and remaining_s <= config.force_entry_max_remaining_s:
            # Even on forced entry, check for strong reversal
            if reversal_score and reversal_score >= config.reversal_block_threshold:
                reversal_info.should_block = True
                return Decision(
                    action=Action.NO_ENTER,
                    side=None,
                    confidence=None,
                    reason=f"forced_entry_blocked_by_reversal:score={reversal_score:.2f}",
                    score=score,
                    persistence_s=persistence_s,
                    zone=zone,
                    regime=regime,
                    reversal=reversal_info,
                )

            return Decision(
                action=Action.ENTER,
                side=side,
                confidence=Confidence.HIGH,
                reason=f"forced_entry:prob={prob_favorite:.0%}_remaining={remaining_s:.0f}s",
                score=score,
                persistence_s=persistence_s,
                zone=zone,
                regime=regime,
                reversal=reversal_info,
            )

    # Check gates first (mandatory)
    if not all_gates_passed:
        return Decision(
            action=Action.NO_ENTER,
            side=None,
            confidence=None,
            reason=f"gates_failed:{gate_failure_reason or 'unknown'}",
            score=score,
            persistence_s=persistence_s,
            zone=zone,
            regime=regime,
            reversal=reversal_info,
        )

    # Check zone
    if zone in config.blocked_zones:
        return Decision(
            action=Action.NO_ENTER,
            side=None,
            confidence=None,
            reason=f"zone_blocked:{zone}",
            score=score,
            persistence_s=persistence_s,
            zone=zone,
            regime=regime,
            reversal=reversal_info,
        )

    # Check volatility regime
    if regime and regime in config.blocked_regimes:
        return Decision(
            action=Action.NO_ENTER,
            side=None,
            confidence=None,
            reason=f"regime_blocked:{regime}",
            score=score,
            persistence_s=persistence_s,
            zone=zone,
            regime=regime,
            reversal=reversal_info,
        )

    # Check persistence
    if persistence_s < config.min_persistence_s:
        return Decision(
            action=Action.NO_ENTER,
            side=None,
            confidence=None,
            reason=f"persistence_low:{persistence_s:.0f}s<{config.min_persistence_s:.0f}s",
            score=score,
            persistence_s=persistence_s,
            zone=zone,
            regime=regime,
            reversal=reversal_info,
        )

    # Check score thresholds
    if score < config.score_low:
        return Decision(
            action=Action.NO_ENTER,
            side=None,
            confidence=None,
            reason=f"score_too_low:{score:.2f}<{config.score_low:.2f}",
            score=score,
            persistence_s=persistence_s,
            zone=zone,
            regime=regime,
            reversal=reversal_info,
        )

    # Determine confidence level
    if score >= config.score_high:
        confidence = Confidence.HIGH
    elif score >= config.score_medium:
        confidence = Confidence.MEDIUM
    else:
        confidence = Confidence.LOW

    # All conditions met - ENTER!
    return Decision(
        action=Action.ENTER,
        side=side,
        confidence=confidence,
        reason="all_conditions_met",
        score=score,
        persistence_s=persistence_s,
        zone=zone,
        regime=regime,
        reversal=reversal_info,
    )


def format_decision(decision: Decision) -> str:
    """Format decision for logging."""
    if decision.action == Action.ENTER:
        return (
            f"★ ENTER {decision.side.value} ★ "
            f"conf={decision.confidence.value} "
            f"score={decision.score:.2f} "
            f"persist={decision.persistence_s:.0f}s "
            f"zone={decision.zone}"
        )
    else:
        return (
            f"NO_ENTER: {decision.reason} "
            f"score={decision.score:.2f} "
            f"zone={decision.zone}"
        )


def get_entry_price(prob_up: float, side: Side) -> float:
    """
    Get the entry price based on side.

    Args:
        prob_up: Probability of UP
        side: Which side we're betting on

    Returns:
        Entry price (cost of the bet)
    """
    if side == Side.UP:
        return prob_up
    else:
        return 1 - prob_up


def get_potential_payout(entry_price: float) -> float:
    """
    Get potential payout if we win.

    Args:
        entry_price: Cost of the bet

    Returns:
        Profit if we win (always 1.0 - entry_price)
    """
    return 1.0 - entry_price


def get_risk_reward(entry_price: float) -> float:
    """
    Get risk/reward ratio.

    Args:
        entry_price: Cost of the bet

    Returns:
        Risk/reward ratio (potential profit / risk)
    """
    if entry_price == 0:
        return float('inf')

    potential_profit = 1.0 - entry_price
    risk = entry_price

    return potential_profit / risk
