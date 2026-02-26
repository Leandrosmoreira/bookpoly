"""
CLI entry point for the Backtest Analyzer.

Usage:
    # Full report from a CSV
    python -m backtestv2.analyzer.run_analyzer --trades backtest_trades.csv

    # Full report from a backtestv2 run
    python -m backtestv2.analyzer.run_analyzer --run run_20260224_120000

    # Single chart
    python -m backtestv2.analyzer.run_analyzer --trades backtest_trades.csv --chart equity_curve

    # Compare multiple runs
    python -m backtestv2.analyzer.run_analyzer --compare run_001 run_002

    # Leaderboard of all runs
    python -m backtestv2.analyzer.run_analyzer --leaderboard

    # Custom output directory
    python -m backtestv2.analyzer.run_analyzer --trades backtest_trades.csv -o my_report/
"""

import sys
import os
import argparse
import logging

# Add parent dirs to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from config import AnalyzerConfig
from loader import load_trades_csv, load_all_runs, compute_metrics
from charts import CHART_FUNCTIONS
from heatmaps import HEATMAP_FUNCTIONS
from report import generate_report
from compare import compare_runs
from ranking import generate_leaderboard, print_leaderboard, save_leaderboard_csv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("analyzer")


def cmd_report(args, config: AnalyzerConfig):
    """Generate full report from trades CSV."""
    trades = load_trades_csv(args.trades)
    if not trades:
        log.error(f"No trades loaded from {args.trades}")
        return

    output_dir = args.output or config.output_dir
    generate_report(
        trades=trades,
        output_dir=output_dir,
        dpi=config.dpi,
        style=config.style,
        figsize=config.figsize,
    )
    print(f"\nReport saved to: {output_dir}/")


def cmd_chart(args, config: AnalyzerConfig):
    """Generate a single chart."""
    trades = load_trades_csv(args.trades)
    if not trades:
        log.error(f"No trades loaded from {args.trades}")
        return

    chart_name = args.chart
    all_charts = {**CHART_FUNCTIONS, **HEATMAP_FUNCTIONS}

    if chart_name not in all_charts:
        log.error(f"Unknown chart: {chart_name}")
        print(f"Available charts: {', '.join(all_charts.keys())}")
        return

    output_dir = args.output or config.output_dir
    os.makedirs(output_dir, exist_ok=True)

    fig = all_charts[chart_name](trades, figsize=config.figsize, style=config.style)
    filepath = os.path.join(output_dir, f"{chart_name}.png")
    fig.savefig(filepath, dpi=config.dpi, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)

    print(f"Chart saved: {filepath}")


def cmd_compare(args, config: AnalyzerConfig):
    """Compare multiple runs."""
    results_dir = config.results_dir
    all_runs = load_all_runs(results_dir)

    if not all_runs:
        log.error(f"No runs found in {results_dir}")
        return

    # Filter to requested run IDs
    if args.compare:
        run_ids = set(args.compare)
        runs = [r for r in all_runs if r["run_id"] in run_ids]
        if not runs:
            log.error(f"None of {args.compare} found in {results_dir}")
            print(f"Available runs: {[r['run_id'] for r in all_runs]}")
            return
    else:
        runs = all_runs

    output_dir = args.output or config.output_dir
    os.makedirs(output_dir, exist_ok=True)

    fig = compare_runs(runs, figsize=(16, 10), style=config.style)
    filepath = os.path.join(output_dir, "comparison.png")
    fig.savefig(filepath, dpi=config.dpi, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)

    print(f"Comparison saved: {filepath}")


def cmd_leaderboard(args, config: AnalyzerConfig):
    """Generate leaderboard from all runs."""
    results_dir = config.results_dir
    all_runs = load_all_runs(results_dir)

    if not all_runs:
        log.error(f"No runs found in {results_dir}")
        return

    leaderboard = generate_leaderboard(all_runs)
    print_leaderboard(leaderboard)

    # Save CSV
    output_dir = args.output or config.output_dir
    os.makedirs(output_dir, exist_ok=True)
    csv_path = os.path.join(output_dir, "leaderboard.csv")
    save_leaderboard_csv(leaderboard, csv_path)
    print(f"Leaderboard CSV: {csv_path}")


def cmd_run(args, config: AnalyzerConfig):
    """Generate report from a specific backtestv2 run."""
    run_dir = os.path.join(config.results_dir, args.run)
    trades_csv = os.path.join(run_dir, "trades.csv")

    if not os.path.exists(trades_csv):
        log.error(f"trades.csv not found in {run_dir}")
        return

    trades = load_trades_csv(trades_csv)
    if not trades:
        log.error(f"No trades loaded from {trades_csv}")
        return

    output_dir = args.output or os.path.join(run_dir, "report")
    generate_report(
        trades=trades,
        output_dir=output_dir,
        dpi=config.dpi,
        style=config.style,
        figsize=config.figsize,
    )
    print(f"\nReport saved to: {output_dir}/")


def main():
    parser = argparse.ArgumentParser(
        description="Backtest Analyzer — charts, heatmaps, comparisons, rankings",
    )

    # Mutually exclusive modes
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--trades", type=str, help="Path to trades CSV (full report)")
    group.add_argument("--run", type=str, help="Run ID from backtestv2 results")
    group.add_argument("--compare", nargs="+", help="Compare multiple run IDs")
    group.add_argument("--leaderboard", action="store_true", help="Rank all runs")

    # Optional
    parser.add_argument("--chart", type=str, help="Generate single chart only")
    parser.add_argument("-o", "--output", type=str, help="Output directory")

    args = parser.parse_args()
    config = AnalyzerConfig()

    if args.leaderboard:
        cmd_leaderboard(args, config)
    elif args.compare:
        cmd_compare(args, config)
    elif args.run:
        cmd_run(args, config)
    elif args.trades and args.chart:
        cmd_chart(args, config)
    elif args.trades:
        cmd_report(args, config)


if __name__ == "__main__":
    main()
