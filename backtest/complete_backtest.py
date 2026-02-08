"""
Backtest completo da estrategia contra-azarao.

Analisa dados historicos de order book para verificar se a estrategia
de entrar contra o azarao (prob >= 95%) nos ultimos 4 minutos da ROI positivo.

Inclui TODOS os indicadores no CSV para analise.
"""

import json
from pathlib import Path
from dataclasses import dataclass, field
from collections import defaultdict
from datetime import datetime
import csv


@dataclass
class Trade:
    """Representa um trade simulado com todos os indicadores."""
    # Identificacao
    window_start: int
    market: str
    entry_ts: int

    # Trade info
    side: str  # "UP" or "DOWN"
    entry_price: float
    prob_at_entry: float
    remaining_s: float

    # Resultado
    outcome: str
    pnl: float
    won: bool

    # Indicadores do Order Book
    spread: float = 0.0
    spread_pct: float = 0.0
    bid_depth: float = 0.0
    ask_depth: float = 0.0
    total_depth: float = 0.0
    imbalance: float = 0.0

    # Indicadores de Execucao
    latency_ms: float = 0.0

    # Prob do favorito
    prob_favorite: float = 0.0


def load_book_data(filepath: Path) -> list[dict]:
    """Carrega dados de order book de um arquivo JSONL."""
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

    # Ordenar ticks dentro de cada janela por timestamp
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

    # Verificar se janela esta completa
    ws = last.get('window_start', 0)
    ts = last.get('ts_ms', 0) / 1000
    elapsed = ts - ws

    if elapsed < 870:  # Menos de 14.5 minutos
        return None, prob_up

    outcome = "UP" if prob_up >= 0.5 else "DOWN"
    return outcome, prob_up


def simulate_window(ticks: list[dict], outcome: str, market: str) -> Trade | None:
    """Simula uma janela e retorna trade se houver entrada."""
    if not ticks or not outcome:
        return None

    window_start = ticks[0].get('window_start', 0)

    for tick in ticks:
        ts = tick.get('ts_ms', 0) / 1000
        remaining = 900 - (ts - window_start)

        # Verificar se estamos nos ultimos 4 minutos (mas nao ultimos 30s)
        if remaining <= 240 and remaining >= 30:
            yes_data = tick.get('yes', {})
            no_data = tick.get('no', {})
            fetch_data = tick.get('fetch', {})

            prob_up = yes_data.get('mid')

            if prob_up is None:
                continue

            # Extrair indicadores
            spread = yes_data.get('spread') or 0
            mid = yes_data.get('mid') or 0.5

            # Calcular spread_pct corretamente
            if prob_up >= 0.5:
                spread_pct = (spread / mid * 100) if mid > 0.01 else 0
            else:
                spread_pct = (spread / (1 - mid) * 100) if (1 - mid) > 0.01 else 0

            bid_depth = yes_data.get('bid_depth', 0) or 0
            ask_depth = yes_data.get('ask_depth', 0) or 0
            total_depth = bid_depth + ask_depth
            imbalance = yes_data.get('imbalance', 0) or 0
            latency_ms = fetch_data.get('latency_ms', 0) or 0

            # Estrategia: Entrar COM o favorito (contra o azarao) quando prob >= 95%
            if prob_up >= 0.95:
                # UP e favorito (95%), compramos UP a $0.95
                # FILTRO: Imbalance deve ser positivo (confirma direcao UP)
                if imbalance < 0:
                    continue  # Imbalance contra nossa aposta - nao entrar
                entry_price = prob_up
                side = "UP"
                prob_favorite = prob_up
            elif prob_up <= 0.05:
                # DOWN e favorito (95%), compramos DOWN a $0.95
                # FILTRO: Imbalance deve ser negativo (confirma direcao DOWN)
                if imbalance > 0:
                    continue  # Imbalance contra nossa aposta - nao entrar
                entry_price = 1 - prob_up
                side = "DOWN"
                prob_favorite = 1 - prob_up
            else:
                continue  # Prob nao extrema, nao entrar

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
                side=side,
                entry_price=entry_price,
                prob_at_entry=prob_up,
                remaining_s=remaining,
                outcome=outcome,
                pnl=pnl,
                won=won,
                # Indicadores
                spread=spread,
                spread_pct=spread_pct,
                bid_depth=bid_depth,
                ask_depth=ask_depth,
                total_depth=total_depth,
                imbalance=imbalance,
                latency_ms=latency_ms,
                prob_favorite=prob_favorite,
            )

    return None


