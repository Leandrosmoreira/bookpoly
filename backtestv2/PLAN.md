# PLAN — backtestv2 (Data Builder + Backtest Engine)

## Meta

Construir um sistema reprodutivel e auditavel de backtest para Polymarket (BTC/ETH/SOL/XRP, janelas 15m/1h).

**Dois modulos independentes:**

1. **Data Builder** — normaliza raw JSONL do recorder e "congela" dataset enriquecido com features
2. **Backtest Engine** — roda estrategias sobre dataset congelado (sem recalcular features)

**Regra de ouro:** mudar feature = gerar dataset v2. O backtest sempre roda em dataset versionado.

---

## Analise do plan original do analista

### O que esta BOM (mantido):
- Conceito de dataset congelado + manifest (reprodutibilidade)
- Separacao builder vs engine
- Lista de features (microprice, imbalance, rv, z-scores)
- Logs auditaveis (decisions, orders, fills)
- Train/test split para evitar overfit

### O que foi "viagem" (removido/simplificado):

| Item do analista | Problema | Solucao |
|---|---|---|
| `pyproject.toml` | Projeto usa `requirements.txt` | Manter padrao existente |
| YAML configs | Projeto usa env vars + dataclasses | Usar dataclasses Python |
| `structlog` | Projeto usa `logging` padrao | Manter `logging` |
| `schema.py` validacao complexa | Over-engineering | Asserts simples no builder |
| `grid_search.py` + leaderboard | Prematuro, poucos dados | Implementar depois se necessario |
| Walk-forward analysis | Overkill para o estagio atual | Futuro |
| Strategy Score com regime filters | Sem dados suficientes para regime detection | Comeca com threshold, score futuro |
| Modelo de execucao maker/taker/hybrid | Complexidade desnecessaria | Simular o que o bot REALMENTE faz |
| `partial_fill` + `slippage_model` | Nao reflete a Polymarket real | Polymarket = fill total ou nao |
| `equity_curve.csv` continuo | Polymarket nao e equity continua | PnL por ciclo/window |

### O que FALTOU no plan do analista:

| Gap | Importancia |
|---|---|
| **Reusar backtest/ existente** | Ja tem loader, simulator, metrics funcionais |
| **Reusar microstructure.py existente** | Features ja implementadas (microprice, imbalance, depth_ratio, etc.) |
| **Compatibilidade com dados reais** | Schema do raw e `v:2` com `yes/no/derived`, nao o que o analista assumiu |
| **Dados 1h alem de 15m** | Plan so mencionou BTC-15M, mas temos BTC1h agora |
| **VPIN como feature** | VPIN acabou de ser implementado, deveria ser feature no dataset |
| **Logica do bot real** | O backtest deveria simular bot_15min.py / bot_light.py, nao inventar estrategias novas |

---

## 0) Estrutura do projeto

```
backtestv2/
    PLAN.md                     # Este arquivo
    __init__.py

    builder/
        __init__.py
        config.py               # BuilderConfig (dataclass)
        normalize.py            # raw JSONL -> tick canonico
        features.py             # book math + rolling features
        validate.py             # quality flags + sanity checks
        build_dataset.py        # CLI: raw -> dataset_vX + manifest

    engine/
        __init__.py
        config.py               # EngineConfig (dataclass)
        types.py                # Order, Fill, Position, Trade, WindowResult
        execution.py            # Simulacao maker com timeout + retry
        portfolio.py            # PnL por window/ciclo
        metrics.py              # EV, PF, DD, Sharpe, win_rate, zones
        engine.py               # Loop event-driven por window
        run_backtest.py         # CLI: dataset_vX -> results/

    strategies/
        __init__.py
        base.py                 # StrategyBase ABC
        threshold.py            # Regra: prob >= X, time_left em [Y1,Y2]
        bot_light.py            # Replica logica do scripts/bot_light.py

    data/
        datasets/               # Datasets congelados (output do builder)
        results/                # Resultados de runs (output do engine)

    logs/                       # Logs JSONL dos runs
```

~1200-1500 linhas total.

---

## 1) MODULO 1 — Data Builder

### 1.1 Entrada (raw)

Arquivos em `data/raw/books/*.jsonl` com schema v2:

