"""
Backtest v2: roda o backtest com os 6 conjuntos de parâmetros definidos pelo usuário,
nos dados BTC1h, ETH1h, SOL1h, XRP1h para 2026-02-22 a 2026-02-24.

Parâmetros (tabela do usuário):
  Min Prob. | Max Prob. | Min Tempo Restante | Max Tempo Restante | Share
  93%      | 99%      | 5 min             | 15 min             | 5
  95%      | 99%      | 3min30s           | 15 min             | 5
  ...

Uso:
  cd /root/bookpoly && python -m backtestv2.run_param_grid
  python -m backtestv2.run_param_grid --data-dir data/raw --verbose
"""

import sys
import argparse
from pathlib import Path
from dataclasses import dataclass
from typing import Callable, Optional

# Project root and paths so backtest.* and indicators.signals resolve
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "backtest"))  # so backtest/metrics.py "from simulator" works
sys.path.insert(0, str(PROJECT_ROOT / "indicators" / "signals"))

from backtest.loader import iter_windows, _window_seconds_for_market
from backtest.simulator import Simulator
from backtest.metrics import calculate_metrics, format_metrics
from indicators.signals.config import SignalConfig
from indicators.signals.decision import DecisionConfig


def _progress_bar(done_pct: float, width: int = 30, fill: str = "=", empty: str = "-") -> str:
    """Barra ASCII: done_pct 0..100."""
    p = max(0.0, min(100.0, done_pct))
    n = int(round(width * p / 100))
    return "[" + fill * n + empty * (width - n) + "]"


@dataclass
class ParamSet:
    """Um conjunto de parâmetros para o backtest."""
    name: str
    min_prob: float
    max_prob: float
    min_remaining_s: float
    max_remaining_s: float
    shares: int


# 6 conjuntos de parâmetros (tabela do usuário)
# Tempos convertidos: 5min=300, 15min=900, 3min30s=210, 18min=1080, 3min=180, 12min=720, 4min=240
PARAM_SETS = [
    ParamSet("93%_5m-15m",    0.93, 0.99, 300,  900,  5),
    ParamSet("95%_3m30s-15m", 0.95, 0.99, 210,  900,  5),
    ParamSet("95%_5m-18m",    0.95, 0.99, 300,  1080, 5),
    ParamSet("95%_3m-12m",    0.95, 0.99, 180,  720,  5),
    ParamSet("93%_3m-12m",    0.93, 0.99, 180,  720,  5),
    ParamSet("92%_4m-12m",    0.92, 0.99, 240,  720,  5),
]

MARKETS = ["BTC1h", "ETH1h", "SOL1h", "XRP1h"]
START_DATE = "2026-02-22"
END_DATE = "2026-02-24"


def run_backtest_for_params(
    data_dir: Path,
    param: ParamSet,
    markets: list[str],
    start_date: str,
    end_date: str,
    verbose: bool = False,
    total_windows: Optional[int] = None,
    progress_callback: Optional[Callable[..., None]] = None,
    param_index: int = 0,
    total_params: int = 1,
):
    """
    Roda o backtest para um ParamSet em todos os mercados e datas.
    Retorna lista de WindowResult e o param (para aplicar shares no PnL).
    """
    signal_config = SignalConfig()
    signal_config.max_latency_ms = 999999.0

    decision_config = DecisionConfig()
    decision_config.force_entry_min_prob = param.min_prob
    decision_config.force_entry_max_prob = param.max_prob
    decision_config.force_entry_max_remaining_s = param.max_remaining_s
    decision_config.force_entry_min_remaining_s = param.min_remaining_s
    decision_config.score_low = 0.35  # manter baixo para não bloquear entradas

    simulator = Simulator(signal_config, decision_config)
    results = []
    window_count = 0

    for mi, market in enumerate(markets):
        for suffix in ("15m", "5m", "1h", "4h", "1d"):
            if market.upper().endswith(suffix.upper()):
                coin = market[: -len(suffix)].lower()
                break
        else:
            coin = market.lower()

        duration_s = _window_seconds_for_market(market)
        for window_data in iter_windows(data_dir, start_date, end_date, market):
            result = simulator.simulate_window(
                ticks=window_data.ticks,
                outcome=window_data.outcome,
                coin=coin,
                window_duration_s=duration_s,
                entry_window_max_remaining_s=int(param.max_remaining_s),
                entry_window_min_remaining_s=int(param.min_remaining_s),
            )
            result.market = market
            results.append(result)
            window_count += 1
            if progress_callback:
                if total_windows and total_windows > 0:
                    progress_callback(window_count, total_windows, market, mi + 1, len(markets), None, None)
                else:
                    progress_callback(window_count, None, market, mi + 1, len(markets), param_index, total_params)

    return results, param


