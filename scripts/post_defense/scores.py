"""
Scores compostos: regime_shift, reversal, RPI, severity.

RPI (Reversal Pressure Index) é o indicador central de decisão.
Combina 3 pilares: volatilidade, direção e book.
"""

from __future__ import annotations

import math
from typing import Optional


EPS = 1e-12


def _clamp01(v: float) -> float:
    """Clampa valor em [0.0, 1.0]."""
    return max(0.0, min(1.0, v))


def calc_regime_shift_score(
    vol_ratio: float,
    z_vol: float,
    delta_vol_entry: Optional[float],
) -> float:
    """
    Score de mudança de regime de volatilidade (0.0 a 1.0).

    Combina 3 sinais:
    - vol_ratio > 1 = vol crescendo
    - z_vol alto = vol anormal
    - delta_vol_entry > 1 = vol maior que na entrada

    Args:
        vol_ratio: vol_short / vol_long
        z_vol: Z-score da vol_short
        delta_vol_entry: Ratio vol_now / vol_entry (None se sem posição)

    Returns:
        Score [0.0, 1.0]. 0 = regime estável, 1 = regime mudou muito.
    """
    # vol_ratio: 0.5 → 0.0, 1.0 → 0.0, 2.0 → 0.5, 4.0 → 1.0
    ratio_score = _clamp01((vol_ratio - 1.0) / 3.0) if vol_ratio > 1.0 else 0.0

    # z_vol: 0 → 0.0, 1 → 0.25, 2 → 0.5, 4 → 1.0
    z_score = _clamp01(z_vol / 4.0) if z_vol > 0 else 0.0

    # delta_vol_entry: 1.0 → 0.0, 2.0 → 0.33, 4.0 → 1.0
    if delta_vol_entry is not None and delta_vol_entry > 1.0:
        delta_score = _clamp01((delta_vol_entry - 1.0) / 3.0)
    else:
        delta_score = 0.0

    # Pesos: z_vol tem mais peso (mais confiável)
    score = 0.30 * ratio_score + 0.45 * z_score + 0.25 * delta_score

    return round(_clamp01(score), 4)


def calc_reversal_score(
    regime_shift: float,
    z_velocity: float,
    z_imbalance: float,
    book_confirmed: bool,
    spread: float = 0.0,
    max_spread: float = 0.05,
) -> float:
    """
    Score intermediário de reversão (0.0 a 1.0).

    Combina regime + direção + book com penalidade por spread.

    Args:
        regime_shift: Score de regime_shift (0-1)
        z_velocity: Z-score da velocidade direcional
        z_imbalance: Z-score do imbalance
        book_confirmed: Se imbalance contra persistiu
        spread: Spread atual
        max_spread: Spread máximo para penalidade total

    Returns:
        Score [0.0, 1.0]
    """
    # z_velocity contra: 0 → 0.0, 1 → 0.25, 2 → 0.5, 4 → 1.0
    vel_score = _clamp01(z_velocity / 4.0) if z_velocity > 0 else 0.0

    # z_imbalance contra: mesma escala
    imb_score = _clamp01(abs(z_imbalance) / 4.0) if z_imbalance > 0 else 0.0

    # Bonus por book_confirmed (+0.15)
    book_bonus = 0.15 if book_confirmed else 0.0

    # Score bruto
    raw = 0.35 * regime_shift + 0.30 * vel_score + 0.20 * imb_score + book_bonus

    # Penalidade por spread alto (spread dificulta hedge)
    if max_spread > EPS and spread > 0:
        spread_penalty = _clamp01(spread / max_spread) * 0.15
        raw = max(0.0, raw - spread_penalty)

    return round(_clamp01(raw), 4)