```json
{
    "v": 2,
    "ts_ms": 1770258056041,
    "ts_iso": "2026-02-05T02:20:56.041+00:00",
    "seq": 0,
    "market": "BTC15m",
    "condition_id": "0x...",
    "window_start": 1770257700,
    "yes": {
        "token_id": "...",
        "best_bid": 0.77, "best_ask": 0.78,
        "mid": 0.775, "spread": 0.01,
        "bid_depth": 36393.09, "ask_depth": 38073.24,
        "imbalance": -0.0226,
        "bids": [{"p": 0.77, "s": 2001.03}, ...],
        "asks": [{"p": 0.78, "s": 10.0}, ...]
    },
    "no": { ... },
    "derived": {
        "prob_up": 0.775, "prob_down": 0.225,
        "overround": 0.01
    }
}
```

**Mercados suportados:** BTC15m, ETH15m, SOL15m, XRP15m, BTC1h

### 1.2 Saida (dataset congelado)

**(A) `{market}_v{N}_features.jsonl`** — um tick por linha:

```json
{
    "ts_ms": 1770258056041,
    "seq": 0,
    "market": "BTC15m",
    "window_start": 1770257700,
    "window_duration_s": 900,
    "time_remaining_s": 644,

    "book": {
        "best_bid": 0.77, "best_ask": 0.78,
        "mid": 0.775, "spread": 0.01, "spread_bps": 129
    },

    "features": {
        "microprice": 0.776,
        "microprice_edge": 0.001,
        "imbalance_1": -0.02,
        "imbalance_3": -0.01,
        "imbalance_5": 0.005,
        "imbalance_10": 0.01,
        "depth_bid_5": 5000.0,
        "depth_ask_5": 5200.0,
        "depth_shift_5": -200.0,
        "prob_delta_5s": 0.005,
        "prob_delta_10s": 0.01,
        "prob_momentum_30s": 0.02,
        "rv_30s": 0.001,
        "rv_60s": 0.0015,
        "z_prob_60": 1.2,
        "z_spread_60": -0.5
    },

    "quality": {
        "valid": true,
        "gap_s": 0,
        "flags": []
    }
}
```

**(B) `{market}_v{N}_manifest.json`:**

```json
{
    "version": "v1",
    "market": "BTC15m",
    "created_at": "2026-02-24T12:00:00Z",
    "input_files": ["BTC15m_2026-02-05.jsonl"],
    "input_sha256": "abc123...",
    "config": { "levels_used": 10, "windows": [30, 60] },
    "stats": {
        "total_ticks": 85000,
        "valid_ticks": 84500,
        "invalid_ticks": 500,
        "windows_count": 96,
        "date_range": ["2026-02-05", "2026-02-05"],
        "prob_range": [0.01, 0.99],
        "spread_range": [0.0, 0.10]
    }
}
```

### 1.3 Config (dataclass, nao YAML)

```python
@dataclass
class BuilderConfig:
    input_dir: str = "data/raw/books"
    output_dir: str = "backtestv2/data/datasets"
    market: str = "BTC15m"
    version: str = "v1"
    levels_used: int = 10               # Top N niveis do book
    rolling_windows: list = (30, 60)    # Janelas para RV, z-scores
    window_duration_s: int = 900        # 15m = 900s, 1h = 3600s
    strict_mode: bool = False           # True = drop invalidos
    dates: list = None                  # None = todas disponiveis
```

### 1.4 Normalize (`builder/normalize.py`)

Responsabilidades:
- Validar schema v2 (campos obrigatorios: ts_ms, market, yes, no, derived)
- Calcular `time_remaining_s` = `window_start + window_duration_s - ts_ms/1000`
- Ordernar bids desc / asks asc (por preco)
- Remover niveis invalidos (price <= 0, size <= 0)
- Truncar para `levels_used`
- `prob` = `derived.prob_up` (0..1)
- Marcar quality flags se dados inconsistentes

### 1.5 Features (`builder/features.py`)

**Reutilizar logica de `indicators/signals/microstructure.py`** (import direto):

