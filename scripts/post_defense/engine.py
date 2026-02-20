"""
PostDefenseEngine — Orquestrador do sistema de defesa pós-entrada.

Uma instância por asset. Chamado a cada poll (1s) durante HOLDING.
Calcula TODOS os indicadores e grava TickSnapshot em JSONL.
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import PostDefenseConfig
from .types import PositionMeta, BookSnapshot, TickSnapshot
from .volatility import (
    calc_vol_short,
    calc_vol_long,
    calc_vol_ratio,
    calc_z_vol,
    calc_delta_vol_entry,
)
from .direction import (
    calc_velocity,
    calc_acceleration,
    calc_directional_velocity,
    calc_z_velocity,
)
from .book import (
    parse_book,
    calc_depth_shift,
    calc_liquidity_vacuum,
    calc_book_confirmed,
    calc_z_imbalance,
)
from .position import (
    calc_adverse_move,
    calc_distance_entry,
    calc_z_adverse,
)
from .time_phase import (
    calc_phase,
    calc_allow_reversal,
    calc_time_pressure,
)
from .scores import (
    calc_regime_shift_score,
    calc_reversal_score,
    calc_rpi,
    calc_rpi_threshold,
    calc_severity,
)


log = logging.getLogger("post_defense")


class PostDefenseEngine:
    """
    Engine de defesa pós-entrada para um asset.

    Uso:
        engine = PostDefenseEngine("sol", config)

        # No fill:
        engine.start_position(meta)

        # A cada poll (1s) durante HOLDING:
        snap = engine.update(ts, mid_price, time_left_s, book_json)
        # snap.severity → usar para decisão (futuro)

        # No fim do ciclo:
        engine.clear_position()
    """

    def __init__(self, asset: str, config: Optional[PostDefenseConfig] = None):
        self.asset = asset
        self.config = config or PostDefenseConfig()

        # ── Histories (deques com prune por idade) ───────────────
        max_p = self.config.max_price_history
        max_b = self.config.max_book_history

        self.price_history: deque[tuple[float, float]] = deque(maxlen=max_p)
        self.velocity_history: deque[tuple[float, float]] = deque(maxlen=max_p)
        self.dir_velocity_history: deque[tuple[float, float]] = deque(maxlen=max_p)
        self.vol_short_history: deque[tuple[float, float]] = deque(maxlen=max_p)
        self.imbalance_history: deque[tuple[float, float]] = deque(maxlen=max_b)
        self.rpi_history: deque[tuple[float, float]] = deque(maxlen=max_p)
        self.book_history: deque[tuple[float, BookSnapshot]] = deque(maxlen=max_b)

        # ── Posição ──────────────────────────────────────────────
        self.position: Optional[PositionMeta] = None

        # ── Logger JSONL ─────────────────────────────────────────
        self._log_file = None
        self._log_date: Optional[str] = None

    # ═══════════════════════════════════════════════════════════════
    # Posição
    # ═══════════════════════════════════════════════════════════════

    def start_position(self, meta: PositionMeta):
        """
        Chamado no FILL: salva snapshot de regime no momento da entrada.

        O PositionMeta deve conter vol_entry_short/long/z_vol
        calculados no instante do fill.
        """
        self.position = meta
        log.info(
            f"[{self.asset}] Position started: {meta.side} "
            f"@ {meta.entry_price} x{meta.position_shares}"
        )

    def clear_position(self):
        """Chamado no fim do ciclo ou saída."""
        self.position = None
        self._close_log()

    # ═══════════════════════════════════════════════════════════════
    # Snapshot de regime (para usar no start_position)
    # ═══════════════════════════════════════════════════════════════

    def snapshot_regime(self) -> tuple[float, float, float]:
        """
        Retorna (vol_short, vol_long, z_vol) atuais.

        Chamado no momento do fill para preencher PositionMeta.
        """
        prices = list(self.price_history)
        vol_s = calc_vol_short(prices, self.config.vol_short_window_s)
        vol_l = calc_vol_long(prices, self.config.vol_long_window_s)
        z_v = calc_z_vol(vol_s, list(self.vol_short_history), self.config.z_vol_window_s)
        return vol_s, vol_l, z_v

    # ═══════════════════════════════════════════════════════════════
    # Update principal (1Hz)
    # ═══════════════════════════════════════════════════════════════

    def update(
        self,
        ts: float,
        mid_price: float,
        time_left_s: int,
        book_json: Optional[dict] = None,
    ) -> TickSnapshot:
        """
        Chamado a cada poll (1s). Calcula TODOS os indicadores.

        Args:
            ts: Timestamp unix (time.time())
            mid_price: Midpoint do mercado Polymarket
            time_left_s: Segundos até expiração
            book_json: Resposta do CLOB /book (None se não disponível)

        Returns:
            TickSnapshot completo
        """
        cfg = self.config

        # ── 1. Acumula preço ─────────────────────────────────────
        self.price_history.append((ts, mid_price))
        prices = list(self.price_history)

        # ── 2. Tempo ─────────────────────────────────────────────
        phase = calc_phase(time_left_s, cfg.phase_early_s, cfg.phase_late_s)
        allow_reversal = calc_allow_reversal(time_left_s, cfg.t_min_reversal_s)

        # ── 3. Volatilidade ──────────────────────────────────────
        vol_short = calc_vol_short(prices, cfg.vol_short_window_s)
        vol_long = calc_vol_long(prices, cfg.vol_long_window_s)
        vol_ratio = calc_vol_ratio(vol_short, vol_long)

        self.vol_short_history.append((ts, vol_short))
        z_vol = calc_z_vol(vol_short, list(self.vol_short_history), cfg.z_vol_window_s)

        delta_vol_entry = None
        if self.position is not None:
            delta_vol_entry = calc_delta_vol_entry(vol_short, self.position.vol_entry_short)

        # ── 4. Direção ───────────────────────────────────────────
        velocity = calc_velocity(prices, cfg.velocity_window_s, cfg.velocity_smooth_n)
        self.velocity_history.append((ts, velocity))
        acceleration = calc_acceleration(list(self.velocity_history))

        directional_velocity = None
        if self.position is not None:
            directional_velocity = calc_directional_velocity(
                velocity, self.position.side
            )
            self.dir_velocity_history.append((ts, directional_velocity))

        z_velocity = 0.0
        if directional_velocity is not None and len(self.dir_velocity_history) >= 5:
            z_velocity = calc_z_velocity(
                directional_velocity,
                list(self.dir_velocity_history),
                cfg.z_velocity_window_s,
            )

        # ── 5. Book ──────────────────────────────────────────────
        book_snap: Optional[BookSnapshot] = None
        book_confirmed = False
        liquidity_vacuum = False
        z_imb = 0.0

        if book_json is not None:
            book_snap = parse_book(book_json)

        if book_snap is not None:
            self.book_history.append((ts, book_snap))
            self.imbalance_history.append((ts, book_snap.imbalance))

            side = self.position.side if self.position else "YES"

            book_confirmed = calc_book_confirmed(
                list(self.imbalance_history), side, cfg.book_confirm_s
            )
            liquidity_vacuum = calc_liquidity_vacuum(
                list(self.book_history), side,
                cfg.vacuum_threshold_pct, cfg.vacuum_window_s,
            )
            z_imb_raw = calc_z_imbalance(
                book_snap.imbalance,
                list(self.imbalance_history),
                cfg.z_imbalance_window_s,
            )
            # Converter z_imbalance para direcional (positivo = contra posição):
            # YES: imbalance negativo (sell pressure) é contra → inverter sinal
            # NO: imbalance positivo (buy pressure) é contra → manter sinal
            if side.upper() == "YES":
                z_imb = -z_imb_raw
            else:
                z_imb = z_imb_raw

        # ── 6. Posição ───────────────────────────────────────────
        has_position = self.position is not None
        adverse_move = None
        distance_entry = None
        z_adverse = None

        if self.position is not None:
            adverse_move = calc_adverse_move(
                mid_price, self.position.entry_price, self.position.side
            )
            distance_entry = calc_distance_entry(mid_price, self.position.entry_price)
            z_adverse = calc_z_adverse(
                adverse_move if adverse_move is not None else 0.0,
                vol_short,
            )

        # ── 7. Scores ────────────────────────────────────────────
        regime_shift = calc_regime_shift_score(vol_ratio, z_vol, delta_vol_entry)

        spread = book_snap.spread if book_snap else 0.0

        reversal_score = calc_reversal_score(
            regime_shift, z_velocity, z_imb, book_confirmed, spread,
        )

        rpi = calc_rpi(
            vol_ratio=vol_ratio,
            z_vol=z_vol,
            delta_vol_entry=delta_vol_entry,
            z_velocity=z_velocity,
            directional_velocity=directional_velocity,
            z_imbalance=z_imb,
            book_confirmed=book_confirmed,
            liquidity_vacuum=liquidity_vacuum,
            weights=cfg.rpi_weights,
        )

        self.rpi_history.append((ts, rpi))

        rpi_threshold = calc_rpi_threshold(
            list(self.rpi_history), cfg.rpi_window_s, cfg.rpi_k,
        )

        severity = calc_severity(rpi, rpi_threshold)

        # ── 8. Monta TickSnapshot ────────────────────────────────
        snap = TickSnapshot(
            ts_s=round(ts, 3),
            market_id=self.asset,
            mid_price=mid_price,
            time_left_s=time_left_s,
            phase=phase,
            allow_reversal=allow_reversal,
            vol_short=round(vol_short, 6),
            vol_long=round(vol_long, 6),
            vol_ratio=round(vol_ratio, 4),
            z_vol=round(z_vol, 4),
            delta_vol_entry=round(delta_vol_entry, 4) if delta_vol_entry is not None else None,
            velocity=round(velocity, 8),
            acceleration=round(acceleration, 8),
            directional_velocity=(
                round(directional_velocity, 8)
                if directional_velocity is not None else None
            ),
            z_velocity=round(z_velocity, 4),
            book=book_snap,
            book_confirmed=book_confirmed,
            liquidity_vacuum=liquidity_vacuum,
            z_imbalance=round(z_imb, 4),
            has_position=has_position,
            adverse_move=round(adverse_move, 4) if adverse_move is not None else None,
            distance_entry=round(distance_entry, 4) if distance_entry is not None else None,
            z_adverse=round(z_adverse, 4) if z_adverse is not None else None,
            regime_shift_score=regime_shift,
            reversal_score=reversal_score,
            rpi=rpi,
            rpi_threshold_dynamic=rpi_threshold,
            severity=severity,
        )

        # ── 9. Log JSONL ─────────────────────────────────────────
        self._log_tick(snap)

        return snap

    # ═══════════════════════════════════════════════════════════════
    # JSONL Logger
    # ═══════════════════════════════════════════════════════════════

    def _ensure_log_file(self):
        """Abre/rotaciona arquivo de log por data."""
        today = datetime.utcnow().strftime("%Y-%m-%d")

        if self._log_date == today and self._log_file is not None:
            return

        self._close_log()

        log_dir = Path(self.config.log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{self.config.log_prefix}_{today}.jsonl"
        path = log_dir / filename

        self._log_file = open(path, "a", encoding="utf-8")
        self._log_date = today

    def _log_tick(self, snap: TickSnapshot):
        """Grava uma linha JSONL."""
        try:
            self._ensure_log_file()
            line = json.dumps(snap.to_dict(), separators=(",", ":"))
            self._log_file.write(line + "\n")
            self._log_file.flush()
        except Exception as e:
            log.error(f"[{self.asset}] Log error: {e}")

    def _close_log(self):
        """Fecha arquivo de log."""
        if self._log_file is not None:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None
            self._log_date = None

    def __del__(self):
        self._close_log()
