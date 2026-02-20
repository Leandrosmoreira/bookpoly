"""
Indicadores de volatilidade para detecção de regime.

Calcula vol curta/longa, ratio, z-score e delta vs entrada.
Fonte: midpoints Polymarket (1 amostra/s).
"""

from __future__ import annotations

import math
from typing import Optional


EPS = 1e-12  # Evita divisão por zero


def calc_vol_short(prices: list[tuple[float, float]], window_s: int = 10) -> float:
    """
    Volatilidade de curto prazo (std dev dos returns na janela).

    Args:
        prices: Lista de (timestamp_s, price) ordenada por tempo
        window_s: Janela em segundos

    Returns:
        Desvio padrão dos returns na janela (0.0 se dados insuficientes)
    """
    if len(prices) < 3:
        return 0.0

    cutoff = prices[-1][0] - window_s
    recent = [(ts, p) for ts, p in prices if ts >= cutoff]

    if len(recent) < 3:
        return 0.0

    returns = []
    for i in range(1, len(recent)):
        prev_p = recent[i - 1][1]
        curr_p = recent[i][1]
        if prev_p > EPS:
            returns.append((curr_p - prev_p) / prev_p)

    if len(returns) < 2:
        return 0.0

    mean_r = sum(returns) / len(returns)
    variance = sum((r - mean_r) ** 2 for r in returns) / len(returns)
    return math.sqrt(variance)


def calc_vol_long(prices: list[tuple[float, float]], window_s: int = 60) -> float:
    """
    Volatilidade de longo prazo (std dev dos returns na janela longa).

    Mesma lógica que vol_short, janela maior.

    Args:
        prices: Lista de (timestamp_s, price) ordenada por tempo
        window_s: Janela em segundos

    Returns:
        Desvio padrão dos returns na janela
    """
    return calc_vol_short(prices, window_s)


def calc_vol_ratio(vol_short: float, vol_long: float) -> float:
    """
    Ratio entre vol curta e vol longa.

    > 1.0 = vol crescendo (regime mudando)
    < 1.0 = vol diminuindo (mercado acalmando)
    ≈ 1.0 = estável

    Args:
        vol_short: Volatilidade de curto prazo
        vol_long: Volatilidade de longo prazo

    Returns:
        Ratio (clampado em [0, 10])
    """
    ratio = vol_short / max(vol_long, EPS)
    return min(ratio, 10.0)


def calc_z_vol(vol_short: float, vol_history: list[tuple[float, float]], window_s: int = 120) -> float:
    """
    Z-score da vol_short contra o histórico recente.

    Detecta anomalias: z > 2 = vol muito acima do normal.

    Args:
        vol_short: Valor atual de vol_short
        vol_history: Lista de (timestamp_s, vol_short) históricos
        window_s: Janela do z-score

    Returns:
        Z-score (0.0 se histórico insuficiente)
    """
    if len(vol_history) < 5:
        return 0.0

    cutoff = vol_history[-1][0] - window_s
    recent = [v for ts, v in vol_history if ts >= cutoff]

    if len(recent) < 5:
        return 0.0

    mean_v = sum(recent) / len(recent)
    variance = sum((v - mean_v) ** 2 for v in recent) / len(recent)
    std_v = math.sqrt(variance)

    if std_v < EPS:
        return 0.0

    return (vol_short - mean_v) / std_v


def calc_delta_vol_entry(vol_short: float, vol_entry_short: float) -> Optional[float]:
    """
    Razão entre vol atual e vol no momento da entrada.

    > 1.0 = vol cresceu desde a entrada (mercado ficou mais arriscado)
    < 1.0 = vol diminuiu (mercado acalmou)

    Args:
        vol_short: Volatilidade atual
        vol_entry_short: Volatilidade no momento do fill

    Returns:
        Ratio vol_now / vol_entry (None se vol_entry == 0)
    """
    if vol_entry_short < EPS:
        return None

    return vol_short / vol_entry_short
