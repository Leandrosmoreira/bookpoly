"""
Chart generators for backtest analysis.

Generates 6 standard charts as matplotlib figures:
1. Equity curve (cumulative PnL)
2. PnL per trade (bars)
3. PnL distribution (histogram)
4. Rolling win rate
5. Drawdown curve
6. Entry price distribution
"""

import matplotlib
matplotlib.use("Agg")  # Non-interactive backend (works on VPS without display)

import matplotlib.pyplot as plt
import statistics
from loader import TradeRow


def _apply_style(style: str = "dark_background"):
    """Apply matplotlib style."""
    try:
        plt.style.use(style)
    except OSError:
        plt.style.use("default")


def equity_curve(
    trades: list[TradeRow],
    figsize: tuple = (12, 6),
    style: str = "dark_background",
) -> plt.Figure:
    """
    Equity curve: cumulative PnL over trades.

    X = trade number, Y = cumulative PnL.
    Marks max drawdown region in red.
    """
    _apply_style(style)
    fig, ax = plt.subplots(figsize=figsize)

    pnls = [t.pnl for t in trades]
    cumulative = []
    running = 0.0
    for p in pnls:
        running += p
        cumulative.append(running)

    x = list(range(1, len(cumulative) + 1))

    # Fill area under curve
    ax.fill_between(x, cumulative, alpha=0.3, color="cyan")
    ax.plot(x, cumulative, color="cyan", linewidth=1.5, label="Equity")

    # Mark max drawdown
    peak = 0.0
    max_dd = 0.0
    dd_start = 0
    dd_end = 0
    current_start = 0
    for i, c in enumerate(cumulative):
        if c > peak:
            peak = c
            current_start = i
        dd = peak - c
        if dd > max_dd:
            max_dd = dd
            dd_start = current_start
            dd_end = i

    if max_dd > 0:
        ax.axvspan(dd_start + 1, dd_end + 1, alpha=0.2, color="red", label=f"Max DD: ${max_dd:.2f}")

    ax.axhline(y=0, color="gray", linestyle="--", alpha=0.5)
    ax.set_xlabel("Trade #")
    ax.set_ylabel("Cumulative PnL ($)")
    ax.set_title("Equity Curve")
    ax.legend(loc="upper left")
    fig.tight_layout()
    return fig


def pnl_bars(
    trades: list[TradeRow],
    figsize: tuple = (12, 6),
    style: str = "dark_background",
) -> plt.Figure:
    """PnL per trade as colored bars (green=win, red=loss)."""
    _apply_style(style)
    fig, ax = plt.subplots(figsize=figsize)

    x = list(range(1, len(trades) + 1))
    colors = ["#00cc66" if t.won else "#ff4444" for t in trades]
    pnls = [t.pnl for t in trades]

    ax.bar(x, pnls, color=colors, width=1.0, edgecolor="none")
    ax.axhline(y=0, color="white", linewidth=0.5)

    ax.set_xlabel("Trade #")
    ax.set_ylabel("PnL ($)")
    ax.set_title("PnL per Trade")
    fig.tight_layout()
    return fig


def pnl_distribution(
    trades: list[TradeRow],
    bins: int = 30,
    figsize: tuple = (12, 6),
    style: str = "dark_background",
) -> plt.Figure:
    """PnL histogram with mean/median annotations."""
    _apply_style(style)
    fig, ax = plt.subplots(figsize=figsize)

    pnls = [t.pnl for t in trades]
    mean_pnl = statistics.mean(pnls)
    median_pnl = statistics.median(pnls)
    std_pnl = statistics.stdev(pnls) if len(pnls) > 1 else 0

    ax.hist(pnls, bins=bins, color="cyan", alpha=0.7, edgecolor="white", linewidth=0.3)
    ax.axvline(mean_pnl, color="#ff6600", linestyle="--", linewidth=2, label=f"Mean: ${mean_pnl:.4f}")
    ax.axvline(median_pnl, color="#ffcc00", linestyle=":", linewidth=2, label=f"Median: ${median_pnl:.4f}")
    ax.axvline(0, color="gray", linestyle="-", linewidth=0.5)

    ax.set_xlabel("PnL ($)")
    ax.set_ylabel("Count")
    ax.set_title(f"PnL Distribution (std=${std_pnl:.4f})")
    ax.legend()
    fig.tight_layout()
    return fig


