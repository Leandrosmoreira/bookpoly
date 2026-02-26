# Passo a passo: como usar qualquer JSONL para fazer backtest

## Requisitos

- Python 3.11+
- Projeto bookpoly clonado
- Dependencias instaladas (`pip install -r requirements.txt`)

**Se `python` nao for encontrado:** use `python3` ou o interpretador do venv:
```bash
# Opcao A: python3
python3 -m backtest.run --market BTC1h --start 2026-02-24 --end 2026-02-24 -v

# Opcao B: venv do projeto (recomendado)
cd /root/bookpoly
./venv/bin/python backtest/run.py --market BTC1h --start 2026-02-24 --end 2026-02-24 -v
```

---

## 1. Preparar o arquivo JSONL

### 1.1 Formato obrigatorio

Cada linha do JSONL deve ter esta estrutura:

```json
{
    "v": 2,
    "ts_ms": 1770258056041,
    "market": "BTC15m",
    "window_start": 1770257700,
    "yes": {
        "best_bid": 0.77,
        "best_ask": 0.78,
        "mid": 0.775,
        "spread": 0.01,
        "bid_depth": 36393.09,
        "ask_depth": 38073.24,
        "imbalance": -0.0226,
        "bids": [{"p": 0.77, "s": 2001.03}],
        "asks": [{"p": 0.78, "s": 10.0}]
    },
    "no": {
        "best_bid": 0.22,
        "best_ask": 0.23,
        "mid": 0.225,
        "spread": 0.01,
        "bid_depth": 38198.24,
        "ask_depth": 36384.01,
        "imbalance": 0.0243,
        "bids": [{"p": 0.22, "s": 500.0}],
        "asks": [{"p": 0.23, "s": 300.0}]
    },
    "derived": {
        "prob_up": 0.775,
        "prob_down": 0.225
    }
}
```

### 1.2 Campos obrigatorios

| Campo | Tipo | Descricao |
|---|---|---|
| `ts_ms` | int | Timestamp em milissegundos (epoch) |
| `market` | str | Nome do mercado (ex: `BTC15m`, `ETH15m`, `BTC1h`) |
| `window_start` | int | Timestamp do inicio da janela (epoch em SEGUNDOS) |
| `yes.best_bid` | float | Melhor bid do token YES |
| `yes.best_ask` | float | Melhor ask do token YES |
| `yes.mid` | float | Preco medio YES |
| `yes.bid_depth` | float | Profundidade total de bids |
| `yes.ask_depth` | float | Profundidade total de asks |
| `yes.imbalance` | float | (bid - ask) / (bid + ask), range -1 a 1 |
| `yes.bids` | list | Lista de `{"p": preco, "s": tamanho}` (desc por preco) |
| `yes.asks` | list | Lista de `{"p": preco, "s": tamanho}` (asc por preco) |
| `no.*` | ... | Mesma estrutura para token NO |
| `derived.prob_up` | float | Probabilidade UP (0 a 1) |
| `derived.prob_down` | float | Probabilidade DOWN (0 a 1) |

### 1.3 Nome do arquivo

**Padrao obrigatorio:** `{MERCADO}_{YYYY-MM-DD}.jsonl`

Exemplos:
- `BTC15m_2026-02-05.jsonl` (BTC janelas de 15 min)
- `ETH15m_2026-02-06.jsonl` (ETH janelas de 15 min)
- `BTC1h_2026-02-24.jsonl` (BTC janelas de 1 hora)
- `SOL15m_2026-02-05.jsonl` (SOL janelas de 15 min)

### 1.4 Onde colocar o arquivo

```
data/raw/books/{MERCADO}_{YYYY-MM-DD}.jsonl
```

Exemplo:
```
data/raw/books/BTC15m_2026-02-05.jsonl
```

---

## 2. Verificar os dados

### 2.1 Checar se o arquivo e valido

```bash
# Contar linhas (cada linha = 1 tick/segundo)
wc -l data/raw/books/BTC15m_2026-02-05.jsonl

# Ver primeira linha formatada
python -c "
import json
with open('data/raw/books/BTC15m_2026-02-05.jsonl') as f:
    print(json.dumps(json.loads(f.readline()), indent=2))
"
```

### 2.2 Ver dados disponiveis

```bash
python -m backtest.run --summary
```

Saida esperada:
```
=== DATA SUMMARY ===
Available dates for BTC15m:
  2026-02-05  (85000 ticks)
  2026-02-06  (86000 ticks)
```

### 2.3 Quantos ticks por janela?

