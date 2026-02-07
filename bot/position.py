"""
Position and size management.

Uses Fixed Risk sizing (simpler and more predictable than Kelly).
"""

from dataclasses import dataclass
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

    Uses Fixed Risk: always bet the minimum lot size ($5).
    Simple and predictable for testing.
    """

    def __init__(self, config: BotConfig, initial_bankroll: float = 100.0):
        """
        Initialize position manager.

        Args:
            config: Bot configuration
            initial_bankroll: Starting capital (default $100 for testing)
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

    def calculate_position_size(
        self,
        entry_price: float,
        score: float,
        confidence: str,
    ) -> float:
        """
        Calculate position size for a trade.

        Uses FIXED SIZE strategy:
        - Always use minimum lot size ($5)
        - Simple and predictable for testing

        Args:
            entry_price: Price to enter at
            score: Signal score (0-1) - not used in fixed sizing
            confidence: Confidence level - not used in fixed sizing

        Returns:
            Number of shares to trade
        """
        # Fixed size: always use minimum position ($5)
        # This is the Polymarket minimum lot
        fixed_dollar_amount = self.config.min_position_size  # $5

        # Check if we have enough bankroll
        if self.current_bankroll < fixed_dollar_amount:
            return 0.0

        # Convert dollars to shares
        # If entry_price is $0.85, then $5 buys ~5.88 shares
        shares = fixed_dollar_amount / entry_price if entry_price > 0 else 0

        return round(shares, 2)

    def calculate_position_size_percentage(
        self,
        entry_price: float,
        risk_pct: float = 0.05,
    ) -> float:
        """
        Calculate position size based on percentage of bankroll.

        Alternative method: risk X% of bankroll per trade.

        Args:
            entry_price: Price to enter at
            risk_pct: Percentage of bankroll to risk (default 5%)

        Returns:
            Number of shares to trade
        """
        # Calculate dollar amount to risk
        dollar_amount = self.current_bankroll * risk_pct

        # Respect minimum lot size
        dollar_amount = max(dollar_amount, self.config.min_position_size)

        # Check if we have enough
        if self.current_bankroll < dollar_amount:
            return 0.0

        # Convert to shares
        shares = dollar_amount / entry_price if entry_price > 0 else 0

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

        # Check bankroll - need at least minimum lot
        if self.current_bankroll < self.config.min_position_size:
            return False, f"Bankroll too low (${self.current_bankroll:.2f} < ${self.config.min_position_size})"

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

        # Calculate cost
        cost = size * entry_price

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
        # If we bet on UP and outcome is UP, we win
        # If we bet on DOWN and outcome is DOWN, we win
        won = (trade.side == outcome)

        if won:
            # Win: we get $1 per share, minus what we paid
            trade.pnl = trade.size * (1.0 - trade.entry_price)
        else:
            # Loss: we lose what we paid
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

        win_rate = stats.wins / stats.trades * 100 if stats.trades > 0 else 0

        return (
            f"Trades: {stats.trades} | "
            f"W/L: {stats.wins}/{stats.losses} ({win_rate:.0f}%) | "
            f"P&L: ${stats.pnl:+.2f} | "
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
