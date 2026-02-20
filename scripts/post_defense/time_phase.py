"""
Indicadores de tempo e fase do mercado.

Determina a fase temporal e se reversão é permitida
baseado no tempo restante até expiração.
"""

from __future__ import annotations


def calc_phase(time_left_s: int, early_s: int = 360, late_s: int = 180) -> str:
    """
    Determina a fase temporal do mercado.

    Args:
        time_left_s: Segundos até expiração
        early_s: Limite para fase "early" (time_left > early_s)
        late_s: Limite para fase "late" (time_left < late_s)

    Returns:
        "early" | "mid" | "late"
    """
    if time_left_s > early_s:
        return "early"
    elif time_left_s < late_s:
        return "late"
    return "mid"


def calc_allow_reversal(time_left_s: int, t_min_s: int = 240) -> bool:
    """
    Verifica se há tempo suficiente para executar reversão.

    Reversão só é permitida se ainda houver tempo para:
    1. Detectar a reversão
    2. Colocar hedge
    3. Hedge ter tempo de dar fill

    Args:
        time_left_s: Segundos até expiração
        t_min_s: Mínimo de tempo para permitir reversão

    Returns:
        True se reversão é permitida
    """
    return time_left_s >= t_min_s


def calc_time_pressure(time_left_s: int, max_s: int = 900) -> float:
    """
    Calcula urgência temporal normalizada (0.0 a 1.0).

    0.0 = muito tempo restante (sem pressão)
    1.0 = quase expirando (máxima pressão)

    Args:
        time_left_s: Segundos até expiração
        max_s: Tempo máximo considerado (default: 15min = 900s)

    Returns:
        Pressão temporal normalizada [0.0, 1.0]
    """
    if time_left_s >= max_s:
        return 0.0
    if time_left_s <= 0:
        return 1.0
    return 1.0 - (time_left_s / max_s)