- **15 min** = 900 segundos = ~900 ticks (1 por segundo)
- **1 hora** = 3600 segundos = ~3600 ticks
- Janelas com menos de 870 ticks (15m) sao consideradas incompletas e puladas

---

## 3. Rodar o backtest

### 3.1 Backtest basico (todos os dias disponiveis)

```bash
cd /root/bookpoly   # ou C:\Users\Leandro\Downloads\bookpoly

python -m backtest.run --market BTC15m
```

### 3.2 Backtest de um dia especifico

```bash
python -m backtest.run --market BTC15m --start 2026-02-05 --end 2026-02-05
```

### 3.3 Backtest dos ultimos N dias

```bash
python -m backtest.run --market BTC15m --days 3
```

### 3.4 Backtest com detalhes de cada trade

```bash
python -m backtest.run --market BTC15m --days 1 --verbose
```

### 3.5 Backtest de outro mercado

```bash
# ETH
python -m backtest.run --market ETH15m --days 1

# SOL
python -m backtest.run --market SOL15m --days 1

# BTC 1 hora
python -m backtest.run --market BTC1h --start 2026-02-24 --end 2026-02-24
```

### 3.6 Backtest completo (contrarian/underdog)

```bash
python backtest/complete_backtest.py
```

Gera: `backtest_trades.csv` com todas as colunas de book + PnL.

---

## 4. Entender o resultado

### 4.1 Metricas principais

```
==================================================
BACKTEST RESULTS
==================================================

OVERVIEW
  Total Windows:      96        <- Janelas no periodo
  Complete Windows:   90        <- Janelas com dados completos
  Entries:            22        <- Trades executados
  Entry Rate:         24.4%     <- % de janelas com trade

WIN/LOSS
  Wins:               14
  Losses:              8
  Win Rate:           63.6%     <- % de acerto

P&L
  Total P&L:          $2.34     <- Lucro total
  Avg P&L per Trade:  $0.106    <- Media por trade
  Profit Factor:      2.45      <- Lucro bruto / Perda bruta (>1 = bom)

RISK
  Max Drawdown:       $1.20     <- Maior sequencia de perdas
  Sharpe Ratio:       1.85      <- Retorno/risco (>1 = bom)
```

### 4.2 Como o PnL e calculado

```
Trade: comprar YES a $0.95

Se acertou (YES venceu):
  Recebe $1.00, pagou $0.95
  PnL = +$0.05 por share

Se errou (NO venceu):
  Recebe $0.00, pagou $0.95
  PnL = -$0.95 por share
```

### 4.3 Analise por zona

```
ZONE       TRADES  WIN%    PNL
danger       5     60.0%   $0.25    <- prob muito perto de 0 ou 1
caution      8     62.5%   $0.50    <- prob 95-98%
safe        45     64.4%   $3.20    <- prob 85-95%
neutral     12     58.3%   $0.89    <- prob < 85%
```

---

## 5. Exemplo completo: do zero ao resultado

```bash
# 1. Entrar no projeto
cd /root/bookpoly

# 2. Verificar que o arquivo existe
ls -la data/raw/books/BTC15m_2026-02-05.jsonl

# 3. Ver resumo dos dados
python -m backtest.run --summary

# 4. Rodar backtest com verbose
python -m backtest.run --market BTC15m --start 2026-02-05 --end 2026-02-05 -v

# 5. Se quiser exportar trades para CSV
python backtest/complete_backtest.py
```

---

## 6. Se voce tem um JSONL de outra fonte

Se voce tem dados de outra fonte (ex: coletou manualmente, recebeu de alguem), precisa converter para o formato padrao:

