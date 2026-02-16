"""
Guardrails PRO — Filtro de entrada inteligente para bot_15min.py

Previne losses causados por pumps tardios, squeezes, e instabilidade
de preço em mercados 15 minutos da Polymarket.

4 sinais → risk score unificado → BLOCK / CAUTION / ALLOW

Usa APENAS midpoint (já buscado pelo bot a cada 1s, zero custo extra).

Exemplo de uso:
    gr = GuardrailsPro("eth")
    gr.update(ts, yes_price, no_price)       # a cada poll (1s)
    decision = gr.evaluate("YES", ts)         # antes de place_order
    if decision.action == GuardrailAction.BLOCK:
        continue  # pula este ciclo
"""

from __future__ import annotations

import os
import sys
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

# Reuso de normalização do scorer existente
try:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from indicators.signals.scorer import normalize
except ImportError:
    # Fallback se scorer não disponível
    def normalize(value: float, min_val: float, max_val: float, clip: bool = True) -> float:
        if max_val == min_val:
            return 0.5
        result = (value - min_val) / (max_val - min_val)
        if clip:
            result = max(0.0, min(1.0, result))
        return result


# ──────────────────────────────────────────────
#  Config (todos os thresholds tunable via .env)
# ──────────────────────────────────────────────

@dataclass
class GuardrailConfig:
    """Configuração do Guardrails PRO.  Segue padrão de DefenseConfig (defense.py)."""

    # Signal 1: Rapid Pump
    pump_window_s: int   = int(os.getenv("GR_PUMP_WINDOW_S", "60"))
    pump_threshold: float = float(os.getenv("GR_PUMP_THRESHOLD", "0.15"))
    pump_weight: float    = float(os.getenv("GR_PUMP_WEIGHT", "0.30"))

    # Signal 2: Stability (range na janela)
    stability_window_s: int    = int(os.getenv("GR_STABILITY_WINDOW_S", "30"))
    stability_threshold: float = float(os.getenv("GR_STABILITY_THRESHOLD", "0.03"))
    stability_weight: float    = float(os.getenv("GR_STABILITY_WEIGHT", "0.25"))

    # Signal 3: Time-in-band
    band_min: float            = float(os.getenv("GR_BAND_MIN", "0.93"))
    band_max: float            = float(os.getenv("GR_BAND_MAX", "0.98"))
    time_in_band_full_s: float = float(os.getenv("GR_TIME_IN_BAND_FULL_S", "40"))
    time_in_band_weight: float = float(os.getenv("GR_TIME_IN_BAND_WEIGHT", "0.30"))

    # Signal 4: Momentum Direction
    momentum_window_s: int     = int(os.getenv("GR_MOMENTUM_WINDOW_S", "30"))
    momentum_threshold: float  = float(os.getenv("GR_MOMENTUM_THRESHOLD", "0.03"))
    momentum_weight: float     = float(os.getenv("GR_MOMENTUM_WEIGHT", "0.15"))

    # Decisão
    block_threshold: float   = float(os.getenv("GR_BLOCK_THRESHOLD", "0.60"))
    caution_threshold: float = float(os.getenv("GR_CAUTION_THRESHOLD", "0.40"))

    # Buffer de histórico
    history_max_s: int = 180  # cobre toda a janela de entrada (240→60 = 180s)

    # Enable/disable
    enabled: bool = os.getenv("GR_ENABLED", "true").lower() == "true"


# ──────────────────────────────────────────────
#  Enum + Decision dataclass
# ──────────────────────────────────────────────

class GuardrailAction(Enum):
    ALLOW   = "ALLOW"
    CAUTION = "CAUTION"
    BLOCK   = "BLOCK"


@dataclass
class GuardrailDecision:
    """Resultado da avaliação.  Segue padrão de DefenseResult (defense.py)."""
    action: GuardrailAction
    risk_score: float           # 0.0 → 1.0
    pump_score: float           # 0.0 → 1.0
    stability_score: float      # 0.0 → 1.0
    time_in_band_score: float   # 0.0 → 1.0
    momentum_score: float       # 0.0 → 1.0
    time_in_band_s: float       # segundos contínuos na faixa
    reason: str                 # motivo legível


