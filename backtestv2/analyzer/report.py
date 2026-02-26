"""
Generate complete analysis report with all charts and metrics.

Creates a directory with:
  01_equity_curve.png
  02_pnl_bars.png
  03_pnl_distribution.png
  04_win_rate_rolling.png
  05_drawdown.png
  06_entry_prices.png
  07_heatmap_zone_hour.png
  08_heatmap_spread_imbalance.png
  09_heatmap_hour_weekday.png
  summary.txt
"""

import os
import logging
import matplotlib.pyplot as plt

from loader import TradeRow, compute_metrics
from charts import equity_curve, pnl_bars, pnl_distribution
from charts import win_rate_rolling, drawdown_curve, entry_price_dist
from heatmaps import heatmap_zone_hour, heatmap_spread_imbalance, heatmap_hour_weekday

log = logging.getLogger(__name__)


def generate_report(
    trades: list[TradeRow],
    output_dir: str,
    dpi: int = 150,
    style: str = "dark_background",
    figsize: tuple = (12, 6),
):
    """
    Generate complete report with all charts and summary.

    Args:
        trades: List of TradeRow from loader
        output_dir: Directory to save PNGs and summary
        dpi: Image resolution
        style: Matplotlib style
        figsize: Default figure size
    """
    os.makedirs(output_dir, exist_ok=True)

    if not trades:
        log.error("No trades to analyze.")
        return

    log.info(f"Generating report for {len(trades)} trades -> {output_dir}")

    # Generate all charts
    chart_specs = [
        ("01_equity_curve.png", equity_curve),
        ("02_pnl_bars.png", pnl_bars),
        ("03_pnl_distribution.png", pnl_distribution),
        ("04_win_rate_rolling.png", win_rate_rolling),
        ("05_drawdown.png", drawdown_curve),
        ("06_entry_prices.png", entry_price_dist),
        ("07_heatmap_zone_hour.png", heatmap_zone_hour),
        ("08_heatmap_spread_imbalance.png", heatmap_spread_imbalance),
        ("09_heatmap_hour_weekday.png", heatmap_hour_weekday),
    ]

    for filename, chart_fn in chart_specs:
        try:
            fig = chart_fn(trades, figsize=figsize, style=style)
            filepath = os.path.join(output_dir, filename)
            fig.savefig(filepath, dpi=dpi, bbox_inches="tight", facecolor=fig.get_facecolor())
            plt.close(fig)
            log.info(f"  Saved {filename}")
        except Exception as e:
            log.error(f"  Failed {filename}: {e}")

    # Generate summary text
    metrics = compute_metrics(trades)
    summary_path = os.path.join(output_dir, "summary.txt")
    _write_summary(trades, metrics, summary_path)
    log.info(f"  Saved summary.txt")

    log.info(f"Report complete: {len(chart_specs)} charts + summary")


def _write_summary(trades: list[TradeRow], metrics: dict, filepath: str):
    """Write text summary of metrics."""
    lines = [
        "=" * 60,
        "BACKTEST ANALYSIS REPORT",
        "=" * 60,
        "",
        "OVERVIEW",
        f"  Total Trades:      {metrics.get('total_trades', 0)}",
        f"  Wins:              {metrics.get('wins', 0)}",
        f"  Losses:            {metrics.get('losses', 0)}",
        f"  Win Rate:          {metrics.get('win_rate', 0)*100:.1f}%",
        "",
        "P&L",
        f"  Total PnL:         ${metrics.get('total_pnl', 0):.4f}",
        f"  Avg PnL/Trade:     ${metrics.get('avg_pnl', 0):.4f}",
        f"  Avg Win:           ${metrics.get('avg_win', 0):.4f}",
        f"  Avg Loss:          ${metrics.get('avg_loss', 0):.4f}",
        f"  Profit Factor:     {metrics.get('profit_factor', 0):.2f}",
        "",
        "RISK",
        f"  Max Drawdown:      ${metrics.get('max_drawdown', 0):.4f}",
        f"  Sharpe Ratio:      {metrics.get('sharpe', 0):.2f}",
        "",
        "ENTRIES",
        f"  Avg Entry Price:   ${metrics.get('avg_entry_price', 0):.4f}",
        "",
    ]

    # Zone breakdown
    zones = {}
    for t in trades:
        z = t.prob_zone
        if z not in zones:
            zones[z] = {"count": 0, "wins": 0, "pnl": 0.0}
        zones[z]["count"] += 1
        zones[z]["wins"] += 1 if t.won else 0
        zones[z]["pnl"] += t.pnl

    if zones:
        lines.append("BY ZONE")
        for z in ["danger", "caution", "safe", "neutral"]:
            if z in zones:
                d = zones[z]
                wr = d["wins"] / d["count"] * 100 if d["count"] > 0 else 0
                lines.append(
                    f"  {z:<10} {d['count']:>4} trades  "
                    f"win={wr:.1f}%  pnl=${d['pnl']:.4f}"
                )
        lines.append("")

    # Side breakdown
    sides = {}
    for t in trades:
        s = t.side
        if s not in sides:
            sides[s] = {"count": 0, "wins": 0, "pnl": 0.0}
        sides[s]["count"] += 1
        sides[s]["wins"] += 1 if t.won else 0
        sides[s]["pnl"] += t.pnl

    if sides:
        lines.append("BY SIDE")
        for s, d in sides.items():
            wr = d["wins"] / d["count"] * 100 if d["count"] > 0 else 0
            lines.append(
                f"  {s:<10} {d['count']:>4} trades  "
                f"win={wr:.1f}%  pnl=${d['pnl']:.4f}"
            )
        lines.append("")

    lines.append("=" * 60)
    lines.append("Generated by backtestv2 Analyzer")
    lines.append("=" * 60)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
