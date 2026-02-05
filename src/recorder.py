import time
from datetime import datetime, timezone


def _token_metrics(book: dict | None) -> dict:
    """Compute per-token metrics from a normalized book."""
    if book is None:
        return {
            "best_bid": None,
            "best_ask": None,
            "mid": None,
            "spread": None,
            "bid_depth": 0.0,
            "ask_depth": 0.0,
            "imbalance": 0.0,
        }

    bids = book.get("bids", [])
    asks = book.get("asks", [])

    best_bid = bids[0]["p"] if bids else None
    best_ask = asks[0]["p"] if asks else None

    if best_bid is not None and best_ask is not None:
        mid = round((best_bid + best_ask) / 2, 6)
        spread = round(best_ask - best_bid, 6)
    else:
        mid = best_bid if best_bid is not None else best_ask
        spread = None

    bid_depth = round(sum(b["s"] for b in bids), 2)
    ask_depth = round(sum(a["s"] for a in asks), 2)
    total = bid_depth + ask_depth
    imbalance = round((bid_depth - ask_depth) / total, 4) if total > 0 else 0.0

    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": mid,
        "spread": spread,
        "bid_depth": bid_depth,
        "ask_depth": ask_depth,
        "imbalance": imbalance,
    }


def build_row(
    coin: str,
    market_info: dict,
    yes_book: dict | None,
    no_book: dict | None,
    seq: int,
    ts_system: float,
    latency_ms: float,
) -> dict:
    """Build a single JSONL row for one market at one tick."""
    ts_ms = int(ts_system * 1000)
    ts_iso = datetime.fromtimestamp(ts_system, tz=timezone.utc).isoformat(
        timespec="milliseconds"
    )

    yes_metrics = _token_metrics(yes_book)
    no_metrics = _token_metrics(no_book)

    # Derived market-level metrics
    prob_up = yes_metrics["mid"]
    prob_down = round(1.0 - prob_up, 6) if prob_up is not None else None

    overround = None
    if yes_metrics["best_ask"] is not None and no_metrics["best_ask"] is not None:
        overround = round(yes_metrics["best_ask"] + no_metrics["best_ask"] - 1.0, 6)

    mid_yes_cents = round(prob_up * 100, 2) if prob_up is not None else None
    mid_no_cents = round(no_metrics["mid"] * 100, 2) if no_metrics["mid"] is not None else None

    row = {
        "v": 2,
        "ts_ms": ts_ms,
        "ts_iso": ts_iso,
        "seq": seq,
        "market": market_info["market_label"],
        "condition_id": market_info["condition_id"],
        "window_start": market_info["window_ts"],
        "yes": {
            "token_id": market_info["yes_token"],
            **yes_metrics,
            "bids": yes_book["bids"] if yes_book else [],
            "asks": yes_book["asks"] if yes_book else [],
        },
        "no": {
            "token_id": market_info["no_token"],
            **no_metrics,
            "bids": no_book["bids"] if no_book else [],
            "asks": no_book["asks"] if no_book else [],
        },
        "derived": {
            "prob_up": prob_up,
            "prob_down": prob_down,
            "overround": overround,
            "mid_yes_cents": mid_yes_cents,
            "mid_no_cents": mid_no_cents,
        },
        "fetch": {
            "latency_ms": round(latency_ms, 1),
            "method": "rest",
        },
        "err": None,
    }
    return row


def build_error_row(
    coin: str,
    market_info: dict | None,
    seq: int,
    ts_system: float,
    error_msg: str,
) -> dict:
    """Build a JSONL row representing a fetch error."""
    ts_ms = int(ts_system * 1000)
    ts_iso = datetime.fromtimestamp(ts_system, tz=timezone.utc).isoformat(
        timespec="milliseconds"
    )

    label = market_info["market_label"] if market_info else f"{coin.upper()}15m"
    cid = market_info["condition_id"] if market_info else ""
    wts = market_info["window_ts"] if market_info else 0

    return {
        "v": 2,
        "ts_ms": ts_ms,
        "ts_iso": ts_iso,
        "seq": seq,
        "market": label,
        "condition_id": cid,
        "window_start": wts,
        "yes": None,
        "no": None,
        "derived": None,
        "fetch": {"latency_ms": 0, "method": "rest"},
        "err": error_msg,
    }
