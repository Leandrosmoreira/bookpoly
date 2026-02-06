import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class VolatilityConfig:
    # Binance API
    binance_base: str = os.getenv("BINANCE_FUTURES_BASE", "https://fapi.binance.com")
    request_timeout: float = float(os.getenv("BINANCE_REQUEST_TIMEOUT_S", "5.0"))
    max_retries: int = int(os.getenv("BINANCE_MAX_RETRIES", "3"))

    # Polling
    poll_hz: int = int(os.getenv("VOL_POLL_HZ", "1"))

    # Symbols
    symbols: list = field(default_factory=list)

    # Kline settings
    kline_interval: str = os.getenv("VOL_KLINE_INTERVAL", "1m")
    rv_window_short: int = int(os.getenv("VOL_RV_WINDOW_SHORT", "60"))  # 1 hour
    rv_window_long: int = int(os.getenv("VOL_RV_WINDOW_LONG", "360"))  # 6 hours
    atr_period: int = int(os.getenv("VOL_ATR_PERIOD", "14"))

    # Classification
    percentile_lookback_days: int = int(os.getenv("VOL_PERCENTILE_LOOKBACK_DAYS", "7"))

    # Output
    out_dir: str = os.getenv("VOL_OUT_DIR", "data/raw/volatility")

    # Backfill
    backfill_start: str = os.getenv("VOL_BACKFILL_START", "2026-02-01")
    backfill_batch_size: int = int(os.getenv("VOL_BACKFILL_BATCH_SIZE", "1500"))

    def __post_init__(self):
        if not self.symbols:
            raw = os.getenv("VOL_SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT")
            self.symbols = [s.strip().upper() for s in raw.split(",")]
