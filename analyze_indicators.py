#!/usr/bin/env python3
"""
Análise completa dos indicadores coletados.

Gera relatório com:
1. Distribuição dos indicadores
2. Thresholds atuais vs recomendados
3. Análise por zona de probabilidade
4. Análise temporal (horário)

Usage:
    python analyze_indicators.py
    python analyze_indicators.py --days 7
    python analyze_indicators.py --output report.txt
"""

import json
import sys
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict


def load_jsonl(filepath: Path) -> list[dict]:
    """Load all rows from a JSONL file."""
    rows = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except FileNotFoundError:
        pass
    return rows


def calculate_stats(values: list[float]) -> dict:
    """Calculate statistics for a list of values."""
    if not values:
        return {"count": 0}

    sorted_vals = sorted(values)
    n = len(sorted_vals)

    mean = sum(values) / n

    # Percentiles
    p25 = sorted_vals[int(n * 0.25)]
    p50 = sorted_vals[int(n * 0.50)]  # median
    p75 = sorted_vals[int(n * 0.75)]
    p95 = sorted_vals[int(n * 0.95)]

    return {
        "count": n,
        "min": min(values),
        "max": max(values),
        "mean": mean,
        "median": p50,
        "p25": p25,
        "p75": p75,
        "p95": p95,
    }


def analyze_books(data_dir: Path, days: int = 7) -> dict:
    """Analyze Polymarket book data."""
    books_dir = data_dir / "books"

    # Collect values
    spreads = []
    spread_pcts = []
    bid_depths = []
    ask_depths = []
    total_depths = []
    imbalances = []
    latencies = []
    prob_ups = []

    # Load files
    files = list(books_dir.glob("*.jsonl"))

    for filepath in files:
        rows = load_jsonl(filepath)

        for row in rows:
            yes = row.get("yes", {}) or {}
            fetch = row.get("fetch", {}) or {}

            # Extract values
            mid = yes.get("mid", 0)
            spread = yes.get("spread")
            bid_depth = yes.get("bid_depth", 0)
            ask_depth = yes.get("ask_depth", 0)
            imbalance = yes.get("imbalance")
            latency = fetch.get("latency_ms")

            if mid and mid > 0:
                prob_ups.append(mid)

            if spread is not None:
                spreads.append(spread)
                if mid and mid > 0:
                    spread_pcts.append(spread / mid * 100)

            if bid_depth:
                bid_depths.append(bid_depth)
            if ask_depth:
                ask_depths.append(ask_depth)
            if bid_depth and ask_depth:
                total_depths.append(bid_depth + ask_depth)

            if imbalance is not None:
                imbalances.append(imbalance)

            if latency is not None:
                latencies.append(latency)

    return {
        "files": len(files),
        "spread": calculate_stats(spreads),
        "spread_pct": calculate_stats(spread_pcts),
        "bid_depth": calculate_stats(bid_depths),
        "ask_depth": calculate_stats(ask_depths),
        "total_depth": calculate_stats(total_depths),
        "imbalance": calculate_stats(imbalances),
        "latency_ms": calculate_stats(latencies),
        "prob_up": calculate_stats(prob_ups),
    }


def analyze_volatility(data_dir: Path, days: int = 7) -> dict:
    """Analyze Binance volatility data."""
    vol_dir = data_dir / "volatility"

    # Collect values
    rv_5m = []
    rv_1h = []
    atr_norm = []
    regimes = defaultdict(int)
    funding_rates = []
    taker_ratios = []

    # Load files
    files = list(vol_dir.glob("*.jsonl"))

    for filepath in files:
        rows = load_jsonl(filepath)

        for row in rows:
            vol = row.get("volatility", {}) or {}
            classification = row.get("classification", {}) or {}
            sentiment = row.get("sentiment", {}) or {}

            if vol.get("rv_5m") is not None:
                rv_5m.append(vol["rv_5m"] * 100)  # Convert to percentage
            if vol.get("rv_1h") is not None:
                rv_1h.append(vol["rv_1h"] * 100)
            if vol.get("atr_norm") is not None:
                atr_norm.append(vol["atr_norm"] * 100)

            regime = classification.get("cluster")
            if regime:
                regimes[regime] += 1

            if sentiment.get("funding_rate") is not None:
                funding_rates.append(sentiment["funding_rate"] * 100)
            if sentiment.get("taker_buy_sell_ratio") is not None:
                taker_ratios.append(sentiment["taker_buy_sell_ratio"])

    return {
        "files": len(files),
        "rv_5m_pct": calculate_stats(rv_5m),
        "rv_1h_pct": calculate_stats(rv_1h),
        "atr_norm_pct": calculate_stats(atr_norm),
        "regimes": dict(regimes),
        "funding_rate_pct": calculate_stats(funding_rates),
        "taker_ratio": calculate_stats(taker_ratios),
    }


