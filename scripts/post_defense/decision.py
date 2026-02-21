"""
Logica de decisao central do post-defense.

Junta state machine + hedge sizing numa decisao unica por tick.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .types import TickSnapshot
from .config import PostDefenseConfig
from .state_machine import (
    DefensePhase,
    DefenseStateTracker,
    evaluate_transition,
)
from .hedge import calc_hedge_shares, calc_hedge_price, get_opposite_token


@dataclass
class DefenseDecision:
    """Resultado da avaliacao de defesa a cada tick."""

    phase: DefensePhase
    prev_phase: DefensePhase
    phase_changed: bool
    reason: str

    # Acao de hedge
    should_hedge: bool = False
    hedge_shares: int = 0
    hedge_price: float = 0.0
    hedge_token_id: str = ""
    hedge_side: str = ""

    # Diagnostico
    severity: float = 0.0
    rpi: float = 0.0
    rpi_threshold: float = 0.0
    adverse_move: Optional[float] = None
    time_left_s: int = 0


def evaluate_defense(
    tracker: DefenseStateTracker,
    snap: TickSnapshot,
    entered_side: str,
    entered_shares: int,
    yes_token_id: str,
    no_token_id: str,
    best_ask_opposite: Optional[float],
    config: PostDefenseConfig,
    now_ts: float,
) -> DefenseDecision:
    """
    Funcao principal chamada a cada tick durante HOLDING.

    1. Avalia transicao de fase
    2. Calcula se deve hedgear
    3. Retorna decisao completa

    Args:
        tracker: Estado da state machine
        snap: TickSnapshot com todos os indicadores
        entered_side: "YES" ou "NO" (posicao original)
        entered_shares: Quantidade de shares na posicao
        yes_token_id: Token ID do YES
        no_token_id: Token ID do NO
        best_ask_opposite: Best ask do token oposto (para hedge price)
        config: Configuracao
        now_ts: Timestamp atual

    Returns:
        DefenseDecision com acao e diagnostico
    """
    prev_phase = tracker.phase

    # 1. Avaliar transicao
    new_phase, reason = evaluate_transition(tracker, snap, config, now_ts)
    phase_changed = new_phase != prev_phase

    # 2. Montar decisao base
    decision = DefenseDecision(
        phase=new_phase,
        prev_phase=prev_phase,
        phase_changed=phase_changed,
        reason=reason,
        severity=snap.severity,
        rpi=snap.rpi,
        rpi_threshold=snap.rpi_threshold_dynamic,
        adverse_move=snap.adverse_move,
        time_left_s=snap.time_left_s,
    )

    # 3. Verificar se deve hedgear
    if new_phase not in (DefensePhase.DEFENSE, DefensePhase.PANIC):
        return decision

    # Cooldown: nao hedgear muito rapido
    if tracker.last_hedge_ts > 0:
        elapsed_since_hedge = now_ts - tracker.last_hedge_ts
        if elapsed_since_hedge < config.hedge_cooldown_s:
            return decision

    # Calcular shares
    shares = calc_hedge_shares(
        severity=snap.severity,
        position_shares=entered_shares,
        total_hedged=tracker.total_hedge_shares,
        phase=new_phase,
        config=config,
    )

    if shares <= 0:
        return decision

    # Precisa do best_ask do lado oposto para calcular preco
    if best_ask_opposite is None or best_ask_opposite <= 0:
        return decision

    # Calcular preco e token
    hedge_token, hedge_side = get_opposite_token(
        entered_side, yes_token_id, no_token_id,
    )
    hedge_price = calc_hedge_price(best_ask_opposite, new_phase, config)

    # Preencher decisao
    decision.should_hedge = True
    decision.hedge_shares = shares
    decision.hedge_price = hedge_price
    decision.hedge_token_id = hedge_token
    decision.hedge_side = hedge_side
    tracker.hedge_side = hedge_side

    return decision