def main():
    parser = argparse.ArgumentParser(description="Backtest v2: grid de parâmetros")
    parser.add_argument(
        "--data-dir",
        type=str,
        default=None,
        help="Diretório base (default: project/data/raw)",
    )
    parser.add_argument(
        "--markets",
        type=str,
        default=",".join(MARKETS),
        help="Mercados separados por vírgula (default: BTC1h,ETH1h,SOL1h,XRP1h)",
    )
    parser.add_argument(
        "--start",
        type=str,
        default=START_DATE,
        help=f"Data início YYYY-MM-DD (default: {START_DATE})",
    )
    parser.add_argument(
        "--end",
        type=str,
        default=END_DATE,
        help=f"Data fim YYYY-MM-DD (default: {END_DATE})",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Log por param set")
    args = parser.parse_args()

    # iter_windows e load_books_for_date esperam data_dir com subpasta "books"
    data_dir = Path(args.data_dir) if args.data_dir else PROJECT_ROOT / "data" / "raw"
    if not data_dir.exists():
        print(f"Erro: diretório não encontrado: {data_dir}")
        sys.exit(1)
    if (data_dir / "books").exists():
        base_dir = data_dir
    else:
        # Se passou data/raw/books, sobe um nível
        base_dir = data_dir.parent if (data_dir.parent / "books").exists() else data_dir

    markets_list = [m.strip() for m in args.markets.split(",") if m.strip()]
    start_date = args.start
    end_date = args.end

    print("=" * 80)
    print("BACKTEST V2 — Grid de parâmetros")
    print("=" * 80)
    print(f"Data dir: {base_dir}")
    print(f"Mercados: {markets_list}")
    print(f"Período: {start_date} a {end_date}")
    print(f"Conjuntos de parâmetros: {len(PARAM_SETS)}")
    print()
    # Não contamos janelas (evita OOM em VPS com pouca RAM)
    total_windows = None

    all_metrics = []
    for i, param in enumerate(PARAM_SETS):
        param_index = i
        param_name = param.name

        def _progress(w_count: int, t_total: Optional[int], _market: str, m_i: int, m_n: int,
                     _param_i: Optional[int] = None, _total_p: Optional[int] = None,
                     _i=param_index, _pname=param_name):
            if t_total and t_total > 0 and _param_i is None:
                done = (_i * t_total + w_count) / (len(PARAM_SETS) * t_total) * 100
            else:
                # Progresso estimado por conjunto + mercado (sem carregar tudo na RAM)
                _param_i = _param_i if _param_i is not None else _i
                _total_p = _total_p if _total_p is not None else len(PARAM_SETS)
                done = (_param_i + (m_i - 1) / m_n) / _total_p * 100
            restante = 100.0 - done
            bar = _progress_bar(done)
            janela_str = f"{w_count}/{t_total} janelas" if t_total else f"{w_count} janelas"
            sys.stdout.write(
                f"\r  {bar} {done:5.1f}% concluído — {restante:5.1f}% faltando "
                f"| Conjunto {_i+1}/{len(PARAM_SETS)} ({_pname}) | {_market} {m_i}/{m_n} | {janela_str}   "
            )
            sys.stdout.flush()

        results, p = run_backtest_for_params(
            base_dir, param, markets_list, start_date, end_date,
            verbose=args.verbose,
            total_windows=total_windows,
            progress_callback=_progress,
            param_index=i,
            total_params=len(PARAM_SETS),
        )
        # Nova linha após fim do conjunto
        if total_windows is None or total_windows > 0:
            sys.stdout.write("\n")
            sys.stdout.flush()

        if not results:
            if args.verbose:
                print(f"[{i+1}/{len(PARAM_SETS)}] {param.name}: sem dados")
            all_metrics.append({
                "param_name": param.name,
                "param": param,
                "metrics": None,
                "results": [],
            })
            continue

        metrics = calculate_metrics(results)
        # PnL total em $ considerando shares por trade
        trades = [r.trade for r in results if r.trade is not None]
        pnl_per_share = sum(t.pnl for t in trades if t.pnl is not None)
        total_pnl_with_shares = pnl_per_share * p.shares

        all_metrics.append({
            "param_name": param.name,
            "param": param,
            "metrics": metrics,
            "total_pnl_shares": total_pnl_with_shares,
            "results": results,
        })

        if args.verbose:
            print(f"[{i+1}/{len(PARAM_SETS)}] {param.name}: "
                  f"entries={metrics.entries} win_rate={metrics.win_rate:.1%} "
                  f"total_pnl=${total_pnl_with_shares:.2f} (shares={p.shares})")

    # Tabela resumo
    print()
    print("=" * 80)
    print("RESUMO POR CONJUNTO DE PARÂMETROS")
    print("=" * 80)
    print(f"{'Param':<18} {'Min%':<6} {'Max%':<6} {'MinRem':<8} {'MaxRem':<8} "
          f"{'Entries':<8} {'Win%':<8} {'P&L ($)':<12} {'Sharpe':<8}")
    print("-" * 95)

    for m in all_metrics:
        p = m["param"]
        name = m["param_name"]
        if m["metrics"] is None:
            print(f"{name:<18} {p.min_prob*100:.0f}%   {p.max_prob*100:.0f}%   "
                  f"{p.min_remaining_s:.0f}s    {p.max_remaining_s:.0f}s    "
                  f"{'—':<8} {'—':<8} {'—':<12} {'—':<8}")
            continue
        met = m["metrics"]
        pnl = m.get("total_pnl_shares", met.total_pnl * p.shares)
        print(f"{name:<18} {p.min_prob*100:.0f}%   {p.max_prob*100:.0f}%   "
              f"{p.min_remaining_s:.0f}s    {p.max_remaining_s:.0f}s    "
              f"{met.entries:<8} {met.win_rate:<7.1%} ${pnl:<10.2f} {met.sharpe_ratio:<8.2f}")

    # Detalhe do melhor (maior P&L com shares)
    valid = [m for m in all_metrics if m["metrics"] is not None and m["results"]]
    if valid:
        best = max(valid, key=lambda x: x.get("total_pnl_shares", 0))
        print()
        print("=" * 80)
        print("MELHOR CONJUNTO (maior P&L em $)")
        print("=" * 80)
        print(f"  Nome: {best['param_name']}")
        print(f"  Min Prob: {best['param'].min_prob:.0%}  Max Prob: {best['param'].max_prob:.0%}")
        print(f"  Tempo restante: {best['param'].min_remaining_s:.0f}s a {best['param'].max_remaining_s:.0f}s")
        print(f"  Shares por trade: {best['param'].shares}")
        print()
        print(format_metrics(best["metrics"]))
        print(f"  P&L total (com {best['param'].shares} shares/trade): ${best['total_pnl_shares']:.2f}")

    print()


if __name__ == "__main__":
    main()
