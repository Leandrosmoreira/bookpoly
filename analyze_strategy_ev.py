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
    window_start: int
    window_end: int
    result: Optional[str] = None  # UP or DOWN (resultado real)
    pnl: Optional[float] = None
    won: Optional[bool] = None


def extract_logs_since_15h():
    """Extrai logs desde 15h de hoje."""
    cmd = [
        "journalctl",
        "-u", "paper-trading.service",
        "--since", "2026-02-08 15:00:00",
        "--no-pager"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout.split("\n")


def parse_enter_signal(line: str) -> Optional[Signal]:
    """Parse linha de sinal de ENTER."""
    # Formato 1: [BTC15m] ★ ENTER DOWN ★ @ $0.03 score=0.72 conf=high reason=forced_entry_contra_azarão:prob=98%_remaining=106s_side=DOWN
    # Formato 2: [BTC15m] [T:✓L:✓S:✓V:✓N:✓] ALL:✓ | prob=97.5% zone=caution score=0.72 | ... | ★ ENTER DOWN
    match = re.search(r'\[(\w+15m)\]', line)
    if not match:
        return None
    
    market = match.group(1)
    
    # Tentar formato 1 (executado)
    match1 = re.search(r'★ ENTER (\w+) ★ @ \$([\d.]+)', line)
    if match1:
        side = match1.group(1)
        entry_price = float(match1.group(2))
        
        # Verificar se é da estratégia correta
        if "forced_entry_contra_azarão" not in line:
            return None
    else:
        # Tentar formato 2 (sinal de decisão)
        match2 = re.search(r'★ ENTER (\w+)', line)
        if not match2:
            return None
        
        side = match2.group(1)
        
        # Calcular entry_price baseado na prob
        prob_match = re.search(r'prob=([\d.]+)%', line)
        if not prob_match:
            return None
        
        prob = float(prob_match.group(1)) / 100.0
        entry_price = prob if side == "UP" else (1 - prob)
    
    # Extrair prob
    prob_match = re.search(r'prob=([\d.]+)%', line)
    prob = float(prob_match.group(1)) / 100.0 if prob_match else None
    
    market = match.group(1)
    side = match.group(2)
    entry_price = float(match.group(3))
    
    # Extrair prob
    prob_match = re.search(r'prob=(\d+)%', line)
    prob = float(prob_match.group(1)) / 100.0 if prob_match else None
    
    # Extrair remaining_s
    remaining_match = re.search(r'remaining=(\d+)s', line)
    remaining_s = float(remaining_match.group(1)) if remaining_match else None
    
    # Verificar se atende critérios da estratégia
    if prob is None or remaining_s is None:
        return None
    
    if prob < 0.95 and prob > 0.05:
        return None  # Precisa ser >= 95% ou <= 5%
    
    if remaining_s > 240.0 or remaining_s < 30.0:
        return None  # Últimos 4 minutos, mas não últimos 30s
    
    # Extrair score
    score_match = re.search(r'score=([\d.]+)', line)
    score = float(score_match.group(1)) if score_match else 0.0
    
    # Extrair confidence
    conf_match = re.search(r'conf=(\w+)', line)
    confidence = conf_match.group(1) if conf_match else "unknown"
    
    # Extrair timestamp completo
    timestamp_match = re.search(r'(\w{3} \d{2} \d{2}:\d{2}:\d{2})', line)
    datetime_str = timestamp_match.group(1) if timestamp_match else ""
    
    # Extrair apenas hora:minuto:segundo
    time_match = re.search(r'(\d{2}:\d{2}:\d{2})', line)
    timestamp = time_match.group(1) if time_match else ""
    
    # Calcular window_start e window_end (janela de 15 minutos)
    # Assumindo que remaining_s é o tempo restante na janela
    # window_end = agora + remaining_s
    # window_start = window_end - 900 (15 minutos)
    # Como não temos o timestamp exato, vamos usar remaining_s para identificar a janela
    
    return Signal(
        timestamp=timestamp,
        datetime_str=datetime_str,
        market=market,
        side=side,
        entry_price=entry_price,
        prob=prob,
        remaining_s=remaining_s,
        score=score,
        confidence=confidence,
        window_start=0,  # Será preenchido depois
        window_end=0,  # Será preenchido depois
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
    # Se apostamos UP e resultado foi UP, ganhamos
    # PnL = (1.0 - entry_price) * shares - cost
    # shares = $5 / entry_price
    # cost = $5
    # Se ganhamos: payout = shares * 1.0 = $5 / entry_price
    # PnL = payout - cost = ($5 / entry_price) - $5
    
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
    print("📊 ANÁLISE DA ESTRATÉGIA - Desde 15h de hoje")
    print("=" * 80)
    print()
    
    # Extrair logs
    print("📋 Extraindo logs desde 15h...")
    lines = extract_logs_since_15h()
    print(f"   Total de linhas: {len(lines)}")
    print()
    
    # Parsear sinais
    print("🔍 Identificando sinais de entrada...")
    signals: list[Signal] = []
    
    for line in lines:
        # Buscar todos os sinais de ENTER (executados ou não)
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
    closed_results: dict[str, dict] = {}  # market -> result
    
    for line in lines:
        if "CLOSED" in line:
            result = parse_closed_result(line)
            if result:
                # Usar market como chave (pode haver múltiplos trades no mesmo mercado)
                # Vamos usar timestamp aproximado
                closed_results[result["market"]] = result
    
    print(f"   Resultados encontrados: {len(closed_results)}")
    print()
    
    # Para cada sinal, tentar encontrar o resultado
    # Como não temos timestamp exato da janela, vamos usar heurística:
    # - Se há um resultado fechado para o mercado, usar esse resultado
    # - Caso contrário, marcar como "unknown"
    
    print("📊 Calculando PnL teórico...")
    signals_with_results = []
    
    for signal in signals:
        # Tentar encontrar resultado
        if signal.market in closed_results:
            result_data = closed_results[signal.market]
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
    print(f"   Período: Desde 15h de hoje até agora")
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

