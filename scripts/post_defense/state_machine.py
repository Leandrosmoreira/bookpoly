"""
State machine de defesa pos-entrada.

5 fases: NORMAL -> ALERT -> DEFENSE -> PANIC -> EXIT
Transicoes baseadas em severity, tempo e adverse_move.

ALERT -> DEFENSE usa janela movel (2 de 5 ticks com severity > 0)
em vez de ticks consecutivos (que falhava porque severity e transiente).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .types import TickSnapshot
from .config import PostDefenseConfig


class DefensePhase(Enum):
    NORMAL = "NORMAL"       # Monitorando, sem acao
    ALERT = "ALERT"         # severity > 0, aguardando confirmacao
    DEFENSE = "DEFENSE"     # Hedge parcial colocado
    PANIC = "PANIC"         # Hedge maximo, situacao critica
    EXIT = "EXIT"           # Saindo da posicao


@dataclass
class DefenseStateTracker:
    """Estado persistente da state machine por asset."""

    phase: DefensePhase = DefensePhase.NORMAL
    phase_entered_ts: float = 0.0        # Quando entrou na fase atual
    alert_ticks: int = 0                 # Legacy (mantido para compatibilidade de log)
    severity_window: deque = field(default_factory=lambda: deque(maxlen=5))  # Janela movel de severity
    severity_zero_since: float = 0.0     # Desde quando severity == 0 (para cooldown)
    last_hedge_ts: float = 0.0           # Timestamp do ultimo hedge
    total_hedge_shares: int = 0          # Total de shares hedgeadas neste ciclo
    hedge_order_id: Optional[str] = None # Ultima ordem de hedge
    hedge_side: Optional[str] = None     # Lado do hedge ("YES" ou "NO")

    def reset(self):
        """Reset completo para novo ciclo."""
        self.phase = DefensePhase.NORMAL
        self.phase_entered_ts = 0.0
        self.alert_ticks = 0
        self.severity_window.clear()
        self.severity_zero_since = 0.0
        self.last_hedge_ts = 0.0
        self.total_hedge_shares = 0
        self.hedge_order_id = None
        self.hedge_side = None


def evaluate_transition(
    tracker: DefenseStateTracker,
    snap: TickSnapshot,
    config: PostDefenseConfig,
    now_ts: float,
) -> tuple[DefensePhase, str]:
    """
    Avalia transicao de fase baseada no TickSnapshot.

    Retorna (nova_fase, razao_da_transicao).
    Se nao houver transicao, retorna (fase_atual, "").
    """
    phase = tracker.phase
    sev = snap.severity

    # ── Track severity zero (para cooldowns) ─────────────────────
    if sev > 0:
        tracker.severity_zero_since = 0.0
    elif tracker.severity_zero_since == 0.0:
        tracker.severity_zero_since = now_ts

    # ── NORMAL ───────────────────────────────────────────────────
    if phase == DefensePhase.NORMAL:
        if sev > 0:
            tracker.alert_ticks = 1
            tracker.severity_window.clear()
            tracker.severity_window.append(True)
            tracker.phase = DefensePhase.ALERT
            tracker.phase_entered_ts = now_ts
            return DefensePhase.ALERT, f"severity={sev:.4f}"
        return DefensePhase.NORMAL, ""

    # ── ALERT (janela movel: escala se M de N ticks com severity > 0) ──
    if phase == DefensePhase.ALERT:
        # Registrar tick na janela movel
        tracker.severity_window.append(sev > 0)
        if sev > 0:
            tracker.alert_ticks += 1

        # Contar hits na janela
        hits = sum(1 for s in tracker.severity_window if s)
        window_size = config.alert_window_size
        min_hits = config.alert_min_hits

        # Confirmacao: M de N ticks com severity > 0
        if hits >= min_hits and snap.allow_reversal:
            tracker.phase = DefensePhase.DEFENSE
            tracker.phase_entered_ts = now_ts
            return DefensePhase.DEFENSE, (
                f"confirmed_{hits}of{len(tracker.severity_window)}ticks "
                f"sev={sev:.4f} rpi={snap.rpi:.4f}"
            )

        # Cooldown: voltar a NORMAL se NENHUM hit na janela E tempo suficiente
        if hits == 0:
            elapsed = now_ts - tracker.severity_zero_since if tracker.severity_zero_since > 0 else 0
            if elapsed >= config.alert_cooldown_s:
                tracker.alert_ticks = 0
                tracker.severity_window.clear()
                tracker.phase = DefensePhase.NORMAL
                tracker.phase_entered_ts = now_ts
                return DefensePhase.NORMAL, f"alert_cooldown_{elapsed:.0f}s"

        return DefensePhase.ALERT, ""

    # ── DEFENSE ──────────────────────────────────────────────────
    if phase == DefensePhase.DEFENSE:
        # Escalada para PANIC
        adverse = snap.adverse_move if snap.adverse_move is not None else 0.0
        if sev >= config.panic_threshold and adverse >= config.panic_adverse_min:
            tracker.phase = DefensePhase.PANIC
            tracker.phase_entered_ts = now_ts
            return DefensePhase.PANIC, (
                f"sev={sev:.4f}>=panic({config.panic_threshold}) "
                f"adverse={adverse:.4f}"
            )

        # Desescalada para NORMAL
        if sev == 0:
            elapsed = now_ts - tracker.severity_zero_since if tracker.severity_zero_since > 0 else 0
            if elapsed >= config.defense_exit_s:
                tracker.severity_window.clear()
                tracker.phase = DefensePhase.NORMAL
                tracker.phase_entered_ts = now_ts
                return DefensePhase.NORMAL, f"defense_exit_{elapsed:.0f}s"

        return DefensePhase.DEFENSE, ""

    # ── PANIC ────────────────────────────────────────────────────
    if phase == DefensePhase.PANIC:
        # Transicao para EXIT se nao pode mais reverter
        if not snap.allow_reversal or snap.time_left_s < 60:
            tracker.phase = DefensePhase.EXIT
            tracker.phase_entered_ts = now_ts
            return DefensePhase.EXIT, (
                f"no_time t_left={snap.time_left_s}s "
                f"allow_rev={snap.allow_reversal}"
            )

        # Desescalada para DEFENSE se severity caiu
        if sev < config.panic_threshold:
            tracker.phase = DefensePhase.DEFENSE
            tracker.phase_entered_ts = now_ts
            return DefensePhase.DEFENSE, f"panic_deescalate sev={sev:.4f}"

        return DefensePhase.PANIC, ""

    # ── EXIT ─────────────────────────────────────────────────────
    # EXIT nao tem transicao automatica, so reset no fim do ciclo
    return DefensePhase.EXIT, ""
