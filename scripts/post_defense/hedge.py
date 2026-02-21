"""
Calculo de sizing e preco de hedge.

Determina quantas shares hedgear e a que preco,
baseado na severity e fase da state machine.
"""

from __future__ import annotations

import math

from .state_machine import DefensePhase
from .config import PostDefenseConfig


def calc_hedge_shares(
    severity: float,
    position_shares: int,
    total_hedged: int,
    phase: DefensePhase,
    config: PostDefenseConfig,
) -> int:
    """
    Calcula quantas shares hedgear AGORA.

    DEFENSE: hedge_pct = lerp(min_hedge, max_hedge, severity)
    PANIC:   hedge_pct = max_hedge (80%)

    Subtrai total_hedged ja feito.
    Retorna 0 se nao precisa hedgear mais ou se < min_shares.

    Args:
        severity: Severity atual (0-1)
        position_shares: Total de shares na posicao original
        total_hedged: Shares ja hedgeadas neste ciclo
        phase: Fase atual da state machine
        config: Configuracao

    Returns:
        Numero de shares para hedgear (0 se nenhuma)
    """
    if phase not in (DefensePhase.DEFENSE, DefensePhase.PANIC):
        return 0

    if position_shares <= 0:
        return 0

    # Calcula percentual de hedge desejado
    if phase == DefensePhase.PANIC:
        hedge_pct = config.max_hedge
    else:
        # DEFENSE: interpola linear entre min e max baseado na severity
        hedge_pct = config.min_hedge + (config.max_hedge - config.min_hedge) * severity
        hedge_pct = max(config.min_hedge, min(config.max_hedge, hedge_pct))

    # Shares desejadas (total, incluindo ja hedgeadas)
    target_shares = math.ceil(position_shares * hedge_pct)

    # Quanto falta hedgear
    remaining = target_shares - total_hedged

    if remaining < config.min_shares:
        return 0

    return remaining


def calc_hedge_price(best_ask: float, phase: DefensePhase, config: PostDefenseConfig) -> float:
    """
    Preco para ordem de hedge (IOC/FOK taker).

    DEFENSE: best_ask (cruza spread, fill imediato)
    PANIC:   best_ask + markup (garante fill mesmo se book mover)

    Args:
        best_ask: Melhor ask do book do token oposto
        phase: Fase atual
        config: Configuracao

    Returns:
        Preco clampado em [0.01, 0.99]
    """
    if phase == DefensePhase.PANIC:
        price = best_ask + config.hedge_panic_markup
    else:
        price = best_ask

    return max(0.01, min(0.99, round(price, 2)))


def get_opposite_token(
    entered_side: str,
    yes_token_id: str,
    no_token_id: str,
) -> tuple[str, str]:
    """
    Retorna (token_id, side) do lado oposto para hedge.

    YES entrou -> hedge com NO
    NO entrou  -> hedge com YES

    Args:
        entered_side: "YES" ou "NO"
        yes_token_id: Token ID do YES
        no_token_id: Token ID do NO

    Returns:
        (token_id, side) do lado oposto
    """
    if entered_side.upper() == "YES":
        return no_token_id, "NO"
    else:
        return yes_token_id, "YES"
