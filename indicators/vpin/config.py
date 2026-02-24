import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class VpinConfig:
    # Binance Futures WebSocket
    ws_base: str = os.getenv("VPIN_WS_BASE", "wss://fstream.binance.com")
    rest_base: str = os.getenv("VPIN_REST_BASE", "https://fapi.binance.com")
    request_timeout: float = float(os.getenv("VPIN_REQUEST_TIMEOUT_S", "5.0"))

    # Symbols (lowercase for WS streams)
    symbols: list = field(default_factory=list)

    # VPIN parameters
    bucket_volume: str = os.getenv("VPIN_BUCKET_VOLUME", "auto")
    num_buckets: int = int(os.getenv("VPIN_NUM_BUCKETS", "50"))
    warmup_klines: int = int(os.getenv("VPIN_WARMUP_KLINES", "60"))
    bucket_volume_pct: float = float(os.getenv("VPIN_BUCKET_VOLUME_PCT", "0.1"))

    # Emission
    emit_interval_s: float = float(os.getenv("VPIN_EMIT_INTERVAL_S", "1"))

    # Reconnect
    ws_reconnect_delay: float = float(os.getenv("VPIN_WS_RECONNECT_DELAY", "3"))
    ws_max_reconnect_delay: float = float(os.getenv("VPIN_WS_MAX_RECONNECT_DELAY", "60"))

    # Output
    out_dir: str = os.getenv("VPIN_OUT_DIR", "data/raw/vpin")

    def __post_init__(self):
        if not self.symbols:
            raw = os.getenv("VPIN_SYMBOLS", "btcusdt,ethusdt,solusdt,xrpusdt")
            self.symbols = [s.strip().lower() for s in raw.split(",")]
