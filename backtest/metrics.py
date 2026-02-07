"""
Performance metrics for backtesting.

Calculates win rate, Sharpe ratio, drawdown, and other statistics.
"""

from dataclasses import dataclass
from typing import Sequence
import math

from simulator import WindowResult, SimulatedTrade


@dataclass
class PerformanceMetrics:
    """Aggregated performance metrics."""
    # Counts
    total_windows: int
    windows_with_outcome: int
    entries: int
    entry_rate: float  # entries / windows_with_outcome

    # Win/Loss
    wins: int
    losses: int
    win_rate: float

    # P&L
    total_pnl: float
    avg_pnl: float
    avg_win: float
    avg_loss: float
    profit_factor: float  # gross_profit / gross_loss

    # Risk metrics
    max_drawdown: float
    max_drawdown_pct: float
    sharpe_ratio: float
    sortino_ratio: float

    # Entry analysis
    avg_entry_price: float
    avg_score: float
    confidence_distribution: dict[str, int]


def calculate_metrics(results: list[WindowResult]) -> PerformanceMetrics:
    """
    Calculate performance metrics from backtest results.

    Args:
        results: List of WindowResult from simulation

    Returns:
        PerformanceMetrics with all calculated values
    """
    total_windows = len(results)
    windows_with_outcome = sum(1 for r in results if r.outcome is not None)

    # Get all trades
    trades = [r.trade for r in results if r.trade is not None]
    entries = len(trades)

    # Calculate entry rate
    entry_rate = entries / windows_with_outcome if windows_with_outcome > 0 else 0

    # Win/Loss counts
    trades_with_outcome = [t for t in trades if t.won is not None]
    wins = sum(1 for t in trades_with_outcome if t.won)
    losses = sum(1 for t in trades_with_outcome if not t.won)
    win_rate = wins / len(trades_with_outcome) if trades_with_outcome else 0

    # P&L calculations
    pnls = [t.pnl for t in trades if t.pnl is not None]
    total_pnl = sum(pnls)
    avg_pnl = total_pnl / len(pnls) if pnls else 0

    winning_pnls = [p for p in pnls if p > 0]
    losing_pnls = [p for p in pnls if p < 0]

    avg_win = sum(winning_pnls) / len(winning_pnls) if winning_pnls else 0
    avg_loss = sum(losing_pnls) / len(losing_pnls) if losing_pnls else 0

    gross_profit = sum(winning_pnls)
    gross_loss = abs(sum(losing_pnls))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

    # Calculate drawdown
    max_dd, max_dd_pct = calculate_drawdown(pnls)

    # Calculate Sharpe and Sortino
    sharpe = calculate_sharpe(pnls)
    sortino = calculate_sortino(pnls)

    # Entry analysis
    entry_prices = [t.entry_price for t in trades]
    avg_entry_price = sum(entry_prices) / len(entry_prices) if entry_prices else 0

    scores = [t.score for t in trades]
    avg_score = sum(scores) / len(scores) if scores else 0

    confidence_dist = {"high": 0, "medium": 0, "low": 0}
    for t in trades:
        if t.confidence in confidence_dist:
            confidence_dist[t.confidence] += 1

    return PerformanceMetrics(
        total_windows=total_windows,
        windows_with_outcome=windows_with_outcome,
        entries=entries,
        entry_rate=entry_rate,
        wins=wins,
        losses=losses,
        win_rate=win_rate,
        total_pnl=total_pnl,
        avg_pnl=avg_pnl,
        avg_win=avg_win,
        avg_loss=avg_loss,
        profit_factor=profit_factor,
        max_drawdown=max_dd,
        max_drawdown_pct=max_dd_pct,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        avg_entry_price=avg_entry_price,
        avg_score=avg_score,
        confidence_distribution=confidence_dist,
    )


def calculate_drawdown(pnls: list[float]) -> tuple[float, float]:
    """
    Calculate maximum drawdown.

    Args:
        pnls: List of P&L values

    Returns:
        (max_drawdown_absolute, max_drawdown_percentage)
    """
    if not pnls:
        return 0.0, 0.0

    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0

    for pnl in pnls:
        cumulative += pnl
        peak = max(peak, cumulative)
        dd = peak - cumulative
        max_dd = max(max_dd, dd)

    # Calculate percentage (relative to peak)
    max_dd_pct = max_dd / peak if peak > 0 else 0

    return max_dd, max_dd_pct


def calculate_sharpe(pnls: list[float], risk_free_rate: float = 0.0) -> float:
    """
    Calculate Sharpe ratio.

    Args:
        pnls: List of P&L values
        risk_free_rate: Risk-free rate (default 0)

    Returns:
        Sharpe ratio (annualized for 15-min windows = 96 per day * 365)
    """
    if len(pnls) < 2:
        return 0.0

    mean_return = sum(pnls) / len(pnls) - risk_free_rate
    std_return = math.sqrt(sum((p - mean_return) ** 2 for p in pnls) / len(pnls))

    if std_return == 0:
        return 0.0

    # Annualize: 96 windows per day * 365 days
    annualization_factor = math.sqrt(96 * 365)

    return (mean_return / std_return) * annualization_factor


