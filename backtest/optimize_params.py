"""
Otimizador de parametros para a estrategia contra-azarao.

Testa diferentes combinacoes de parametros para encontrar os que maximizam ROI.
"""

import json
from pathlib import Path
from dataclasses import dataclass
from collections import defaultdict
from datetime import datetime
from itertools import product
import csv


@dataclass
class Trade:
    """Representa um trade simulado."""
    window_start: int
    market: str
    entry_ts: int
    entry_price: float
    side: str
    prob_at_entry: float
    remaining_s: float
    outcome: str
    pnl: float
    won: bool


@dataclass
class BacktestParams:
    """Parametros para o backtest."""
    # Probabilidade minima para entrada (ex: 0.95 = 95%)
    min_prob: float = 0.95

    # Tempo maximo restante para entrada (segundos)
    max_remaining_s: float = 240.0

    # Tempo minimo restante (seguranca - nao entrar muito perto do fim)
    min_remaining_s: float = 30.0

    # Spread maximo permitido (%)
    max_spread_pct: float = 5.0

    # Profundidade minima do book ($)
    min_depth: float = 100.0

    # Imbalance minimo (direcao do favorito)
    min_imbalance: float = 0.0


def load_book_data(filepath: Path) -> list[dict]:
    """Carrega dados de order book."""
    rows = []
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def group_by_windows(rows: list[dict]) -> dict[int, list[dict]]:
    """Agrupa ticks por window_start."""
    windows = defaultdict(list)
    for row in rows:
        ws = row.get('window_start', 0)
        windows[ws].append(row)

    for ws in windows:
        windows[ws].sort(key=lambda x: x.get('ts_ms', 0))

    return windows


def determine_outcome(ticks: list[dict]) -> tuple[str | None, float | None]:
    """Determina outcome baseado na ultima probabilidade."""
    if not ticks:
        return None, None

    last = ticks[-1]
    yes_data = last.get('yes', {})
    prob_up = yes_data.get('mid', 0.5)

    if prob_up is None:
        return None, None

    ws = last.get('window_start', 0)
    ts = last.get('ts_ms', 0) / 1000
    elapsed = ts - ws

    if elapsed < 870:
        return None, prob_up

    outcome = "UP" if prob_up >= 0.5 else "DOWN"
    return outcome, prob_up


def simulate_window(ticks: list[dict], outcome: str, market: str, params: BacktestParams) -> Trade | None:
    """Simula uma janela com parametros especificos."""
    if not ticks or not outcome:
        return None

    window_start = ticks[0].get('window_start', 0)

    for tick in ticks:
        ts = tick.get('ts_ms', 0) / 1000
        remaining = 900 - (ts - window_start)

        # Verificar tempo restante
        if remaining > params.max_remaining_s or remaining < params.min_remaining_s:
            continue

        yes_data = tick.get('yes', {})
        no_data = tick.get('no', {})
        prob_up = yes_data.get('mid')

        if prob_up is None:
            continue

        # Verificar spread
        spread = yes_data.get('spread') or 0
        mid = yes_data.get('mid') or 0.5
        spread_pct = (spread / mid * 100) if mid > 0 else 100

        if spread_pct > params.max_spread_pct:
            continue

        # Verificar profundidade
        bid_depth = yes_data.get('bid_depth', 0) or 0
        ask_depth = yes_data.get('ask_depth', 0) or 0
        total_depth = bid_depth + ask_depth

        if total_depth < params.min_depth:
            continue

        # Verificar imbalance (opcional)
        imbalance = yes_data.get('imbalance', 0) or 0

        # Estrategia: Entrar COM o favorito quando prob >= min_prob
        if prob_up >= params.min_prob:
            # UP e favorito
            if params.min_imbalance > 0 and imbalance < params.min_imbalance:
                continue  # Imbalance nao favorece UP
            entry_price = prob_up
            side = "UP"
        elif prob_up <= (1 - params.min_prob):
            # DOWN e favorito
            if params.min_imbalance > 0 and imbalance > -params.min_imbalance:
                continue  # Imbalance nao favorece DOWN
            entry_price = 1 - prob_up
            side = "DOWN"
        else:
            continue

        # Calcular P&L
        won = (side == outcome)
        if won:
            pnl = 1.0 - entry_price
        else:
            pnl = -entry_price

        return Trade(
            window_start=window_start,
            market=market,
            entry_ts=int(ts * 1000),
            entry_price=entry_price,
            side=side,
            prob_at_entry=prob_up,
            remaining_s=remaining,
            outcome=outcome,
            pnl=pnl,
            won=won,
        )

    return None


