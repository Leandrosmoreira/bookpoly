"""
Configuration for Binance real-time data capture and reversal detection.
"""

import os
from dataclasses import dataclass


@dataclass
class BinanceRealtimeConfig:
    """Configuration for Binance WebSocket and reversal detection."""

    # === SYMBOLS ===
    symbols: tuple = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")

    # === WEBSOCKET ===
    ws_url: str = "wss://fstream.binance.com/stream"
    reconnect_delay: float = 5.0
    ping_interval: float = 30.0

    # === INDICATOR PERIODS ===
    rsi_period: int = 14  # RSI period (candles)
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    momentum_period: int = 5  # Price change over N candles
    volume_avg_period: int = 20

    # === REVERSAL THRESHOLDS ===
    # RSI extremes
    rsi_oversold: float = 30.0  # Below = possible reversal UP
    rsi_overbought: float = 70.0  # Above = possible reversal DOWN

    # Momentum threshold (% change that indicates strong move)
    momentum_strong: float = 0.003  # 0.3% move in 5 candles

    # Volume spike threshold
    volume_spike_threshold: float = 2.0  # 2x average volume

    # Trade imbalance threshold
    imbalance_strong: float = 0.6  # 60% one-sided

    # === REVERSAL SCORE THRESHOLDS ===
    reversal_alert: float = 0.50  # Score > 0.50 = alert
    reversal_block: float = 0.70  # Score > 0.70 = block entry

    # === SCORE WEIGHTS ===
    weight_rsi: float = 0.20
    weight_macd: float = 0.20
    weight_momentum: float = 0.25  # Momentum é crítico para sua estratégia
    weight_volume: float = 0.15
    weight_imbalance: float = 0.20

    # === DATA STORAGE ===
    out_dir: str = os.getenv("BINANCE_RT_OUT_DIR", "data/raw/binance_realtime")
    buffer_size: int = 50  # Flush every N records
    flush_interval: float = 5.0  # Or every N seconds

    # === CANDLE BUFFER ===
    max_candles: int = 100  # Keep last 100 candles in memory


# Global config instance
config = BinanceRealtimeConfig()
