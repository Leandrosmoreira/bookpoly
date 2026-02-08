"""
Position and size management.

Uses Fixed Risk sizing (simpler and more predictable than Kelly).
Includes Defense Mode for managing open positions.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from bot.config import BotConfig

# Import defense module (for type hints and usage)
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from indicators.signals.defense import (
    DefenseConfig,
    DefenseState,
    DefenseAction,
    DefenseResult,
    evaluate_defense,
)


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
    status: str = "open"  # open, closed, expired, exited_early, flipped
    exit_reason: str | None = None  # For defense exits


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

        # Defense mode tracking (per position)
        self.defense_states: dict[str, DefenseState] = {}
        self.defense_config = DefenseConfig()

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

        # Initialize defense state for this position
        self.defense_states[token_id] = DefenseState()
        self.defense_states[token_id].start_position(side, entry_price)

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

    # === DEFENSE MODE METHODS ===

    def update_defense_state(
        self,
        token_id: str,
        imbalance: float,
        microprice_vs_mid: float,
        rv_5m: float,
        taker_ratio: float,
    ):
        """
        Update defense state for a position.

        Call this every tick for open positions.
        """
        if token_id not in self.defense_states:
            return

        self.defense_states[token_id].update(
            imbalance=imbalance,
            microprice_vs_mid=microprice_vs_mid,
            rv_5m=rv_5m,
            taker_ratio=taker_ratio,
        )

    def check_defense(
        self,
        token_id: str,
        remaining_s: float,
        prob_up: float,
        imbalance: float,
        imbalance_delta: Optional[float],
        microprice_vs_mid: float,
        taker_ratio: float,
        rv_5m: float,
        regime: Optional[str],
        z_score: Optional[float],
    ) -> DefenseResult:
        """
        Check if position needs defensive action.

        Args:
            token_id: Position token ID
            remaining_s: Seconds remaining in window
            prob_up: Current probability of UP
            imbalance: Current order book imbalance
            imbalance_delta: Change in imbalance
            microprice_vs_mid: Microprice minus mid
            taker_ratio: Taker buy/sell ratio
            rv_5m: 5-minute realized volatility
            regime: Volatility regime
            z_score: Price z-score

        Returns:
            DefenseResult with action and reasoning
        """
        if token_id not in self.positions:
            return DefenseResult(
                action=DefenseAction.HOLD,
                reason="position_not_found",
                score=0.0,
            )

        trade = self.positions[token_id]
        state = self.defense_states.get(token_id, DefenseState())

        return evaluate_defense(
            side=trade.side,
            entry_price=trade.entry_price,
            remaining_s=remaining_s,
            prob_up=prob_up,
            imbalance=imbalance,
            imbalance_delta=imbalance_delta,
            microprice_vs_mid=microprice_vs_mid,
            taker_ratio=taker_ratio,
            rv_5m=rv_5m,
            regime=regime,
            z_score=z_score,
            state=state,
            config=self.defense_config,
        )

    def exit_early(
        self,
        token_id: str,
        current_price: float,
        reason: str,
    ) -> Optional[TradeRecord]:
        """
        Exit position early (before window ends).

        This is called when defense triggers an exit.
        We sell at current market price, not at settlement.

        Args:
            token_id: Position token ID
            current_price: Current market price to exit at
            reason: Why we're exiting early

        Returns:
            Updated TradeRecord or None
        """
        if token_id not in self.positions:
            return None

        trade = self.positions.pop(token_id)
        trade.exit_price = current_price
        trade.status = "exited_early"
        trade.exit_reason = reason

        # Calculate P&L for early exit
        # If we're selling before settlement, we get market price
        # P&L = (exit_price - entry_price) * size
        trade.pnl = trade.size * (trade.exit_price - trade.entry_price)

        # Update bankroll
        self.current_bankroll += trade.pnl

        # Update daily stats
        self._ensure_daily_stats()
        stats = self.daily_stats[self._current_date]
        stats.pnl += trade.pnl
        if trade.pnl > 0:
            stats.wins += 1
        else:
            stats.losses += 1

        # Clean up defense state
        if token_id in self.defense_states:
            del self.defense_states[token_id]

        return trade

    def flip_position(
        self,
        token_id: str,
        current_price: float,
        new_side: str,
        reason: str,
    ) -> tuple[Optional[TradeRecord], Optional[TradeRecord]]:
        """
        Flip position to opposite side.

        Close current position and open new one on opposite side.
        Uses reduced stake (50% of original).

        Args:
            token_id: Current position token ID
            current_price: Current market price
            new_side: New side ("UP" or "DOWN")
            reason: Why we're flipping

        Returns:
            (closed_trade, new_trade) or (None, None) if failed
        """
        if token_id not in self.positions:
            return None, None

        # Get original trade info
        original = self.positions[token_id]
        market = original.market

        # Close original position
        closed = self.exit_early(token_id, current_price, f"flip:{reason}")
        if closed is None:
            return None, None

        # Calculate new position size (50% of original stake)
        new_dollar_amount = original.size * original.entry_price * self.defense_config.flip_stake_pct

        # Get entry price for new side
        if new_side == "UP":
            new_entry_price = 1 - current_price  # Buying the opposite
        else:
            new_entry_price = current_price

        # Calculate shares for new position
        new_shares = new_dollar_amount / new_entry_price if new_entry_price > 0 else 0

        if new_shares < 1:  # Minimum viable position
            return closed, None

        # Generate new token_id (in real usage, this comes from API)
        new_token_id = f"{token_id}_flip"

        # Open new position
        new_trade = self.open_position(
            market=market,
            token_id=new_token_id,
            side=new_side,
            size=new_shares,
            entry_price=new_entry_price,
        )

        # Mark as flipped
        closed.status = "flipped"

        return closed, new_trade

    def has_open_position(self, market: str) -> bool:
        """Check if there's an open position for a market."""
        for trade in self.positions.values():
            if trade.market == market:
                return True
        return False

    def get_position_for_market(self, market: str) -> Optional[TradeRecord]:
        """Get open position for a market."""
        for trade in self.positions.values():
            if trade.market == market:
                return trade
        return None
