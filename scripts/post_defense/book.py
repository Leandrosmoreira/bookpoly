"""
Indicadores de order book (imbalance, spread, depth, vacuum).

Fonte: CLOB /book?token_id=X (buscado no HOLDING a cada 1s).
Resposta: {"bids": [{"price":"0.93","size":"150"},...], "asks": [...]}
"""

from __future__ import annotations

import math
from typing import Optional

from .types import BookSnapshot


EPS = 1e-12


def parse_book(book_json: dict) -> Optional[BookSnapshot]:
    """
    Parseia resposta do CLOB /book para BookSnapshot.

    Args:
        book_json: Dict com "bids" e "asks", cada um lista de
                   {"price": "0.93", "size": "150"}

    Returns:
        BookSnapshot ou None se dados inválidos
    """
    try:
        bids = book_json.get("bids", [])
        asks = book_json.get("asks", [])

        if not bids and not asks:
            return None

        # Best bid/ask
        best_bid = 0.0
        best_ask = 1.0

        bid_qty = 0.0
        ask_qty = 0.0

        for b in bids:
            price = float(b["price"])
            size = float(b["size"])
            bid_qty += size
            if price > best_bid:
                best_bid = price

        for a in asks:
            price = float(a["price"])
            size = float(a["size"])
            ask_qty += size
            if price < best_ask:
                best_ask = price

        spread = best_ask - best_bid

        # Imbalance: positivo = mais bids (buy pressure)
        total = bid_qty + ask_qty
        if total > EPS:
            imbalance = (bid_qty - ask_qty) / total
        else:
            imbalance = 0.0

        return BookSnapshot(
            best_bid=best_bid,
            best_ask=best_ask,
            bid_qty=round(bid_qty, 2),
            ask_qty=round(ask_qty, 2),
            spread=round(spread, 4),
            imbalance=round(imbalance, 4),
        )

    except (KeyError, ValueError, TypeError):
        return None


def calc_depth_shift(
    book_history: list[tuple[float, BookSnapshot]],
    side: str,
    window_s: int = 10,
) -> float:
    """
    Calcula mudança de profundidade num lado do book.

    Detecta se liquidez está sendo retirada do lado que
    nos protege (sinal de reversão).

    Args:
        book_history: Lista de (timestamp_s, BookSnapshot)
        side: "YES" ou "NO" (lado da nossa posição)
        window_s: Janela para medir delta

    Returns:
        Delta percentual de profundidade. Negativo = liquidez sendo removida.
        0.0 se dados insuficientes.
    """
    if len(book_history) < 2:
        return 0.0

    cutoff = book_history[-1][0] - window_s
    recent = [(ts, snap) for ts, snap in book_history if ts >= cutoff]

    if len(recent) < 2:
        return 0.0

    # Para YES: monitorar bid_qty (bids nos protegem)
    # Para NO: monitorar ask_qty (asks nos protegem)
    side_upper = side.upper()

    first_snap = recent[0][1]
    last_snap = recent[-1][1]

    if side_upper == "YES":
        old_depth = first_snap.bid_qty
        new_depth = last_snap.bid_qty
    elif side_upper == "NO":
        old_depth = first_snap.ask_qty
        new_depth = last_snap.ask_qty
    else:
        return 0.0

    if old_depth < EPS:
        return 0.0

    return (new_depth - old_depth) / old_depth


def calc_liquidity_vacuum(
    book_history: list[tuple[float, BookSnapshot]],
    side: str,
    threshold_pct: float = 0.50,
    window_s: int = 10,
) -> bool:
    """
    Detecta vacuum de liquidez (queda abrupta de profundidade).

    Vacuum = queda > threshold_pct num dos lados do book,
    sinal de que market makers retiraram ordens.

    Args:
        book_history: Lista de (timestamp_s, BookSnapshot)
        side: "YES" ou "NO"
        threshold_pct: Queda percentual para considerar vacuum (0.50 = 50%)
        window_s: Janela para detectar vacuum

    Returns:
        True se vacuum detectado
    """
    depth_shift = calc_depth_shift(book_history, side, window_s)
    # depth_shift negativo = perdendo liquidez
    return depth_shift < -threshold_pct


def calc_book_confirmed(
    imbalance_history: list[tuple[float, float]],
    side: str,
    persist_s: float = 5.0,
) -> bool:
    """
    Verifica se imbalance contra a posição persiste por N segundos.

    "Confirmação" = o book está consistentemente contra nós,
    não é apenas um blip momentâneo.

    Args:
        imbalance_history: Lista de (timestamp_s, imbalance)
        side: "YES" ou "NO"
        persist_s: Segundos de persistência necessários

    Returns:
        True se imbalance adverso persistiu por persist_s
    """
    if len(imbalance_history) < 2:
        return False

    now = imbalance_history[-1][0]
    cutoff = now - persist_s

    recent = [(ts, imb) for ts, imb in imbalance_history if ts >= cutoff]

    if not recent:
        return False

    # Para YES: imbalance negativo (mais asks) é contra
    # Para NO:  imbalance positivo (mais bids) é contra
    side_upper = side.upper()

    if side_upper == "YES":
        # Todos recentes devem ter imbalance < 0 (sell pressure)
        return all(imb < 0 for _, imb in recent)
    elif side_upper == "NO":
        # Todos recentes devem ter imbalance > 0 (buy pressure)
        return all(imb > 0 for _, imb in recent)

    return False


def calc_z_imbalance(
    imbalance: float,
    imbalance_history: list[tuple[float, float]],
    window_s: int = 60,
) -> float:
    """
    Z-score do imbalance contra histórico recente.

    Detecta imbalance anormalmente forte.

    Args:
        imbalance: Valor atual de imbalance
        imbalance_history: Lista de (timestamp_s, imbalance)
        window_s: Janela do z-score

    Returns:
        Z-score (0.0 se histórico insuficiente)
    """
    if len(imbalance_history) < 5:
        return 0.0

    cutoff = imbalance_history[-1][0] - window_s
    recent = [v for ts, v in imbalance_history if ts >= cutoff]

    if len(recent) < 5:
        return 0.0

    mean_v = sum(recent) / len(recent)
    variance = sum((v - mean_v) ** 2 for v in recent) / len(recent)
    std_v = math.sqrt(variance)

    if std_v < EPS:
        return 0.0

    return (imbalance - mean_v) / std_v
