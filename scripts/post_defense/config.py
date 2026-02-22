"""
Configuração do sistema de defesa pós-entrada.

Todos os parâmetros são configuráveis via .env com prefixo PD_.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class PostDefenseConfig:
    """Parâmetros do sistema de defesa pós-ordem."""

    # ── Volatilidade ─────────────────────────────────────────────
    vol_short_window_s: int = int(os.getenv("PD_VOL_SHORT_WINDOW", "10"))
    vol_long_window_s: int = int(os.getenv("PD_VOL_LONG_WINDOW", "60"))
    z_vol_window_s: int = int(os.getenv("PD_Z_VOL_WINDOW", "120"))

    # ── Direção ──────────────────────────────────────────────────
    velocity_window_s: int = int(os.getenv("PD_VELOCITY_WINDOW", "5"))
    velocity_smooth_n: int = int(os.getenv("PD_VELOCITY_SMOOTH_N", "3"))
    z_velocity_window_s: int = int(os.getenv("PD_Z_VELOCITY_WINDOW", "60"))

    # ── Book ─────────────────────────────────────────────────────
    book_confirm_s: float = float(os.getenv("PD_BOOK_CONFIRM_S", "5.0"))
    vacuum_threshold_pct: float = float(os.getenv("PD_VACUUM_THRESHOLD", "0.50"))
    vacuum_window_s: int = int(os.getenv("PD_VACUUM_WINDOW", "10"))
    z_imbalance_window_s: int = int(os.getenv("PD_Z_IMBALANCE_WINDOW", "60"))

    # ── Tempo ────────────────────────────────────────────────────
    t_min_reversal_s: int = int(os.getenv("PD_T_MIN_REVERSAL", "240"))
    phase_early_s: int = int(os.getenv("PD_PHASE_EARLY", "360"))
    phase_late_s: int = int(os.getenv("PD_PHASE_LATE", "180"))

    # ── RPI (Reversal Pressure Index) ────────────────────────────
    rpi_window_s: int = int(os.getenv("PD_RPI_WINDOW", "60"))
    rpi_k: float = float(os.getenv("PD_RPI_K", "1.5"))
    rpi_weights: dict = field(default_factory=lambda: {
        "vol": float(os.getenv("PD_RPI_W_VOL", "0.40")),
        "dir": float(os.getenv("PD_RPI_W_DIR", "0.35")),
        "book": float(os.getenv("PD_RPI_W_BOOK", "0.25")),
    })

    # ── Hedge sizing ─────────────────────────────────────────────
    min_hedge: float = float(os.getenv("PD_MIN_HEDGE", "0.20"))
    max_hedge: float = float(os.getenv("PD_MAX_HEDGE", "0.80"))
    min_shares: int = int(os.getenv("PD_MIN_SHARES", "5"))

    # ── State Machine ──────────────────────────────────────────────
    alert_confirm_ticks: int = int(os.getenv("PD_ALERT_CONFIRM_TICKS", "3"))  # Legacy (nao usado com janela movel)
    alert_window_size: int = int(os.getenv("PD_ALERT_WINDOW_SIZE", "5"))      # Tamanho da janela movel de severity
    alert_min_hits: int = int(os.getenv("PD_ALERT_MIN_HITS", "2"))            # Min ticks com severity>0 na janela para escalar
    alert_cooldown_s: float = float(os.getenv("PD_ALERT_COOLDOWN_S", "5.0"))
    panic_threshold: float = float(os.getenv("PD_PANIC_THRESHOLD", "0.70"))
    panic_adverse_min: float = float(os.getenv("PD_PANIC_ADVERSE_MIN", "0.02"))
    defense_exit_s: float = float(os.getenv("PD_DEFENSE_EXIT_S", "10.0"))

    # ── Hedge Execution ────────────────────────────────────────────
    hedge_cooldown_s: float = float(os.getenv("PD_HEDGE_COOLDOWN_S", "10.0"))
    hedge_panic_markup: float = float(os.getenv("PD_HEDGE_PANIC_MARKUP", "0.01"))

    # ── Logging ──────────────────────────────────────────────────
    log_dir: str = os.getenv("PD_LOG_DIR", "logs")
    log_prefix: str = os.getenv("PD_LOG_PREFIX", "post_defense")

    # ── On/Off ───────────────────────────────────────────────────
    enabled: bool = os.getenv("PD_ENABLED", "1") == "1"

    # ── Histories (max amostras retidas) ─────────────────────────
    max_price_history: int = int(os.getenv("PD_MAX_PRICE_HISTORY", "300"))
    max_book_history: int = int(os.getenv("PD_MAX_BOOK_HISTORY", "120"))
