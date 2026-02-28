#!/usr/bin/env python3
"""Analisa log JSONL do bot_15min para um dia específico."""
import json
import sys

logfile = sys.argv[1] if len(sys.argv) > 1 else "bot_15min_2026-02-28.jsonl"

fills = []
stop_events = {}  # key: "market:cycle_end_ts"

with open(logfile, "r") as f:
    for line in f:
        try:
            d = json.loads(line.strip())
        except Exception:
            continue
        action = d.get("action", "")
        if action == "FILLED":
            fills.append(d)
        elif action == "STOP_EXECUTED":
            key = f"{d['market']}:{d['cycle_end_ts']}"
            stop_events[key] = d

total_pnl = 0.0
wins = 0
losses = 0
stops = 0
unresolved = 0

print(f"=== {len(fills)} ENTRADAS (FILLS) ===\n")
print(f"{'#':>2}  {'Hora':19s}  {'Mkt':4s}  {'Side':3s}  {'Price':5s}  {'Resultado'}")
print("-" * 75)

for i, fl in enumerate(fills, 1):
    mkt = fl.get("market", "").upper()
    side = fl.get("side", "")
    price = fl.get("price", 0)
    size = 8
    ts = fl.get("ts_iso", "")[:19]
    cycle = fl.get("cycle_end_ts")
    key = f"{fl.get('market')}:{cycle}"

    if key in stop_events:
        pnl = stop_events[key].get("stop_pnl", 0)
        total_pnl += pnl
        stops += 1
        result = f"STOP-LOSS  pnl=${pnl:+.2f}  (prob caiu para {stop_events[key].get('our_price','')})"
    else:
        # Sem outcome no log — mas em mercados 93%+ a taxa de acerto é alta
        # Assumir WIN se prob era >= 93% (entrada no range)
        # Isso é uma estimativa — o log não gravou o outcome real
        assumed_win = True  # Entrada só acontece entre 93-98%
        if assumed_win:
            pnl = round((1.0 - price) * size, 2)
            wins += 1
        else:
            pnl = round(-price * size, 2)
            losses += 1
        total_pnl += pnl
        result = f"WIN (est)  pnl=${pnl:+.2f}  (prob alta, sem outcome no log)"

    print(f"{i:>2}  {ts}  {mkt:4s}  {side:3s}  {price:.2f}   {result}")

print()
print("=" * 75)
print(f"  Entradas:          {len(fills)}")
print(f"  Stop-loss:         {stops}")
print(f"  Wins (estimado):   {wins}")
print(f"  Losses:            {losses}")
print(f"  PnL do dia:        ${total_pnl:+.2f}")
print(f"  PnL médio/trade:   ${total_pnl/len(fills):+.2f}" if fills else "")
print("=" * 75)
print()
print("NOTA: O log não gravou 'outcome_winner' para a maioria dos trades.")
print("Wins estimados assumem que mercados 93%+ resolveram a favor (>93% chance).")
print("O stop-loss do XRP@01:28 confirma prob caiu para 19.5% → vendeu a mercado.")
