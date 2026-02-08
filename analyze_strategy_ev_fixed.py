#!/usr/bin/env python3
"""
Analisa a estratégia desde 15h de hoje:
- Quantas entradas teria dado
- Se é EV positivo
- Gera CSV com todas as entradas teóricas
"""

import re
import subprocess
import csv
from datetime import datetime
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional


@dataclass
class Signal:
    """Sinal de entrada."""
    timestamp: str
    datetime_str: str
    market: str
    side: str  # UP or DOWN
    entry_price: float
    prob: float
    remaining_s: float
    score: float
    confidence: str
    result: Optional[str] = None  # UP or DOWN (resultado real)
    pnl: Optional[float] = None
    won: Optional[bool] = None


def extract_logs_since_15h():
    """Extrai logs desde 00:00 de hoje (ou 15h se especificado)."""
    # Buscar desde 00:00 para ter mais dados
    cmd = [
        "journalctl",
        "-u", "paper-trading.service",
        "--since", "2026-02-08 00:00:00",
        "--no-pager"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout.split("\n")


def parse_enter_signal(line: str) -> Optional[Signal]:
    """Parse linha de sinal de ENTER."""
    # Formato 1: [BTC15m] ★ ENTER DOWN ★ @ $0.03 score=0.72 conf=high reason=forced_entry_contra_azarão:prob=98%_remaining=106s_side=DOWN
    # Formato 2: [BTC15m] [T:✓L:✓S:✓V:✓N:✓] ALL:✓ | prob=97.5% zone=caution score=0.72 | ... | ★ ENTER DOWN
    
    # Extrair market
    market_match = re.search(r'\[(\w+15m)\]', line)
    if not market_match:
        return None
    
    market = market_match.group(1)
    
    # Extrair prob (obrigatório)
    prob_match = re.search(r'prob=([\d.]+)%', line)
    if not prob_match:
        return None
    
    prob = float(prob_match.group(1)) / 100.0
    
    # Verificar se atende critérios da estratégia: prob >= 95% ou <= 5%
    if prob < 0.95 and prob > 0.05:
        return None
    
    # Extrair side
    side_match = re.search(r'★ ENTER (\w+)', line)
    if not side_match:
        return None
    
    side = side_match.group(1)
    
    # Extrair entry_price
    # Se tem formato completo: @ $0.03
    price_match = re.search(r'@ \$([\d.]+)', line)
    if price_match:
        entry_price = float(price_match.group(1))
    else:
        # Calcular baseado na prob
        entry_price = prob if side == "UP" else (1 - prob)
    
    # Extrair remaining_s
    remaining_match = re.search(r'remain=(\d+)s', line)
    if not remaining_match:
        remaining_match = re.search(r'remaining=(\d+)s', line)
    
    remaining_s = float(remaining_match.group(1)) if remaining_match else None
    
    # Verificar remaining_s (últimos 4 minutos, mas não últimos 30s)
    if remaining_s is not None:
        if remaining_s > 240.0 or remaining_s < 30.0:
            return None
    
    # Extrair score
    score_match = re.search(r'score=([\d.]+)', line)
    score = float(score_match.group(1)) if score_match else 0.0
    
    # Extrair confidence
    conf_match = re.search(r'conf=(\w+)', line)
    confidence = conf_match.group(1) if conf_match else "unknown"
    
    # Extrair timestamp
    time_match = re.search(r'(\d{2}:\d{2}:\d{2})', line)
    timestamp = time_match.group(1) if time_match else ""
    
    # Extrair datetime completo
    datetime_match = re.search(r'(\w{3} \d{2} \d{2}:\d{2}:\d{2})', line)
    datetime_str = datetime_match.group(1) if datetime_match else timestamp
    
    return Signal(
        timestamp=timestamp,
        datetime_str=datetime_str,
        market=market,
        side=side,
        entry_price=entry_price,
        prob=prob,
        remaining_s=remaining_s if remaining_s else 0.0,
        score=score,
        confidence=confidence,
    )


def parse_closed_result(line: str) -> Optional[dict]:
    """Parse linha de resultado de trade fechado."""
    # Formato: [BTC15m] ✅ CLOSED: bet=DOWN result=UP BTC $69496→$69538 (+0.06%) PnL=$-5.00
    match = re.search(r'\[(\w+15m)\] .* CLOSED.*bet=(\w+).*result=(\w+)', line)
    if not match:
        return None
    
    market = match.group(1)
    bet_side = match.group(2)
    result_side = match.group(3)
    
    # Extrair PnL
    pnl_match = re.search(r'PnL=\$([+-]?[\d.]+)', line)
    pnl = float(pnl_match.group(1)) if pnl_match else None
    
    return {
        "market": market,
        "bet_side": bet_side,
        "result_side": result_side,
        "pnl": pnl,
    }


def calculate_pnl(entry_price: float, side: str, result: str) -> float:
    """Calcula PnL teórico de uma entrada."""
    size_usd = 5.0
    shares = size_usd / entry_price
    
    if side == result:
        # Ganhamos
        payout = shares * 1.0
        pnl = payout - size_usd
    else:
        # Perdemos tudo
        pnl = -size_usd
    
    return round(pnl, 2)


def analyze_strategy():
    """Analisa a estratégia e gera CSV."""
    print("=" * 80)
    print("📊 ANÁLISE DA ESTRATÉGIA - Desde 00:00 de hoje")
    print("=" * 80)
    print()
    
    # Extrair logs
    print("📋 Extraindo logs desde 00:00...")
    lines = extract_logs_since_15h()
    print(f"   Total de linhas: {len(lines)}")
    print()
    
    # Parsear sinais
    print("🔍 Identificando sinais de entrada...")
    signals: list[Signal] = []
    
    for line in lines:
        if "★ ENTER" in line:
            # Verificar se tem prob >= 95% ou <= 5%
            prob_match = re.search(r'prob=([\d.]+)%', line)
            if prob_match:
                prob = float(prob_match.group(1)) / 100.0
                if prob >= 0.95 or prob <= 0.05:
                    signal = parse_enter_signal(line)
                    if signal:
                        signals.append(signal)
    
    print(f"   Sinais encontrados: {len(signals)}")
    print()
    
    # Parsear resultados de trades fechados
    print("🔍 Identificando resultados de trades...")
    closed_results: list[dict] = []
    
    for line in lines:
        if "CLOSED" in line:
            result = parse_closed_result(line)
            if result:
                closed_results.append(result)
    
    print(f"   Resultados encontrados: {len(closed_results)}")
    print()
    
    # Para cada sinal, tentar encontrar o resultado
    # Como não temos timestamp exato da janela, vamos usar heurística:
    # - Se há um resultado fechado para o mercado próximo no tempo, usar esse resultado
    # - Caso contrário, marcar como "unknown"
    
    print("📊 Calculando PnL teórico...")
    signals_with_results = []
    
    # Criar mapa de resultados por mercado (usar o mais recente)
    results_by_market: dict[str, dict] = {}
    for result in closed_results:
        market = result["market"]
        # Manter o mais recente (último no loop)
        results_by_market[market] = result
    
    for signal in signals:
        # Tentar encontrar resultado
        if signal.market in results_by_market:
            result_data = results_by_market[signal.market]
            signal.result = result_data["result_side"]
            signal.won = (signal.side == signal.result)
            signal.pnl = calculate_pnl(signal.entry_price, signal.side, signal.result)
        else:
            # Sem resultado conhecido (janela ainda não fechou ou não foi executado)
            signal.result = "UNKNOWN"
            signal.won = None
            signal.pnl = None
        
        signals_with_results.append(signal)
    
    # Filtrar apenas sinais com resultado conhecido
    signals_known = [s for s in signals_with_results if s.result != "UNKNOWN"]
    
    print(f"   Sinais com resultado conhecido: {len(signals_known)}")
    print()
    
    # Estatísticas
    if signals_known:
        wins = [s for s in signals_known if s.won]
        losses = [s for s in signals_known if not s.won]
        
        total_pnl = sum(s.pnl for s in signals_known if s.pnl is not None)
        avg_pnl = total_pnl / len(signals_known)
        
        win_rate = len(wins) / len(signals_known) * 100
        
        # EV = Expected Value = (prob_win * avg_win) + (prob_loss * avg_loss)
        prob_win = len(wins) / len(signals_known)
        prob_loss = len(losses) / len(signals_known)
        avg_win = sum(s.pnl for s in wins if s.pnl) / len(wins) if wins else 0
        avg_loss = sum(s.pnl for s in losses if s.pnl) / len(losses) if losses else 0
        ev = (prob_win * avg_win) + (prob_loss * avg_loss)
        
        print("=" * 80)
        print("📈 ESTATÍSTICAS")
        print("=" * 80)
        print(f"   Total de entradas: {len(signals_known)}")
        print(f"   Ganhos: {len(wins)}")
        print(f"   Perdas: {len(losses)}")
        print(f"   Win Rate: {win_rate:.1f}%")
        print(f"   PnL Total: ${total_pnl:+.2f}")
        print(f"   PnL Médio: ${avg_pnl:+.2f}")
        print(f"   PnL Médio (Ganhos): ${avg_win:+.2f}")
        print(f"   PnL Médio (Perdas): ${avg_loss:+.2f}")
        print(f"   Expected Value (EV): ${ev:+.2f}")
        print()
        
        if ev > 0:
            print("   ✅ ESTRATÉGIA É EV POSITIVO!")
        else:
            print("   ❌ Estratégia é EV negativo")
        print()
    
    # Gerar CSV
    csv_filename = "estrategia_entradas_15h.csv"
    print(f"📄 Gerando CSV: {csv_filename}...")
    
    with open(csv_filename, 'w', newline='') as csvfile:
        fieldnames = [
            'timestamp', 'datetime', 'market', 'side', 'entry_price', 
            'prob', 'remaining_s', 'score', 'confidence',
            'result', 'won', 'pnl'
        ]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        
        for signal in signals_with_results:
            writer.writerow({
                'timestamp': signal.timestamp,
                'datetime': signal.datetime_str,
                'market': signal.market,
                'side': signal.side,
                'entry_price': f"{signal.entry_price:.4f}",
                'prob': f"{signal.prob:.1%}",
                'remaining_s': f"{signal.remaining_s:.0f}",
                'score': f"{signal.score:.2f}",
                'confidence': signal.confidence,
                'result': signal.result or "UNKNOWN",
                'won': "YES" if signal.won else "NO" if signal.won is False else "UNKNOWN",
                'pnl': f"{signal.pnl:+.2f}" if signal.pnl is not None else "UNKNOWN",
            })
    
    print(f"   ✅ CSV gerado: {csv_filename}")
    print(f"   Total de linhas: {len(signals_with_results)}")
    print()
    
    # Resumo final
    print("=" * 80)
    print("📊 RESUMO FINAL")
    print("=" * 80)
    print(f"   Período: Desde 00:00 de hoje até agora")
    print(f"   Total de sinais gerados: {len(signals)}")
    print(f"   Sinais com resultado conhecido: {len(signals_known)}")
    print(f"   Sinais sem resultado (janela aberta): {len(signals) - len(signals_known)}")
    if signals_known:
        print(f"   Win Rate: {len([s for s in signals_known if s.won]) / len(signals_known) * 100:.1f}%")
        print(f"   PnL Total: ${sum(s.pnl for s in signals_known if s.pnl):+.2f}")
        print(f"   EV: ${ev:+.2f}")
    print("=" * 80)


if __name__ == "__main__":
    analyze_strategy()

