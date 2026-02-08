#!/usr/bin/env python3
"""
Dashboard web em tempo real para o bot de paper trading.
"""

from flask import Flask, render_template, jsonify
from pathlib import Path
import re
import json
from datetime import datetime
import time

app = Flask(__name__)

# Caminhos
PROJECT_ROOT = Path(__file__).parent.parent
LOG_DIR = PROJECT_ROOT / "logs"


def get_latest_log_file():
    """Encontra o log mais recente do bot."""
    log_files = list(LOG_DIR.glob("paper_trading_*.log"))
    if not log_files:
        return None
    return max(log_files, key=lambda p: p.stat().st_mtime)


def get_journalctl_log():
    """Lê logs do systemd journal (mais atualizado)."""
    import subprocess
    try:
        result = subprocess.run(
            ['journalctl', '-u', 'paper-trading.service', '--no-pager', '-n', '1000'],
            capture_output=True,
            text=True,
            timeout=2
        )
        if result.returncode == 0:
            return result.stdout
    except:
        pass
    return None


def parse_log_line(line: str) -> dict | None:
    """Extrai informações de uma linha do log."""
    if not line.strip():
        return None
    
    result = {
        "timestamp": None,
        "level": None,
        "message": line.strip(),
        "type": "info"
    }
    
    # Extrair timestamp (formato: HH:MM:SS)
    time_match = re.search(r'(\d{2}:\d{2}:\d{2})', line)
    if time_match:
        result["timestamp"] = time_match.group(1)
    
    # Detectar tipo de mensagem
    if "★ ENTER" in line:
        result["type"] = "enter"
        # Extrair detalhes da entrada
        market_match = re.search(r'\[(\w+15m)\]', line)
        side_match = re.search(r'ENTER (\w+)', line)
        price_match = re.search(r'@ \$([\d.]+)', line)
        score_match = re.search(r'score=([\d.]+)', line)
        
        if market_match:
            result["market"] = market_match.group(1)
        if side_match:
            result["side"] = side_match.group(1)
        if price_match:
            result["entry_price"] = float(price_match.group(1))
        if score_match:
            result["score"] = float(score_match.group(1))
    
    elif "CLOSED" in line or "ended" in line.lower():
        result["type"] = "closed"
        # Extrair detalhes do fechamento (formato: [BTC15m] CLOSED bet=UP result=UP PnL=$+2.50 BTC $69338→$69434)
        market_match = re.search(r'\[(\w+15m)\]', line)
        side_match = re.search(r'bet=(\w+)', line)
        result_match = re.search(r'result=(\w+)', line)
        pnl_match = re.search(r'PnL=\$([+-]?[\d.]+)', line)
        btc_match = re.search(r'BTC \$([\d.]+)→\$([\d.]+)', line)
        
        if market_match:
            result["market"] = market_match.group(1)
        if side_match:
            result["side"] = side_match.group(1)
        if result_match:
            result["result"] = result_match.group(1)
        if pnl_match:
            try:
                result["pnl"] = float(pnl_match.group(1))
            except:
                pass
        if btc_match:
            try:
                result["btc_start"] = float(btc_match.group(1))
                result["btc_end"] = float(btc_match.group(2))
            except:
                pass
    
    elif "BLOCKED" in line:
        result["type"] = "blocked"
        market_match = re.search(r'\[(\w+15m)\]', line)
        reason_match = re.search(r'BLOCKED: (.+)', line)
        if market_match:
            result["market"] = market_match.group(1)
        if reason_match:
            result["reason"] = reason_match.group(1)
    
    elif "📊" in line or "Balance:" in line:
        result["type"] = "summary"
        # Extrair balance e stats (formato: Balance: $100.00 | Open: 0 | Trades: 0 (W:0/L:0) | Win Rate: 0% | PnL: $+0.00 | ROI: +0.0%)
        balance_match = re.search(r'Balance: \$([\d.]+)', line)
        trades_match = re.search(r'Trades: (\d+)', line)
        wins_match = re.search(r'W:(\d+)', line)
        losses_match = re.search(r'L:(\d+)', line)
        pnl_match = re.search(r'PnL: \$([+-]?[\d.]+)', line)
        roi_match = re.search(r'ROI: ([+-]?[\d.]+)%', line)
        open_match = re.search(r'Open: (\d+)', line)
        
        if balance_match:
            result["balance"] = float(balance_match.group(1))
        if trades_match:
            result["total_trades"] = int(trades_match.group(1))
        if wins_match:
            result["wins"] = int(wins_match.group(1))
        if losses_match:
            result["losses"] = int(losses_match.group(1))
        if pnl_match:
            result["pnl"] = float(pnl_match.group(1))
        if roi_match:
            result["roi"] = float(roi_match.group(1))
        if open_match:
            result["open"] = int(open_match.group(1))
    
    return result