def run_backtest(data_dir: Path, verbose: bool = True) -> dict:
    """Executa backtest completo."""
    all_trades = []
    stats = {
        'total_windows': 0,
        'complete_windows': 0,
        'windows_with_entry': 0,
        'by_market': defaultdict(lambda: {'windows': 0, 'entries': 0, 'wins': 0})
    }

    for filepath in sorted(data_dir.glob('*.jsonl')):
        market = filepath.stem.split('_')[0]

        if verbose:
            print(f"Processing {filepath.name}...")

        rows = load_book_data(filepath)
        windows = group_by_windows(rows)

        for window_start, ticks in sorted(windows.items()):
            stats['total_windows'] += 1
            stats['by_market'][market]['windows'] += 1

            outcome, final_prob = determine_outcome(ticks)
            if outcome:
                stats['complete_windows'] += 1

                trade = simulate_window(ticks, outcome, market)
                if trade:
                    stats['windows_with_entry'] += 1
                    stats['by_market'][market]['entries'] += 1
                    if trade.won:
                        stats['by_market'][market]['wins'] += 1
                    all_trades.append(trade)

    # Calcular metricas
    if all_trades:
        wins = sum(1 for t in all_trades if t.won)
        losses = len(all_trades) - wins
        total_pnl = sum(t.pnl for t in all_trades)
        total_invested = sum(t.entry_price for t in all_trades)

        up_trades = [t for t in all_trades if t.side == "UP"]
        down_trades = [t for t in all_trades if t.side == "DOWN"]

        up_wins = sum(1 for t in up_trades if t.won)
        down_wins = sum(1 for t in down_trades if t.won)

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
                'up_trades': len(up_trades),
                'up_wins': up_wins,
                'up_win_rate': up_wins / len(up_trades) if up_trades else 0,
                'down_trades': len(down_trades),
                'down_wins': down_wins,
                'down_win_rate': down_wins / len(down_trades) if down_trades else 0,
            }
        }

    return {'trades': [], 'stats': stats, 'metrics': {}}


def export_trades_csv(trades: list[Trade], output_path: Path):
    """Exporta trades para CSV com TODOS os indicadores."""
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        # Header com todos os campos
        writer.writerow([
            'window_start', 'market', 'entry_ts', 'side',
            'prob_at_entry', 'prob_favorite', 'entry_price', 'remaining_s',
            'spread', 'spread_pct', 'bid_depth', 'ask_depth', 'total_depth',
            'imbalance', 'latency_ms',
            'outcome', 'won', 'pnl'
        ])
        for t in trades:
            writer.writerow([
                datetime.fromtimestamp(t.window_start).isoformat(),
                t.market,
                datetime.fromtimestamp(t.entry_ts / 1000).isoformat(),
                t.side,
                f"{t.prob_at_entry:.4f}",
                f"{t.prob_favorite:.4f}",
                f"{t.entry_price:.4f}",
                f"{t.remaining_s:.1f}",
                f"{t.spread:.4f}",
                f"{t.spread_pct:.2f}",
                f"{t.bid_depth:.0f}",
                f"{t.ask_depth:.0f}",
                f"{t.total_depth:.0f}",
                f"{t.imbalance:.4f}",
                f"{t.latency_ms:.0f}",
                t.outcome,
                "YES" if t.won else "NO",
                f"{t.pnl:+.4f}"
            ])


