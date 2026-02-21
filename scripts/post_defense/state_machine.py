"""
State machine de defesa pos-entrada.

5 fases: NORMAL -> ALERT -> DEFENSE -> PANIC -> EXIT
Transicoes baseadas em severity, tempo e adverse_move.
"""

from __future__ import annotations

from dataclasses import dataclass
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
    alert_ticks: int = 0                 # Ticks consecutivos com severity > 0
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
            tracker.phase = DefensePhase.ALERT
            tracker.phase_entered_ts = now_ts
            return DefensePhase.ALERT, f"severity={sev:.4f}"
        return DefensePhase.NORMAL, ""

    # ── ALERT ────────────────────────────────────────────────────
    if phase == DefensePhase.ALERT:
        if sev > 0:
            tracker.alert_ticks += 1
            # Confirmacao: N ticks consecutivos
            if tracker.alert_ticks >= config.alert_confirm_ticks and snap.allow_reversal:
                tracker.phase = DefensePhase.DEFENSE
                tracker.phase_entered_ts = now_ts
                return DefensePhase.DEFENSE, (
                    f"confirmed_{tracker.alert_ticks}ticks "
                    f"sev={sev:.4f} rpi={snap.rpi:.4f}"
                )
            return DefensePhase.ALERT, ""
        else:
            # Severity voltou a 0 — cooldown antes de voltar a NORMAL
            elapsed = now_ts - tracker.severity_zero_since if tracker.severity_zero_since > 0 else 0
            if elapsed >= config.alert_cooldown_s:
                tracker.alert_ticks = 0
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
