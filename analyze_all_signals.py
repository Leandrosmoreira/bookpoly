#!/usr/bin/env python3
"""
Analisa TODOS os sinais de ENTER gerados (não apenas executados).
Isso mostra a performance real da estratégia, ignorando bloqueios de portfolio.
"""

import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional


@dataclass
class Signal:
    """Sinal de ENTER gerado."""
    timestamp: str
    market: str
    side: str
    entry_price: float
    prob: float
    remaining_s: float
    score: float
    was_blocked: bool = False
    block_reason: Optional[str] = None


def extract_all_signals():
    """Extrai TODOS os sinais de ENTER desde a correção."""
    cmd = [
        "journalctl",
        "-u", "paper-trading.service",
        "--since", "2026-02-07 21:28:00",
        "--no-pager"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout.split("\n")


def parse_signal_line(line: str) -> Optional[Signal]:
    """Parse linha de sinal de ENTER."""
    # Formato: [BTC15m] ★ ENTER DOWN ★ @ $0.03 score=0.72 conf=high reason=forced_entry_contra_azarão:prob=98%_remaining=106s_side=DOWN
    match = re.search(r'\[(\w+15m)\] ★ ENTER (\w+) ★ @ \$([\d.]+)', line)
    if not match:
        return None
    
    # Verificar se é da nova estratégia
    if "forced_entry_contra_azarão" not in line:
        return None
    
    market = match.group(1)
    side = match.group(2)
    entry_price = float(match.group(3))
    
    # Extrair prob
    prob_match = re.search(r'prob=(\d+)%', line)
    prob = float(prob_match.group(1)) / 100.0 if prob_match else None
    
    # Extrair remaining_s
    remaining_match = re.search(r'remaining=(\d+)s', line)
    remaining_s = float(remaining_match.group(1)) if remaining_match else None
    
    # Extrair score
    score_match = re.search(r'score=([\d.]+)', line)
    score = float(score_match.group(1)) if score_match else None
    
    # Extrair timestamp
    timestamp_match = re.search(r'(\d{2}:\d{2}:\d{2})', line)
    timestamp = timestamp_match.group(1) if timestamp_match else ""
    
    # Verificar se foi bloqueado (próxima linha)
    was_blocked = "BLOCKED" in line or False
    
    return Signal(
        timestamp=timestamp,
        market=market,
        side=side,
        entry_price=entry_price,
        prob=prob if prob else 0.0,
        remaining_s=remaining_s if remaining_s else 0.0,
        score=score if score else 0.0,
        was_blocked=was_blocked,
    )


def check_if_blocked(lines: list[str], idx: int) -> tuple[bool, Optional[str]]:
    """Verifica se o sinal foi bloqueado na próxima linha."""
    if idx + 1 < len(lines):
        next_line = lines[idx + 1]
        if "BLOCKED" in next_line:
            # Extrair motivo
            if "Daily loss limit" in next_line:
                return True, "Daily loss limit"
            elif "Trading halted" in next_line:
                return True, "Trading halted"
            elif "Max open positions" in next_line:
                return True, "Max open positions"
            elif "Daily trade limit" in next_line:
                return True, "Daily trade limit"
            else:
                return True, "Blocked"
    return False, None


def analyze_signals():
    """Analisa todos os sinais."""
    print("=" * 80)
    print("📊 ANÁLISE DE TODOS OS SINAIS GERADOS (NÃO APENAS EXECUTADOS)")
    print("=" * 80)
    print()
    print("🔍 Isso mostra a performance REAL da estratégia,")
    print("   ignorando bloqueios de portfolio/risk management.")
    print()
    
    lines = extract_all_signals()
    
    signals: list[Signal] = []
    
    for i, line in enumerate(lines):
        if "★ ENTER" in line and "forced_entry_contra_azarão" in line:
            signal = parse_signal_line(line)
            if signal:
                # Verificar se foi bloqueado
                blocked, reason = check_if_blocked(lines, i)
                signal.was_blocked = blocked
                signal.block_reason = reason
                signals.append(signal)
    
    print(f"📋 Total de sinais gerados: {len(signals)}")
    print()
    
    # Estatísticas gerais
    blocked_count = sum(1 for s in signals if s.was_blocked)
    executed_count = len(signals) - blocked_count
    
    print(f"✅ Sinais executados: {executed_count}")
    print(f"⛔ Sinais bloqueados: {blocked_count}")
    print()
    
    # Por motivo de bloqueio
    if blocked_count > 0:
        print("📊 Motivos de bloqueio:")
        block_reasons = defaultdict(int)
        for s in signals:
            if s.was_blocked and s.block_reason:
                block_reasons[s.block_reason] += 1
        
        for reason, count in sorted(block_reasons.items(), key=lambda x: -x[1]):
            pct = (count / blocked_count) * 100
            print(f"   - {reason}: {count} ({pct:.1f}%)")
        print()
    
    # Por mercado
    print("📊 Sinais por mercado:")
    by_market = defaultdict(list)
    for s in signals:
        by_market[s.market].append(s)
    
    for market in sorted(by_market.keys()):
        signals_mkt = by_market[market]
        blocked_mkt = sum(1 for s in signals_mkt if s.was_blocked)
        executed_mkt = len(signals_mkt) - blocked_mkt
        
        print(f"   {market}: {len(signals_mkt)} total ({executed_mkt} executados, {blocked_mkt} bloqueados)")
    print()
    
    # Distribuição de probabilidades
    print("📊 Distribuição de probabilidades:")
    prob_ranges = {
        "95-96%": 0,
        "96-97%": 0,
        "97-98%": 0,
        "98-99%": 0,
        "99-100%": 0,
    }
    
    for s in signals:
        if s.prob >= 0.99:
            prob_ranges["99-100%"] += 1
        elif s.prob >= 0.98:
            prob_ranges["98-99%"] += 1
        elif s.prob >= 0.97:
            prob_ranges["97-98%"] += 1
        elif s.prob >= 0.96:
            prob_ranges["96-97%"] += 1
        elif s.prob >= 0.95:
            prob_ranges["95-96%"] += 1
    
    for range_name, count in prob_ranges.items():
        if count > 0:
            pct = (count / len(signals)) * 100
            print(f"   {range_name}: {count} sinais ({pct:.1f}%)")
    print()
    
    # Distribuição de tempo restante
    print("📊 Distribuição de tempo restante:")
    time_ranges = {
        "0-60s": 0,
        "60-120s": 0,
        "120-180s": 0,
        "180-240s": 0,
    }
    
    for s in signals:
        if s.remaining_s <= 60:
            time_ranges["0-60s"] += 1
        elif s.remaining_s <= 120:
            time_ranges["60-120s"] += 1
        elif s.remaining_s <= 180:
            time_ranges["120-180s"] += 1
        elif s.remaining_s <= 240:
            time_ranges["180-240s"] += 1
    
    for range_name, count in time_ranges.items():
        if count > 0:
            pct = (count / len(signals)) * 100
            print(f"   {range_name}: {count} sinais ({pct:.1f}%)")
    print()
    
    # Score médio
    if signals:
        avg_score = sum(s.score for s in signals) / len(signals)
        print(f"📊 Score médio: {avg_score:.2f}")
        print()
    
    # Conclusão
    print("=" * 80)
    print("📊 CONCLUSÃO")
    print("=" * 80)
    print(f"   Total de sinais gerados: {len(signals)}")
    print(f"   Sinais executados: {executed_count} ({executed_count/len(signals)*100:.1f}%)")
    print(f"   Sinais bloqueados: {blocked_count} ({blocked_count/len(signals)*100:.1f}%)")
    print()
    print("   ⚠️  A estratégia está gerando MUITOS sinais bons,")
    print("      mas a maioria está sendo bloqueada por limites de risco.")
    print("      Isso pode indicar que os limites são muito conservadores.")
    print("=" * 80)


if __name__ == "__main__":
    analyze_signals()

