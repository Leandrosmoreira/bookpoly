"""
Position and size management.

Handles position sizing using Kelly criterion and risk limits.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from config import BotConfig


@dataclass
class TradeRecord:
    """Record of a trade."""
    timestamp: int
    market: str
    token_id: str
    side: str
    size: float
    entry_price: float
    exit_price: float | None = None
    pnl: float | None = None
    status: str = "open"  # open, closed, expired


@dataclass
class DailyStats:
    """Daily trading statistics."""
    date: str
    trades: int = 0
    wins: int = 0
    losses: int = 0
    pnl: float = 0.0
    max_drawdown: float = 0.0


class PositionManager:
    """
    Manages position sizing and tracks P&L.

    Uses Kelly criterion for optimal sizing with configurable fraction.
    """

    def __init__(self, config: BotConfig, initial_bankroll: float = 1000.0):
        """
        Initialize position manager.

        Args:
            config: Bot configuration
            initial_bankroll: Starting capital
        """
        self.config = config
        self.initial_bankroll = initial_bankroll
        self.current_bankroll = initial_bankroll

        # Open positions by token_id
        self.positions: dict[str, TradeRecord] = {}

        # Trade history
        self.trades: list[TradeRecord] = []

        # Daily stats
        self.daily_stats: dict[str, DailyStats] = {}
        self._current_date: str = ""

    def _get_today(self) -> str:
        """Get current date string."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _ensure_daily_stats(self):
        """Ensure we have stats for today."""
        today = self._get_today()
        if today != self._current_date:
            self._current_date = today
            if today not in self.daily_stats:
                self.daily_stats[today] = DailyStats(date=today)

    def calculate_kelly_size(
        self,
        win_prob: float,
        win_payout: float,
        loss_amount: float,
    ) -> float:
        """
        Calculate optimal position size using Kelly criterion.

        Kelly formula: f* = (bp - q) / b
        where:
            f* = fraction of bankroll to bet
            b = odds received on the bet (win_payout / loss_amount)
            p = probability of winning
            q = probability of losing (1 - p)

        Args:
            win_prob: Probability of winning
            win_payout: Amount won if successful
            loss_amount: Amount lost if unsuccessful

        Returns:
            Optimal fraction of bankroll to bet
        """
        if loss_amount == 0:
            return 0.0

        b = win_payout / loss_amount
        p = win_prob
        q = 1 - p

        kelly = (b * p - q) / b

        # Apply Kelly fraction (e.g., 0.25 = quarter Kelly)
        kelly *= self.config.kelly_fraction

        # Clamp to reasonable range
        kelly = max(0, min(kelly, self.config.max_risk_per_trade))

        return kelly

    def calculate_position_size(
        self,
        entry_price: float,
        score: float,
        confidence: str,
    ) -> float:
        """
        Calculate position size for a trade.

        Args:
            entry_price: Price to enter at
            score: Signal score (0-1)
            confidence: Confidence level (high, medium, low)

        Returns:
            Number of shares to trade
        """
        # Estimate win probability from score
        # Score of 0.5 = 50% win rate, 1.0 = ~70% win rate
        win_prob = 0.5 + (score - 0.5) * 0.4  # Maps 0.5-1.0 to 0.5-0.7

        # Calculate potential payout
        win_payout = 1.0 - entry_price  # If we win, we get $1 - entry_price
        loss_amount = entry_price  # If we lose, we lose entry_price

        # Get Kelly fraction
        kelly = self.calculate_kelly_size(win_prob, win_payout, loss_amount)

        # Calculate dollar amount
        dollar_amount = self.current_bankroll * kelly

        # Convert to shares (at entry_price per share)
        shares = dollar_amount / entry_price if entry_price > 0 else 0

        # Apply confidence multiplier
        if confidence == "high":
            shares *= 1.0
        elif confidence == "medium":
            shares *= 0.7
        else:  # low
            shares *= 0.4

        # Clamp to limits
        shares = max(self.config.min_position_size, min(shares, self.config.max_position_size))

        return round(shares, 2)

    def can_trade(self) -> tuple[bool, str]:
        """
        Check if we can place a new trade.

        Returns:
            (can_trade, reason)
        """
        self._ensure_daily_stats()
        today = self._current_date
        stats = self.daily_stats[today]

        # Check daily trade limit
        if stats.trades >= self.config.max_daily_trades:
            return False, f"Daily trade limit reached ({stats.trades})"

        # Check daily loss limit
        if stats.pnl <= -self.config.max_daily_loss:
            return False, f"Daily loss limit reached (${stats.pnl:.2f})"

        # Check bankroll
        if self.current_bankroll <= 0:
            return False, "Bankroll depleted"

        return True, "OK"

    def open_position(
        self,
        market: str,
        token_id: str,
        side: str,
        size: float,
        entry_price: float,
    ) -> TradeRecord:
        """
        Record opening a new position.

        Args:
            market: Market name
            token_id: Token ID
            side: UP or DOWN
            size: Number of shares
            entry_price: Entry price

        Returns:
            TradeRecord
        """
        self._ensure_daily_stats()

        trade = TradeRecord(
            timestamp=int(datetime.now(timezone.utc).timestamp() * 1000),
            market=market,
            token_id=token_id,
            side=side,
            size=size,
            entry_price=entry_price,
        )

        self.positions[token_id] = trade
        self.trades.append(trade)

        # Update daily stats
        self.daily_stats[self._current_date].trades += 1

        return trade

    def close_position(
        self,
        token_id: str,
        exit_price: float,
        outcome: str,
    ) -> TradeRecord | None:
        """
        Close an open position.

        Args:
            token_id: Token ID
            exit_price: Exit price (usually 1.0 if won, 0.0 if lost)
            outcome: UP or DOWN (actual result)

        Returns:
            Updated TradeRecord or None if not found
        """
        if token_id not in self.positions:
            return None

        trade = self.positions.pop(token_id)
        trade.exit_price = exit_price
        trade.status = "closed"

        # Calculate P&L
        won = (trade.side == outcome)
        if won:
            trade.pnl = trade.size * (1.0 - trade.entry_price)
        else:
            trade.pnl = -trade.size * trade.entry_price

        # Update bankroll
        self.current_bankroll += trade.pnl

        # Update daily stats
        self._ensure_daily_stats()
        stats = self.daily_stats[self._current_date]
        stats.pnl += trade.pnl
        if won:
            stats.wins += 1
        else:
            stats.losses += 1

        return trade

    def expire_position(self, token_id: str) -> TradeRecord | None:
        """
        Mark a position as expired (window ended without clear outcome).

        Args:
            token_id: Token ID

        Returns:
            Updated TradeRecord or None
        """
        if token_id not in self.positions:
            return None

        trade = self.positions.pop(token_id)
        trade.status = "expired"
        trade.pnl = 0.0  # Assume no loss on expiry

        return trade

    def get_open_positions(self) -> list[TradeRecord]:
        """Get all open positions."""
        return list(self.positions.values())

    def get_daily_summary(self) -> str:
        """Get summary of today's trading."""
        self._ensure_daily_stats()
        stats = self.daily_stats[self._current_date]

        return (
            f"Date: {stats.date} | "
            f"Trades: {stats.trades} | "
            f"Wins: {stats.wins} | "
            f"Losses: {stats.losses} | "
            f"P&L: ${stats.pnl:.2f} | "
            f"Bankroll: ${self.current_bankroll:.2f}"
        )

    def get_stats(self) -> dict:
        """Get overall statistics."""
        total_trades = len([t for t in self.trades if t.status == "closed"])
        total_wins = len([t for t in self.trades if t.status == "closed" and t.pnl and t.pnl > 0])
        total_pnl = sum(t.pnl or 0 for t in self.trades if t.status == "closed")

        return {
            "total_trades": total_trades,
            "total_wins": total_wins,
            "win_rate": total_wins / total_trades if total_trades > 0 else 0,
            "total_pnl": total_pnl,
            "current_bankroll": self.current_bankroll,
            "return_pct": (self.current_bankroll - self.initial_bankroll) / self.initial_bankroll * 100,
        }