def analyze_signals(data_dir: Path, days: int = 7) -> dict:
    """Analyze signals data (if available)."""
    signals_dir = data_dir / "signals"

    if not signals_dir.exists():
        return {"files": 0}

    # Collect values
    scores = []
    decisions = defaultdict(int)
    zones = defaultdict(int)

    files = list(signals_dir.glob("*.jsonl"))

    for filepath in files:
        rows = load_jsonl(filepath)

        for row in rows:
            score = row.get("score", {})
            if score and score.get("value") is not None:
                scores.append(score["value"])

            decision = row.get("decision", {})
            if decision:
                action = decision.get("action")
                if action:
                    decisions[action] += 1

            prob = row.get("probability", {})
            if prob:
                zone = prob.get("zone")
                if zone:
                    zones[zone] += 1

    return {
        "files": len(files),
        "score": calculate_stats(scores),
        "decisions": dict(decisions),
        "zones": dict(zones),
    }


def format_stats(stats: dict, unit: str = "") -> str:
    """Format statistics for display."""
    if stats.get("count", 0) == 0:
        return "  Sem dados"

    lines = [
        f"  Count:   {stats['count']:,}",
        f"  Min:     {stats['min']:.2f}{unit}",
        f"  Max:     {stats['max']:.2f}{unit}",
        f"  Mean:    {stats['mean']:.2f}{unit}",
        f"  Median:  {stats['median']:.2f}{unit}",
        f"  P25:     {stats['p25']:.2f}{unit}",
        f"  P75:     {stats['p75']:.2f}{unit}",
        f"  P95:     {stats['p95']:.2f}{unit}",
    ]
    return "\n".join(lines)


