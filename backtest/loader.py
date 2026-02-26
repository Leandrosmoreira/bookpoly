"""
Data loader for backtesting.

Loads historical JSONL files and organizes data by windows.
"""

import json
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Iterator, Generator


@dataclass
class WindowData:
    """Data for a single 15-minute window."""
    market: str
    window_start: int
    window_end: int
    ticks: list[dict]
    outcome: str | None  # "UP" or "DOWN" - determined at window end
    final_prob_up: float | None


def _window_seconds_for_market(market: str) -> int:
    """Return window duration in seconds from market name (e.g. BTC15m -> 900, BTC1h -> 3600)."""
    m = (market or "").lower()
    if "1h" in m or "1d" in m:
        return 3600 if "1h" in m else 86400
    if "4h" in m:
        return 14400
    if "5m" in m:
        return 300
    return 900  # 15m default


def load_jsonl(filepath: Path) -> list[dict]:
    """
    Load all rows from a JSONL file.

    Args:
        filepath: Path to JSONL file

    Returns:
        List of parsed JSON dicts
    """
    rows = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def load_books_for_date(
    data_dir: Path,
    date_str: str,
    market: str = "BTC15m",
) -> list[dict]:
    """
    Load Polymarket book data for a specific date and market.

    Args:
        data_dir: Base data directory (e.g., data/raw)
        date_str: Date string (e.g., "2026-02-06")
        market: Market name (e.g., "BTC15m", "BTC1h")

    Returns:
        List of book rows sorted by timestamp (error rows excluded)
    """
    # Pattern: {market}_{date}.jsonl (e.g. BTC15m_2026-02-06.jsonl, BTC1h_2026-02-24.jsonl)
    filepath = data_dir / "books" / f"{market}_{date_str}.jsonl"

    if not filepath.exists():
        return []

    rows = load_jsonl(filepath)
    # Exclude error rows (err set) so backtest uses only valid book data
    rows = [r for r in rows if not r.get("err")]
    return sorted(rows, key=lambda r: r.get("ts_ms", 0))


def load_volatility_for_date(
    data_dir: Path,
    date_str: str,
    symbol: str = "BTCUSDT",
) -> list[dict]:
    """
    Load Binance volatility data for a specific date and symbol.

    Args:
        data_dir: Base data directory
        date_str: Date string
        symbol: Binance symbol (e.g., "BTCUSDT")

    Returns:
        List of volatility rows sorted by timestamp
    """
    filepath = data_dir / "volatility" / f"{symbol}_volatility_{date_str}.jsonl"

    if not filepath.exists():
        return []

    rows = load_jsonl(filepath)
    return sorted(rows, key=lambda r: r.get("ts_ms", r.get("ts_system", 0) * 1000))


def load_signals_for_date(
    data_dir: Path,
    date_str: str,
) -> list[dict]:
    """
    Load signal data for a specific date.

    Args:
        data_dir: Base data directory
        date_str: Date string

    Returns:
        List of signal rows sorted by timestamp
    """
    filepath = data_dir / "signals" / f"signals_{date_str}.jsonl"

    if not filepath.exists():
        return []

    rows = load_jsonl(filepath)
    return sorted(rows, key=lambda r: r.get("ts_ms", 0))


def group_by_windows(rows: list[dict]) -> dict[int, list[dict]]:
    """
    Group rows by window_start.

    Args:
        rows: List of data rows

    Returns:
        Dict mapping window_start -> list of rows
    """
    windows: dict[int, list[dict]] = {}

    for row in rows:
        window_start = row.get("window_start", 0)
        if window_start not in windows:
            windows[window_start] = []
        windows[window_start].append(row)

    return windows