# ──────────────────────────────────────────────
#  Price History (sliding window)
# ──────────────────────────────────────────────

class PriceHistory:
    """Deque de (ts, price) com prune automático por idade.
    Segue padrão de DefenseState.imbalance_history."""

    __slots__ = ("_data", "_max_age_s")

    def __init__(self, max_age_s: int = 180):
        self._data: deque[tuple[float, float]] = deque()
        self._max_age_s = max_age_s

    def append(self, ts: float, price: float) -> None:
        self._data.append((ts, price))
        self._prune(ts)

    def _prune(self, now: float) -> None:
        cutoff = now - self._max_age_s
        while self._data and self._data[0][0] < cutoff:
            self._data.popleft()

    def price_at_offset(self, now: float, offset_s: int) -> Optional[float]:
        """Preço mais próximo de (now - offset_s).  Tolerância: 3s."""
        target = now - offset_s
        best: Optional[float] = None
        best_dist = float("inf")
        for ts, p in self._data:
            dist = abs(ts - target)
            if dist < best_dist:
                best_dist = dist
                best = p
        return best if best_dist <= 3.0 else None

    def range_in_window(self, now: float, window_s: int) -> Optional[tuple[float, float]]:
        """(min, max) dos preços nos últimos window_s segundos."""
        cutoff = now - window_s
        prices = [p for ts, p in self._data if ts >= cutoff]
        if not prices:
            return None
        return (min(prices), max(prices))

    def samples_in_window(self, now: float, window_s: int) -> int:
        cutoff = now - window_s
        return sum(1 for ts, _ in self._data if ts >= cutoff)

    @property
    def latest(self) -> Optional[float]:
        return self._data[-1][1] if self._data else None

    def clear(self) -> None:
        self._data.clear()


# ──────────────────────────────────────────────
#  GuardrailsPro — classe principal
# ──────────────────────────────────────────────

