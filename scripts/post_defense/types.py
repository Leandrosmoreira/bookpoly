"""
Estruturas de dados do sistema de defesa pós-entrada.

Dataclasses imutáveis que descrevem o estado a cada tick.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class PositionMeta:
    """Snapshot da posição no momento do fill."""

    market_id: str
    side: str                     # "YES" ou "NO"
    entry_price: float
    entry_time_s: float           # timestamp unix do fill
    position_shares: int
    vol_entry_short: float        # vol_short no momento do fill
    vol_entry_long: float         # vol_long no momento do fill
    z_vol_entry: float            # z_vol no momento do fill


@dataclass
class BookSnapshot:
    """Estado do order book num instante."""

    best_bid: float
    best_ask: float
    bid_qty: float                # Quantidade total top-N bids
    ask_qty: float                # Quantidade total top-N asks
    spread: float                 # best_ask - best_bid
    imbalance: float              # (bid_qty - ask_qty) / (bid_qty + ask_qty)


@dataclass
class TickSnapshot:
    """
    Estado completo a cada poll (1s). Uma linha JSONL.

    Contém TODOS os indicadores calculados naquele instante.
    """

    ts_s: float
    market_id: str

    # ── Preço ────────────────────────────────────────────────────
    mid_price: float

    # ── Tempo ────────────────────────────────────────────────────
    time_left_s: int
    phase: str                    # "early" | "mid" | "late"
    allow_reversal: bool

    # ── Volatilidade ─────────────────────────────────────────────
    vol_short: float
    vol_long: float
    vol_ratio: float
    z_vol: float
    delta_vol_entry: Optional[float]   # None se sem posição

    # ── Direção ──────────────────────────────────────────────────
    velocity: float
    acceleration: float
    directional_velocity: Optional[float]  # None se sem posição
    z_velocity: float

    # ── Book ─────────────────────────────────────────────────────
    book: Optional[BookSnapshot]       # None se não disponível
    book_confirmed: bool
    liquidity_vacuum: bool
    z_imbalance: float

    # ── Posição ──────────────────────────────────────────────────
    has_position: bool
    adverse_move: Optional[float]
    distance_entry: Optional[float]
    z_adverse: Optional[float]

    # ── Scores ───────────────────────────────────────────────────
    regime_shift_score: float
    reversal_score: float
    rpi: float
    rpi_raw: float = 0.0             # RPI antes do EMA (para debug)
    rpi_threshold_dynamic: float = 0.0
    severity: float = 0.0

    def to_dict(self) -> dict:
        """Serializa para JSON (converte BookSnapshot aninhado)."""
        d = asdict(self)
        return d