def determine_outcome(ticks: list[dict]) -> tuple[str | None, float | None]:
    """
    Determine the outcome of a window based on final probability.

    In Polymarket 15min markets:
    - If final prob_up >= 0.5, UP wins
    - If final prob_up < 0.5, DOWN wins

    Args:
        ticks: List of ticks in the window

    Returns:
        (outcome, final_prob_up) or (None, None) if can't determine
    """
    if not ticks:
        return None, None

    # Get the last tick
    last_tick = ticks[-1]

    # Try to get probability from different sources
    prob_up = None

    # From signals JSONL
    if "probability" in last_tick:
        prob_up = last_tick["probability"].get("prob_up")

    # From books JSONL
    if prob_up is None and "derived" in last_tick:
        prob_up = last_tick["derived"].get("prob_up")
    if prob_up is None and "yes" in last_tick:
        prob_up = last_tick["yes"].get("mid")

    if prob_up is None:
        return None, None

    # Window duration and minimum elapsed to consider complete
    window_start = last_tick.get("window_start", 0)
    last_ts = last_tick.get("ts_ms", 0) / 1000
    elapsed = last_ts - window_start

    # Infer duration from market name in first tick if available
    market = last_tick.get("market", "")
    duration = _window_seconds_for_market(market)
    min_elapsed = max(0, duration - 30)  # consider complete if within last 30s of window

    if elapsed < min_elapsed:
        return None, prob_up  # Window not complete

    # Determine outcome
    # Note: In reality, the outcome depends on the actual BTC price movement
    # For backtesting, we use the final probability as a proxy
    # A more accurate backtest would fetch actual price data
    outcome = "UP" if prob_up >= 0.5 else "DOWN"

    return outcome, prob_up


def iter_windows(
    data_dir: Path,
    start_date: str,
    end_date: str,
    market: str = "BTC15m",
) -> Generator[WindowData, None, None]:
    """
    Iterate over all complete windows in a date range.

    Args:
        data_dir: Base data directory
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        market: Market to load

    Yields:
        WindowData for each complete window
    """
    from datetime import timedelta

    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    current = start
    while current <= end:
        date_str = current.strftime("%Y-%m-%d")

        # Try signals first only for 15m (signals are 15m); otherwise use books
        if market == "BTC15m":
            rows = load_signals_for_date(data_dir, date_str)
        else:
            rows = []
        if not rows:
            rows = load_books_for_date(data_dir, date_str, market)

        if rows:
            windows = group_by_windows(rows)
            duration = _window_seconds_for_market(market)

            for window_start, ticks in sorted(windows.items()):
                outcome, final_prob = determine_outcome(ticks)

                yield WindowData(
                    market=market,
                    window_start=window_start,
                    window_end=window_start + duration,
                    ticks=ticks,
                    outcome=outcome,
                    final_prob_up=final_prob,
                )

        current += timedelta(days=1)


def merge_book_and_volatility(
    books: list[dict],
    volatility: list[dict],
    tolerance_ms: int = 2000,
) -> list[dict]:
    """
    Merge book and volatility data by timestamp.

    Args:
        books: Book data rows
        volatility: Volatility data rows
        tolerance_ms: Maximum time difference for matching

    Returns:
        Merged rows with both book and volatility data
    """
    merged = []
    vol_idx = 0

    for book in books:
        book_ts = book.get("ts_ms", 0)

        # Find closest volatility reading
        best_vol = None
        best_diff = float("inf")

        while vol_idx < len(volatility):
            vol = volatility[vol_idx]
            vol_ts = vol.get("ts_ms", vol.get("ts_system", 0) * 1000)

            diff = abs(book_ts - vol_ts)

            if diff < best_diff:
                best_diff = diff
                best_vol = vol

            if vol_ts > book_ts + tolerance_ms:
                break

            vol_idx += 1

        # Merge if within tolerance
        row = book.copy()
        if best_vol and best_diff <= tolerance_ms:
            row["volatility_data"] = best_vol

        merged.append(row)

    return merged


def get_available_dates(data_dir: Path, subdir: str = "books") -> list[str]:
    """
    Get list of available dates in the data directory.

    Args:
        data_dir: Base data directory
        subdir: Subdirectory to check (books, volatility, signals)

    Returns:
        List of date strings (sorted)
    """
    path = data_dir / subdir
    if not path.exists():
        return []

    dates = set()
    for f in path.glob("*.jsonl"):
        # Extract date from filename
        parts = f.stem.split("_")
        for part in parts:
            if len(part) == 10 and part[4] == "-" and part[7] == "-":
                dates.add(part)
                break

    return sorted(dates)


def print_data_summary(data_dir: Path):
    """Print summary of available data."""
    print("=== DATA SUMMARY ===\n")

    for subdir in ["books", "volatility", "signals"]:
        dates = get_available_dates(data_dir, subdir)
        if dates:
            print(f"{subdir}:")
            print(f"  Dates: {dates[0]} to {dates[-1]}")
            print(f"  Total days: {len(dates)}")

            # Count rows in first file
            first_file = list((data_dir / subdir).glob("*.jsonl"))[0]
            rows = load_jsonl(first_file)
            print(f"  Sample file rows: {len(rows)}")
        else:
            print(f"{subdir}: No data found")
        print()
