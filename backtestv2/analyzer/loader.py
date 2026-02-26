"""
Load backtest results from CSV and JSON files.

Compatible with both backtest v1 (backtest_trades.csv)
and backtestv2 (results/run_*/trades.csv) formats.
"""

import csv
import json
import os
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class TradeRow:
    """Single trade from backtest results."""
    window_start: str
    market: str
    side: str
    entry_price: float
    prob_at_entry: float
    spread: float
    imbalance: float
    outcome: str
    won: bool
    pnl: float
    remaining_s: float = 0.0
    spread_pct: float = 0.0
    bid_depth: float = 0.0
    ask_depth: float = 0.0
    total_depth: float = 0.0
    latency_ms: float = 0.0
    prob_favorite: float = 0.0
    confidence: str = ""
    score: float = 0.0
    strategy: str = ""
    run_id: str = ""

    @property
    def hour(self) -> int:
        """Hour of the trade (0-23)."""
        try:
            dt = datetime.fromisoformat(self.window_start)
            return dt.hour
        except (ValueError, TypeError):
            return 0

    @property
    def weekday(self) -> int:
        """Day of week (0=Mon, 6=Sun)."""
        try:
            dt = datetime.fromisoformat(self.window_start)
            return dt.weekday()
        except (ValueError, TypeError):
            return 0

    @property
    def date_str(self) -> str:
        """Date string YYYY-MM-DD."""
        try:
            dt = datetime.fromisoformat(self.window_start)
            return dt.strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            return ""

    @property
    def prob_zone(self) -> str:
        """Classify probability into zones."""
        p = self.prob_at_entry
        if p >= 0.98:
            return "danger"
        if p >= 0.95:
            return "caution"
        if p >= 0.85:
            return "safe"
        return "neutral"


def _safe_float(val: str, default: float = 0.0) -> float:
    """Parse float, returning default on failure."""
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _safe_bool(val: str) -> bool:
    """Parse boolean from YES/NO/True/False."""
    return val.strip().upper() in ("YES", "TRUE", "1")


def load_trades_csv(filepath: str) -> list[TradeRow]:
    """
    Load trades from CSV file.

    Supports both v1 format (backtest_trades.csv) and v2 format.
    """
    trades = []
    filepath = str(filepath)

    if not os.path.exists(filepath):
        log.error(f"File not found: {filepath}")
        return trades

    with open(filepath, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                trade = TradeRow(
                    window_start=row.get("window_start", ""),
                    market=row.get("market", ""),
                    side=row.get("side", ""),
                    entry_price=_safe_float(row.get("entry_price", row.get("prob_at_entry", "0"))),
                    prob_at_entry=_safe_float(row.get("prob_at_entry", "0")),
                    spread=_safe_float(row.get("spread", "0")),
                    imbalance=_safe_float(row.get("imbalance", "0")),
                    outcome=row.get("outcome", ""),
                    won=_safe_bool(row.get("won", "NO")),
                    pnl=_safe_float(row.get("pnl", "0")),
                    remaining_s=_safe_float(row.get("remaining_s", "0")),
                    spread_pct=_safe_float(row.get("spread_pct", "0")),
                    bid_depth=_safe_float(row.get("bid_depth", "0")),
                    ask_depth=_safe_float(row.get("ask_depth", "0")),
                    total_depth=_safe_float(row.get("total_depth", "0")),
                    latency_ms=_safe_float(row.get("latency_ms", "0")),
                    prob_favorite=_safe_float(row.get("prob_favorite", "0")),
                    confidence=row.get("confidence", ""),
                    score=_safe_float(row.get("score", "0")),
                    strategy=row.get("strategy", ""),
                    run_id=row.get("run_id", ""),
                )
                trades.append(trade)
            except Exception as e:
                log.warning(f"Skipping invalid row: {e}")

    log.info(f"Loaded {len(trades)} trades from {filepath}")
    return trades


def load_summary_json(filepath: str) -> dict:
    """Load summary.json from a backtest run."""
    with open(filepath, encoding="utf-8") as f:
        return json.load(f)


def load_all_runs(results_dir: str) -> list[dict]:
    """
    Load all runs from the results directory.

    Returns list of dicts with: run_id, summary, trades
    """
    runs = []
    results_path = Path(results_dir)

    if not results_path.exists():
        log.warning(f"Results directory not found: {results_dir}")
        return runs

    for run_dir in sorted(results_path.iterdir()):
        if not run_dir.is_dir() or not run_dir.name.startswith("run_"):
            continue

        run = {"run_id": run_dir.name, "summary": {}, "trades": []}

        summary_file = run_dir / "summary.json"
        if summary_file.exists():
            run["summary"] = load_summary_json(str(summary_file))

        trades_file = run_dir / "trades.csv"
        if trades_file.exists():
            run["trades"] = load_trades_csv(str(trades_file))

        if run["trades"] or run["summary"]:
            runs.append(run)

    log.info(f"Loaded {len(runs)} runs from {results_dir}")
    return runs


def compute_metrics(trades: list[TradeRow]) -> dict:
    """Compute basic metrics from a list of trades."""
    if not trades:
        return {}

    wins = [t for t in trades if t.won]
    losses = [t for t in trades if not t.won]
    pnls = [t.pnl for t in trades]
    win_pnls = [t.pnl for t in wins]
    loss_pnls = [t.pnl for t in losses]

    total_pnl = sum(pnls)
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))

    # Drawdown
    cumulative = []
    running = 0.0
    for p in pnls:
        running += p
        cumulative.append(running)

    peak = 0.0
    max_dd = 0.0
    for c in cumulative:
        if c > peak:
            peak = c
        dd = peak - c
        if dd > max_dd:
            max_dd = dd

    # Sharpe (annualized: 96 windows/day * 365)
    n = len(pnls)
    mean_pnl = total_pnl / n
    variance = sum((p - mean_pnl) ** 2 for p in pnls) / n if n > 1 else 0
    std_pnl = variance ** 0.5
    sharpe = (mean_pnl / std_pnl) * (96 * 365) ** 0.5 if std_pnl > 0 else 0

    return {
        "total_trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / n if n > 0 else 0,
        "total_pnl": total_pnl,
        "avg_pnl": mean_pnl,
        "avg_win": sum(win_pnls) / len(win_pnls) if win_pnls else 0,
        "avg_loss": sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else float("inf"),
        "max_drawdown": max_dd,
        "sharpe": sharpe,
        "avg_entry_price": sum(t.entry_price for t in trades) / n if n > 0 else 0,
    }
