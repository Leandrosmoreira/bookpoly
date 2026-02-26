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
from loader import _window_seconds_for_market
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
    # Backtest: ignore latency gate (historical latency is not live)
    signal_config.max_latency_ms = 999999.0
    # Teste: 93% a 98%, janela "18 min restantes a 5 min restantes", score >= 0.55
    decision_config.force_entry_min_prob = 0.93
    decision_config.force_entry_max_prob = 0.98
    decision_config.force_entry_max_remaining_s = 1080.0   # até 18 min restantes
    decision_config.force_entry_min_remaining_s = 300.0   # não entrar nos últimos 5 min
    decision_config.score_low = 0.55
    simulator = Simulator(signal_config, decision_config)

    results = []
    # Base symbol: BTC15m/BTC1h/BTC4h/BTC1d -> btc, ETH15m -> eth, etc.
    for suffix in ("15m", "5m", "1h", "4h", "1d"):
        if market.upper().endswith(suffix.upper()):
            coin = market[: -len(suffix)].lower()
            break
    else:
        coin = market.lower()

    window_count = 0
    for window_data in iter_windows(data_dir, start_date, end_date, market):
        window_count += 1

        duration_s = window_data.window_end - window_data.window_start
        # Janela: entre 18 min e 5 min restantes (não nos últimos 5 min)
        result = simulator.simulate_window(
            ticks=window_data.ticks,
            outcome=window_data.outcome,
            coin=coin,
            window_duration_s=duration_s,
            entry_window_max_remaining_s=1080,   # até 18 min restantes
            entry_window_min_remaining_s=300,    # pelo menos 5 min restantes
        )
        result.market = market
        results.append(result)

        if verbose and result.trade:
            outcome_str = result.outcome or "?"
            won_str = "WIN" if result.trade.won else "LOSS" if result.trade.won is False else "?"
            # Tempo: segundos restantes e decorridos na janela
            entry_ts_s = result.trade.entry_ts / 1000.0
            remaining_s = window_data.window_end - entry_ts_s
            elapsed_s = entry_ts_s - window_data.window_start
            # Probabilidade no momento da entrada (entry_price = preço do lado comprado)
            prob_pct = result.trade.entry_price * 100
            print(
                f"Window {window_data.window_start}: "
                f"ENTRY {result.trade.side} @ {result.trade.entry_price:.2f} ({prob_pct:.0f}%) "
                f"| tempo: {elapsed_s:.0f}s na janela, {remaining_s:.0f}s restantes -> "
                f"{outcome_str} ({won_str}) P&L: ${result.trade.pnl or 0:.4f}"
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
        default=None,
        help="Market to backtest (e.g. BTC1h). Ignored if --markets is set.",
    )
    parser.add_argument(
        "--markets",
        type=str,
        default=None,
        help="Comma-separated markets (e.g. BTC1h,ETH1h,SOL1h,XRP1h). Runs all and aggregates.",
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

    # Single market or multiple?
    if args.markets:
        market_list = [m.strip() for m in args.markets.split(",") if m.strip()]
    else:
        market_list = [args.market or "BTC15m"]

    all_results = []
    for market in market_list:
        if len(market_list) > 1:
            print(f"=== BACKTEST: {market} ===")
            print(f"Period: {start_date} to {end_date}")
            print()
        res = run_backtest(
            data_dir=data_dir,
            start_date=start_date,
            end_date=end_date,
            market=market,
            verbose=args.verbose if len(market_list) == 1 else False,
        )
        all_results.extend(res)

    results = all_results

    if len(market_list) > 1:
        print(f"=== BACKTEST COMBINADO: {', '.join(market_list)} ===")
        print(f"Period: {start_date} to {end_date}")
        print(f"Markets: {len(market_list)} | Total windows: {len(results)}")
        print()

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

    # Resumo das entradas: probabilidade e tempo na janela
    trades_with_result = [(r, r.trade) for r in results if r.trade is not None]
    if trades_with_result:
        show_market = len(market_list) > 1
        duration_s = _window_seconds_for_market(market_list[0] if market_list else "BTC1h")
        print("\n=== ENTRADAS (probabilidade e tempo) ===")
        if show_market:
            print(f"  {'Market':<8} {'Janela (início)':<22} {'Lado':<6} {'Prob':<8} {'Decorrido':<10} {'Restante':<10} {'P&L':<8}")
        else:
            print(f"  {'Janela (início)':<22} {'Lado':<6} {'Prob':<8} {'Decorrido':<10} {'Restante':<10} {'P&L':<8}")
        print("  " + "-" * (78 if show_market else 68))
        for r, t in trades_with_result:
            dur = _window_seconds_for_market(r.market or market_list[0]) if market_list else duration_s
            entry_ts_s = t.entry_ts / 1000.0
            elapsed_s = entry_ts_s - r.window_start
            remaining_s = dur - elapsed_s
            dt_str = datetime.fromtimestamp(r.window_start).strftime("%Y-%m-%d %H:%M") if r.window_start else "?"
            prob_pct = f"{t.entry_price * 100:.0f}%"
            pnl_str = f"${t.pnl:.4f}" if t.pnl is not None else "-"
            if show_market:
                print(f"  {(r.market or '-'):<8} {dt_str:<22} {t.side:<6} {prob_pct:<8} {elapsed_s:.0f}s      {remaining_s:.0f}s      {pnl_str:<8}")
            else:
                print(f"  {dt_str:<22} {t.side:<6} {prob_pct:<8} {elapsed_s:.0f}s      {remaining_s:.0f}s      {pnl_str:<8}")
        print()

    print()


if __name__ == "__main__":
    main()