def calculate_sortino(pnls: list[float], risk_free_rate: float = 0.0) -> float:
    """
    Calculate Sortino ratio (uses only downside deviation).

    Args:
        pnls: List of P&L values
        risk_free_rate: Risk-free rate (default 0)

    Returns:
        Sortino ratio (annualized)
    """
    if len(pnls) < 2:
        return 0.0

    mean_return = sum(pnls) / len(pnls) - risk_free_rate

    # Only negative returns for downside deviation
    negative_returns = [p for p in pnls if p < 0]
    if not negative_returns:
        return float('inf')  # No losses

    downside_dev = math.sqrt(sum(p ** 2 for p in negative_returns) / len(negative_returns))

    if downside_dev == 0:
        return 0.0

    # Annualize
    annualization_factor = math.sqrt(96 * 365)

    return (mean_return / downside_dev) * annualization_factor


def format_metrics(metrics: PerformanceMetrics) -> str:
    """Format metrics for display."""
    lines = [
        "=" * 50,
        "BACKTEST RESULTS",
        "=" * 50,
        "",
        "OVERVIEW",
        f"  Total Windows:      {metrics.total_windows}",
        f"  Complete Windows:   {metrics.windows_with_outcome}",
        f"  Entries:            {metrics.entries}",
        f"  Entry Rate:         {metrics.entry_rate:.1%}",
        "",
        "WIN/LOSS",
        f"  Wins:               {metrics.wins}",
        f"  Losses:             {metrics.losses}",
        f"  Win Rate:           {metrics.win_rate:.1%}",
        "",
        "P&L",
        f"  Total P&L:          ${metrics.total_pnl:.2f}",
        f"  Avg P&L per Trade:  ${metrics.avg_pnl:.4f}",
        f"  Avg Win:            ${metrics.avg_win:.4f}",
        f"  Avg Loss:           ${metrics.avg_loss:.4f}",
        f"  Profit Factor:      {metrics.profit_factor:.2f}",
        "",
        "RISK",
        f"  Max Drawdown:       ${metrics.max_drawdown:.2f}",
        f"  Max Drawdown %:     {metrics.max_drawdown_pct:.1%}",
        f"  Sharpe Ratio:       {metrics.sharpe_ratio:.2f}",
        f"  Sortino Ratio:      {metrics.sortino_ratio:.2f}",
        "",
        "ENTRIES",
        f"  Avg Entry Price:    ${metrics.avg_entry_price:.4f}",
        f"  Avg Score:          {metrics.avg_score:.3f}",
        f"  High Confidence:    {metrics.confidence_distribution.get('high', 0)}",
        f"  Medium Confidence:  {metrics.confidence_distribution.get('medium', 0)}",
        f"  Low Confidence:     {metrics.confidence_distribution.get('low', 0)}",
        "",
        "=" * 50,
    ]

    return "\n".join(lines)


def analyze_by_zone(results: list[WindowResult]) -> dict[str, dict]:
    """
    Analyze performance by probability zone.

    Args:
        results: List of WindowResult

    Returns:
        Dict mapping zone -> performance stats
    """
    zones = {"danger": [], "caution": [], "safe": [], "neutral": []}

    for r in results:
        if r.trade:
            # Determine zone from entry price
            prob = r.trade.entry_price if r.trade.side == "UP" else (1 - r.trade.entry_price)
            underdog = min(prob, 1 - prob)

            if underdog < 0.02:
                zone = "danger"
            elif underdog < 0.05:
                zone = "caution"
            elif underdog < 0.15:
                zone = "safe"
            else:
                zone = "neutral"

            zones[zone].append(r.trade)

    stats = {}
    for zone, trades in zones.items():
        if trades:
            wins = sum(1 for t in trades if t.won)
            pnls = [t.pnl for t in trades if t.pnl is not None]
            stats[zone] = {
                "count": len(trades),
                "wins": wins,
                "win_rate": wins / len(trades),
                "total_pnl": sum(pnls),
                "avg_pnl": sum(pnls) / len(pnls) if pnls else 0,
            }
        else:
            stats[zone] = {"count": 0, "wins": 0, "win_rate": 0, "total_pnl": 0, "avg_pnl": 0}

    return stats


def analyze_by_confidence(results: list[WindowResult]) -> dict[str, dict]:
    """
    Analyze performance by confidence level.

    Args:
        results: List of WindowResult

    Returns:
        Dict mapping confidence -> performance stats
    """
    confs = {"high": [], "medium": [], "low": []}

    for r in results:
        if r.trade and r.trade.confidence in confs:
            confs[r.trade.confidence].append(r.trade)

    stats = {}
    for conf, trades in confs.items():
        if trades:
            wins = sum(1 for t in trades if t.won)
            pnls = [t.pnl for t in trades if t.pnl is not None]
            stats[conf] = {
                "count": len(trades),
                "wins": wins,
                "win_rate": wins / len(trades),
                "total_pnl": sum(pnls),
                "avg_pnl": sum(pnls) / len(pnls) if pnls else 0,
            }
        else:
            stats[conf] = {"count": 0, "wins": 0, "win_rate": 0, "total_pnl": 0, "avg_pnl": 0}

    return stats
