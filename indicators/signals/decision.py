"""
Decision logic for trading signals.

Combines gates, score, and context to make final ENTER/NO_ENTER decision.
"""

from dataclasses import dataclass
from enum import Enum


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
    # Estratégia: Entrar APENAS nos últimos 4 minutos com prob >= 95% CONTRA o azarão
    force_entry_enabled: bool = True
    force_entry_min_prob: float = 0.95  # 95% - probabilidade muito alta
    force_entry_max_remaining_s: float = 240.0  # 4 minutos (últimos 4 min da janela)


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
    # ESTRATÉGIA: Sempre CONTRA o azarão (fade the favorite)
    # Se prob_up >= 95%, entrar DOWN (contra o favorito UP)
    # Se prob_up <= 5%, entrar UP (contra o favorito DOWN)
    if prob_up >= 0.95:
        side = Side.DOWN  # Entrar contra o favorito UP
        prob_favorite = prob_up
    elif prob_up <= 0.05:
        side = Side.UP  # Entrar contra o favorito DOWN
        prob_favorite = 1 - prob_up
    else:
        # Para entradas normais (não forçadas), usar lógica padrão
        side = Side.UP if prob_up > 0.5 else Side.DOWN
        prob_favorite = max(prob_up, 1 - prob_up)

    # === FORCED ENTRY CHECK (com segurança) ===
    # ESTRATÉGIA: Entrar APENAS nos últimos 4 minutos com prob >= 95% CONTRA o azarão
    # Só permite entrada forçada se:
    # 1. Probabilidade muito alta (>= 95% para qualquer lado)
    # 2. Tempo restante adequado (<= 240s = últimos 4 minutos)
    # 3. TODOS os gates passaram (segurança básica)
    # 4. Zone não é perigosa
    # 5. Regime não é muito alto
    # 6. Score mínimo aceitável
    if config.force_entry_enabled and remaining_s is not None:
        # Verificar se temos prob >= 95% em qualquer direção
        has_extreme_prob = (prob_up >= config.force_entry_min_prob) or (prob_up <= (1 - config.force_entry_min_prob))
        
        if (
            has_extreme_prob
            and remaining_s <= config.force_entry_max_remaining_s  # Últimos 4 minutos
            and remaining_s >= 30  # Mas não nos últimos 30 segundos (segurança)
            and all_gates_passed  # ✅ OBRIGATÓRIO: Gates devem passar
            and zone not in config.blocked_zones  # ✅ OBRIGATÓRIO: Zone segura
            and (regime is None or regime not in config.blocked_regimes)  # ✅ OBRIGATÓRIO: Regime OK
            and score >= config.score_low  # ✅ OBRIGATÓRIO: Score mínimo
        ):
            return Decision(
                action=Action.ENTER,
                side=side,  # Já definido como CONTRA o azarão acima
                confidence=Confidence.HIGH,
                reason=f"forced_entry_contra_azarão:prob={prob_favorite:.0%}_remaining={remaining_s:.0f}s_side={side.value}",
                score=score,
                persistence_s=persistence_s,
                zone=zone,
                regime=regime,
            )

    # === ESTRATÉGIA RESTRITA: APENAS ENTRADA FORÇADA ===
    # Desabilitar entradas normais - só permitir entrada forçada com:
    # - Prob >= 95% (qualquer lado)
    # - Últimos 4 minutos (240s >= remaining >= 30s)
    # - Contra o azarão
    # Se não passou pela entrada forçada acima, NÃO ENTRAR
    
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
        )

    # === BLOQUEAR ENTRADAS NORMAIS ===
    # Só permitir entrada forçada (já verificada acima)
    # Se chegou aqui, não passou pela entrada forçada, então NÃO ENTRAR
    return Decision(
        action=Action.NO_ENTER,
        side=None,
        confidence=None,
        reason=f"only_forced_entry_allowed:prob={prob_favorite:.0%}_remaining={remaining_s:.0f}s if available",
        score=score,
        persistence_s=persistence_s,
        zone=zone,
        regime=regime,
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