def run_backtest(data_dir: Path, params: BacktestParams) -> dict:
    """Executa backtest com parametros especificos."""
    all_trades = []
    stats = {
        'total_windows': 0,
        'complete_windows': 0,
        'windows_with_entry': 0,
    }

    for filepath in sorted(data_dir.glob('*.jsonl')):
        market = filepath.stem.split('_')[0]

        rows = load_book_data(filepath)
        windows = group_by_windows(rows)

        for window_start, ticks in sorted(windows.items()):
            stats['total_windows'] += 1

            outcome, final_prob = determine_outcome(ticks)
            if outcome:
                stats['complete_windows'] += 1

                trade = simulate_window(ticks, outcome, market, params)
                if trade:
                    stats['windows_with_entry'] += 1
                    all_trades.append(trade)

    if all_trades:
        wins = sum(1 for t in all_trades if t.won)
        losses = len(all_trades) - wins
        total_pnl = sum(t.pnl for t in all_trades)
        total_invested = sum(t.entry_price for t in all_trades)

        return {
            'trades': all_trades,
            'stats': stats,
            'metrics': {
                'total_trades': len(all_trades),
                'wins': wins,
                'losses': losses,
                'win_rate': wins / len(all_trades) if all_trades else 0,
                'total_pnl': total_pnl,
                'total_invested': total_invested,
                'roi': total_pnl / total_invested if total_invested > 0 else 0,
                'avg_pnl_per_trade': total_pnl / len(all_trades) if all_trades else 0,
                'avg_entry_price': total_invested / len(all_trades) if all_trades else 0,
            }
        }

    return {'trades': [], 'stats': stats, 'metrics': {}}


def optimize_params(data_dir: Path) -> list[dict]:
    """Testa diferentes combinacoes de parametros."""

    # Parametros para testar
    param_grid = {
        'min_prob': [0.93, 0.94, 0.95, 0.96, 0.97, 0.98],
        'max_remaining_s': [120, 180, 240, 300],
        'min_remaining_s': [15, 30, 45, 60],
        'max_spread_pct': [2.0, 3.0, 5.0, 10.0],
        'min_depth': [0, 50, 100, 200],
    }

    # Gerar todas as combinacoes
    keys = list(param_grid.keys())
    combinations = list(product(*param_grid.values()))

    print(f"Testando {len(combinations)} combinacoes de parametros...")
    print()

    results = []
    best_roi = -999
    best_params = None

    for i, combo in enumerate(combinations):
        params = BacktestParams(
            min_prob=combo[0],
            max_remaining_s=combo[1],
            min_remaining_s=combo[2],
            max_spread_pct=combo[3],
            min_depth=combo[4],
        )

        # Skip invalid combinations
        if params.min_remaining_s >= params.max_remaining_s:
            continue

        result = run_backtest(data_dir, params)

        if result['metrics']:
            m = result['metrics']

            # Filtrar resultados com poucas entradas
            if m['total_trades'] < 50:
                continue

            results.append({
                'params': params,
                'metrics': m,
            })

            if m['roi'] > best_roi:
                best_roi = m['roi']
                best_params = params
                print(f"[{i+1}/{len(combinations)}] NOVO MELHOR: ROI={m['roi']:+.2%} "
                      f"WR={m['win_rate']:.1%} trades={m['total_trades']} "
                      f"prob>={params.min_prob:.0%} remain<={params.max_remaining_s:.0f}s "
                      f"spread<={params.max_spread_pct:.0f}% depth>=${params.min_depth:.0f}")

        if (i + 1) % 100 == 0:
            print(f"[{i+1}/{len(combinations)}] Processando... melhor ROI ate agora: {best_roi:+.2%}")

    # Ordenar por ROI
    results.sort(key=lambda x: x['metrics']['roi'], reverse=True)

    return results


