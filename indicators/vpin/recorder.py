"""
JSONL row builder for VPIN recorder.
"""

from datetime import datetime, timezone
from calculator import VpinCalculator


def build_row(
    symbol: str,
    calc: VpinCalculator,
    seq: int,
    ts: float,
    latency_ms: float,
) -> dict:
    """Build a JSONL row from current VPIN state."""
    m = calc.get_metrics()

    return {
        "ts_ms": int(ts * 1000),
        "ts_iso": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
        "symbol": symbol,
        "seq": seq,
        "vpin": round(m.vpin, 4) if m.vpin is not None else None,
        "vpin_ema": round(m.vpin_ema, 4) if m.vpin_ema is not None else None,
        "flow_toxicity": m.flow_toxicity,
        "buy_pct_5": round(m.buy_pct_last_5, 3) if m.buy_pct_last_5 is not None else None,
        "bucket_fill_pct": round(m.bucket_fill_pct, 2),
        "avg_bucket_duration_s": round(m.avg_bucket_duration_s, 1),
        "completed_buckets": m.completed_buckets,
        "bucket_volume": m.bucket_volume,
        "trades_total": m.trades_total,
        "latency_ms": round(latency_ms, 1),
    }


def build_error_row(symbol: str, seq: int, ts: float, error: str) -> dict:
    """Build an error JSONL row."""
    return {
        "ts_ms": int(ts * 1000),
        "ts_iso": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
        "symbol": symbol,
        "seq": seq,
        "error": error,
    }