```python
"""Conversor generico para formato bookpoly."""
import json
from datetime import datetime

def convert_to_bookpoly(input_file, output_file, market="BTC15m", window_duration=900):
    """
    Converte JSONL customizado para formato bookpoly.

    Seu JSONL precisa ter pelo menos:
    - timestamp (epoch ms ou ISO)
    - probabilidade YES (0..1)
    - order book (bids/asks) ou pelo menos best_bid/best_ask
    """
    with open(input_file) as f_in, open(output_file, "w") as f_out:
        for line in f_in:
            raw = json.loads(line)

            # ---- ADAPTE ESTES CAMPOS AO SEU FORMATO ----
            ts_ms = raw["ts_ms"]                    # ou converter de ISO
            prob_up = raw["prob"]                    # ou raw["yes_mid"]
            best_bid = raw.get("best_bid", prob_up - 0.01)
            best_ask = raw.get("best_ask", prob_up + 0.01)
            # ---- FIM DA ADAPTACAO ----

            prob_down = round(1.0 - prob_up, 4)
            mid = round(prob_up, 4)
            spread = round(best_ask - best_bid, 4) if best_ask and best_bid else 0

            # Calcular window_start (arredonda para baixo ao multiplo de window_duration)
            ts_s = ts_ms / 1000
            window_start = int(ts_s // window_duration) * window_duration

            row = {
                "v": 2,
                "ts_ms": int(ts_ms),
                "market": market,
                "window_start": window_start,
                "yes": {
                    "best_bid": best_bid,
                    "best_ask": best_ask,
                    "mid": mid,
                    "spread": spread,
                    "bid_depth": raw.get("bid_depth", 1000),
                    "ask_depth": raw.get("ask_depth", 1000),
                    "imbalance": 0.0,
                    "bids": raw.get("bids", [{"p": best_bid, "s": 100}]),
                    "asks": raw.get("asks", [{"p": best_ask, "s": 100}]),
                },
                "no": {
                    "best_bid": round(1 - best_ask, 4) if best_ask else None,
                    "best_ask": round(1 - best_bid, 4) if best_bid else None,
                    "mid": round(prob_down, 4),
                    "spread": spread,
                    "bid_depth": raw.get("ask_depth", 1000),
                    "ask_depth": raw.get("bid_depth", 1000),
                    "imbalance": 0.0,
                    "bids": [],
                    "asks": [],
                },
                "derived": {
                    "prob_up": prob_up,
                    "prob_down": prob_down,
                },
            }

            f_out.write(json.dumps(row, separators=(",", ":")) + "\n")

    print(f"Convertido: {input_file} -> {output_file}")


# Uso:
# convert_to_bookpoly(
#     "meus_dados.jsonl",
#     "data/raw/books/BTC15m_2026-02-20.jsonl",
#     market="BTC15m",
#     window_duration=900,
# )
```

---

## 7. Checklist rapido

```
[ ] 1. Arquivo JSONL com formato correto (schema v2)
[ ] 2. Nome: {MERCADO}_{YYYY-MM-DD}.jsonl
[ ] 3. Salvo em: data/raw/books/
[ ] 4. Pelo menos 1 janela completa (~900 ticks para 15m)
[ ] 5. Campos obrigatorios: ts_ms, market, window_start, yes, no, derived
[ ] 6. Rodar: python -m backtest.run --market {MERCADO} --start {DATA} --end {DATA}
[ ] 7. Analisar resultado: win_rate, PnL, profit_factor, sharpe
```

---

## 8. Backtest v2 — Grid de parâmetros

O script `run_param_grid.py` roda o backtest com **6 conjuntos de parâmetros** (Min/Max Prob., Min/Max Tempo Restante, Share) nos dados **BTC1h, ETH1h, SOL1h, XRP1h** para **2026-02-22 a 2026-02-24**.

### Parâmetros usados (tabela)

| Min Prob. | Max Prob. | Min Tempo Restante | Max Tempo Restante | Share |
|-----------|-----------|--------------------|--------------------|-------|
| 93%       | 99%       | 5 min              | 15 min             | 5     |
| 95%       | 99%       | 3min30s            | 15 min             | 5     |
| 95%       | 99%       | 5 min              | 18 min             | 5     |
| 95%       | 99%       | 3 min              | 12 min             | 5     |
| 93%       | 99%       | 3 min              | 12 min             | 5     |
| 92%       | 99%       | 4 min              | 12 min             | 5     |

### Como rodar

```bash
cd /root/bookpoly   # ou raiz do projeto

# Padrão: data/raw, BTC1h+ETH1h+SOL1h+XRP1h, 2026-02-22 a 2026-02-24
python3 -m backtestv2.run_param_grid

# Com diretório e verbose
python3 -m backtestv2.run_param_grid --data-dir data/raw -v

# Um mercado, um dia (teste rápido)
python3 -m backtestv2.run_param_grid --markets BTC1h --start 2026-02-24 --end 2026-02-24 -v
```

### Saída

- Tabela resumo: cada linha = um conjunto de parâmetros (Entries, Win%, P&L em $ com 5 shares/trade, Sharpe).
- Melhor conjunto: detalhe do conjunto com maior P&L total.

Os arquivos de book devem estar em `data/raw/books/` no formato `{MERCADO}_{YYYY-MM-DD}.jsonl` (ex.: `BTC1h_2026-02-22.jsonl`).
