import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    clob_base: str = os.getenv("CLOB_BASE_URL", "https://clob.polymarket.com")
    gamma_base: str = os.getenv("GAMMA_BASE_URL", "https://gamma-api.polymarket.com")
    poll_hz: int = int(os.getenv("POLL_HZ", "1"))
    depth_levels: int = int(os.getenv("DEPTH_LEVELS", "50"))
    request_timeout: float = float(os.getenv("REQUEST_TIMEOUT_S", "2.0"))
    max_retries: int = int(os.getenv("MAX_RETRIES", "2"))
    window: str = os.getenv("WINDOW", "15m")
    out_dir: str = os.getenv("OUT_DIR", "data/raw/books")
    coins: list = field(default_factory=list)
    # Moedas para gravar também no mercado 5min (ex.: COINS_5M=btc)
    coins_5m: list = field(default_factory=list)

    def __post_init__(self):
        if not self.coins:
            raw = os.getenv("COINS", "btc,eth,sol,xrp")
            self.coins = [c.strip().lower() for c in raw.split(",")]
        if not self.coins_5m:
            raw_5m = os.getenv("COINS_5M", "btc")
            self.coins_5m = [c.strip().lower() for c in raw_5m.split(",")] if raw_5m else []
