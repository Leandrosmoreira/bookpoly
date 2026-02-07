"""
Bot configuration.

Load settings from environment variables.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env file
load_dotenv()


@dataclass
class BotConfig:
    """Bot configuration."""
    # API credentials
    api_key: str = ""
    api_secret: str = ""
    wallet_address: str = ""

    # Trading parameters
    max_position_size: float = 10.0  # Maximum shares per position
    min_position_size: float = 1.0   # Minimum shares per trade
    max_daily_trades: int = 50       # Maximum trades per day
    max_daily_loss: float = 50.0     # Stop trading if loss exceeds this

    # Risk parameters
    kelly_fraction: float = 0.25     # Fraction of Kelly criterion to use
    max_risk_per_trade: float = 0.02 # Maximum 2% of bankroll per trade

    # Execution parameters
    slippage_tolerance: float = 0.01  # 1% max slippage
    order_timeout_s: float = 5.0      # Cancel order after N seconds

    # Mode
    paper_trading: bool = True  # If True, don't execute real trades
    dry_run: bool = True        # If True, don't even send orders

    def __post_init__(self):
        """Load from environment variables."""
        self.api_key = os.getenv("POLYMARKET_API_KEY", "")
        self.api_secret = os.getenv("POLYMARKET_API_SECRET", "")
        self.wallet_address = os.getenv("POLYMARKET_WALLET", "")

        # Parse trading params
        self.max_position_size = float(os.getenv("BOT_MAX_POSITION", "10.0"))
        self.min_position_size = float(os.getenv("BOT_MIN_POSITION", "1.0"))
        self.max_daily_trades = int(os.getenv("BOT_MAX_DAILY_TRADES", "50"))
        self.max_daily_loss = float(os.getenv("BOT_MAX_DAILY_LOSS", "50.0"))

        # Parse risk params
        self.kelly_fraction = float(os.getenv("BOT_KELLY_FRACTION", "0.25"))
        self.max_risk_per_trade = float(os.getenv("BOT_MAX_RISK", "0.02"))

        # Parse mode
        self.paper_trading = os.getenv("BOT_PAPER_TRADING", "true").lower() == "true"
        self.dry_run = os.getenv("BOT_DRY_RUN", "true").lower() == "true"

    def validate(self) -> list[str]:
        """
        Validate configuration.

        Returns:
            List of error messages (empty if valid)
        """
        errors = []

        if not self.paper_trading and not self.dry_run:
            if not self.api_key:
                errors.append("POLYMARKET_API_KEY is required for live trading")
            if not self.api_secret:
                errors.append("POLYMARKET_API_SECRET is required for live trading")
            if not self.wallet_address:
                errors.append("POLYMARKET_WALLET is required for live trading")

        if self.max_position_size <= 0:
            errors.append("max_position_size must be positive")

        if self.kelly_fraction <= 0 or self.kelly_fraction > 1:
            errors.append("kelly_fraction must be between 0 and 1")

        if self.max_risk_per_trade <= 0 or self.max_risk_per_trade > 0.1:
            errors.append("max_risk_per_trade must be between 0 and 0.1 (10%)")

        return errors

    def __str__(self):
        mode = "DRY_RUN" if self.dry_run else ("PAPER" if self.paper_trading else "LIVE")
        return (
            f"BotConfig(mode={mode}, "
            f"max_pos={self.max_position_size}, "
            f"kelly={self.kelly_fraction}, "
            f"max_risk={self.max_risk_per_trade:.1%})"
        )