| Feature | Fonte | Descricao |
|---|---|---|
| `microprice` | microstructure.py | VWAP top-3 levels |
| `microprice_edge` | microstructure.py | microprice - mid |
| `imbalance_N` | Calculado | (bid_depth_N - ask_depth_N) / total, N=1,3,5,10 |
| `depth_bid_N` / `depth_ask_N` | Calculado | Sum sizes top N levels |
| `depth_shift_5` | Delta | depth_bid_5 - depth_ask_5 (tick atual vs anterior) |
| `spread_bps` | Calculado | spread / mid * 10000 |
| `prob_delta_Xs` | Rolling | prob[t] - prob[t-X], X=1,5,10 |
| `prob_momentum_30s` | Rolling | prob[t] - prob[t-30] |
| `rv_30s` / `rv_60s` | Rolling | Realized vol (std de log-returns do mid) |
| `z_prob_60` | Rolling | (prob - mean_60) / std_60 |
| `z_spread_60` | Rolling | (spread - mean_60) / std_60 |
| `time_remaining_s` | Normalize | Tempo ate settlement |
| `is_last_minute` | Calculado | time_remaining_s < 60 |

### 1.6 Validate (`builder/validate.py`)

Quality flags por tick:
- `gap_ts` — diferenca > 2s entre ticks consecutivos
- `bad_prob` — prob fora [0.001, 0.999]
- `empty_book` — sem bids OU sem asks
- `spread_negative` — spread < 0 (bug)
- `mid_zero` — mid == 0

### 1.7 CLI Builder

```bash
python -m backtestv2.builder.build_dataset \
    --market BTC15m \
    --version v1 \
    --dates 2026-02-05
```

Output:
```
backtestv2/data/datasets/BTC15m_v1_features.jsonl
backtestv2/data/datasets/BTC15m_v1_manifest.json
```

---

## 2) MODULO 2 — Backtest Engine

### 2.1 Modelo de execucao (realista)

O engine simula **exatamente o que o bot faz na vida real**:

```
Ordem maker (GTC, post_only) a (mid - 0.01)
  -> espera fill por FILL_TIMEOUT segundos
  -> se nao fill: cancela, ajusta preco (+0.01), retry
  -> ate MAX_FILL_ATTEMPTS tentativas
  -> se preencheu: HOLDING ate settlement
  -> PnL = (1.0 - entry_price) * shares  se acertou
  -> PnL = -entry_price * shares          se errou
```

**Config de execucao:**

```python
@dataclass
class ExecutionConfig:
    entry_price_offset: float = 0.01    # Maker: mid - offset
    fill_timeout_s: int = 5             # Segundos por tentativa
    max_fill_attempts: int = 3          # Max retries
    max_retry_delta: float = 0.04       # Max ajuste total de preco
    min_shares: int = 6                 # Shares por trade
    fees_bps: float = 0.0              # Fees (Polymarket = 0 maker)
```

**Logica de fill simulado:**
- Tick t: bot coloca ordem a `price = mid - 0.01`
- Ticks t+1..t+5: se `best_ask <= price` em qualquer tick -> FILLED
- Se nao fill em 5s: cancela, retry a `price = best_ask`
- Se `price > entry_price + max_retry_delta`: SKIP

Isso e muito mais realista que o modelo maker/taker/hybrid do analista.

### 2.2 Strategy Interface

```python
class StrategyBase(ABC):
    @abstractmethod
    def on_tick(self, tick: dict, state: PositionState) -> Action:
        """Retorna ENTER, SKIP, ou HOLD."""
        ...

    @abstractmethod
    def choose_side(self, tick: dict) -> str:
        """Retorna 'YES' ou 'NO'."""
        ...
```

**Strategy 1 — Threshold** (replica bot_light.py):

```python
@dataclass
class ThresholdConfig:
    min_price: float = 0.93             # Preco minimo de entrada
    max_price: float = 0.98             # Preco maximo
    entry_window_start_s: int = 240     # Janela: 4min antes do fim
    entry_window_end_s: int = 60        # Hard stop: 1min antes
    max_trades_per_window: int = 1
```

Regras:
1. `entry_window_end_s <= time_remaining <= entry_window_start_s`
2. Escolhe lado com maior probabilidade (mais perto de 1.0)
3. Preco do lado escolhido em [min_price, max_price]
4. Maximo 1 trade por janela

**Strategy 2 — BotLight** (replica completa com flip):