def calc_rpi(
    vol_ratio: float,
    z_vol: float,
    delta_vol_entry: Optional[float],
    z_velocity: float,
    directional_velocity: Optional[float],
    z_imbalance: float,
    book_confirmed: bool,
    liquidity_vacuum: bool,
    weights: Optional[dict] = None,
) -> float:
    """
    RPI — Reversal Pressure Index.

    Score central do sistema. 3 pilares:
    1. vol_pressure: regime de vol contra
    2. dir_pressure: direção contra a posição
    3. book_pressure: order book contra

    Cada pilar só conta quando é "contra" (>= 0).

    Args:
        vol_ratio: vol_short / vol_long
        z_vol: Z-score da vol_short
        delta_vol_entry: Ratio vol_now / vol_entry
        z_velocity: Z-score da velocidade direcional
        directional_velocity: Velocidade direcional (positivo = contra)
        z_imbalance: Z-score do imbalance
        book_confirmed: Se imbalance contra persistiu
        liquidity_vacuum: Se vacuum detectado
        weights: Dict com pesos {"vol": 0.40, "dir": 0.35, "book": 0.25}

    Returns:
        RPI (float >= 0). Quanto maior, mais pressão de reversão.
    """
    if weights is None:
        weights = {"vol": 0.40, "dir": 0.35, "book": 0.25}

    # ── Pilar 1: Vol Pressure ────────────────────────────────────
    vol_components = []

    if vol_ratio > 1.0:
        vol_components.append(vol_ratio - 1.0)  # Excesso acima de 1

    if z_vol > 0:
        vol_components.append(z_vol * 0.5)  # Ponderado

    if delta_vol_entry is not None and delta_vol_entry > 1.0:
        vol_components.append((delta_vol_entry - 1.0) * 0.5)

    vol_pressure = sum(vol_components) if vol_components else 0.0

    # ── Pilar 2: Dir Pressure ────────────────────────────────────
    dir_components = []

    if z_velocity > 0:
        dir_components.append(z_velocity * 0.7)

    if directional_velocity is not None and directional_velocity > 0:
        # Normaliza: 0.001/s é forte para Polymarket
        dir_components.append(min(directional_velocity / 0.001, 3.0))

    dir_pressure = sum(dir_components) if dir_components else 0.0

    # ── Pilar 3: Book Pressure ───────────────────────────────────
    book_components = []

    if z_imbalance > 0:
        book_components.append(z_imbalance * 0.5)

    if book_confirmed:
        book_components.append(1.0)

    if liquidity_vacuum:
        book_components.append(1.5)  # Vacuum é sinal forte

    book_pressure = sum(book_components) if book_components else 0.0

    # ── Combina com pesos ────────────────────────────────────────
    rpi = (
        weights["vol"] * vol_pressure
        + weights["dir"] * dir_pressure
        + weights["book"] * book_pressure
    )

    return round(max(0.0, rpi), 4)


def calc_rpi_threshold(
    rpi_history: list[tuple[float, float]],
    window_s: int = 60,
    k: float = 1.5,
) -> float:
    """
    Threshold dinâmico para o RPI.

    threshold = mean(rpi_window) + k * std(rpi_window)

    Adapta-se ao mercado recente: mercado mais volátil → threshold mais alto.

    Args:
        rpi_history: Lista de (timestamp_s, rpi)
        window_s: Janela para cálculo
        k: Multiplicador sigma

    Returns:
        Threshold dinâmico (mínimo 0.5 para evitar falsos positivos)
    """
    MIN_THRESHOLD = 0.5

    if len(rpi_history) < 5:
        return MIN_THRESHOLD

    cutoff = rpi_history[-1][0] - window_s
    recent = [v for ts, v in rpi_history if ts >= cutoff]

    if len(recent) < 5:
        return MIN_THRESHOLD

    mean_rpi = sum(recent) / len(recent)
    variance = sum((v - mean_rpi) ** 2 for v in recent) / len(recent)
    std_rpi = math.sqrt(variance)

    threshold = mean_rpi + k * std_rpi

    return max(MIN_THRESHOLD, round(threshold, 4))


def calc_severity(rpi: float, threshold: float) -> float:
    """
    Quão acima do threshold o RPI está.

    0.0 = RPI abaixo do threshold (sem reversão).
    0.5 = RPI 50% acima do threshold.
    1.0 = RPI >= 2x o threshold (reversão severa).

    Escala linear clampada. Monotônica.

    Args:
        rpi: RPI atual
        threshold: Threshold dinâmico

    Returns:
        Severity [0.0, 1.0]
    """
    if threshold < EPS or rpi <= threshold:
        return 0.0

    # Quanto acima do threshold (como fração)
    excess = (rpi - threshold) / threshold

    # Clampa: excess de 1.0 (= 2x threshold) → severity 1.0
    return round(_clamp01(excess), 4)
