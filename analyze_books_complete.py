#!/usr/bin/env python3
"""
Análise completa dos arquivos JSONL de order books.
Gera insights para melhorar a decisão do bot.
"""

import json
import statistics
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional
import sys


def process_jsonl_streaming(filepath: Path, callback):
    """Processa um arquivo JSONL linha por linha (streaming)."""
    count = 0
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        row = json.loads(line)
                        callback(row)
                        count += 1
                        if count % 100000 == 0:
                            print(f"  Processadas {count:,} linhas...", end="\r")
                    except json.JSONDecodeError:
                        continue
    except Exception as e:
        print(f"Erro ao ler {filepath}: {e}", file=sys.stderr)
    return count


def safe_float(value, default=0.0):
    """Converte para float com fallback."""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


class MarketAnalyzer:
    """Analisador de mercado que processa dados incrementalmente."""
    
    def __init__(self, market: str):
        self.market = market
        self.count = 0
        self.spreads = []
        self.spreads_pct = []
        self.depths = []
        self.imbalances = []
        self.probs = []
        self.overrounds = []
        self.latencies = []
        self.windows = set()
        self.hourly_data: Dict[int, Dict] = defaultdict(lambda: {"count": 0, "depths": [], "spreads_pct": []})
    
    def process_row(self, row: dict):
        """Processa uma linha de dados."""
        self.count += 1
        
        yes_data = row.get("yes", {}) or {}
        no_data = row.get("no", {}) or {}
        derived = row.get("derived", {}) or {}
        fetch = row.get("fetch", {}) or {}
        
        # Spread
        spread = safe_float(yes_data.get("spread"))
        mid = safe_float(yes_data.get("mid"))
        if spread > 0 and mid > 0:
            self.spreads.append(spread)
            self.spreads_pct.append((spread / mid) * 100)
        
        # Depth
        bid_depth = safe_float(yes_data.get("bid_depth", 0))
        ask_depth = safe_float(yes_data.get("ask_depth", 0))
        total_depth = bid_depth + ask_depth
        if total_depth > 0:
            self.depths.append(total_depth)
        
        # Imbalance
        imbalance = safe_float(yes_data.get("imbalance"))
        if imbalance is not None:
            self.imbalances.append(imbalance)
        
        # Probability
        prob = safe_float(derived.get("prob_up"))
        if 0 <= prob <= 1:
            self.probs.append(prob)
        
        # Overround
        overround = safe_float(derived.get("overround"))
        if overround is not None:
            self.overrounds.append(overround)
        
        # Latency
        latency = safe_float(fetch.get("latency_ms"))
        if latency > 0:
            self.latencies.append(latency)
        
        # Agrupar por window
        window_start = row.get("window_start", 0)
        if window_start:
            self.windows.add(window_start)
        
        # Agrupar por hora
        ts_ms = row.get("ts_ms", 0)
        if ts_ms:
            dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            hour = dt.hour
            hour_data = self.hourly_data[hour]
            hour_data["count"] += 1
            if mid > 0 and spread > 0:
                hour_data["spreads_pct"].append((spread / mid) * 100)
            if total_depth > 0:
                hour_data["depths"].append(total_depth)
    
    def get_results(self) -> dict:
        """Retorna os resultados da análise."""
        if self.count == 0:
            return {}
        
        def stats(values: List[float], name: str) -> dict:
            if not values:
                return {}
            return {
                f"{name}_count": len(values),
                f"{name}_mean": statistics.mean(values),
                f"{name}_median": statistics.median(values),
                f"{name}_min": min(values),
                f"{name}_max": max(values),
                f"{name}_std": statistics.stdev(values) if len(values) > 1 else 0.0,
                f"{name}_p25": statistics.quantiles(values, n=4)[0] if len(values) > 1 else values[0],
                f"{name}_p75": statistics.quantiles(values, n=4)[2] if len(values) > 1 else values[-1],
                f"{name}_p90": statistics.quantiles(values, n=10)[8] if len(values) > 10 else max(values),
                f"{name}_p95": statistics.quantiles(values, n=20)[18] if len(values) > 20 else max(values),
            }
        
        result = {
            "market": self.market,
            "total_rows": self.count,
            "total_windows": len(self.windows),
            **stats(self.spreads, "spread"),
            **stats(self.spreads_pct, "spread_pct"),
            **stats(self.depths, "depth"),
            **stats(self.imbalances, "imbalance"),
            **stats(self.probs, "prob"),
            **stats(self.overrounds, "overround"),
            **stats(self.latencies, "latency"),
        }
        
        # Análise de distribuição de probabilidade
        if self.probs:
            prob_zones = {
                "danger": sum(1 for p in self.probs if 0 <= p < 0.02 or 0.98 < p <= 1),
                "caution": sum(1 for p in self.probs if 0.02 <= p < 0.05 or 0.95 <= p < 0.98),
                "safe": sum(1 for p in self.probs if 0.05 <= p < 0.15 or 0.85 <= p < 0.95),
                "neutral": sum(1 for p in self.probs if 0.15 <= p < 0.85),
            }
            result["prob_zones"] = prob_zones
            result["prob_zones_pct"] = {
                k: (v / len(self.probs) * 100) if self.probs else 0
                for k, v in prob_zones.items()
            }
        
        # Análise por hora
        hourly_stats = {}
        for hour in sorted(self.hourly_data.keys()):
            hour_data = self.hourly_data[hour]
            hourly_stats[hour] = {
                "count": hour_data["count"],
                "avg_depth": statistics.mean(hour_data["depths"]) if hour_data["depths"] else 0,
                "avg_spread_pct": statistics.mean(hour_data["spreads_pct"]) if hour_data["spreads_pct"] else 0,
            }
        
        result["hourly_stats"] = hourly_stats
        
        return result


