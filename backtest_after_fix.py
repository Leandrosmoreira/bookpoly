#!/usr/bin/env python3
"""
Backtest do bot APÓS a correção da estratégia.
Analisa apenas trades que seguem a nova estratégia:
- Prob >= 95% ou <= 5%
- Últimos 4 minutos (<=240s)
- Contra o azarão (fade the favorite)
"""

import re
import subprocess
from datetime import datetime
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional


@dataclass
class Trade:
    """Trade extraído do log."""
    timestamp: str
    market: str
    side: str  # UP or DOWN
    entry_price: float
    entry_prob: float  # Probabilidade no momento da entrada
    entry_score: Optional[float] = None
    entry_confidence: Optional[str] = None
    entry_reason: Optional[str] = None
    remaining_s: Optional[float] = None  # Tempo restante na entrada
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    result: Optional[str] = None  # won, lost
    status: str = "open"  # open, closed


def extract_logs_since_today():
    """Extrai logs desde hoje."""
    cmd = [
        "journalctl",
        "-u", "paper-trading.service",
        "--since", "2026-02-08 00:00:00",
        "--no-pager"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout.split("\n")


def parse_enter_line(line: str) -> Optional[Trade]:
    """Parse linha de ENTER."""
    # Formato: [BTC15m] ★ ENTER DOWN ★ @ $0.03 score=0.72 conf=high reason=forced_entry_contra_azarão:prob=97%_remaining=159s_side=DOWN
    match = re.search(r'\[(\w+15m)\] ★ ENTER (\w+) ★ @ \$([\d.]+)', line)
    if not match:
        return None
    
    market = match.group(1)
    side = match.group(2)
    entry_price = float(match.group(3))
    
    # Extrair score
    score_match = re.search(r'score=([\d.]+)', line)
    score = float(score_match.group(1)) if score_match else None
    
    # Extrair confidence
    conf_match = re.search(r'conf=(\w+)', line)
    confidence = conf_match.group(1) if conf_match else None
    
    # Extrair reason (deve conter "forced_entry_contra_azarão" para ser válido)
    reason_match = re.search(r'reason=([^\s]+)', line)
    reason = reason_match.group(1) if reason_match else None
    
    # Extrair prob do reason
    prob_match = re.search(r'prob=(\d+)%', line) if reason else None
    prob = float(prob_match.group(1)) / 100.0 if prob_match else None
    
    # Extrair remaining_s do reason
    remaining_match = re.search(r'remaining=(\d+)s', line) if reason else None
    remaining_s = float(remaining_match.group(1)) if remaining_match else None
    
    # Extrair timestamp
    timestamp_match = re.search(r'(\d{2}:\d{2}:\d{2})', line)
    timestamp = timestamp_match.group(1) if timestamp_match else ""
    
    # Verificar se segue a nova estratégia
    is_new_strategy = (
        reason and "forced_entry_contra_azarão" in reason and
        prob is not None and (prob >= 0.95 or prob <= 0.05) and
        remaining_s is not None and remaining_s <= 240.0
    )
    
    if not is_new_strategy:
        return None  # Ignorar trades da estratégia antiga
    
    return Trade(
        timestamp=timestamp,
        market=market,
        side=side,
        entry_price=entry_price,
        entry_prob=prob if prob else 0.0,
        entry_score=score,
        entry_confidence=confidence,
        entry_reason=reason,
        remaining_s=remaining_s,
    )


def parse_closed_line(line: str) -> Optional[dict]:
    """Parse linha de CLOSED."""
    # Formato: [BTC15m] ✅ CLOSED: bet=DOWN result=UP PnL=$-5.00 BTC $69338→$69434
    match = re.search(r'\[(\w+15m)\] .* CLOSED.*bet=(\w+).*result=(\w+)', line)
    if not match:
        return None
    
    market = match.group(1)
    bet_side = match.group(2)
    result_side = match.group(3)
    
    # Extrair PnL
    pnl_match = re.search(r'PnL=\$([+-]?[\d.]+)', line)
    pnl = float(pnl_match.group(1)) if pnl_match else None
    
    # Determinar se ganhou ou perdeu
    won = (bet_side == result_side)
    
    return {
        "market": market,
        "bet_side": bet_side,
        "result_side": result_side,
        "pnl": pnl,
        "won": won,
    }


def run_backtest():
    """Executa backtest completo."""
    print("=" * 80)
    print("📊 BACKTEST DO BOT - APÓS CORREÇÃO DA ESTRATÉGIA")
    print("=" * 80)
    print()
    print("🔍 Critérios da nova estratégia:")
    print("   - Probabilidade >= 95% ou <= 5%")
    print("   - Últimos 4 minutos (remaining <= 240s)")
    print("   - Sempre CONTRA o azarão (fade the favorite)")
    print()
    
    # Extrair logs
    print("📋 Extraindo logs desde hoje (00:00)...")
    lines = extract_logs_since_today()
    print(f"   Total de linhas: {len(lines)}")
    print()
    
    # Parsear trades
    trades: dict[str, Trade] = {}  # market -> Trade
    closed_trades: list[Trade] = []
    
    for line in lines:
        # ENTER
        if "★ ENTER" in line and "★" in line:
            trade = parse_enter_line(line)
            if trade:
                trades[trade.market] = trade
                print(f"✅ ENTER: {trade.market} {trade.side} @ ${trade.entry_price:.2f} (prob={trade.entry_prob:.1%}, remaining={trade.remaining_s:.0f}s, score={trade.entry_score})")
        
        # CLOSED
        elif "CLOSED" in line:
            closed_data = parse_closed_line(line)
            if closed_data:
                market = closed_data["market"]
                if market in trades:
                    trade = trades.pop(market)
                    trade.exit_price = 1.0 if closed_data["result_side"] == "UP" else 0.0
                    trade.pnl = closed_data["pnl"]
                    trade.result = "won" if closed_data["won"] else "lost"
                    trade.status = "closed"
                    closed_trades.append(trade)
                    emoji = "✅" if closed_data["won"] else "❌"
                    print(f"{emoji} CLOSED: {market} {trade.side} → {closed_data['result_side']} PnL=${trade.pnl:+.2f}")
    
    # Estatísticas
    print()
    print("=" * 80)
    print("📈 ESTATÍSTICAS (APÓS CORREÇÃO)")
    print("=" * 80)
    print()
    
    # Trades abertos
    open_trades = list(trades.values())
    print(f"🔄 Trades Abertos: {len(open_trades)}")
    for trade in open_trades:
        print(f"   - {trade.market} {trade.side} @ ${trade.entry_price:.2f} (prob={trade.entry_prob:.1%}, remaining={trade.remaining_s:.0f}s)")
    print()
    
    # Trades fechados
    print(f"✅ Trades Fechados: {len(closed_trades)}")
    if closed_trades:
        wins = [t for t in closed_trades if t.result == "won"]
        losses = [t for t in closed_trades if t.result == "lost"]
        
        print(f"   ✅ Ganhos: {len(wins)}")
        print(f"   ❌ Perdas: {len(losses)}")
        
        if closed_trades:
            win_rate = len(wins) / len(closed_trades) * 100
            print(f"   📊 Win Rate: {win_rate:.1f}%")
        
        total_pnl = sum(t.pnl for t in closed_trades if t.pnl is not None)
        print(f"   💰 PnL Total: ${total_pnl:+.2f}")
        
        avg_pnl = total_pnl / len(closed_trades) if closed_trades else 0
        print(f"   📈 PnL Médio: ${avg_pnl:+.2f}")
        
        if wins:
            avg_win = sum(t.pnl for t in wins if t.pnl) / len(wins)
            print(f"   ✅ PnL Médio (Ganhos): ${avg_win:+.2f}")
        
        if losses:
            avg_loss = sum(t.pnl for t in losses if t.pnl) / len(losses)
            print(f"   ❌ PnL Médio (Perdas): ${avg_loss:+.2f}")
        
        print()
        print("   📋 Detalhes dos Trades:")
        for trade in closed_trades:
            emoji = "✅" if trade.result == "won" else "❌"
            print(f"      {emoji} {trade.market} {trade.side} @ ${trade.entry_price:.2f} (prob={trade.entry_prob:.1%}) → PnL=${trade.pnl:+.2f}")
    else:
        print("   ⚠️  Nenhum trade fechado ainda com a nova estratégia")
        print("   (O bot pode estar aguardando condições adequadas)")
    
    # Por mercado
    if closed_trades:
        print()
        print("=" * 80)
        print("📊 POR MERCADO")
        print("=" * 80)
        print()
        
        by_market = defaultdict(list)
        for trade in closed_trades:
            by_market[trade.market].append(trade)
        
        for market in sorted(by_market.keys()):
            trades_mkt = by_market[market]
            wins_mkt = [t for t in trades_mkt if t.result == "won"]
            pnl_mkt = sum(t.pnl for t in trades_mkt if t.pnl is not None)
            
            print(f"{market}:")
            print(f"   Trades: {len(trades_mkt)} (✅ {len(wins_mkt)}, ❌ {len(trades_mkt) - len(wins_mkt)})")
            print(f"   PnL: ${pnl_mkt:+.2f}")
            if trades_mkt:
                win_rate_mkt = len(wins_mkt) / len(trades_mkt) * 100
                print(f"   Win Rate: {win_rate_mkt:.1f}%")
            print()
    
    # Resumo final
    print("=" * 80)
    print("📊 RESUMO FINAL (APÓS CORREÇÃO)")
    print("=" * 80)
    print(f"   Período: Desde hoje (00:00) até agora")
    print(f"   Trades Fechados: {len(closed_trades)}")
    print(f"   Trades Abertos: {len(open_trades)}")
    if closed_trades:
        total_pnl = sum(t.pnl for t in closed_trades if t.pnl is not None)
        wins = len([t for t in closed_trades if t.result == "won"])
        win_rate = wins / len(closed_trades) * 100
        print(f"   Win Rate: {win_rate:.1f}%")
        print(f"   PnL Total: ${total_pnl:+.2f}")
        print(f"   ROI: {(total_pnl / (len(closed_trades) * 5.0)) * 100:.1f}%")
    else:
        print("   ⚠️  Ainda não há trades fechados com a nova estratégia")
    print("=" * 80)


if __name__ == "__main__":
    run_backtest()

