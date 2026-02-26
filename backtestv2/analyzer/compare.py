"""
Compare multiple backtest runs side by side.

Generates a 4-panel comparison chart:
1. Equity curves overlaid
2. Win rate comparison bars
3. Total PnL comparison bars
4. Sharpe ratio comparison bars
"""

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
from loader import TradeRow, compute_metrics


def compare_runs(
    runs: list[dict],
    figsize: tuple = (16, 10),
    style: str = "dark_background",
) -> plt.Figure:
    """
    Generate comparison chart for multiple runs.

    Each run dict must have: run_id, trades (list[TradeRow])
    """
    try:
        plt.style.use(style)
    except OSError:
        plt.style.use("default")

    fig, axes = plt.subplots(2, 2, figsize=figsize)

    colors = ["#00ccff", "#ff6600", "#00cc66", "#ff4444", "#ffcc00", "#cc66ff"]
    labels = []
    all_metrics = []

    for i, run in enumerate(runs):
        run_id = run.get("run_id", f"Run {i+1}")
        trades = run.get("trades", [])
        metrics = run.get("summary", {}) or compute_metrics(trades)
        labels.append(run_id)
        all_metrics.append(metrics)

        # 1. Equity curve
        if trades:
            cumulative = []
            running = 0.0
            for t in trades:
                running += t.pnl
                cumulative.append(running)
            color = colors[i % len(colors)]
            axes[0, 0].plot(
                range(1, len(cumulative) + 1), cumulative,
                color=color, linewidth=1.5, label=run_id, alpha=0.8,
            )

    axes[0, 0].axhline(y=0, color="gray", linestyle="--", alpha=0.5)
    axes[0, 0].set_title("Equity Curves")
    axes[0, 0].set_xlabel("Trade #")
    axes[0, 0].set_ylabel("Cumulative PnL ($)")
    axes[0, 0].legend(fontsize=8)

    # 2. Win rate bars
    win_rates = [m.get("win_rate", 0) * 100 for m in all_metrics]
    bar_colors = [colors[i % len(colors)] for i in range(len(labels))]
    axes[0, 1].bar(labels, win_rates, color=bar_colors, alpha=0.8)
    axes[0, 1].axhline(y=50, color="#ff6600", linestyle="--", alpha=0.5)
    axes[0, 1].set_title("Win Rate (%)")
    axes[0, 1].set_ylabel("%")
    for j, v in enumerate(win_rates):
        axes[0, 1].text(j, v + 1, f"{v:.1f}%", ha="center", fontsize=9)

    # 3. Total PnL bars
    pnls = [m.get("total_pnl", 0) for m in all_metrics]
    pnl_colors = ["#00cc66" if p >= 0 else "#ff4444" for p in pnls]
    axes[1, 0].bar(labels, pnls, color=pnl_colors, alpha=0.8)
    axes[1, 0].axhline(y=0, color="gray", linestyle="--", alpha=0.5)
    axes[1, 0].set_title("Total PnL ($)")
    axes[1, 0].set_ylabel("$")
    for j, v in enumerate(pnls):
        axes[1, 0].text(j, v + (0.1 if v >= 0 else -0.3), f"${v:.2f}", ha="center", fontsize=9)

    # 4. Sharpe ratio bars
    sharpes = [m.get("sharpe", 0) for m in all_metrics]
    axes[1, 1].bar(labels, sharpes, color=bar_colors, alpha=0.8)
    axes[1, 1].axhline(y=1, color="#00cc66", linestyle="--", alpha=0.5, label="Good (>1)")
    axes[1, 1].set_title("Sharpe Ratio")
    axes[1, 1].legend(fontsize=8)
    for j, v in enumerate(sharpes):
        axes[1, 1].text(j, v + 0.05, f"{v:.2f}", ha="center", fontsize=9)

    fig.suptitle("Strategy Comparison", fontsize=14, fontweight="bold")
    fig.tight_layout()
    return fig
