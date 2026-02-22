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
    # Moedas para gravar no mercado 1h (ex.: COINS_1H=btc,eth,sol,xrp)
    coins_1h: list = field(default_factory=list)
    # Moedas para gravar no mercado 4h (ex.: COINS_4H=btc,eth,sol,xrp)
    coins_4h: list = field(default_factory=list)
    # Moedas para gravar no mercado diário (ex.: COINS_1D=btc,eth,sol,xrp,hype)
    coins_1d: list = field(default_factory=list)

    def __post_init__(self):
        if not self.coins:
            raw = os.getenv("COINS", "btc,eth,sol,xrp")
            self.coins = [c.strip().lower() for c in raw.split(",")]
        if not self.coins_5m:
            raw_5m = os.getenv("COINS_5M", "btc")
            self.coins_5m = [c.strip().lower() for c in raw_5m.split(",")] if raw_5m else []
        if not self.coins_1h:
            raw_1h = os.getenv("COINS_1H", "")
            self.coins_1h = [c.strip().lower() for c in raw_1h.split(",")] if raw_1h else []
        if not self.coins_4h:
            raw_4h = os.getenv("COINS_4H", "")
            self.coins_4h = [c.strip().lower() for c in raw_4h.split(",")] if raw_4h else []
        if not self.coins_1d:
            raw_1d = os.getenv("COINS_1D", "")
            self.coins_1d = [c.strip().lower() for c in raw_1d.split(",")] if raw_1d else []
