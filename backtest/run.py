"""
Main backtest runner script.

Usage:
    python -m backtest.run --start 2026-02-01 --end 2026-02-07
    python -m backtest.run --days 7
"""

import sys
import os
import argparse
from pathlib import Path
from datetime import datetime, timedelta

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "indicators" / "signals"))

from loader import iter_windows, get_available_dates, print_data_summary
from simulator import Simulator, WindowResult
from metrics import calculate_metrics, format_metrics, analyze_by_zone, analyze_by_confidence

from config import SignalConfig
from decision import DecisionConfig


def run_backtest(
    data_dir: Path,
    start_date: str,
    end_date: str,
    market: str = "BTC15m",
    verbose: bool = False,
) -> list[WindowResult]:
    """
    Run backtest on historical data.

    Args:
        data_dir: Base data directory
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        market: Market to backtest
        verbose: Print progress

    Returns:
        List of WindowResult
    """
    if verbose:
        print(f"Running backtest from {start_date} to {end_date}")
        print(f"Market: {market}")
        print(f"Data directory: {data_dir}")
        print()

    # Initialize simulator
    signal_config = SignalConfig()
    decision_config = DecisionConfig()
    simulator = Simulator(signal_config, decision_config)

    results = []
    coin = market.replace("15m", "").lower()

    window_count = 0
    for window_data in iter_windows(data_dir, start_date, end_date, market):
        window_count += 1

        result = simulator.simulate_window(
            ticks=window_data.ticks,
            outcome=window_data.outcome,
            coin=coin,
        )
        results.append(result)

        if verbose and result.trade:
            outcome_str = result.outcome or "?"
            won_str = "WIN" if result.trade.won else "LOSS" if result.trade.won is False else "?"
            print(
                f"Window {window_data.window_start}: "
                f"ENTRY {result.trade.side} @ {result.trade.entry_price:.2f} "
                f"-> {outcome_str} ({won_str}) "
                f"P&L: ${result.trade.pnl or 0:.4f}"
            )

    if verbose:
        print(f"\nProcessed {window_count} windows")
        print()

    return results


def main():
    parser = argparse.ArgumentParser(description="Run backtest on historical data")
    parser.add_argument(
        "--data-dir",
        type=str,
        default="data/raw",
        help="Data directory (default: data/raw)",
    )
    parser.add_argument(
        "--start",
        type=str,
        help="Start date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end",
        type=str,
        help="End date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--days",
        type=int,
        help="Number of days to backtest (from most recent data)",
    )
    parser.add_argument(
        "--market",
        type=str,
        default="BTC15m",
        help="Market to backtest (default: BTC15m)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Print detailed progress",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Only show data summary",
    )

    args = parser.parse_args()

    # Resolve data directory
    project_root = Path(__file__).parent.parent
    data_dir = project_root / args.data_dir

    if not data_dir.exists():
        print(f"Error: Data directory not found: {data_dir}")
        sys.exit(1)

    # Show data summary if requested
    if args.summary:
        print_data_summary(data_dir)
        sys.exit(0)

    # Determine date range
    available_dates = get_available_dates(data_dir, "signals")
    if not available_dates:
        available_dates = get_available_dates(data_dir, "books")

    if not available_dates:
        print("Error: No data found in data directory")
        sys.exit(1)

    if args.days:
        # Use last N days
        end_date = available_dates[-1]
        start_idx = max(0, len(available_dates) - args.days)
        start_date = available_dates[start_idx]
    elif args.start and args.end:
        start_date = args.start
        end_date = args.end
    else:
        # Default: all available data
        start_date = available_dates[0]
        end_date = available_dates[-1]

    print(f"=== BACKTEST: {args.market} ===")
    print(f"Period: {start_date} to {end_date}")
    print(f"Available dates: {len(available_dates)}")
    print()

    # Run backtest
    results = run_backtest(
        data_dir=data_dir,
        start_date=start_date,
        end_date=end_date,
        market=args.market,
        verbose=args.verbose,
    )

    if not results:
        print("No results - no data found for the specified period")
        sys.exit(1)

    # Calculate and display metrics
    metrics = calculate_metrics(results)
    print(format_metrics(metrics))

    # Zone analysis
    print("\n=== ANALYSIS BY ZONE ===")
    zone_stats = analyze_by_zone(results)
    for zone, stats in zone_stats.items():
        if stats["count"] > 0:
            print(
                f"  {zone:8s}: {stats['count']:3d} trades, "
                f"win rate {stats['win_rate']:.1%}, "
                f"P&L ${stats['total_pnl']:.2f}"
            )

    # Confidence analysis
    print("\n=== ANALYSIS BY CONFIDENCE ===")
    conf_stats = analyze_by_confidence(results)
    for conf, stats in conf_stats.items():
        if stats["count"] > 0:
            print(
                f"  {conf:8s}: {stats['count']:3d} trades, "
                f"win rate {stats['win_rate']:.1%}, "
                f"P&L ${stats['total_pnl']:.2f}"
            )

    print()


if __name__ == "__main__":
    main()