def main():
    """Executa otimizacao de parametros."""
    data_dir = Path(__file__).parent.parent / 'data' / 'compressed' / 'books'
    if not data_dir.exists():
        data_dir = Path(__file__).parent.parent / 'data' / 'raw' / 'books'

    if not data_dir.exists():
        print(f"ERROR: Data directory not found: {data_dir}")
        return

    print("="*80)
    print("        OTIMIZACAO DE PARAMETROS - Estrategia Contra-Azarao")
    print("="*80)
    print(f"\nUsando dados de: {data_dir}")
    print()

    results = optimize_params(data_dir)

    print("\n" + "="*80)
    print("                      TOP 20 MELHORES COMBINACOES")
    print("="*80)

    print(f"\n{'#':<3} {'ROI':>8} {'WR':>7} {'Trades':>7} {'P&L':>10} {'MinProb':>8} "
          f"{'MaxTime':>8} {'MinTime':>8} {'Spread':>8} {'Depth':>8}")
    print("-"*90)

    for i, r in enumerate(results[:20]):
        m = r['metrics']
        p = r['params']
        print(f"{i+1:<3} {m['roi']:>+7.2%} {m['win_rate']:>6.1%} {m['total_trades']:>7} "
              f"${m['total_pnl']:>9.2f} {p.min_prob:>7.0%} {p.max_remaining_s:>7.0f}s "
              f"{p.min_remaining_s:>7.0f}s {p.max_spread_pct:>7.1f}% ${p.min_depth:>7.0f}")

    if results:
        best = results[0]
        m = best['metrics']
        p = best['params']

        print("\n" + "="*80)
        print("                        MELHOR CONFIGURACAO")
        print("="*80)

        print(f"\n[PARAMETROS OTIMOS]")
        print(f"   min_prob:          {p.min_prob:.0%} (probabilidade minima)")
        print(f"   max_remaining_s:   {p.max_remaining_s:.0f}s (tempo maximo restante)")
        print(f"   min_remaining_s:   {p.min_remaining_s:.0f}s (tempo minimo restante)")
        print(f"   max_spread_pct:    {p.max_spread_pct:.1f}% (spread maximo)")
        print(f"   min_depth:         ${p.min_depth:.0f} (profundidade minima)")

        print(f"\n[PERFORMANCE]")
        print(f"   Total trades:      {m['total_trades']}")
        print(f"   Win Rate:          {m['win_rate']:.1%}")
        print(f"   ROI:               {m['roi']:+.2%}")
        print(f"   Total P&L:         ${m['total_pnl']:.2f}")
        print(f"   EV por trade:      ${m['avg_pnl_per_trade']:+.4f}")

        # Calcular breakeven
        avg_entry = m['avg_entry_price']
        breakeven_wr = avg_entry / 1.0  # entry / (entry + payout) onde payout = 1 - entry
        print(f"   Breakeven WR:      {breakeven_wr:.1%}")
        print(f"   Edge:              {(m['win_rate'] - breakeven_wr)*100:+.2f} pontos percentuais")

        # Exportar melhores resultados para CSV
        output_path = Path(__file__).parent.parent / 'optimization_results.csv'
        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['rank', 'roi', 'win_rate', 'trades', 'total_pnl',
                           'min_prob', 'max_remaining_s', 'min_remaining_s',
                           'max_spread_pct', 'min_depth'])
            for i, r in enumerate(results[:50]):
                m = r['metrics']
                p = r['params']
                writer.writerow([
                    i+1, f"{m['roi']:.4f}", f"{m['win_rate']:.4f}", m['total_trades'],
                    f"{m['total_pnl']:.2f}", f"{p.min_prob:.2f}",
                    f"{p.max_remaining_s:.0f}", f"{p.min_remaining_s:.0f}",
                    f"{p.max_spread_pct:.1f}", f"{p.min_depth:.0f}"
                ])

        print(f"\nResultados exportados para: {output_path}")


if __name__ == '__main__':
    main()