def analyze_market_data_streaming(files: List[Path], market: str) -> dict:
    """Analisa dados de um mercado específico usando streaming."""
    analyzer = MarketAnalyzer(market)
    
    for filepath in sorted(files):
        process_jsonl_streaming(filepath, analyzer.process_row)
    
    return analyzer.get_results()


def analyze_all_books(data_dir: Path) -> dict:
    """Analisa todos os arquivos JSONL de order books."""
    books_dir = data_dir / "raw" / "books"
    
    if not books_dir.exists():
        print(f"Diretório não encontrado: {books_dir}")
        return {}
    
    # Agrupar arquivos por mercado
    market_files: Dict[str, List[Path]] = defaultdict(list)
    
    for filepath in books_dir.glob("*.jsonl"):
        # Formato: BTC15m_2026-02-07.jsonl
        parts = filepath.stem.split("_")
        if len(parts) >= 2:
            market = parts[0]
            market_files[market].append(filepath)
    
    results = {}
    
    for market, files in sorted(market_files.items()):
        print(f"\nAnalisando {market}... ({len(files)} arquivos)")
        
        result = analyze_market_data_streaming(files, market)
        
        if result:
            results[market] = result
            print(f"  ✓ {result.get('total_rows', 0):,} registros processados")
        else:
            print(f"  ✗ Nenhum registro encontrado")
    
    return results


def generate_recommendations(analysis: dict) -> List[str]:
    """Gera recomendações baseadas na análise."""
    recommendations = []
    
    for market, data in analysis.items():
        recs = []
        
        # Spread
        spread_pct_p95 = data.get("spread_pct_p95", 0)
        if spread_pct_p95 > 10:
            recs.append(f"Spread muito alto (P95={spread_pct_p95:.1f}%). Considere aumentar max_spread_pct ou evitar horários com spread alto.")
        elif spread_pct_p95 < 2:
            recs.append(f"Spread baixo (P95={spread_pct_p95:.1f}%). Pode reduzir max_spread_pct para ser mais seletivo.")
        
        # Depth
        depth_p25 = data.get("depth_p25", 0)
        if depth_p25 < 300:
            recs.append(f"Depth baixo (P25=${depth_p25:.0f}). Considere aumentar min_depth ou evitar horários com pouca liquidez.")
        
        # Probabilidade
        prob_zones = data.get("prob_zones_pct", {})
        danger_pct = prob_zones.get("danger", 0)
        if danger_pct < 1:
            recs.append(f"Poucas oportunidades em zona danger ({danger_pct:.1f}%). Forced entry pode ser raro.")
        
        safe_pct = prob_zones.get("safe", 0)
        if safe_pct > 30:
            recs.append(f"Muitas oportunidades em zona safe ({safe_pct:.1f}%). Pode ser uma boa zona para operar.")
        
        # Latency
        latency_p95 = data.get("latency_p95", 0)
        if latency_p95 > 500:
            recs.append(f"Latency alta (P95={latency_p95:.0f}ms). Considere aumentar max_latency_ms ou otimizar conexão.")
        
        # Horários
        hourly_stats = data.get("hourly_stats", {})
        if hourly_stats:
            best_hours = sorted(
                hourly_stats.items(),
                key=lambda x: x[1].get("avg_depth", 0),
                reverse=True
            )[:3]
            if best_hours:
                hours_str = ", ".join(f"{h:02d}h" for h, _ in best_hours)
                recs.append(f"Melhores horários para liquidez: {hours_str}")
        
        if recs:
            recommendations.append(f"\n### {market}")
            recommendations.extend(f"  - {r}" for r in recs)
    
    return recommendations


