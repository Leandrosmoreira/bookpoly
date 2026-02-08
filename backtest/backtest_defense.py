"""
Backtest do sistema de defesa.

Analisa os 21 losses do backtest original para verificar:
1. Quantas vezes EXIT seria acionado
2. Quantas vezes FLIP seria acionado
3. Se os exits teriam sido lucrativos

Usa dados historicos de order book + volatilidade.
"""

import json
from pathlib import Path
from dataclasses import dataclass, field
from collections import defaultdict, deque
from datetime import datetime
import csv
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from indicators.signals.defense import (
    DefenseConfig,
    DefenseState,
    DefenseAction,
    evaluate_defense,
    format_defense_result,
)


@dataclass
class DefenseBacktestResult:
    """Resultado do backtest de defesa para uma janela."""
    window_start: int
    market: str
    side: str
    entry_price: float
    entry_ts: int

    # Original outcome (sem defesa)
    original_outcome: str
    original_won: bool
    original_pnl: float

    # Defense actions taken
    defense_actions: list = field(default_factory=list)
    exit_triggered: bool = False
    exit_action: str = ""
    exit_ts: int = 0
    exit_price: float = 0.0
    exit_reason: str = ""

    # Defense P&L (se tivesse saido antes)
    defense_pnl: float = 0.0

    # Flip info
    flip_triggered: bool = False
    flip_side: str = ""
    flip_pnl: float = 0.0


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


def load_volatility_data(filepath: Path) -> dict[int, dict]:
    """Carrega dados de volatilidade indexados por timestamp."""
    data = {}
    if not filepath.exists():
        return data

    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                try:
                    row = json.loads(line)
                    ts = int(row.get("ts_system", 0))
                    if ts > 0:
                        # Index by second
                        data[ts] = row
                except json.JSONDecodeError:
                    continue
    return data


def get_volatility_at_ts(vol_data: dict, ts: int, window_s: int = 5) -> dict:
    """Busca dados de volatilidade mais proximos do timestamp."""
    # Procura nos ultimos window_s segundos
    for offset in range(window_s + 1):
        check_ts = ts - offset
        if check_ts in vol_data:
            return vol_data[check_ts]
    return {}


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


