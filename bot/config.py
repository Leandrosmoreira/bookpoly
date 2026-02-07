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
    initial_bankroll: float = 100.0  # Starting capital for testing
    max_position_size: float = 10.0  # Maximum $ per position
    min_position_size: float = 5.0   # Minimum $ per trade (Polymarket minimum)
    max_daily_trades: int = 20       # Maximum trades per day
    max_daily_loss: float = 25.0     # Stop trading if loss exceeds this (25% of bankroll)

    # Execution parameters
    slippage_tolerance: float = 0.02  # 2% max slippage
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
        self.initial_bankroll = float(os.getenv("BOT_BANKROLL", "100.0"))
        self.max_position_size = float(os.getenv("BOT_MAX_POSITION", "10.0"))
        self.min_position_size = float(os.getenv("BOT_MIN_POSITION", "5.0"))
        self.max_daily_trades = int(os.getenv("BOT_MAX_DAILY_TRADES", "20"))
        self.max_daily_loss = float(os.getenv("BOT_MAX_DAILY_LOSS", "25.0"))

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

        if self.initial_bankroll <= 0:
            errors.append("initial_bankroll must be positive")

        if self.min_position_size < 5.0:
            errors.append("min_position_size must be at least $5 (Polymarket minimum)")

        if self.max_position_size < self.min_position_size:
            errors.append("max_position_size must be >= min_position_size")

        return errors

    def __str__(self):
        mode = "DRY_RUN" if self.dry_run else ("PAPER" if self.paper_trading else "LIVE")
        return (
            f"BotConfig(mode={mode}, "
            f"bankroll=${self.initial_bankroll:.0f}, "
            f"lot=${self.min_position_size:.0f}-${self.max_position_size:.0f})"
        )
