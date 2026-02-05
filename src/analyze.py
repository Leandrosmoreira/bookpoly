"""
Analyze JSONL book recordings.

Usage:
    python src/analyze.py                          # analyzes data/raw/books/
    python src/analyze.py /path/to/folder
    python src/analyze.py --market BTC15m          # filter by market
    python src/analyze.py --date 2026-02-05        # filter by date
"""

import sys
import os
import json
import glob
import argparse
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def load_rows(folder: str, market_filter: str = None, date_filter: str = None) -> list[dict]:
    """Load all JSONL rows from a folder, optionally filtered."""
    pattern = os.path.join(folder, "*.jsonl")
    files = sorted(glob.glob(pattern))

    if not files:
        print(f"No .jsonl files found in {folder}")
        sys.exit(1)

    if market_filter:
        files = [f for f in files if market_filter in os.path.basename(f)]
    if date_filter:
        files = [f for f in files if date_filter in os.path.basename(f)]

    rows = []
    errors = 0
    for fpath in files:
        with open(fpath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    rows.append(row)
                except json.JSONDecodeError:
                    errors += 1

    print(f"Loaded {len(rows)} rows from {len(files)} files ({errors} parse errors)\n")
    return rows


def summary(rows: list[dict]):
    """Print overall summary."""
    by_market = defaultdict(list)
    for r in rows:
        by_market[r.get("market", "?")].append(r)

    err_count = sum(1 for r in rows if r.get("err"))
    ts_min = min(r["ts_ms"] for r in rows) if rows else 0
    ts_max = max(r["ts_ms"] for r in rows) if rows else 0
    duration_s = (ts_max - ts_min) / 1000 if ts_max > ts_min else 0

    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Total rows:    {len(rows)}")
    print(f"  Error rows:    {err_count}")
    print(f"  Markets:       {', '.join(sorted(by_market.keys()))}")
    print(f"  Time range:    {datetime.fromtimestamp(ts_min/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} "
          f"-> {datetime.fromtimestamp(ts_max/1000, tz=timezone.utc).strftime('%H:%M:%S')} UTC")
    print(f"  Duration:      {duration_s:.0f}s ({duration_s/60:.1f} min)")
    print(f"  Windows seen:  {len(set(r.get('window_start', 0) for r in rows if r.get('window_start')))}")
    print()

    return by_market


def market_stats(market: str, rows: list[dict]):
    """Print stats for a single market."""
    valid = [r for r in rows if r.get("yes") and r.get("derived")]
    errors = [r for r in rows if r.get("err")]

    if not valid:
        print(f"  [{market}] No valid rows (errors: {len(errors)})")
        return

    # Extract series
    mids = [r["derived"]["prob_up"] for r in valid if r["derived"].get("prob_up") is not None]
    spreads = [r["yes"]["spread"] for r in valid if r["yes"].get("spread") is not None]
    overrounds = [r["derived"]["overround"] for r in valid if r["derived"].get("overround") is not None]
    latencies = [r["fetch"]["latency_ms"] for r in valid if r.get("fetch")]
    imbalances = [r["yes"]["imbalance"] for r in valid if r["yes"].get("imbalance") is not None]
    bid_depths = [r["yes"]["bid_depth"] for r in valid if r["yes"].get("bid_depth") is not None]
    ask_depths = [r["yes"]["ask_depth"] for r in valid if r["yes"].get("ask_depth") is not None]

    windows = set(r.get("window_start", 0) for r in valid)

    print(f"  [{market}]  {len(valid)} ticks | {len(errors)} errors | {len(windows)} windows")

    if mids:
        print(f"    Prob Up (mid YES):  min={min(mids)*100:.1f}%  max={max(mids)*100:.1f}%  "
              f"avg={sum(mids)/len(mids)*100:.1f}%  last={mids[-1]*100:.1f}%")

    if spreads:
        avg_sp = sum(spreads) / len(spreads)
        print(f"    Spread YES:         min={min(spreads)*100:.1f}c  max={max(spreads)*100:.1f}c  "
              f"avg={avg_sp*100:.1f}c")

    if overrounds:
        avg_or = sum(overrounds) / len(overrounds)
        print(f"    Overround:          min={min(overrounds)*100:.1f}c  max={max(overrounds)*100:.1f}c  "
              f"avg={avg_or*100:.1f}c")

    if latencies:
        print(f"    Latency:            min={min(latencies):.0f}ms  max={max(latencies):.0f}ms  "
              f"avg={sum(latencies)/len(latencies):.0f}ms")

    if imbalances:
        print(f"    Imbalance YES:      min={min(imbalances):.3f}  max={max(imbalances):.3f}  "
              f"avg={sum(imbalances)/len(imbalances):.3f}")

    if bid_depths and ask_depths:
        print(f"    Depth YES:          bid_avg={sum(bid_depths)/len(bid_depths):.0f}  "
              f"ask_avg={sum(ask_depths)/len(ask_depths):.0f}")

    print()


def window_breakdown(rows: list[dict]):
    """Show per-window breakdown."""
    by_window = defaultdict(list)
    for r in rows:
        ws = r.get("window_start", 0)
        if ws:
            by_window[ws].append(r)

    if not by_window:
        return

    print("-" * 60)
    print("PER-WINDOW BREAKDOWN")
    print("-" * 60)

    for ws in sorted(by_window.keys()):
        wrows = by_window[ws]
        valid = [r for r in wrows if r.get("derived") and r["derived"].get("prob_up") is not None]
        markets_in_window = set(r.get("market", "?") for r in wrows)
        t = datetime.fromtimestamp(ws, tz=timezone.utc).strftime("%H:%M")

        if valid:
            mids = [r["derived"]["prob_up"] for r in valid]
            print(f"  {t} UTC | {len(wrows)} rows | markets: {','.join(sorted(markets_in_window))} | "
                  f"prob_up range: {min(mids)*100:.1f}%-{max(mids)*100:.1f}%")
        else:
            print(f"  {t} UTC | {len(wrows)} rows | markets: {','.join(sorted(markets_in_window))} | no valid data")

    print()


def main():
    parser = argparse.ArgumentParser(description="Analyze Polymarket book recordings")
    parser.add_argument("folder", nargs="?", default="data/raw/books", help="Path to JSONL folder")
    parser.add_argument("--market", "-m", help="Filter by market (e.g. BTC15m)")
    parser.add_argument("--date", "-d", help="Filter by date (e.g. 2026-02-05)")
    args = parser.parse_args()

    rows = load_rows(args.folder, args.market, args.date)
    if not rows:
        return

    by_market = summary(rows)

    print("-" * 60)
    print("PER-MARKET STATS")
    print("-" * 60)
    for market in sorted(by_market.keys()):
        market_stats(market, by_market[market])

    window_breakdown(rows)


if __name__ == "__main__":
    main()