def simulate_window_with_defense(
    ticks: list[dict],
    outcome: str,
    market: str,
    vol_data: dict,
    config: DefenseConfig,
) -> DefenseBacktestResult | None:
    """Simula uma janela COM sistema de defesa."""
    if not ticks or not outcome:
        return None

    window_start = ticks[0].get('window_start', 0)

    # Primeiro, encontrar ponto de entrada (mesma logica do backtest original)
    entry_tick = None
    entry_idx = 0

    for idx, tick in enumerate(ticks):
        ts = tick.get('ts_ms', 0) / 1000
        remaining = 900 - (ts - window_start)

        if remaining <= 240 and remaining >= 30:
            yes_data = tick.get('yes', {})
            prob_up = yes_data.get('mid')

            if prob_up is None:
                continue

            imbalance = yes_data.get('imbalance', 0) or 0

            # Estrategia: Entrar COM o favorito quando prob >= 95%
            if prob_up >= 0.95:
                if imbalance < 0:
                    continue
                entry_tick = tick
                entry_idx = idx
                side = "UP"
                entry_price = prob_up
                break
            elif prob_up <= 0.05:
                if imbalance > 0:
                    continue
                entry_tick = tick
                entry_idx = idx
                side = "DOWN"
                entry_price = 1 - prob_up
                break

    if entry_tick is None:
        return None

    # Calcular P&L original (sem defesa)
    original_won = (side == outcome)
    if original_won:
        original_pnl = 1.0 - entry_price
    else:
        original_pnl = -entry_price

    # Inicializar estado de defesa
    defense_state = DefenseState()
    defense_state.start_position(side, entry_price)

    # Historicos para calculos
    imbalance_history = deque(maxlen=60)
    microprice_history = deque(maxlen=60)
    rv_history = deque(maxlen=60)

    result = DefenseBacktestResult(
        window_start=window_start,
        market=market,
        side=side,
        entry_price=entry_price,
        entry_ts=int(entry_tick.get('ts_ms', 0)),
        original_outcome=outcome,
        original_won=original_won,
        original_pnl=original_pnl,
    )

    # Simular ticks apos entrada
    for tick in ticks[entry_idx + 1:]:
        ts = tick.get('ts_ms', 0) / 1000
        remaining = 900 - (ts - window_start)

        if remaining < 0:
            break

        yes_data = tick.get('yes', {})
        prob_up = yes_data.get('mid', 0.5)

        # Extrair indicadores
        imbalance = yes_data.get('imbalance', 0) or 0
        spread = yes_data.get('spread', 0) or 0
        mid = yes_data.get('mid', 0.5) or 0.5
        bid_depth = yes_data.get('bid_depth', 0) or 0
        ask_depth = yes_data.get('ask_depth', 0) or 0

        # Calcular microprice simples
        if bid_depth + ask_depth > 0:
            best_bid = yes_data.get('best_bid', mid)
            best_ask = yes_data.get('best_ask', mid)
            if best_bid and best_ask:
                microprice = (best_bid * ask_depth + best_ask * bid_depth) / (bid_depth + ask_depth)
            else:
                microprice = mid
        else:
            microprice = mid

        microprice_vs_mid = microprice - mid

        # Buscar dados de volatilidade
        vol_row = get_volatility_at_ts(vol_data, int(ts))
        vol_info = vol_row.get('volatility', {}) or {}
        rv_5m = vol_info.get('rv_5m', 0.3) or 0.3

        sentiment = vol_row.get('sentiment', {}) or {}
        taker_ratio = sentiment.get('taker_buy_sell_ratio', 1.0) or 1.0

        class_info = vol_row.get('classification', {}) or {}
        regime = class_info.get('cluster', 'normal')

        # Atualizar historicos
        imbalance_history.append((ts, imbalance))
        microprice_history.append((ts, microprice_vs_mid))
        rv_history.append((ts, rv_5m))

        # Calcular imbalance_delta (30s)
        imbalance_delta = None
        if len(imbalance_history) >= 10:
            old_imb = imbalance_history[0][1]
            imbalance_delta = imbalance - old_imb

        # Calcular z-score simples
        z_score = None
        if len(imbalance_history) >= 30:
            values = [v for _, v in list(imbalance_history)[-30:]]
            mean = sum(values) / len(values)
            variance = sum((x - mean) ** 2 for x in values) / len(values)
            std = variance ** 0.5
            if std > 0:
                z_score = (imbalance - mean) / std

        # Atualizar estado de defesa
        defense_state.update(
            imbalance=imbalance,
            microprice_vs_mid=microprice_vs_mid,
            rv_5m=rv_5m,
            taker_ratio=taker_ratio,
            now_ts=ts,
        )

        # Avaliar defesa
        defense_result = evaluate_defense(
            side=side,
            entry_price=entry_price,
            remaining_s=remaining,
            prob_up=prob_up,
            imbalance=imbalance,
            imbalance_delta=imbalance_delta,
            microprice_vs_mid=microprice_vs_mid,
            taker_ratio=taker_ratio,
            rv_5m=rv_5m,
            regime=regime,
            z_score=z_score,
            state=defense_state,
            config=config,
        )

        # Registrar acao
        if defense_result.action != DefenseAction.HOLD:
            result.defense_actions.append({
                'ts': int(ts * 1000),
                'remaining_s': remaining,
                'action': defense_result.action.value,
                'reason': defense_result.reason,
                'score': defense_result.score,
                'prob_up': prob_up,
            })

            # Primeira acao de saida
            if not result.exit_triggered:
                result.exit_triggered = True
                result.exit_action = defense_result.action.value
                result.exit_ts = int(ts * 1000)
                result.exit_reason = defense_result.reason

                # Calcular preco de saida
                if side == "UP":
                    result.exit_price = prob_up
                else:
                    result.exit_price = 1 - prob_up

                # Calcular P&L de defesa (saida antecipada)
                result.defense_pnl = result.exit_price - entry_price

                # Se for FLIP, calcular P&L do flip
                if defense_result.action == DefenseAction.FLIP:
                    result.flip_triggered = True
                    result.flip_side = "DOWN" if side == "UP" else "UP"

                    # P&L do flip = resultado final - preco de entrada do flip
                    if result.flip_side == "UP":
                        flip_entry = 1 - prob_up
                        flip_won = outcome == "UP"
                    else:
                        flip_entry = prob_up
                        flip_won = outcome == "DOWN"

                    if flip_won:
                        result.flip_pnl = (1.0 - flip_entry) * 0.5  # 50% stake
                    else:
                        result.flip_pnl = -flip_entry * 0.5

                # Parar simulacao apos primeira saida
                break

    return result


