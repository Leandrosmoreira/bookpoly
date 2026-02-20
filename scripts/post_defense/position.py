"""
Indicadores de contexto da posição.

Mede quanto o preço moveu contra a posição
e normaliza pelo regime de volatilidade atual.
"""

from __future__ import annotations

from typing import Optional


EPS = 1e-12


def calc_adverse_move(mid_price: float, entry_price: float, side: str) -> Optional[float]:
    """
    Movimento adverso do preço relativo à entrada.

    Positivo = preço moveu CONTRA a posição.
    Negativo = preço moveu A FAVOR.

    Para YES: queda é adversa → entry_price - mid_price
    Para NO:  subida é adversa → mid_price - entry_price

    Args:
        mid_price: Preço mid atual
        entry_price: Preço de entrada
        side: "YES" ou "NO"

    Returns:
        Movimento adverso (positivo = contra). None se side inválido.
    """
    side_upper = side.upper()
    if side_upper == "YES":
        return entry_price - mid_price
    elif side_upper == "NO":
        return mid_price - entry_price
    return None


def calc_distance_entry(mid_price: float, entry_price: float) -> float:
    """
    Distância absoluta entre preço atual e entrada.

    Args:
        mid_price: Preço mid atual
        entry_price: Preço de entrada

    Returns:
        |mid_price - entry_price|
    """
    return abs(mid_price - entry_price)


def calc_z_adverse(adverse_move: float, vol_short: float) -> Optional[float]:
    """
    Movimento adverso normalizado pela volatilidade atual.

    Quantos "desvios" de vol o preço se moveu contra.
    z_adverse > 2 = movimento adverso significativo para o regime atual.

    Args:
        adverse_move: Movimento adverso (output de calc_adverse_move)
        vol_short: Volatilidade de curto prazo atual

    Returns:
        Z-score adverso. None se vol muito baixa.
    """
    if vol_short < EPS:
        return None

    return adverse_move / vol_short
