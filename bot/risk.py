"""
Risk management module.

Implements safety checks and circuit breakers.
"""

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from collections import deque

from config import BotConfig


@dataclass
class RiskLimits:
    """Risk limits configuration."""
    # Position limits
    max_open_positions: int = 3
    max_position_per_market: float = 10.0

    # Time limits
    min_time_between_trades_s: float = 10.0
    max_trades_per_hour: int = 20

    # Loss limits
    max_consecutive_losses: int = 5
    max_loss_per_trade_pct: float = 0.05  # 5% of bankroll

    # Volatility limits
    blocked_regimes: tuple = ("muito_alta",)
    max_volatility: float = 0.80  # 80% RV

    # Market conditions
    min_liquidity: float = 300.0
    max_spread_pct: float = 0.03  # 3%


class RiskManager:
    """
    Risk management and circuit breaker.

    Enforces risk limits and can halt trading if conditions are met.
    """

    def __init__(self, config: BotConfig, limits: RiskLimits | None = None):
        """
        Initialize risk manager.

        Args:
            config: Bot configuration
            limits: Risk limits (uses defaults if not provided)
        """
        self.config = config
        self.limits = limits or RiskLimits()

        # State tracking
        self.consecutive_losses = 0
        self.last_trade_time = 0.0
        self.hourly_trades: deque = deque(maxlen=100)  # Track trade times
        self.open_positions: set[str] = set()  # Market names with open positions

        # Circuit breaker
        self.trading_halted = False
        self.halt_reason: str | None = None
        self.halt_until: float | None = None

    def reset(self):
        """Reset risk manager state."""
        self.consecutive_losses = 0
        self.last_trade_time = 0.0
        self.hourly_trades.clear()
        self.open_positions.clear()
        self.trading_halted = False
        self.halt_reason = None
        self.halt_until = None

    def record_trade(self, market: str, won: bool):
        """
        Record a completed trade.

        Args:
            market: Market name
            won: Whether the trade was profitable
        """
        now = time.time()
        self.last_trade_time = now
        self.hourly_trades.append(now)

        if won:
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1

            # Check consecutive loss limit
            if self.consecutive_losses >= self.limits.max_consecutive_losses:
                self.halt_trading(
                    f"Consecutive losses: {self.consecutive_losses}",
                    duration_s=3600,  # Halt for 1 hour
                )

    def open_position(self, market: str):
        """Record opening a position."""
        self.open_positions.add(market)

    def close_position(self, market: str):
        """Record closing a position."""
        self.open_positions.discard(market)

    def halt_trading(self, reason: str, duration_s: float = 3600):
        """
        Halt trading for a specified duration.

        Args:
            reason: Reason for halting
            duration_s: Duration in seconds
        """
        self.trading_halted = True
        self.halt_reason = reason
        self.halt_until = time.time() + duration_s

    def check_halt(self) -> bool:
        """Check if trading halt has expired."""
        if not self.trading_halted:
            return False

        if self.halt_until and time.time() >= self.halt_until:
            self.trading_halted = False
            self.halt_reason = None
            self.halt_until = None
            return False

        return True

    def can_trade(
        self,
        market: str,
        volatility: float | None = None,
        regime: str | None = None,
        liquidity: float | None = None,
        spread_pct: float | None = None,
    ) -> tuple[bool, str]:
        """
        Check if a trade is allowed based on risk limits.

        Args:
            market: Market to trade
            volatility: Current RV (optional)
            regime: Volatility regime (optional)
            liquidity: Current liquidity (optional)
            spread_pct: Current spread percentage (optional)

        Returns:
            (can_trade, reason)
        """
        # Check circuit breaker
        if self.check_halt():
            return False, f"Trading halted: {self.halt_reason}"

        # Check open position limit
        if len(self.open_positions) >= self.limits.max_open_positions:
            return False, f"Max open positions: {len(self.open_positions)}"

        # Check if already have position in this market
        if market in self.open_positions:
            return False, f"Already have position in {market}"

        # Check time since last trade
        now = time.time()
        time_since_last = now - self.last_trade_time
        if time_since_last < self.limits.min_time_between_trades_s:
            return False, f"Too soon since last trade: {time_since_last:.1f}s"

        # Check hourly trade limit
        hour_ago = now - 3600
        recent_trades = sum(1 for t in self.hourly_trades if t > hour_ago)
        if recent_trades >= self.limits.max_trades_per_hour:
            return False, f"Hourly trade limit: {recent_trades}"

        # Check volatility regime
        if regime and regime in self.limits.blocked_regimes:
            return False, f"Blocked regime: {regime}"

        # Check volatility level
        if volatility is not None and volatility > self.limits.max_volatility:
            return False, f"High volatility: {volatility:.1%}"

        # Check liquidity
        if liquidity is not None and liquidity < self.limits.min_liquidity:
            return False, f"Low liquidity: ${liquidity:.0f}"

        # Check spread
        if spread_pct is not None and spread_pct > self.limits.max_spread_pct:
            return False, f"Wide spread: {spread_pct:.1%}"

        return True, "OK"

    def calculate_max_size(
        self,
        bankroll: float,
        entry_price: float,
    ) -> float:
        """
        Calculate maximum allowed position size.

        Args:
            bankroll: Current bankroll
            entry_price: Entry price

        Returns:
            Maximum shares allowed
        """
        # Max loss per trade
        max_loss = bankroll * self.limits.max_loss_per_trade_pct

        # Max shares based on loss limit
        max_shares_by_loss = max_loss / entry_price if entry_price > 0 else 0

        # Also respect position limit
        max_shares = min(max_shares_by_loss, self.limits.max_position_per_market)

        # And config limit
        max_shares = min(max_shares, self.config.max_position_size)

        return max_shares

    def get_status(self) -> dict:
        """Get current risk status."""
        now = time.time()
        hour_ago = now - 3600
        recent_trades = sum(1 for t in self.hourly_trades if t > hour_ago)

        return {
            "trading_halted": self.trading_halted,
            "halt_reason": self.halt_reason,
            "halt_remaining_s": max(0, self.halt_until - now) if self.halt_until else 0,
            "open_positions": len(self.open_positions),
            "consecutive_losses": self.consecutive_losses,
            "hourly_trades": recent_trades,
            "time_since_last_trade": now - self.last_trade_time if self.last_trade_time else None,
        }

    def format_status(self) -> str:
        """Format status for logging."""
        status = self.get_status()

        if status["trading_halted"]:
            return f"HALTED: {status['halt_reason']} ({status['halt_remaining_s']:.0f}s remaining)"

        return (
            f"Open: {status['open_positions']}/{self.limits.max_open_positions} | "
            f"Losses: {status['consecutive_losses']}/{self.limits.max_consecutive_losses} | "
            f"Hourly: {status['hourly_trades']}/{self.limits.max_trades_per_hour}"
        )
