import math
from typing import Optional


def realized_volatility(closes: list[float], window: int = 60) -> float:
    """Calculate annualized realized volatility from close prices.

    Uses log returns and annualizes assuming 1-minute data (365*24*60 periods/year).
    """
    if len(closes) < 2:
        return 0.0

    returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    recent = returns[-window:] if len(returns) >= window else returns

    if not recent:
        return 0.0

    variance = sum(r ** 2 for r in recent) / len(recent)
    # Annualize: sqrt(variance) * sqrt(periods_per_year)
    # For 1m data: 365 * 24 * 60 = 525600 periods/year
    annualized = math.sqrt(variance) * math.sqrt(525600)
    return round(annualized, 6)


def parkinson_volatility(highs: list[float], lows: list[float], window: int = 60) -> float:
    """Parkinson volatility estimator using high-low range.

    More efficient than close-to-close as it uses intrabar information.
    Formula: sqrt( sum(ln(H/L)^2) / (4*n*ln(2)) ) * annualization
    """
    if len(highs) < 1 or len(lows) < 1:
        return 0.0

    n = min(window, len(highs), len(lows))
    highs = highs[-n:]
    lows = lows[-n:]

    sum_sq = 0.0
    for h, l in zip(highs, lows):
        if h > 0 and l > 0 and h >= l:
            sum_sq += math.log(h / l) ** 2

    if n == 0:
        return 0.0

    variance = sum_sq / (4 * n * math.log(2))
    annualized = math.sqrt(variance) * math.sqrt(525600)
    return round(annualized, 6)


def garman_klass_volatility(
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    window: int = 60,
) -> float:
    """Garman-Klass volatility estimator using OHLC data.

    More efficient than Parkinson, uses all OHLC information.
    """
    n = min(window, len(opens), len(highs), len(lows), len(closes))
    if n < 1:
        return 0.0

    opens = opens[-n:]
    highs = highs[-n:]
    lows = lows[-n:]
    closes = closes[-n:]

    total = 0.0
    for o, h, l, c in zip(opens, highs, lows, closes):
        if h > 0 and l > 0 and o > 0 and c > 0 and h >= l:
            hl = math.log(h / l) ** 2
            co = math.log(c / o) ** 2
            total += 0.5 * hl - (2 * math.log(2) - 1) * co

    variance = total / n
    # Can be negative in rare cases, clamp to 0
    variance = max(0, variance)
    annualized = math.sqrt(variance) * math.sqrt(525600)
    return round(annualized, 6)