def extract_portfolio_stats(log_file: Path) -> dict:
    """Extrai estatísticas do portfólio do log."""
    stats = {
        "balance": 100.0,
        "initial_balance": 100.0,
        "total_trades": 0,
        "open_trades": 0,
        "closed_trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": 0.0,
        "total_pnl": 0.0,
        "daily_pnl": 0.0,
        "roi": 0.0,
        "recent_trades": [],
        "open_positions": []
    }
    
    # Primeiro tentar ler do journalctl (mais atualizado)
    journal_log = get_journalctl_log()
    lines = []
    
    if journal_log:
        # Processar journalctl (formato diferente)
        for line in journal_log.split('\n'):
            # Remover prefixo do journalctl: "Feb 07 20:49:16 srv985979 python[4161511]: "
            if 'python[' in line and 'INFO' in line:
                # Extrair apenas a parte do log após o prefixo
                parts = line.split('INFO  | ', 1)
                if len(parts) > 1:
                    lines.append(parts[1])
                else:
                    # Tentar outro formato
                    if '|' in line:
                        parts = line.split('|', 1)
                        if len(parts) > 1:
                            lines.append(parts[1].strip())
    
    # Se não tiver journal, ler do arquivo
    if not lines and log_file and log_file.exists():
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except:
            pass
    
    if not lines:
        return stats
    
    # Processar linhas (últimas 2000 para pegar mais dados)
    processed_lines = lines[-2000:] if len(lines) > 2000 else lines
    
    # Ler de trás para frente para pegar os dados mais recentes
    latest_summary = None
    for line in reversed(processed_lines):
        if not line.strip():
            continue
            
        parsed = parse_log_line(line)
        if not parsed:
            continue
        
        # Atualizar stats do summary (pegar o mais recente)
        if parsed["type"] == "summary" and latest_summary is None:
            latest_summary = parsed
            if "balance" in parsed:
                stats["balance"] = parsed["balance"]
            if "total_trades" in parsed:
                stats["total_trades"] = parsed["total_trades"]
            if "wins" in parsed:
                stats["wins"] = parsed["wins"]
            if "losses" in parsed:
                stats["losses"] = parsed["losses"]
            if "pnl" in parsed:
                stats["total_pnl"] = parsed["pnl"]
            if "roi" in parsed:
                stats["roi"] = parsed["roi"]
            # Não break - continuar para coletar trades
        
        # Coletar trades recentes (últimos 50)
        if parsed["type"] == "enter":
            # Verificar se já não está na lista
            market = parsed.get("market")
            if market and not any(t.get("market") == market and t.get("type") == "enter" for t in stats["recent_trades"]):
                stats["recent_trades"].insert(0, parsed)
        elif parsed["type"] == "closed":
            stats["recent_trades"].insert(0, parsed)
        
        # Limitar a 50 trades
        if len(stats["recent_trades"]) > 50:
            stats["recent_trades"] = stats["recent_trades"][:50]
    
    # Calcular win rate
    if stats["wins"] + stats["losses"] > 0:
        stats["win_rate"] = (stats["wins"] / (stats["wins"] + stats["losses"])) * 100
    
    # Contar posições abertas (últimas entradas sem fechamento)
    open_markets = set()
    for line in reversed(processed_lines[-1000:]):
        parsed = parse_log_line(line)
        if parsed and parsed["type"] == "enter":
            market = parsed.get("market")
            if market:
                open_markets.add(market)
        elif parsed and parsed["type"] == "closed":
            market = parsed.get("market")
            if market and market in open_markets:
                open_markets.remove(market)
    
    stats["open_trades"] = len(open_markets)
    stats["open_positions"] = list(open_markets)
    
    # Contar trades fechados
    stats["closed_trades"] = stats["wins"] + stats["losses"]
    
    return stats


def get_recent_log_lines(log_file: Path, n: int = 200) -> list:
    """Retorna as últimas N linhas do log."""
    lines = []
    
    # Tentar journalctl primeiro (mais atualizado)
    journal_log = get_journalctl_log()
    if journal_log:
        for line in journal_log.split('\n'):
            if 'python[' in line and ('INFO' in line or 'ERROR' in line):
                parts = line.split('INFO  | ', 1) if 'INFO  |' in line else line.split('|', 1)
                if len(parts) > 1:
                    lines.append(parts[1].strip())
                elif '|' in line:
                    parts = line.split('|', 1)
                    if len(parts) > 1:
                        lines.append(parts[1].strip())
    
    # Se não tiver journal, ler do arquivo
    if not lines and log_file and log_file.exists():
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except:
            pass
    
    # Processar e retornar últimas N linhas
    if not lines:
        return []
    
    recent = lines[-n:] if len(lines) > n else lines
    return [parse_log_line(line) for line in recent if line.strip()]


@app.route('/')
def index():
    """Página principal do dashboard."""
    return render_template('index.html')


@app.route('/api/stats')
def api_stats():
    """API endpoint para estatísticas do portfólio."""
    log_file = get_latest_log_file()
    stats = extract_portfolio_stats(log_file)
    return jsonify(stats)


@app.route('/api/log')
def api_log():
    """API endpoint para últimas linhas do log."""
    log_file = get_latest_log_file()
    lines = get_recent_log_lines(log_file, n=100)
    return jsonify({"lines": lines, "log_file": str(log_file) if log_file else None})


if __name__ == '__main__':
    import sys
    # Tentar portas alternativas se 5001 não funcionar
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5001
    print(f"🌐 Iniciando dashboard na porta {port}...")
    print(f"📊 Acesse: http://0.0.0.0:{port}")
    app.run(host='0.0.0.0', port=port, debug=False)