def main():
    """Executa o backtest e mostra resultados."""
    data_dir = Path(__file__).parent.parent / 'data' / 'compressed' / 'books'
    if not data_dir.exists():
        data_dir = Path(__file__).parent.parent / 'data' / 'raw' / 'books'

    if not data_dir.exists():
        print(f"ERROR: Data directory not found: {data_dir}")
        return

    print(f"Using data from: {data_dir}")
    print(f"Files found: {len(list(data_dir.glob('*.jsonl')))}")
    print()

    results = run_backtest(data_dir)

    print("\n" + "="*70)
    print("                    RESULTADOS DO BACKTEST")
    print("                  Estrategia: Contra-Azarao (prob >= 95%)")
    print("="*70)

    stats = results['stats']
    print(f"\n[DADOS ANALISADOS]")
    print(f"   Janelas totais:       {stats['total_windows']:,}")
    print(f"   Janelas completas:    {stats['complete_windows']:,}")
    print(f"   Janelas com entrada:  {stats['windows_with_entry']:,}")
    print(f"   Taxa de entrada:      {stats['windows_with_entry']/stats['complete_windows']*100:.1f}%" if stats['complete_windows'] > 0 else "")

    if results['metrics']:
        m = results['metrics']
        print(f"\n[PERFORMANCE GERAL]")
        print(f"   Total de trades:      {m['total_trades']:,}")
        print(f"   Wins:                 {m['wins']:,}")
        print(f"   Losses:               {m['losses']:,}")
        print(f"   Win Rate:             {m['win_rate']:.1%}")
        print(f"   Total P&L:            ${m['total_pnl']:,.2f}")
        print(f"   Total Investido:      ${m['total_invested']:,.2f}")
        print(f"   ROI:                  {m['roi']:+.1%}")
        print(f"   EV por trade:         ${m['avg_pnl_per_trade']:+.4f}")
        print(f"   Preco medio entrada:  ${m['avg_entry_price']:.4f}")

        print(f"\n[POR LADO]")
        print(f"   UP trades:    {m['up_trades']:,} ({m['up_wins']} wins = {m['up_win_rate']:.1%})")
        print(f"   DOWN trades:  {m['down_trades']:,} ({m['down_wins']} wins = {m['down_win_rate']:.1%})")

        print(f"\n[POR MERCADO]")
        for market, data in sorted(stats['by_market'].items()):
            entries = data['entries']
            wins = data['wins']
            wr = wins / entries * 100 if entries > 0 else 0
            print(f"   {market:8} {entries:3} entries, {wins:3} wins ({wr:.0f}%)")

        # Analise de EV
        print(f"\n[ANALISE DE EV]")
        avg_entry = m['avg_entry_price']
        win_payout = 1.0 - avg_entry
        win_rate = m['win_rate']

        ev = win_rate * win_payout - (1 - win_rate) * avg_entry
        print(f"   Preco medio entrada:  ${avg_entry:.4f}")
        print(f"   Ganho se acertar:     ${win_payout:.4f}")
        print(f"   Perda se errar:       ${avg_entry:.4f}")
        print(f"   Win Rate:             {win_rate:.1%}")
        print(f"   EV calculado:         ${ev:+.4f} per trade")

        breakeven_wr = avg_entry / (avg_entry + win_payout)
        print(f"   Breakeven Win Rate:   {breakeven_wr:.1%}")

        if m['roi'] > 0:
            print(f"\n>>> CONCLUSAO: Estrategia LUCRATIVA (ROI = {m['roi']:+.1%})")
        elif m['roi'] > -0.1:
            print(f"\n>>> CONCLUSAO: Estrategia MARGINAL (ROI = {m['roi']:+.1%})")
        else:
            print(f"\n>>> CONCLUSAO: Estrategia NAO LUCRATIVA (ROI = {m['roi']:+.1%})")

    # Exportar trades para analise
    if results['trades']:
        output_path = Path(__file__).parent.parent / 'backtest_trades.csv'
        export_trades_csv(results['trades'], output_path)
        print(f"\nTrades exportados para: {output_path}")

    # Mostrar exemplos de trades
    if results['trades']:
        losses = [t for t in results['trades'] if not t.won]
        wins = [t for t in results['trades'] if t.won]

        if losses:
            print(f"\n[EXEMPLOS DE LOSSES] ({len(losses)} total):")
            for t in losses[:5]:
                dt = datetime.fromtimestamp(t.window_start)
                print(f"   {dt} | {t.market:8} | {t.side:4} @ {t.prob_favorite:.1%} | "
                      f"spread={t.spread_pct:.1f}% depth=${t.total_depth:,.0f} imb={t.imbalance:+.2f} | "
                      f"Result: {t.outcome} | P&L: {t.pnl:+.4f}")

        if wins:
            print(f"\n[EXEMPLOS DE WINS] ({len(wins)} total):")
            for t in wins[:5]:
                dt = datetime.fromtimestamp(t.window_start)
                print(f"   {dt} | {t.market:8} | {t.side:4} @ {t.prob_favorite:.1%} | "
                      f"spread={t.spread_pct:.1f}% depth=${t.total_depth:,.0f} imb={t.imbalance:+.2f} | "
                      f"Result: {t.outcome} | P&L: {t.pnl:+.4f}")


if __name__ == '__main__':
    main()