Mesma logica do `scripts/bot_light.py` incluindo:
- Entry com mesmas regras
- Flip se preco cai abaixo de 0.50
- Formula: Q2 = ceil(Q1 * P1 / (1 - P2))

### 2.3 Engine Loop

```python
for window in iter_windows(dataset):
    strategy.reset()
    position = None

    for tick in window.ticks:
        if position is None:
            action = strategy.on_tick(tick, state)
            if action == ENTER:
                side = strategy.choose_side(tick)
                position = simulate_entry(tick, execution_config)

        elif position.state == HOLDING:
            # Avaliar flip (se strategy suporta)
            strategy.on_holding_tick(tick, position)

    # Settlement
    if position:
        pnl = settle(position, window.outcome)
        trades.append(Trade(...))
```

### 2.4 Metricas (reutilizar `backtest/metrics.py` existente)

| Metrica | Descricao |
|---|---|
| `total_trades` | Numero de trades executados |
| `win_rate` | % de trades vencedores |
| `avg_pnl` | PnL medio por trade |
| `total_pnl` | PnL acumulado |
| `profit_factor` | Gross profit / gross loss |
| `max_drawdown` | Maior sequencia de perdas |
| `sharpe` | Sharpe ratio |
| `entry_rate` | % de janelas com trade |
| `avg_entry_price` | Preco medio de entrada |
| `pnl_by_zone` | PnL segmentado por faixa de probabilidade |

### 2.5 Logs auditaveis (JSONL)

```
backtestv2/logs/
    engine_decisions.jsonl   # Cada tick: motivo ENTER/SKIP/HOLD
    engine_orders.jsonl      # Ordens colocadas/canceladas
    engine_fills.jsonl       # Fills simulados
```

Campos fixos por log:
`run_id, ts_ms, market, window_start, time_remaining_s, prob, action, reason`

### 2.6 Outputs

```
backtestv2/data/results/run_YYYYMMDD_HHMMSS/
    summary.json          # Metricas agregadas
    trades.csv            # Lista de trades com entry/exit/pnl
    params.json           # Config + manifest linkado
```

### 2.7 CLI Engine

```bash
python -m backtestv2.engine.run_backtest \
    --dataset backtestv2/data/datasets/BTC15m_v1_features.jsonl \
    --strategy threshold \
    --verbose
```

---

## 3) Train/Test Split

Simples e eficaz:

```python
# No CLI do engine
--split 0.5           # Primeiro 50% = train, ultimo 50% = test
--split-by-date       # Ou split por data (ex: primeiro dia train, segundo test)
```

Output no `summary.json`:
```json
{
    "train": { "trades": 45, "win_rate": 0.78, "pnl": 2.50 },
    "test":  { "trades": 40, "win_rate": 0.72, "pnl": 1.80 },
    "overfit_ratio": 0.92  // test_pnl / train_pnl (< 0.5 = overfit)
}
```

---

## Dependencias

Nenhuma nova. Usa apenas:
- `json`, `csv`, `hashlib`, `pathlib`, `dataclasses`, `datetime`, `math`, `logging`
- `argparse` (para CLIs)

---

## Ordem de implementacao

1. `builder/config.py` + `builder/normalize.py` (~100 linhas)
2. `builder/features.py` (~200 linhas)
3. `builder/validate.py` + `builder/build_dataset.py` (~150 linhas)
4. Testar: gerar dataset v1 do BTC15m
5. `engine/types.py` + `engine/config.py` (~80 linhas)
6. `engine/execution.py` (~120 linhas)
7. `engine/portfolio.py` + `engine/metrics.py` (~150 linhas)
8. `engine/engine.py` + `engine/run_backtest.py` (~200 linhas)
9. `strategies/base.py` + `strategies/threshold.py` (~100 linhas)
10. Testar: rodar backtest no dataset v1

Total estimado: ~1100-1300 linhas

---

## Anexo: arquivo raw BTC1h

O arquivo do analista (`BTC1h_2026-02-24_1.txt`) foi salvo em:
`data/raw/books/BTC1h_2026-02-24.jsonl` (496 linhas, 2.3MB)

Schema identico ao BTC15m mas com `window_duration_s = 3600` (1h).
