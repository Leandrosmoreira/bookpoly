"""
Indicadores de direção (velocity, acceleration).

Mede velocidade e aceleração do preço para detectar
reversões em andamento.
Fonte: midpoints Polymarket (1 amostra/s).
"""

from __future__ import annotations

import math
from typing import Optional


EPS = 1e-12


def calc_velocity(
    prices: list[tuple[float, float]],
    window_s: int = 5,
    smooth_n: int = 3,
) -> float:
    """
    Velocidade do preço (delta_price / delta_time), suavizada.

    Positivo = preço subindo.
    Negativo = preço caindo.

    Args:
        prices: Lista de (timestamp_s, price) ordenada por tempo
        window_s: Janela para calcular delta
        smooth_n: Número de amostras para média móvel (suavização)

    Returns:
        Velocidade suavizada (price_units/segundo). 0.0 se dados insuficientes.
    """
    if len(prices) < 2:
        return 0.0

    cutoff = prices[-1][0] - window_s
    recent = [(ts, p) for ts, p in prices if ts >= cutoff]

    if len(recent) < 2:
        return 0.0

    # Calcula velocidades instantâneas entre cada par
    velocities = []
    for i in range(1, len(recent)):
        dt = recent[i][0] - recent[i - 1][0]
        if dt > EPS:
            dp = recent[i][1] - recent[i - 1][1]
            velocities.append(dp / dt)

    if not velocities:
        return 0.0

    # Suavização: média das últimas smooth_n velocidades
    tail = velocities[-smooth_n:]
    return sum(tail) / len(tail)


def calc_acceleration(velocity_history: list[tuple[float, float]]) -> float:
    """
    Aceleração do preço (delta_velocity / delta_time).

    Positivo = preço acelerando para cima (ou desacelerando queda).
    Negativo = preço acelerando para baixo (ou desacelerando subida).

    Args:
        velocity_history: Lista de (timestamp_s, velocity) recentes

    Returns:
        Aceleração (velocity_units/segundo). 0.0 se dados insuficientes.
    """
    if len(velocity_history) < 2:
        return 0.0

    # Usa as 2 últimas medidas
    ts1, v1 = velocity_history[-2]
    ts2, v2 = velocity_history[-1]

    dt = ts2 - ts1
    if dt < EPS:
        return 0.0

    return (v2 - v1) / dt


def calc_directional_velocity(
    velocity: float,
    side: str,
) -> Optional[float]:
    """
    Velocidade direcional relativa à posição.

    Positivo = preço movendo CONTRA a posição (perigoso).
    Negativo = preço movendo A FAVOR da posição (bom).

    Para YES: preço caindo é contra → inverte sinal.
    Para NO:  preço subindo é contra → mantém sinal.

    Args:
        velocity: Velocidade atual do preço
        side: "YES" ou "NO"

    Returns:
        Velocidade direcional (positivo = contra). None se side inválido.
    """
    side_upper = side.upper()
    if side_upper == "YES":
        # YES ganha quando preço sobe. Queda é contra → inverte.
        return -velocity
    elif side_upper == "NO":
        # NO ganha quando preço cai. Subida é contra → mantém.
        return velocity
    return None


def calc_z_velocity(
    directional_velocity: float,
    velocity_history: list[tuple[float, float]],
    window_s: int = 60,
) -> float:
    """
    Z-score da velocidade direcional contra histórico.

    z > 2 = velocidade anormalmente alta contra a posição.

    Args:
        directional_velocity: Velocidade direcional atual
        velocity_history: Lista de (timestamp_s, dir_velocity)
        window_s: Janela do z-score

    Returns:
        Z-score (0.0 se histórico insuficiente)
    """
    if len(velocity_history) < 5:
        return 0.0

    cutoff = velocity_history[-1][0] - window_s
    recent = [v for ts, v in velocity_history if ts >= cutoff]

    if len(recent) < 5:
        return 0.0

    mean_v = sum(recent) / len(recent)
    variance = sum((v - mean_v) ** 2 for v in recent) / len(recent)
    std_v = math.sqrt(variance)

    if std_v < EPS:
        return 0.0

    return (directional_velocity - mean_v) / std_v
