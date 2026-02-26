"""
Leaderboard / ranking of backtest runs.

Ranks strategies by a composite score combining
Sharpe ratio, profit factor, and win rate.
"""

import csv
import logging
from loader import TradeRow, compute_metrics

log = logging.getLogger(__name__)


def generate_leaderboard(runs: list[dict]) -> list[dict]:
    """
    Generate ranked leaderboard from multiple runs.

    Composite score = (sharpe * 0.4) + (profit_factor_norm * 0.3) + (win_rate * 0.3)

    Args:
        runs: List of dicts with run_id, trades, and/or summary

    Returns:
        Sorted list of leaderboard entries (best first)
    """
    entries = []

    for run in runs:
        run_id = run.get("run_id", "unknown")
        trades = run.get("trades", [])
        metrics = run.get("summary", {}) or compute_metrics(trades)

        if not metrics or metrics.get("total_trades", 0) == 0:
            continue

        sharpe = metrics.get("sharpe", 0)
        pf = metrics.get("profit_factor", 0)
        wr = metrics.get("win_rate", 0)
        pf_norm = min(pf, 5) / 5  # Normalize profit factor to 0-1 (cap at 5)

        # Composite score
        score = (sharpe * 0.4) + (pf_norm * 0.3) + (wr * 0.3)

        entries.append({
            "rank": 0,
            "run_id": run_id,
            "strategy": run.get("strategy", metrics.get("strategy", "")),
            "trades": metrics.get("total_trades", len(trades)),
            "win_rate": wr,
            "total_pnl": metrics.get("total_pnl", 0),
            "sharpe": sharpe,
            "profit_factor": pf,
            "max_drawdown": metrics.get("max_drawdown", 0),
            "score": score,
        })

    # Sort by score descending
    entries.sort(key=lambda e: e["score"], reverse=True)

    # Assign ranks
    for i, entry in enumerate(entries):
        entry["rank"] = i + 1

    return entries


def print_leaderboard(leaderboard: list[dict]):
    """Print formatted leaderboard to console."""
    if not leaderboard:
        print("No runs to rank.")
        return

    header = (
        f"{'#':>3} {'Run ID':<25} {'Trades':>7} {'Win%':>6} "
        f"{'PnL':>8} {'Sharpe':>7} {'PF':>6} {'MaxDD':>7} {'Score':>7}"
    )
    print("\n" + "=" * len(header))
    print("STRATEGY LEADERBOARD")
    print("=" * len(header))
    print(header)
    print("-" * len(header))

    for e in leaderboard:
        print(
            f"{e['rank']:>3} {e['run_id']:<25} {e['trades']:>7} "
            f"{e['win_rate']*100:>5.1f}% "
            f"${e['total_pnl']:>7.2f} "
            f"{e['sharpe']:>7.2f} "
            f"{e['profit_factor']:>5.2f} "
            f"${e['max_drawdown']:>6.2f} "
            f"{e['score']:>7.3f}"
        )

    print("=" * len(header))
    print(f"Score = Sharpe*0.4 + PF_norm*0.3 + WinRate*0.3")
    print()


def save_leaderboard_csv(leaderboard: list[dict], filepath: str):
    """Save leaderboard to CSV file."""
    if not leaderboard:
        return

    fieldnames = [
        "rank", "run_id", "strategy", "trades", "win_rate",
        "total_pnl", "sharpe", "profit_factor", "max_drawdown", "score",
    ]

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for entry in leaderboard:
            row = {k: entry.get(k, "") for k in fieldnames}
            row["win_rate"] = f"{entry['win_rate']:.4f}"
            row["total_pnl"] = f"{entry['total_pnl']:.4f}"
            row["sharpe"] = f"{entry['sharpe']:.4f}"
            row["profit_factor"] = f"{entry['profit_factor']:.4f}"
            row["max_drawdown"] = f"{entry['max_drawdown']:.4f}"
            row["score"] = f"{entry['score']:.4f}"
            writer.writerow(row)

    log.info(f"Leaderboard saved to {filepath}")
