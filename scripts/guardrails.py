"""
Guardrails PRO — Filtro de entrada inteligente para bot_15min.py

Previne losses causados por pumps tardios, squeezes, e instabilidade
de preco em mercados 15 minutos da Polymarket.

4 sinais → risk score unificado → BLOCK / CAUTION / ALLOW

Thresholds DINAMICOS: cada sinal calcula seus limiares a partir do
proprio mercado recente (rolling stats do PriceHistory).
Zero dependencia externa, zero custo de API.

Usa APENAS midpoint (ja buscado pelo bot a cada 1s, zero custo extra).

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

# Reuso de normalizacao do scorer existente
try:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from indicators.signals.scorer import normalize
except ImportError:
    # Fallback se scorer nao disponivel
    def normalize(value: float, min_val: float, max_val: float, clip: bool = True) -> float:
        if max_val == min_val:
            return 0.5
        result = (value - min_val) / (max_val - min_val)
        if clip:
            result = max(0.0, min(1.0, result))
        return result


# ──────────────────────────────────────────────
#  Config (thresholds dinamicos via .env)
# ──────────────────────────────────────────────

@dataclass
class GuardrailConfig:
    """Configuracao do Guardrails PRO.  Segue padrao de DefenseConfig (defense.py).

    Thresholds dinamicos: pump e stability calculam seus limiares a partir
    do mercado recente (rolling stats do PriceHistory).
    Os params abaixo controlam a *sensibilidade* do calculo, nao valores fixos.
    """

    # Signal 1: Rapid Pump — threshold dinamico (sigma-based)
    pump_window_s: int        = int(os.getenv("GR_PUMP_WINDOW_S", "60"))
    pump_sigma: float         = float(os.getenv("GR_PUMP_SIGMA", "2.0"))
    pump_threshold_min: float = float(os.getenv("GR_PUMP_THRESHOLD_MIN", "0.03"))
    pump_threshold_max: float = float(os.getenv("GR_PUMP_THRESHOLD_MAX", "0.25"))
    pump_weight: float        = float(os.getenv("GR_PUMP_WEIGHT", "0.30"))

    # Signal 2: Stability — whipsaw dinamico (spike vs baseline)
    stability_window_s: int       = int(os.getenv("GR_STABILITY_WINDOW_S", "30"))
    stability_spike_factor: float = float(os.getenv("GR_STABILITY_SPIKE_FACTOR", "2.5"))
    stability_weight: float       = float(os.getenv("GR_STABILITY_WEIGHT", "0.25"))

    # Signal 3: Time-in-band
    band_min: float            = float(os.getenv("GR_BAND_MIN", "0.93"))
    band_max: float            = float(os.getenv("GR_BAND_MAX", "0.98"))
    time_in_band_full_s: float = float(os.getenv("GR_TIME_IN_BAND_FULL_S", "40"))
    time_in_band_weight: float = float(os.getenv("GR_TIME_IN_BAND_WEIGHT", "0.30"))

    # Signal 4: Momentum Direction — threshold dinamico (sigma-based)
    momentum_window_s: int        = int(os.getenv("GR_MOMENTUM_WINDOW_S", "30"))
    momentum_sigma: float         = float(os.getenv("GR_MOMENTUM_SIGMA", "2.0"))
    momentum_threshold_min: float = float(os.getenv("GR_MOMENTUM_THRESHOLD_MIN", "0.02"))
    momentum_threshold_max: float = float(os.getenv("GR_MOMENTUM_THRESHOLD_MAX", "0.15"))
    momentum_weight: float        = float(os.getenv("GR_MOMENTUM_WEIGHT", "0.15"))

    # Decisao
    block_threshold: float   = float(os.getenv("GR_BLOCK_THRESHOLD", "0.60"))
    caution_threshold: float = float(os.getenv("GR_CAUTION_THRESHOLD", "0.40"))

    # Buffer de historico
    history_max_s: int = 180  # cobre toda a janela de entrada (240->60 = 180s)

    # Reset: quantos segundos preservar no novo ciclo
    reset_preserve_s: int = int(os.getenv("GR_RESET_PRESERVE_S", "60"))

    # Dados minimos para avaliar (amostras nos ultimos N segundos)
    min_samples: int = int(os.getenv("GR_MIN_SAMPLES", "3"))
    min_samples_window_s: int = int(os.getenv("GR_MIN_SAMPLES_WINDOW_S", "10"))

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
    """Resultado da avaliacao.  Segue padrao de DefenseResult (defense.py)."""
    action: GuardrailAction
    risk_score: float           # 0.0 -> 1.0
    pump_score: float           # 0.0 -> 1.0
    stability_score: float      # 0.0 -> 1.0
    time_in_band_score: float   # 0.0 -> 1.0
    momentum_score: float       # 0.0 -> 1.0
    time_in_band_s: float       # segundos continuos na faixa
    reason: str                 # motivo legivel
    pump_threshold: float = 0.0       # threshold dinamico calculado para pump
    momentum_threshold: float = 0.0   # threshold dinamico calculado para momentum


# ──────────────────────────────────────────────
#  Price History (sliding window)
# ──────────────────────────────────────────────

class PriceHistory:
    """Deque de (ts, price) com prune automatico por idade.
    Segue padrao de DefenseState.imbalance_history."""

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
        """Preco mais proximo de (now - offset_s).  Tolerancia: 3s."""
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
        """(min, max) dos precos nos ultimos window_s segundos."""
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
    """Filtro de entrada inteligente.  Uma instancia por asset."""

    def __init__(self, asset: str, config: Optional[GuardrailConfig] = None):
        self.asset = asset
        self.config = config or GuardrailConfig()
        self.yes_history = PriceHistory(max_age_s=self.config.history_max_s)
        self.no_history  = PriceHistory(max_age_s=self.config.history_max_s)
        self._yes_band_entry_ts: Optional[float] = None
        self._no_band_entry_ts: Optional[float] = None

    # ── update (chamado a cada poll, 1s) ──

    def update(self, ts: float, yes_price: float, no_price: float) -> None:
        """Armazena precos e rastreia entrada na faixa.  Custo: O(1)."""
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
        """Avalia se a entrada candidata e segura.

        Args:
            candidate_side: "YES" ou "NO"
            now: timestamp atual (time.time())

        Returns:
            GuardrailDecision com action e todos os scores
        """
        cfg = self.config

        # Desabilitado -> ALLOW sempre
        if not cfg.enabled:
            return self._allow("guardrails_disabled")

        history = self.yes_history if candidate_side == "YES" else self.no_history
        band_ts = self._yes_band_entry_ts if candidate_side == "YES" else self._no_band_entry_ts

        # FIX 3: Dados insuficientes -> BLOCK (nao CAUTION)
        if history.samples_in_window(now, cfg.min_samples_window_s) < cfg.min_samples:
            return GuardrailDecision(
                action=GuardrailAction.BLOCK,
                risk_score=1.0,
                pump_score=0.0, stability_score=0.0,
                time_in_band_score=1.0, momentum_score=0.0,
                time_in_band_s=0.0,
                reason="BLOCK:insufficient_data",
            )

        # ── Signal 1: Rapid Pump ──
        pump, pump_thr = self._pump(history, now)

        # ── Signal 2: Stability ──
        stability = self._stability(history, now)

        # ── Signal 3: Time-in-Band ──
        tib_s = (now - band_ts) if band_ts is not None else 0.0
        tib = self._time_in_band(tib_s)

        # ── Signal 4: Momentum Direction ──
        momentum, mom_thr = self._momentum(history, now)

        # ── Risk Score ──
        risk = (
            cfg.pump_weight      * pump
            + cfg.stability_weight * stability
            + cfg.time_in_band_weight * tib
            + cfg.momentum_weight * momentum
        )
        risk = max(0.0, min(1.0, risk))

        # ── Decisao ──
        # Hard block: pump extremo + mercado instavel (pump trap protection)
        if pump >= 0.60 and stability <= 0.30:
            action = GuardrailAction.BLOCK
            reason = f"BLOCK:pump_extreme({pump:.2f})+unstable({stability:.2f})"
        elif risk >= cfg.block_threshold:
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
            pump_threshold=pump_thr,
            momentum_threshold=mom_thr,
        )

    # ── reset (novo ciclo) ──

    def reset(self) -> None:
        """Novo ciclo: preserva historico recente (ultimos reset_preserve_s).

        FIX 4: Em vez de apagar TUDO, manter os ultimos N segundos.
        O mercado nao reseta a cada 15min — o guardrail tambem nao deve.
        """
        preserve_s = self.config.reset_preserve_s

        if self.yes_history._data:
            now = self.yes_history._data[-1][0]
            cutoff = now - preserve_s
            self.yes_history._data = deque(
                (ts, p) for ts, p in self.yes_history._data if ts >= cutoff
            )
            self.no_history._data = deque(
                (ts, p) for ts, p in self.no_history._data if ts >= cutoff
            )
        # else: historico vazio, nada a preservar

        # Band entry: preservar se ainda valido
        # (nao resetar — o preco pode estar na faixa continuamente)

    # ──────────────────────────────────────
    #  Sinais privados (thresholds dinamicos)
    # ──────────────────────────────────────

    def _pump(self, h: PriceHistory, now: float) -> tuple[float, float]:
        """Rapid Pump: detecta movimento anormalmente rapido vs baseline recente.

        FIX 1: Threshold dinamico (media + K*std dos deltas recentes)
        + fator de uniformidade (desconto para movimentos graduais).

        Returns: (score, dynamic_threshold)
        """
        cfg = self.config
        p_now = h.latest
        p_ago = h.price_at_offset(now, cfg.pump_window_s)
        if p_now is None or p_ago is None:
            return 0.0, 0.0
        delta = p_now - p_ago
        if delta <= 0:
            return 0.0, 0.0

        # --- Threshold dinamico ---
        # Calcular baseline de deltas rolling (ultimos 120s, cada ponto vs pump_window_s atras)
        prices = [(ts, p) for ts, p in h._data if ts >= now - 120]
        deltas: list[float] = []
        for i, (ts_i, p_i) in enumerate(prices):
            target_ts = ts_i - cfg.pump_window_s
            for ts_j, p_j in prices:
                if abs(ts_j - target_ts) <= 3.0:
                    deltas.append(abs(p_i - p_j))
                    break

        if len(deltas) >= 5:
            mean_delta = sum(deltas) / len(deltas)
            variance = sum((d - mean_delta) ** 2 for d in deltas) / len(deltas)
            std_delta = variance ** 0.5
            dynamic_threshold = mean_delta + cfg.pump_sigma * std_delta
            dynamic_threshold = max(cfg.pump_threshold_min,
                                   min(cfg.pump_threshold_max, dynamic_threshold))
        else:
            # Cold start: usar floor conservador
            dynamic_threshold = cfg.pump_threshold_min

        # --- Uniformidade ---
        # Movimento gradual (std baixo) → desconto de 50%
        # Movimento erratico (std alto) → sem desconto
        window_prices = [p for ts, p in h._data if ts >= now - cfg.pump_window_s]
        uniformity_factor = 1.0
        if len(window_prices) >= 5:
            returns = [window_prices[i] - window_prices[i - 1]
                       for i in range(1, len(window_prices))]
            mean_ret = sum(returns) / len(returns)
            var_ret = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
            std_ret = var_ret ** 0.5
            # std esperado se o movimento fosse perfeitamente linear
            expected_std = abs(delta) / max(1, len(window_prices))
            if std_ret < expected_std * 1.5:
                uniformity_factor = 0.5

        score = normalize(delta, 0.0, dynamic_threshold) * uniformity_factor
        return score, round(dynamic_threshold, 4)

    def _stability(self, h: PriceHistory, now: float) -> float:
        """Whipsaw: muitas mudancas de direcao = instavel.

        FIX 2: Combina dois componentes:
        - Absoluto: taxa de whipsaw alta por si so e perigosa (mercado choppy)
        - Relativo: spike de whipsaw vs baseline recente (deterioracao subita)
        Score final = max(absoluto, relativo) — o pior caso prevalece.
        """
        cfg = self.config
        cutoff = now - cfg.stability_window_s
        prices = [p for ts, p in h._data if ts >= cutoff]
        if len(prices) < 5:
            return 0.0

        # Contar mudancas de direcao na janela curta
        direction_changes = 0
        for i in range(2, len(prices)):
            d_prev = prices[i - 1] - prices[i - 2]
            d_curr = prices[i] - prices[i - 1]
            if d_prev * d_curr < 0:  # mudou de direcao
                direction_changes += 1

        current_rate = direction_changes / max(1, len(prices) - 2)

        # --- Componente 1: Absoluto ---
        # Taxa de whipsaw alta por si so = instavel
        # 0% mudancas = 0.0, 50%+ mudancas = 1.0
        # (50% = metade dos ticks muda de direcao, mercado muito choppy)
        absolute_score = normalize(current_rate, 0.0, 0.50)

        # --- Componente 2: Relativo (spike vs baseline) ---
        relative_score = 0.0
        long_cutoff = now - 120
        long_prices = [p for ts, p in h._data if ts >= long_cutoff]
        if len(long_prices) >= 10:
            long_changes = 0
            for i in range(2, len(long_prices)):
                d_prev = long_prices[i - 1] - long_prices[i - 2]
                d_curr = long_prices[i] - long_prices[i - 1]
                if d_prev * d_curr < 0:
                    long_changes += 1

            baseline_rate = long_changes / max(1, len(long_prices) - 2)
            if baseline_rate < 0.01:
                baseline_rate = 0.01
            spike_threshold = baseline_rate * cfg.stability_spike_factor
            if current_rate > baseline_rate:
                relative_score = normalize(current_rate, baseline_rate, spike_threshold)

        # Score final: o pior caso prevalece
        return max(absolute_score, relative_score)

    def _time_in_band(self, seconds_in_band: float) -> float:
        """Time-in-band: recem-entrou = risco alto (1.0), estavel = baixo (0.0)."""
        full = self.config.time_in_band_full_s
        if seconds_in_band >= full:
            return 0.0
        return max(0.0, 1.0 - seconds_in_band / full)

    def _momentum(self, h: PriceHistory, now: float) -> tuple[float, float]:
        """Momentum: preco caindo no lado candidato = risco.

        Threshold dinamico: media + K*std dos deltas negativos recentes.

        Returns: (score, dynamic_threshold)
        """
        cfg = self.config
        p_now = h.latest
        p_ago = h.price_at_offset(now, cfg.momentum_window_s)
        if p_now is None or p_ago is None:
            return 0.0, 0.0
        delta = p_now - p_ago
        if delta >= 0:
            return 0.0, 0.0  # momentum confirma entrada

        abs_delta = abs(delta)

        # --- Threshold dinamico ---
        prices = [(ts, p) for ts, p in h._data if ts >= now - 120]
        neg_deltas: list[float] = []
        for i, (ts_i, p_i) in enumerate(prices):
            target_ts = ts_i - cfg.momentum_window_s
            for ts_j, p_j in prices:
                if abs(ts_j - target_ts) <= 3.0:
                    d = p_i - p_j
                    if d < 0:
                        neg_deltas.append(abs(d))
                    break

        if len(neg_deltas) >= 3:
            mean_nd = sum(neg_deltas) / len(neg_deltas)
            variance = sum((d - mean_nd) ** 2 for d in neg_deltas) / len(neg_deltas)
            std_nd = variance ** 0.5
            dynamic_threshold = mean_nd + cfg.momentum_sigma * std_nd
            dynamic_threshold = max(cfg.momentum_threshold_min,
                                   min(cfg.momentum_threshold_max, dynamic_threshold))
        else:
            dynamic_threshold = cfg.momentum_threshold_min

        score = normalize(abs_delta, 0.0, dynamic_threshold)
        return score, round(dynamic_threshold, 4)

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