def atr(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> float:
    """Calculate Average True Range."""
    if len(closes) < 2:
        return 0.0

    n = min(len(highs), len(lows), len(closes))
    trs = []

    for i in range(1, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)

    if not trs:
        return 0.0

    recent = trs[-period:] if len(trs) >= period else trs
    return round(sum(recent) / len(recent), 6)


def atr_normalized(atr_value: float, price: float) -> float:
    """Normalize ATR as percentage of price."""
    if price <= 0:
        return 0.0
    return round(atr_value / price, 6)


def volume_volatility(volumes: list[float], window: int = 60) -> float:
    """Calculate standard deviation of volume."""
    if len(volumes) < 2:
        return 0.0

    recent = volumes[-window:] if len(volumes) >= window else volumes
    mean = sum(recent) / len(recent)
    variance = sum((v - mean) ** 2 for v in recent) / len(recent)
    return round(math.sqrt(variance), 2)


def funding_zscore(current_rate: float, historical_rates: list[float]) -> float:
    """Calculate z-score of funding rate vs historical mean."""
    if not historical_rates or len(historical_rates) < 2:
        return 0.0

    mean = sum(historical_rates) / len(historical_rates)
    variance = sum((r - mean) ** 2 for r in historical_rates) / len(historical_rates)
    std = math.sqrt(variance) if variance > 0 else 0.0001

    return round((current_rate - mean) / std, 4)


def oi_change_pct(current_oi: float, previous_oi: float) -> float:
    """Calculate open interest change percentage."""
    if previous_oi <= 0:
        return 0.0
    return round((current_oi - previous_oi) / previous_oi * 100, 4)


def compute_metrics(klines: list[dict], sentiment: dict) -> dict:
    """Compute all volatility and sentiment metrics from raw data."""
    if not klines:
        return {}

    opens = [k["open"] for k in klines]
    highs = [k["high"] for k in klines]
    lows = [k["low"] for k in klines]
    closes = [k["close"] for k in klines]
    volumes = [k["volume"] for k in klines]

    current_price = closes[-1] if closes else 0

    # Volatility metrics
    rv_5m = realized_volatility(closes, 5)
    rv_1h = realized_volatility(closes, 60)
    rv_6h = realized_volatility(closes, 360)

    park_vol = parkinson_volatility(highs, lows, 60)
    gk_vol = garman_klass_volatility(opens, highs, lows, closes, 60)

    atr_val = atr(highs, lows, closes, 14)
    atr_norm = atr_normalized(atr_val, current_price)

    vol_vol = volume_volatility(volumes, 60)

    # Sentiment metrics
    funding_rate = 0.0
    funding_z = 0.0
    if sentiment.get("funding") and len(sentiment["funding"]) > 0:
        funding_rate = sentiment["funding"][0].get("funding_rate", 0)
        # For z-score we'd need historical data, use 0 for now
        funding_z = 0.0

    oi_value = 0.0
    oi_change = 0.0
    if sentiment.get("oi"):
        oi_value = sentiment["oi"].get("open_interest", 0)

    ls_ratio = 1.0
    if sentiment.get("ls_ratio") and len(sentiment["ls_ratio"]) > 0:
        ls_ratio = sentiment["ls_ratio"][0].get("long_short_ratio", 1.0)

    top_ls_ratio = 1.0
    if sentiment.get("top_ls_ratio") and len(sentiment["top_ls_ratio"]) > 0:
        top_ls_ratio = sentiment["top_ls_ratio"][0].get("long_short_ratio", 1.0)

    taker_ratio = 1.0
    if sentiment.get("taker_ratio") and len(sentiment["taker_ratio"]) > 0:
        taker_ratio = sentiment["taker_ratio"][0].get("buy_sell_ratio", 1.0)

    # Composite Volatility Index (CVI)
    # Normalize each component to 0-1 range and weight them
    # Using simple scaling for now (can be improved with historical percentiles)
    cvi = compute_cvi(rv_5m, rv_1h, atr_norm, funding_rate, oi_change, ls_ratio, taker_ratio)

    return {
        "volatility": {
            "rv_5m": rv_5m,
            "rv_1h": rv_1h,
            "rv_6h": rv_6h,
            "parkinson": park_vol,
            "garman_klass": gk_vol,
            "atr_14": atr_val,
            "atr_norm": atr_norm,
            "volume_vol": vol_vol,
            "cvi": cvi,
        },
        "sentiment": {
            "funding_rate": funding_rate,
            "funding_zscore": funding_z,
            "open_interest": oi_value,
            "oi_change_1h_pct": oi_change,
            "long_short_ratio": ls_ratio,
            "top_trader_ls_ratio": top_ls_ratio,
            "taker_buy_sell_ratio": taker_ratio,
        },
        "price": {
            "close": current_price,
            "high_24h": sentiment.get("ticker", {}).get("high_24h", 0) if sentiment.get("ticker") else 0,
            "low_24h": sentiment.get("ticker", {}).get("low_24h", 0) if sentiment.get("ticker") else 0,
            "change_24h_pct": sentiment.get("ticker", {}).get("change_pct", 0) if sentiment.get("ticker") else 0,
        },
    }


def compute_cvi(
    rv_5m: float,
    rv_1h: float,
    atr_norm: float,
    funding_rate: float,
    oi_change: float,
    ls_ratio: float,
    taker_ratio: float,
) -> float:
    """Compute Composite Volatility Index.

    Weights:
    - RV 5m: 30%
    - RV 1h: 20%
    - ATR normalized: 15%
    - Funding rate deviation: 10%
    - OI change: 10%
    - L/S ratio deviation: 10%
    - Taker imbalance: 5%
    """
    # Normalize each component to roughly 0-1 scale
    # Using typical crypto ranges for normalization

    # RV typically 0.1 (10%) to 2.0 (200%) annualized
    rv_5m_norm = min(rv_5m / 1.0, 1.0)
    rv_1h_norm = min(rv_1h / 1.0, 1.0)

    # ATR norm typically 0.001 to 0.05
    atr_norm_scaled = min(atr_norm / 0.03, 1.0)

    # Funding rate typically -0.001 to 0.001, deviation from 0
    funding_dev = min(abs(funding_rate) / 0.001, 1.0)

    # OI change typically -10% to +10%
    oi_dev = min(abs(oi_change) / 10.0, 1.0)

    # L/S ratio typically 0.8 to 1.2, deviation from 1.0
    ls_dev = min(abs(ls_ratio - 1.0) / 0.2, 1.0)

    # Taker ratio typically 0.8 to 1.2, deviation from 1.0
    taker_dev = min(abs(taker_ratio - 1.0) / 0.2, 1.0)

    cvi = (
        0.30 * rv_5m_norm
        + 0.20 * rv_1h_norm
        + 0.15 * atr_norm_scaled
        + 0.10 * funding_dev
        + 0.10 * oi_dev
        + 0.10 * ls_dev
        + 0.05 * taker_dev
    )

    return round(cvi, 4)