def run_defense_backtest(data_dir: Path, vol_dir: Path, verbose: bool = True) -> dict:
    """Executa backtest do sistema de defesa."""
    config = DefenseConfig()

    all_results = []
    stats = {
        'total_entries': 0,
        'original_wins': 0,
        'original_losses': 0,
        'exits_triggered': 0,
        'exit_emergency': 0,
        'exit_tactical': 0,
        'exit_time': 0,
        'flips_triggered': 0,
        'defense_would_save': 0,  # Losses que defesa teria evitado
        'defense_false_positive': 0,  # Wins que defesa teria atrapalhado
    }

    for filepath in sorted(data_dir.glob('*.jsonl')):
        market = filepath.stem.split('_')[0]
        coin = market.replace('15m', '').lower()

        if verbose:
            print(f"Processing {filepath.name}...")

        # Carregar dados
        rows = load_book_data(filepath)
        windows = group_by_windows(rows)

        # Carregar volatilidade para este ativo
        vol_pattern = f"{coin.upper()}USDT_volatility_*.jsonl"
        vol_files = list(vol_dir.glob(vol_pattern))
        vol_data = {}
        for vf in vol_files:
            vol_data.update(load_volatility_data(vf))

        if verbose and vol_data:
            print(f"  Loaded {len(vol_data)} volatility records for {coin}")

        for window_start, ticks in sorted(windows.items()):
            outcome, final_prob = determine_outcome(ticks)
            if not outcome:
                continue

            result = simulate_window_with_defense(
                ticks, outcome, market, vol_data, config
            )

            if result:
                all_results.append(result)
                stats['total_entries'] += 1

                if result.original_won:
                    stats['original_wins'] += 1
                else:
                    stats['original_losses'] += 1

                if result.exit_triggered:
                    stats['exits_triggered'] += 1

                    if result.exit_action == "EXIT_EMERGENCY":
                        stats['exit_emergency'] += 1
                    elif result.exit_action == "EXIT_TACTICAL":
                        stats['exit_tactical'] += 1
                    elif result.exit_action == "EXIT_TIME":
                        stats['exit_time'] += 1

                    if result.flip_triggered:
                        stats['flips_triggered'] += 1

                    # Defesa teria ajudado?
                    if not result.original_won:
                        # Era um loss
                        if result.defense_pnl > result.original_pnl:
                            stats['defense_would_save'] += 1
                    else:
                        # Era um win
                        if result.defense_pnl < result.original_pnl:
                            stats['defense_false_positive'] += 1

    return {
        'results': all_results,
        'stats': stats,
    }


