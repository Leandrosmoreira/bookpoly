#!/usr/bin/env python3
"""
Backtest do bot baseado nos logs desde 15h.
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
    entry_score: Optional[float] = None
    entry_confidence: Optional[str] = None
    entry_reason: Optional[str] = None
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    result: Optional[str] = None  # won, lost
    status: str = "open"  # open, closed


def extract_logs_since_15h():
    """Extrai logs desde 15h de hoje."""
    cmd = [
        "journalctl",
        "-u", "paper-trading.service",
        "--since", "2026-02-07 15:00:00",
        "--no-pager"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout.split("\n")


def parse_enter_line(line: str) -> Optional[Trade]:
    """Parse linha de ENTER."""
    # Formato: [BTC15m] ★ ENTER UP ★ @ $0.95 score=0.85 conf=high BTC=$69338 reason=...
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
    
    # Extrair reason
    reason_match = re.search(r'reason=([^\s]+)', line)
    reason = reason_match.group(1) if reason_match else None
    
    # Extrair timestamp
    timestamp_match = re.search(r'(\d{2}:\d{2}:\d{2})', line)
    timestamp = timestamp_match.group(1) if timestamp_match else ""
    
    return Trade(
        timestamp=timestamp,
        market=market,
        side=side,
        entry_price=entry_price,
        entry_score=score,
        entry_confidence=confidence,
        entry_reason=reason,
    )


def parse_closed_line(line: str) -> Optional[dict]:
    """Parse linha de CLOSED."""
    # Formato: [BTC15m] ✅ CLOSED: bet=UP result=UP PnL=$+2.50 BTC $69338→$69434
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


def parse_portfolio_summary(line: str) -> Optional[dict]:
    """Parse linha de resumo do portfolio."""
    # Formato: 📊 Balance=$100.00 | Trades: 0 open, 0 closed | PnL: $0.00 | Wins: 0/0
    match = re.search(r'Balance=\$([\d.]+).*Trades: (\d+) open, (\d+) closed.*PnL: \$([+-]?[\d.]+).*Wins: (\d+)/(\d+)', line)
    if not match:
        return None
    
    return {
        "balance": float(match.group(1)),
        "open_trades": int(match.group(2)),
        "closed_trades": int(match.group(3)),
        "pnl": float(match.group(4)),
        "wins": int(match.group(5)),
        "total": int(match.group(6)),
    }


def run_backtest():
    """Executa backtest completo."""
    print("=" * 80)
    print("📊 BACKTEST DO BOT - Desde 15h de hoje")
    print("=" * 80)
    print()
    
    # Extrair logs
    print("📋 Extraindo logs desde 15h...")
    lines = extract_logs_since_15h()
    print(f"   Total de linhas: {len(lines)}")
    print()
    
    # Parsear trades
    trades: dict[str, Trade] = {}  # market -> Trade
    closed_trades: list[Trade] = []
    portfolio_summaries: list[dict] = []
    
    for line in lines:
        # ENTER
        if "★ ENTER" in line:
            trade = parse_enter_line(line)
            if trade:
                trades[trade.market] = trade
                print(f"✅ ENTER: {trade.market} {trade.side} @ ${trade.entry_price:.2f} (score={trade.entry_score}, conf={trade.entry_confidence})")
        
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
        
        # Portfolio summary
        elif "📊" in line and "Balance=" in line:
            summary = parse_portfolio_summary(line)
            if summary:
                portfolio_summaries.append(summary)
    
    # Estatísticas
    print()
    print("=" * 80)
    print("📈 ESTATÍSTICAS")
    print("=" * 80)
    print()
    
    # Trades abertos
    open_trades = list(trades.values())
    print(f"🔄 Trades Abertos: {len(open_trades)}")
    for trade in open_trades:
        print(f"   - {trade.market} {trade.side} @ ${trade.entry_price:.2f} (entrada: {trade.timestamp})")
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
            print(f"      {emoji} {trade.market} {trade.side} @ ${trade.entry_price:.2f} → PnL=${trade.pnl:+.2f}")
    
    # Por mercado
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
    
    # Último resumo do portfolio
    if portfolio_summaries:
        latest = portfolio_summaries[-1]
        print("=" * 80)
        print("💰 ÚLTIMO STATUS DO PORTFOLIO")
        print("=" * 80)
        print(f"   Balance: ${latest['balance']:.2f}")
        print(f"   Trades Abertos: {latest['open_trades']}")
        print(f"   Trades Fechados: {latest['closed_trades']}")
        print(f"   PnL Total: ${latest['pnl']:+.2f}")
        print(f"   Wins: {latest['wins']}/{latest['total']}")
        if latest['total'] > 0:
            win_rate_portfolio = latest['wins'] / latest['total'] * 100
            print(f"   Win Rate: {win_rate_portfolio:.1f}%")
        print()
    
    # Resumo final
    print("=" * 80)
    print("📊 RESUMO FINAL")
    print("=" * 80)
    print(f"   Período: Desde 15h de hoje até agora")
    print(f"   Trades Fechados: {len(closed_trades)}")
    print(f"   Trades Abertos: {len(open_trades)}")
    if closed_trades:
        total_pnl = sum(t.pnl for t in closed_trades if t.pnl is not None)
        wins = len([t for t in closed_trades if t.result == "won"])
        win_rate = wins / len(closed_trades) * 100
        print(f"   Win Rate: {win_rate:.1f}%")
        print(f"   PnL Total: ${total_pnl:+.2f}")
        print(f"   ROI: {(total_pnl / (len(closed_trades) * 5.0)) * 100:.1f}%")
    print("=" * 80)


if __name__ == "__main__":
    run_backtest()

