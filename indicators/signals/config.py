import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class SignalConfig:
    """Configuration for trading signals and gates."""

    # === TIME GATE ===
    # Only trade in the last N seconds of the 15-min window
    time_window_start_s: int = int(os.getenv("SIGNAL_TIME_START_S", "660"))  # 11 min = últimos 4 min
    time_window_end_s: int = int(os.getenv("SIGNAL_TIME_END_S", "870"))  # 14:30 = não entrar nos últimos 30s
    window_duration_s: int = 900  # 15 minutes

    # === LIQUIDITY GATE ===
    # Minimum total depth (bid + ask) in shares
    min_depth: float = float(os.getenv("SIGNAL_MIN_DEPTH", "300"))

    # === SPREAD GATE ===
    # Maximum spread as percentage of mid price
    # Dados reais: mediana ~9.5%, então 10% permite maioria dos trades
    max_spread_pct: float = float(os.getenv("SIGNAL_MAX_SPREAD_PCT", "0.10"))  # 10%

    # === STABILITY GATE ===
    # Maximum volatility (annualized) from Binance
    # Dados reais: média ~111% (crypto é volátil!), então 150% permite mais entradas
    max_volatility: float = float(os.getenv("SIGNAL_MAX_VOL", "1.50"))  # 150%
    # Or use regime: block if "muito_alta"
    block_high_vol_regime: bool = True

    # === LATENCY GATE ===
    # Maximum acceptable latency in ms
    max_latency_ms: float = float(os.getenv("SIGNAL_MAX_LATENCY_MS", "500"))

    # === PROBABILITY ZONES ===
    # Underdog probability thresholds
    zone_danger_max: float = 0.02  # < 2% = danger
    zone_caution_max: float = 0.05  # 2-5% = caution
    zone_safe_max: float = 0.15  # 5-15% = safe, > 15% = neutral

    # === PERSISTENCE ===
    # Minimum seconds gates must be satisfied before entry
    min_persistence_s: float = float(os.getenv("SIGNAL_MIN_PERSISTENCE_S", "20"))

    # === OUTPUT ===
    out_dir: str = os.getenv("SIGNAL_OUT_DIR", "data/raw/signals")

    # === COINS ===
    coins: list = None

    def __post_init__(self):
        if self.coins is None:
            raw = os.getenv("SIGNAL_COINS", "btc")
            self.coins = [c.strip().lower() for c in raw.split(",")]
