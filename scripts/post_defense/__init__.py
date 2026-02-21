"""
Post-Defense: Sistema de defesa pos-entrada para bot_15min.

Indicadores de reversao + state machine + hedge execution.
"""

from .config import PostDefenseConfig
from .types import PositionMeta, BookSnapshot, TickSnapshot
from .engine import PostDefenseEngine
from .state_machine import DefensePhase, DefenseStateTracker
from .decision import DefenseDecision, evaluate_defense
from .hedge import calc_hedge_shares, calc_hedge_price, get_opposite_token

__all__ = [
    "PostDefenseConfig",
    "PositionMeta",
    "BookSnapshot",
    "TickSnapshot",
    "PostDefenseEngine",
    "DefensePhase",
    "DefenseStateTracker",
    "DefenseDecision",
    "evaluate_defense",
    "calc_hedge_shares",
    "calc_hedge_price",
    "get_opposite_token",
]