class GuardrailsPro:
    """Filtro de entrada inteligente.  Uma instância por asset."""

    def __init__(self, asset: str, config: Optional[GuardrailConfig] = None):
        self.asset = asset
        self.config = config or GuardrailConfig()
        self.yes_history = PriceHistory(max_age_s=self.config.history_max_s)
        self.no_history  = PriceHistory(max_age_s=self.config.history_max_s)
        self._yes_band_entry_ts: Optional[float] = None
        self._no_band_entry_ts: Optional[float] = None

    # ── update (chamado a cada poll, 1s) ──

    def update(self, ts: float, yes_price: float, no_price: float) -> None:
        """Armazena preços e rastreia entrada na faixa.  Custo: O(1)."""
        self.yes_history.append(ts, yes_price)
        self.no_history.append(ts, no_price)

        cfg = self.config

        # Track YES in-band
        if cfg.band_min <= yes_price <= cfg.band_max:
            if self._yes_band_entry_ts is None:
                self._yes_band_entry_ts = ts
        else:
            self._yes_band_entry_ts = None

        # Track NO in-band
        if cfg.band_min <= no_price <= cfg.band_max:
            if self._no_band_entry_ts is None:
                self._no_band_entry_ts = ts
        else:
            self._no_band_entry_ts = None

    # ── evaluate (chamado antes de place_order) ──

    def evaluate(self, candidate_side: str, now: float) -> GuardrailDecision:
        """Avalia se a entrada candidata é segura.

        Args:
            candidate_side: "YES" ou "NO"
            now: timestamp atual (time.time())

        Returns:
            GuardrailDecision com action e todos os scores
        """
        cfg = self.config

        # Desabilitado → ALLOW sempre
        if not cfg.enabled:
            return self._allow("guardrails_disabled")

        history = self.yes_history if candidate_side == "YES" else self.no_history
        band_ts = self._yes_band_entry_ts if candidate_side == "YES" else self._no_band_entry_ts

        # Dados insuficientes → CAUTION (permitir, mas avisar)
        if history.samples_in_window(now, 10) < 5:
            return GuardrailDecision(
                action=GuardrailAction.CAUTION,
                risk_score=0.50,
                pump_score=0.0, stability_score=0.0,
                time_in_band_score=1.0, momentum_score=0.0,
                time_in_band_s=0.0, reason="insufficient_data",
            )

        # ── Signal 1: Rapid Pump ──
        pump = self._pump(history, now)

        # ── Signal 2: Stability ──
        stability = self._stability(history, now)

        # ── Signal 3: Time-in-Band ──
        tib_s = (now - band_ts) if band_ts is not None else 0.0
        tib = self._time_in_band(tib_s)

        # ── Signal 4: Momentum Direction ──
        momentum = self._momentum(history, now)

        # ── Risk Score ──
        risk = (
            cfg.pump_weight      * pump
            + cfg.stability_weight * stability
            + cfg.time_in_band_weight * tib
            + cfg.momentum_weight * momentum
        )
        risk = max(0.0, min(1.0, risk))

        # ── Decisão ──
        if risk >= cfg.block_threshold:
            action = GuardrailAction.BLOCK
            reason = self._reason(pump, stability, tib_s, momentum)
        elif risk >= cfg.caution_threshold:
            action = GuardrailAction.CAUTION
            reason = "moderate_risk"
        else:
            action = GuardrailAction.ALLOW
            reason = "ok"

        return GuardrailDecision(
            action=action,
            risk_score=round(risk, 3),
            pump_score=round(pump, 3),
            stability_score=round(stability, 3),
            time_in_band_score=round(tib, 3),
            momentum_score=round(momentum, 3),
            time_in_band_s=round(tib_s, 1),
            reason=reason,
        )

    # ── reset (novo ciclo) ──

    def reset(self) -> None:
        """Limpa histórico para novo ciclo de 15min."""
        self.yes_history.clear()
        self.no_history.clear()
        self._yes_band_entry_ts = None
        self._no_band_entry_ts = None

    # ──────────────────────────────────────
    #  Sinais privados
    # ──────────────────────────────────────

    def _pump(self, h: PriceHistory, now: float) -> float:
        """Rapid Pump: quanto o preço subiu nos últimos N segundos."""
        p_now = h.latest
        p_ago = h.price_at_offset(now, self.config.pump_window_s)
        if p_now is None or p_ago is None:
            return 0.0
        delta = p_now - p_ago
        if delta <= 0:
            return 0.0
        return normalize(delta, 0.0, self.config.pump_threshold)

    def _stability(self, h: PriceHistory, now: float) -> float:
        """Stability: range alto na janela = instável."""
        r = h.range_in_window(now, self.config.stability_window_s)
        if r is None:
            return 0.0
        price_range = r[1] - r[0]
        return normalize(price_range, 0.0, self.config.stability_threshold)

    def _time_in_band(self, seconds_in_band: float) -> float:
        """Time-in-band: recém-entrou = risco alto (1.0), estável = baixo (0.0)."""
        full = self.config.time_in_band_full_s
        if seconds_in_band >= full:
            return 0.0
        return max(0.0, 1.0 - seconds_in_band / full)

    def _momentum(self, h: PriceHistory, now: float) -> float:
        """Momentum: preço caindo no lado candidato = risco."""
        p_now = h.latest
        p_ago = h.price_at_offset(now, self.config.momentum_window_s)
        if p_now is None or p_ago is None:
            return 0.0
        delta = p_now - p_ago
        if delta >= 0:
            return 0.0  # momentum confirma entrada
        return normalize(abs(delta), 0.0, self.config.momentum_threshold)

    # ──────────────────────────────────────
    #  Helpers
    # ──────────────────────────────────────

    def _allow(self, reason: str) -> GuardrailDecision:
        return GuardrailDecision(
            action=GuardrailAction.ALLOW,
            risk_score=0.0,
            pump_score=0.0, stability_score=0.0,
            time_in_band_score=0.0, momentum_score=0.0,
            time_in_band_s=999.0, reason=reason,
        )

    @staticmethod
    def _reason(pump: float, stability: float, tib_s: float, momentum: float) -> str:
        parts: list[str] = []
        if pump > 0.5:
            parts.append(f"pump={pump:.2f}")
        if stability > 0.5:
            parts.append(f"unstable={stability:.2f}")
        if tib_s < 20:
            parts.append(f"band_{tib_s:.0f}s")
        if momentum > 0.5:
            parts.append(f"mom_against={momentum:.2f}")
        return "BLOCK:" + "+".join(parts) if parts else "BLOCK:combined"