def generate_report(data_dir: Path, days: int = 7) -> str:
    """Generate complete analysis report."""

    lines = []
    lines.append("=" * 60)
    lines.append("📊 ANÁLISE DOS INDICADORES")
    lines.append("=" * 60)
    lines.append(f"Data: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"Diretório: {data_dir}")
    lines.append("")

    # === POLYMARKET BOOKS ===
    lines.append("=" * 60)
    lines.append("📈 POLYMARKET - ORDER BOOKS")
    lines.append("=" * 60)

    books = analyze_books(data_dir, days)
    lines.append(f"\nArquivos analisados: {books['files']}")

    lines.append("\n--- SPREAD (absoluto) ---")
    lines.append(format_stats(books["spread"]))

    lines.append("\n--- SPREAD (%) ---")
    lines.append(format_stats(books["spread_pct"], "%"))

    spread_median = books["spread_pct"].get("median", 0)
    lines.append(f"\n  ⚠️  Threshold atual: 10%")
    lines.append(f"  📊 Mediana real: {spread_median:.1f}%")
    if spread_median > 10:
        lines.append(f"  🔴 RESTRITIVO - considere aumentar para {spread_median * 1.2:.0f}%")
    else:
        lines.append(f"  🟢 OK - maioria dos trades passa")

    lines.append("\n--- DEPTH (bid) ---")
    lines.append(format_stats(books["bid_depth"], " USD"))

    lines.append("\n--- DEPTH (ask) ---")
    lines.append(format_stats(books["ask_depth"], " USD"))

    lines.append("\n--- DEPTH (total) ---")
    lines.append(format_stats(books["total_depth"], " USD"))

    depth_median = books["total_depth"].get("median", 0)
    lines.append(f"\n  ⚠️  Threshold atual: $300")
    lines.append(f"  📊 Mediana real: ${depth_median:,.0f}")
    if depth_median < 300:
        lines.append(f"  🔴 Pouca liquidez - cuidado!")
    else:
        lines.append(f"  🟢 OK - boa liquidez")

    lines.append("\n--- IMBALANCE ---")
    lines.append(format_stats(books["imbalance"]))
    lines.append("  (positivo = mais bids, negativo = mais asks)")

    lines.append("\n--- LATENCY (ms) ---")
    lines.append(format_stats(books["latency_ms"], " ms"))

    latency_median = books["latency_ms"].get("median", 0)
    lines.append(f"\n  ⚠️  Threshold atual: 500ms")
    lines.append(f"  📊 Mediana real: {latency_median:.0f}ms")
    if latency_median > 500:
        lines.append(f"  🔴 RESTRITIVO - considere aumentar")
    else:
        lines.append(f"  🟢 OK")

    lines.append("\n--- PROBABILIDADE (mid) ---")
    lines.append(format_stats(books["prob_up"]))

    # === BINANCE VOLATILITY ===
    lines.append("\n")
    lines.append("=" * 60)
    lines.append("📉 BINANCE - VOLATILIDADE")
    lines.append("=" * 60)

    vol = analyze_volatility(data_dir, days)
    lines.append(f"\nArquivos analisados: {vol['files']}")

    lines.append("\n--- RV 5min (%) ---")
    lines.append(format_stats(vol["rv_5m_pct"], "%"))

    rv_median = vol["rv_5m_pct"].get("median", 0)
    lines.append(f"\n  ⚠️  Threshold atual: 100%")
    lines.append(f"  📊 Mediana real: {rv_median:.1f}%")
    if rv_median > 100:
        lines.append(f"  🔴 RESTRITIVO - considere aumentar para {rv_median * 1.2:.0f}%")
    else:
        lines.append(f"  🟢 OK - maioria dos trades passa")

    lines.append("\n--- RV 1h (%) ---")
    lines.append(format_stats(vol["rv_1h_pct"], "%"))

    lines.append("\n--- ATR Normalizado (%) ---")
    lines.append(format_stats(vol["atr_norm_pct"], "%"))

    lines.append("\n--- REGIME DE VOLATILIDADE ---")
    total_regimes = sum(vol["regimes"].values())
    for regime, count in sorted(vol["regimes"].items()):
        pct = count / total_regimes * 100 if total_regimes > 0 else 0
        bar = "█" * int(pct / 5)
        lines.append(f"  {regime:12s}: {count:5d} ({pct:5.1f}%) {bar}")

    lines.append("\n--- FUNDING RATE (%) ---")
    lines.append(format_stats(vol["funding_rate_pct"], "%"))

    lines.append("\n--- TAKER BUY/SELL RATIO ---")
    lines.append(format_stats(vol["taker_ratio"]))
    lines.append("  (>0.5 = mais compradores, <0.5 = mais vendedores)")

    # === SIGNALS ===
    signals = analyze_signals(data_dir, days)

    if signals.get("files", 0) > 0:
        lines.append("\n")
        lines.append("=" * 60)
        lines.append("🎯 SIGNALS")
        lines.append("=" * 60)
        lines.append(f"\nArquivos analisados: {signals['files']}")

        lines.append("\n--- SCORE ---")
        lines.append(format_stats(signals["score"]))

        lines.append("\n--- DECISÕES ---")
        total_decisions = sum(signals["decisions"].values())
        for action, count in sorted(signals["decisions"].items()):
            pct = count / total_decisions * 100 if total_decisions > 0 else 0
            lines.append(f"  {action:12s}: {count:5d} ({pct:5.1f}%)")

        lines.append("\n--- ZONAS ---")
        total_zones = sum(signals["zones"].values())
        for zone, count in sorted(signals["zones"].items()):
            pct = count / total_zones * 100 if total_zones > 0 else 0
            lines.append(f"  {zone:12s}: {count:5d} ({pct:5.1f}%)")

    # === RECOMENDAÇÕES ===
    lines.append("\n")
    lines.append("=" * 60)
    lines.append("💡 RECOMENDAÇÕES DE THRESHOLDS")
    lines.append("=" * 60)

    lines.append("\n┌─────────────────┬─────────┬─────────────┬─────────────┐")
    lines.append("│ Parâmetro       │ Atual   │ Dados Reais │ Sugerido    │")
    lines.append("├─────────────────┼─────────┼─────────────┼─────────────┤")

    # Spread
    spread_atual = "10%"
    spread_real = f"{spread_median:.1f}%"
    spread_sug = f"{max(10, spread_median * 1.1):.0f}%" if spread_median > 10 else "10% ✓"
    lines.append(f"│ max_spread_pct  │ {spread_atual:7s} │ {spread_real:11s} │ {spread_sug:11s} │")

    # Volatility
    vol_atual = "100%"
    vol_real = f"{rv_median:.1f}%"
    vol_sug = f"{max(100, rv_median * 1.1):.0f}%" if rv_median > 100 else "100% ✓"
    lines.append(f"│ max_volatility  │ {vol_atual:7s} │ {vol_real:11s} │ {vol_sug:11s} │")

    # Depth
    depth_atual = "$300"
    depth_real = f"${depth_median:,.0f}"
    depth_sug = "$300 ✓" if depth_median >= 300 else f"${depth_median * 0.8:.0f}"
    lines.append(f"│ min_depth       │ {depth_atual:7s} │ {depth_real:11s} │ {depth_sug:11s} │")

    # Latency
    lat_atual = "500ms"
    lat_real = f"{latency_median:.0f}ms"
    lat_sug = "500ms ✓" if latency_median < 500 else f"{latency_median * 1.2:.0f}ms"
    lines.append(f"│ max_latency_ms  │ {lat_atual:7s} │ {lat_real:11s} │ {lat_sug:11s} │")

    lines.append("└─────────────────┴─────────┴─────────────┴─────────────┘")

    lines.append("\n")
    lines.append("=" * 60)

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Analyze collected indicators")
    parser.add_argument("--days", type=int, default=7, help="Days to analyze")
    parser.add_argument("--output", type=str, help="Output file")
    parser.add_argument("--data-dir", type=str, default="data/raw", help="Data directory")

    args = parser.parse_args()

    # Find data directory
    script_dir = Path(__file__).parent
    data_dir = script_dir / args.data_dir

    if not data_dir.exists():
        print(f"❌ Diretório não encontrado: {data_dir}")
        sys.exit(1)

    # Generate report
    report = generate_report(data_dir, args.days)

    # Output
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"✅ Relatório salvo em: {args.output}")
    else:
        print(report)


if __name__ == "__main__":
    main()