def export_defense_results(results: list[DefenseBacktestResult], output_path: Path):
    """Exporta resultados para CSV."""
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow([
            'window_start', 'market', 'side', 'entry_price',
            'original_outcome', 'original_won', 'original_pnl',
            'exit_triggered', 'exit_action', 'exit_reason',
            'exit_price', 'defense_pnl', 'defense_saved',
            'flip_triggered', 'flip_side', 'flip_pnl',
        ])

        for r in results:
            defense_saved = 0
            if not r.original_won and r.exit_triggered:
                if r.defense_pnl > r.original_pnl:
                    defense_saved = r.defense_pnl - r.original_pnl

            writer.writerow([
                datetime.fromtimestamp(r.window_start).isoformat(),
                r.market,
                r.side,
                f"{r.entry_price:.4f}",
                r.original_outcome,
                "YES" if r.original_won else "NO",
                f"{r.original_pnl:+.4f}",
                "YES" if r.exit_triggered else "NO",
                r.exit_action,
                r.exit_reason,
                f"{r.exit_price:.4f}" if r.exit_triggered else "",
                f"{r.defense_pnl:+.4f}" if r.exit_triggered else "",
                f"{defense_saved:+.4f}" if defense_saved > 0 else "",
                "YES" if r.flip_triggered else "NO",
                r.flip_side if r.flip_triggered else "",
                f"{r.flip_pnl:+.4f}" if r.flip_triggered else "",
            ])


def main():
    """Executa backtest de defesa."""
    data_dir = Path(__file__).parent.parent / 'data' / 'compressed' / 'books'
    if not data_dir.exists():
        data_dir = Path(__file__).parent.parent / 'data' / 'raw' / 'books'

    vol_dir = Path(__file__).parent.parent / 'data' / 'raw' / 'volatility'

    if not data_dir.exists():
        print(f"ERROR: Data directory not found: {data_dir}")
        return

    print("=" * 70)
    print("          BACKTEST DO SISTEMA DE DEFESA")
    print("=" * 70)
    print(f"\nUsando dados de: {data_dir}")
    print(f"Volatilidade de: {vol_dir}")
    print()

    results = run_defense_backtest(data_dir, vol_dir)
    stats = results['stats']

    print("\n" + "=" * 70)
    print("                       RESULTADOS")
    print("=" * 70)

    print(f"\n[ENTRADAS ANALISADAS]")
    print(f"   Total:           {stats['total_entries']}")
    print(f"   Wins originais:  {stats['original_wins']}")
    print(f"   Losses originais:{stats['original_losses']}")

    print(f"\n[ACOES DE DEFESA]")
    print(f"   Exits acionados: {stats['exits_triggered']}")
    print(f"     - Emergency:   {stats['exit_emergency']}")
    print(f"     - Tactical:    {stats['exit_tactical']}")
    print(f"     - Time:        {stats['exit_time']}")
    print(f"   Flips acionados: {stats['flips_triggered']}")

    print(f"\n[EFICACIA DA DEFESA]")
    print(f"   Losses que defesa SALVARIA:    {stats['defense_would_save']} de {stats['original_losses']}")
    print(f"   False positives (wins afetados): {stats['defense_false_positive']}")

    if stats['original_losses'] > 0:
        save_rate = stats['defense_would_save'] / stats['original_losses'] * 100
        print(f"   Taxa de salvamento:            {save_rate:.1f}%")

    # Analise detalhada dos losses
    print(f"\n[ANALISE DOS LOSSES]")
    losses = [r for r in results['results'] if not r.original_won]

    for r in losses:
        dt = datetime.fromtimestamp(r.window_start)
        exit_info = ""
        if r.exit_triggered:
            saved = r.defense_pnl - r.original_pnl
            exit_info = f" | EXIT: {r.exit_action} @ {r.exit_price:.2f} | Saved: ${saved:+.4f}"
        else:
            exit_info = " | NO EXIT triggered"

        print(f"   {dt} | {r.market:8} | {r.side:4} @ {r.entry_price:.2f} | "
              f"P&L: ${r.original_pnl:+.4f}{exit_info}")

    # Exportar resultados
    if results['results']:
        output_path = Path(__file__).parent.parent / 'defense_backtest_results.csv'
        export_defense_results(results['results'], output_path)
        print(f"\nResultados exportados para: {output_path}")


if __name__ == '__main__':
    main()