def print_report(analysis: dict):
    """Imprime relatório formatado."""
    print("\n" + "=" * 80)
    print("ANÁLISE COMPLETA DOS ORDER BOOKS")
    print("=" * 80)
    
    for market, data in sorted(analysis.items()):
        print(f"\n{'=' * 80}")
        print(f"MERCADO: {market}")
        print(f"{'=' * 80}")
        
        print(f"\n📊 Estatísticas Gerais:")
        print(f"  Total de registros: {data.get('total_rows', 0):,}")
        print(f"  Total de windows: {data.get('total_windows', 0):,}")
        
        print(f"\n💰 Spread:")
        print(f"  Média: {data.get('spread_mean', 0):.4f} ({data.get('spread_pct_mean', 0):.2f}%)")
        print(f"  Mediana: {data.get('spread_median', 0):.4f} ({data.get('spread_pct_median', 0):.2f}%)")
        print(f"  P95: {data.get('spread_p95', 0):.4f} ({data.get('spread_pct_p95', 0):.2f}%)")
        print(f"  Min: {data.get('spread_min', 0):.4f} | Max: {data.get('spread_max', 0):.4f}")
        
        print(f"\n💧 Depth (Liquidez):")
        print(f"  Média: ${data.get('depth_mean', 0):,.0f}")
        print(f"  Mediana: ${data.get('depth_median', 0):,.0f}")
        print(f"  P25: ${data.get('depth_p25', 0):,.0f} | P75: ${data.get('depth_p75', 0):,.0f}")
        print(f"  P95: ${data.get('depth_p95', 0):,.0f}")
        
        print(f"\n⚖️  Imbalance:")
        print(f"  Média: {data.get('imbalance_mean', 0):.4f}")
        print(f"  Mediana: {data.get('imbalance_median', 0):.4f}")
        print(f"  Range: [{data.get('imbalance_min', 0):.4f}, {data.get('imbalance_max', 0):.4f}]")
        
        print(f"\n📈 Probabilidade:")
        print(f"  Média: {data.get('prob_mean', 0):.2%}")
        print(f"  Mediana: {data.get('prob_median', 0):.2%}")
        zones = data.get("prob_zones_pct", {})
        if zones:
            print(f"  Distribuição por zona:")
            print(f"    Danger (<2% ou >98%): {zones.get('danger', 0):.1f}%")
            print(f"    Caution (2-5% ou 95-98%): {zones.get('caution', 0):.1f}%")
            print(f"    Safe (5-15% ou 85-95%): {zones.get('safe', 0):.1f}%")
            print(f"    Neutral (15-85%): {zones.get('neutral', 0):.1f}%")
        
        print(f"\n⏱️  Latency:")
        print(f"  Média: {data.get('latency_mean', 0):.1f}ms")
        print(f"  Mediana: {data.get('latency_median', 0):.1f}ms")
        print(f"  P95: {data.get('latency_p95', 0):.1f}ms")
        
        hourly_stats = data.get("hourly_stats", {})
        if hourly_stats:
            print(f"\n🕐 Análise por Hora (Top 5 por liquidez):")
            sorted_hours = sorted(
                hourly_stats.items(),
                key=lambda x: x[1].get("avg_depth", 0),
                reverse=True
            )[:5]
            for hour, stats in sorted_hours:
                print(f"  {hour:02d}h: {stats['count']:,} registros | "
                      f"Depth médio: ${stats['avg_depth']:,.0f} | "
                      f"Spread médio: {stats['avg_spread_pct']:.2f}%")
    
    # Recomendações
    print(f"\n{'=' * 80}")
    print("RECOMENDAÇÕES PARA O BOT")
    print(f"{'=' * 80}")
    recommendations = generate_recommendations(analysis)
    if recommendations:
        print("\n".join(recommendations))
    else:
        print("\nNenhuma recomendação específica.")
    
    print(f"\n{'=' * 80}\n")


def main():
    """Função principal."""
    project_root = Path(__file__).parent
    data_dir = project_root / "data"
    
    print("🔍 Iniciando análise completa dos order books...")
    print(f"📁 Diretório: {data_dir}")
    
    analysis = analyze_all_books(data_dir)
    
    if not analysis:
        print("❌ Nenhum dado encontrado para análise.")
        return
    
    print_report(analysis)
    
    # Salvar resultados em JSON
    output_file = project_root / "analysis_books_complete.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(analysis, f, indent=2, ensure_ascii=False)
    print(f"💾 Resultados salvos em: {output_file}")


if __name__ == "__main__":
    main()

