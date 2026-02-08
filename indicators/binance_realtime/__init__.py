"""
Binance Real-Time Module - Detecção de Reversão.

Usa WebSocket da Binance para detectar reversões de preço em tempo real,
permitindo bloquear entradas quando o mercado está revertendo.
"""

from .indicators import calc_rsi, calc_macd, calc_momentum, calc_volume_spike
from .reversal_detector import ReversalDetector, ReversalSignal

__all__ = [
    "calc_rsi",
    "calc_macd",
    "calc_momentum",
    "calc_volume_spike",
    "ReversalDetector",
    "ReversalSignal",
]