def win_rate_rolling(
    trades: list[TradeRow],
    window: int = 20,
    figsize: tuple = (12, 6),
    style: str = "dark_background",
) -> plt.Figure:
    """Rolling win rate over last N trades."""
    _apply_style(style)
    fig, ax = plt.subplots(figsize=figsize)

    if len(trades) < window:
        window = max(5, len(trades) // 2)

    wins = [1 if t.won else 0 for t in trades]
    rolling = []
    for i in range(len(wins)):
        start = max(0, i - window + 1)
        chunk = wins[start:i + 1]
        rolling.append(sum(chunk) / len(chunk) * 100)

    x = list(range(1, len(rolling) + 1))
    ax.plot(x, rolling, color="cyan", linewidth=1.5, label=f"Win Rate ({window}-trade)")
    ax.axhline(y=50, color="#ff6600", linestyle="--", alpha=0.7, label="50% (breakeven)")
    ax.fill_between(x, rolling, 50, where=[r >= 50 for r in rolling], alpha=0.2, color="green")
    ax.fill_between(x, rolling, 50, where=[r < 50 for r in rolling], alpha=0.2, color="red")

    overall = sum(wins) / len(wins) * 100 if wins else 0
    ax.axhline(y=overall, color="yellow", linestyle=":", alpha=0.5, label=f"Overall: {overall:.1f}%")

    ax.set_xlabel("Trade #")
    ax.set_ylabel("Win Rate (%)")
    ax.set_title(f"Rolling Win Rate (window={window})")
    ax.set_ylim(0, 100)
    ax.legend()
    fig.tight_layout()
    return fig


def drawdown_curve(
    trades: list[TradeRow],
    figsize: tuple = (12, 6),
    style: str = "dark_background",
) -> plt.Figure:
    """Drawdown curve (negative values)."""
    _apply_style(style)
    fig, ax = plt.subplots(figsize=figsize)

    cumulative = []
    running = 0.0
    for t in trades:
        running += t.pnl
        cumulative.append(running)

    drawdowns = []
    peak = 0.0
    max_dd = 0.0
    for c in cumulative:
        if c > peak:
            peak = c
        dd = c - peak  # Negative when in drawdown
        drawdowns.append(dd)
        if abs(dd) > max_dd:
            max_dd = abs(dd)

    x = list(range(1, len(drawdowns) + 1))
    ax.fill_between(x, drawdowns, 0, color="red", alpha=0.4)
    ax.plot(x, drawdowns, color="red", linewidth=1, label=f"Max DD: ${max_dd:.2f}")

    ax.axhline(y=0, color="gray", linestyle="-", linewidth=0.5)
    ax.set_xlabel("Trade #")
    ax.set_ylabel("Drawdown ($)")
    ax.set_title("Drawdown Curve")
    ax.legend()
    fig.tight_layout()
    return fig


def entry_price_dist(
    trades: list[TradeRow],
    bins: int = 20,
    figsize: tuple = (12, 6),
    style: str = "dark_background",
) -> plt.Figure:
    """Entry price distribution, split by win/loss."""
    _apply_style(style)
    fig, ax = plt.subplots(figsize=figsize)

    win_prices = [t.entry_price for t in trades if t.won]
    loss_prices = [t.entry_price for t in trades if not t.won]

    ax.hist(
        win_prices, bins=bins, alpha=0.6, color="#00cc66",
        label=f"Wins ({len(win_prices)})", edgecolor="white", linewidth=0.3,
    )
    ax.hist(
        loss_prices, bins=bins, alpha=0.6, color="#ff4444",
        label=f"Losses ({len(loss_prices)})", edgecolor="white", linewidth=0.3,
    )

    ax.set_xlabel("Entry Price (probability)")
    ax.set_ylabel("Count")
    ax.set_title("Entry Price Distribution")
    ax.legend()
    fig.tight_layout()
    return fig


# Map chart names to functions
CHART_FUNCTIONS = {
    "equity_curve": equity_curve,
    "pnl_bars": pnl_bars,
    "pnl_distribution": pnl_distribution,
    "win_rate_rolling": win_rate_rolling,
    "drawdown": drawdown_curve,
    "entry_prices": entry_price_dist,
}
