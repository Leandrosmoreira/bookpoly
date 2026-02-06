from datetime import datetime, timezone


def build_row(
    symbol: str,
    metrics: dict,
    cluster: str,
    percentile: float,
    seq: int,
    ts_system: float,
    latency_ms: float,
) -> dict:
    """Build a single JSONL row for one symbol at one tick."""
    ts_ms = int(ts_system * 1000)
    ts_iso = datetime.fromtimestamp(ts_system, tz=timezone.utc).isoformat(
        timespec="milliseconds"
    )

    return {
        "v": 1,
        "ts_ms": ts_ms,
        "ts_iso": ts_iso,
        "seq": seq,
        "symbol": symbol,
        "price": metrics.get("price", {}),
        "volatility": metrics.get("volatility", {}),
        "sentiment": metrics.get("sentiment", {}),
        "classification": {
            "cluster": cluster,
            "percentile": percentile,
        },
        "fetch": {
            "latency_ms": round(latency_ms, 1),
        },
        "err": None,
    }


def build_error_row(
    symbol: str,
    seq: int,
    ts_system: float,
    error_msg: str,
) -> dict:
    """Build a JSONL row representing a fetch error."""
    ts_ms = int(ts_system * 1000)
    ts_iso = datetime.fromtimestamp(ts_system, tz=timezone.utc).isoformat(
        timespec="milliseconds"
    )

    return {
        "v": 1,
        "ts_ms": ts_ms,
        "ts_iso": ts_iso,
        "seq": seq,
        "symbol": symbol,
        "price": None,
        "volatility": None,
        "sentiment": None,
        "classification": None,
        "fetch": {"latency_ms": 0},
        "err": error_msg,
    }
