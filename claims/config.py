"""
Configuration for Claim Sweeper module.
"""
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class ClaimConfig:
    """Configuration for the Claim Sweeper."""

    # === TIMING ===
    poll_seconds: int = int(os.getenv("CLAIM_POLL_SECONDS", "120"))  # 2 minutes
    jitter_seconds: int = int(os.getenv("CLAIM_JITTER_SECONDS", "10"))  # Random 0-10s
    wait_after_resolution_s: int = int(os.getenv("CLAIM_WAIT_AFTER_RESOLUTION", "900"))  # 15 min

    # === LIMITS ===
    max_per_cycle: int = int(os.getenv("CLAIM_MAX_PER_CYCLE", "5"))
    max_retries: int = int(os.getenv("CLAIM_MAX_RETRIES", "5"))
    backoff_base: int = int(os.getenv("CLAIM_BACKOFF_BASE", "10"))  # Exponential backoff

    # === SELL PRICE (workaround) ===
    # Max price accepted by Polymarket API is 0.99
    # We lose $0.01 per share (1% fee) but this is the only way to claim programmatically
    sell_price: float = float(os.getenv("CLAIM_SELL_PRICE", "0.99"))

    # === POLYMARKET API ===
    clob_base_url: str = os.getenv("CLOB_BASE_URL", "https://clob.polymarket.com")
    gamma_base_url: str = os.getenv("GAMMA_BASE_URL", "https://gamma-api.polymarket.com")
    api_key: str = os.getenv("POLYMARKET_API_KEY", "")
    api_secret: str = os.getenv("POLYMARKET_API_SECRET", "")
    funder: str = os.getenv("POLYMARKET_FUNDER", "")  # Wallet address

    # === POLYGON (optional, for balance checks) ===
    polygon_rpc: str = os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com")
    chain_id: int = 137  # Polygon mainnet

    # === MODES ===
    enabled: bool = os.getenv("CLAIM_ENABLED", "true").lower() == "true"
    dry_run: bool = os.getenv("CLAIM_DRY_RUN", "true").lower() == "true"

    # === PATHS ===
    db_path: str = os.getenv("CLAIM_DB_PATH", "data/claims.db")
    lock_path: str = os.getenv("CLAIM_LOCK_PATH", "data/claim.lock")
    log_dir: str = os.getenv("CLAIM_LOG_DIR", "logs/claims")

    # === MARKETS ===
    # Only claim from these market slugs (e.g., "btc-15m", "eth-15m")
    # Empty list = claim from all markets
    market_slugs: list = field(default_factory=list)

    def __post_init__(self):
        # Parse market slugs from env
        raw_slugs = os.getenv("CLAIM_MARKET_SLUGS", "")
        if raw_slugs:
            self.market_slugs = [s.strip() for s in raw_slugs.split(",")]

    def is_configured(self) -> bool:
        """Check if API credentials are configured."""
        return bool(self.api_key and self.api_secret and self.funder)

    def validate(self) -> list[str]:
        """Validate configuration, return list of errors."""
        errors = []

        if not self.enabled:
            return ["Claim sweeper is disabled (CLAIM_ENABLED=false)"]

        if not self.api_key:
            errors.append("POLYMARKET_API_KEY not set")
        if not self.api_secret:
            errors.append("POLYMARKET_API_SECRET not set")
        if not self.funder:
            errors.append("POLYMARKET_FUNDER not set")

        if self.sell_price > 0.99:
            errors.append(f"sell_price {self.sell_price} > 0.99 (API max)")
        if self.sell_price < 0.90:
            errors.append(f"sell_price {self.sell_price} < 0.90 (too much loss)")

        return errors
