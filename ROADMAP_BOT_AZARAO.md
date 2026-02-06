# ROADMAP — Bot Contra o Azarão (Polymarket BTC 15min)

## Objetivo Final

Criar um bot que entra nos **últimos 4 minutos** da janela de 15min, **apostando contra o azarão** (comprando o favorito quando o azarão está caro demais por um spike temporário).

---

## Visão Geral das Fases

| Fase | Nome | Objetivo | Esforço | Impacto |
|------|------|----------|---------|---------|
| 0 | ✅ Coleta de Dados | Gravar books + volatilidade 24/7 | FEITO | Base |
| 1 | Gates Básicos | Filtros binários (tempo, liquidez, spread, vol) | 4h | CRÍTICO |
| 2 | Microestrutura | Microprice, imbalance delta, price impact | 6h | ALTO |
| 3 | Estado Temporal | Persistência, tracking entre ticks | 4h | ALTO |
| 4 | Scorer & Sinais | Combinar tudo em score + decisão ENTER | 6h | CRÍTICO |
| 5 | Backtester | Testar estratégia em dados históricos | 8h | ALTO |
| 6 | Execução | Integrar com API de trading | 10h | FINAL |

**Total: ~38 horas de desenvolvimento**

---

# FASE 0 — Coleta de Dados ✅ CONCLUÍDA

## O que já temos rodando

### Polymarket Book Recorder (`src/main.py`)
```
Dados coletados a cada segundo:
├── Order book completo (50 níveis bid/ask)
├── best_bid, best_ask, mid, spread
├── bid_depth, ask_depth (liquidez total)
├── imbalance = (bid - ask) / (bid + ask)
├── prob_up, prob_down (probabilidade implícita)
├── overround (vig do mercado)
├── window_start (timestamp da janela 15min)
└── latency_ms (latência de rede)
```

### Binance Volatility (`indicators/volatility/main.py`)
```
Dados coletados a cada segundo:
├── rv_5m, rv_1h, rv_6h (realized volatility)
├── parkinson, garman_klass (estimadores alternativos)
├── atr_14, atr_norm (average true range)
├── cvi (composite volatility index)
├── funding_rate, open_interest
├── long_short_ratio, top_trader_ls_ratio
├── taker_buy_sell_ratio
└── regime: muito_baixa | baixa | normal | alta | muito_alta
```

### Saída
```
data/raw/
├── books/BTC15m_2026-02-06.jsonl      # ~86k linhas/dia
└── volatility/BTCUSDT_2026-02-06.jsonl # ~86k linhas/dia
```

---

# FASE 1 — Gates Básicos

## Objetivo
Criar **filtros binários** que eliminam situações ruins ANTES de avaliar score.

## Indicadores a Implementar

### 1.1 Time Gate (CRÍTICO)
```python
window_elapsed = now - window_start
time_remaining = 900 - window_elapsed

time_gate = (time_remaining >= 30) and (time_remaining <= 240)
# Só entra nos últimos 4 min, mas não nos últimos 30s
```
**Relevância**: MÁXIMA — define a janela de operação

### 1.2 Liquidity Gate (CRÍTICO)
```python
total_depth = bid_depth + ask_depth

liquidity_gate = total_depth >= MIN_DEPTH_USD  # ex: $300
```
**Relevância**: ALTA — garante que dá pra entrar/sair

### 1.3 Spread Gate (ALTO)
```python
spread_pct = spread / mid * 100

spread_gate = spread_pct <= MAX_SPREAD_PCT  # ex: 2%
```
**Relevância**: ALTA — spread alto = slippage escondida

### 1.4 Stability Gate (ALTO)
```python
stability_gate = rv_5m <= MAX_VOL  # ex: 0.50 (50% anualizado)
# OU usar regime != "muito_alta"
```
**Relevância**: ALTA — vol alta = chance de flip

### 1.5 Latency Gate (MÉDIO)
```python
latency_gate = latency_ms <= MAX_LATENCY  # ex: 500ms
```
**Relevância**: MÉDIA — latência alta = dados atrasados

## Saída da Fase 1
```python
all_gates = time_gate and liquidity_gate and spread_gate and stability_gate and latency_gate
```

## Arquivos a Criar
```
indicators/signals/
├── __init__.py
├── config.py      # Thresholds configuráveis
└── gates.py       # Funções de cada gate
```

---

# FASE 2 — Microestrutura

## Objetivo
Extrair **sinais do order book** que indicam pressão direcional.

## Indicadores a Implementar

### 2.1 Imbalance (JÁ TEMOS)
```python
imbalance = (bid_depth - ask_depth) / (bid_depth + ask_depth)
# > 0: pressão compradora (favorece UP)
# < 0: pressão vendedora (favorece DOWN)
```
**Relevância**: ALTA — sinal primário de pressão

### 2.2 Imbalance Delta (NOVO)
```python
imbalance_delta = imbalance_now - imbalance_prev
# Positivo: pressão aumentando pro lado bid
# Negativo: pressão aumentando pro lado ask
```
**Relevância**: ALTA — detecta mudança de fluxo

### 2.3 Microprice (NOVO)
```python
# Preço justo ponderado pelo volume no topo do book
best_bid_size = book['bids'][0]['size']
best_ask_size = book['asks'][0]['size']
microprice = (best_bid * best_ask_size + best_ask * best_bid_size) / (best_bid_size + best_ask_size)

microprice_vs_mid = microprice - mid
# > 0: mercado "puxa" pra cima
# < 0: mercado "puxa" pra baixo
```
**Relevância**: ALTA — melhor estimativa de fair price

### 2.4 Price Impact (NOVO)
```python
def price_impact(book_side, order_size):
    """Quanto custa executar order_size shares"""
    remaining = order_size
    total_cost = 0
    for level in book_side:
        take = min(remaining, level['size'])
        total_cost += take * level['price']
        remaining -= take
        if remaining <= 0:
            break
    avg_price = total_cost / order_size
    return avg_price - book_side[0]['price']  # slippage

impact_buy = price_impact(book['asks'], 5)   # custo de comprar 5 shares
impact_sell = price_impact(book['bids'], 5)  # custo de vender 5 shares
```
**Relevância**: MÉDIA — evita entrar em book fino

### 2.5 Spread Stickiness (NOVO)
```python
# Variância do spread nos últimos N segundos
spread_history = [...]  # últimos 30 spreads
spread_std = std(spread_history)

spread_stable = spread_std <= MAX_SPREAD_STD
```
**Relevância**: MÉDIA — spread instável = risco de execução

### 2.6 Book Shape (AVANÇADO)
```python
# Detectar "cliff" vs "ladder"
top5_depth = sum(level['size'] for level in book['bids'][:5])
total_depth = bid_depth
concentration = top5_depth / total_depth

# > 0.8: cliff (concentrado no topo)
# < 0.5: ladder (distribuído)
```
**Relevância**: MÉDIA — cliff pode sumir rápido (spoof)

## Arquivos a Criar
```
indicators/signals/
└── microstructure.py  # Todas as funções acima
```

---

# FASE 3 — Estado Temporal

## Objetivo
Manter **memória entre ticks** para calcular deltas, persistência e históricos.

## Indicadores a Implementar

### 3.1 Condition Persistence (CRÍTICO)
```python
class StateTracker:
    def __init__(self):
        self.gates_satisfied_since = None

    def update(self, all_gates: bool, now: float):
        if all_gates:
            if self.gates_satisfied_since is None:
                self.gates_satisfied_since = now
            persistence = now - self.gates_satisfied_since
        else:
            self.gates_satisfied_since = None
            persistence = 0
        return persistence

# Só entra se gates satisfeitos por >= 20s
persistence_ok = persistence >= 20
```
**Relevância**: CRÍTICA — evita entrar em spike falso

### 3.2 Rolling Windows (ALTO)
```python
from collections import deque

class RollingWindow:
    def __init__(self, size=60):
        self.data = deque(maxlen=size)

    def add(self, value):
        self.data.append(value)

    def mean(self):
        return sum(self.data) / len(self.data)

    def std(self):
        m = self.mean()
        return sqrt(sum((x-m)**2 for x in self.data) / len(self.data))

# Usar para: spread, imbalance, prob_up, etc.
```
**Relevância**: ALTA — base para z-scores e deltas

### 3.3 Z-Score de Probabilidade (ALTO)
```python
prob_window = RollingWindow(300)  # 5 min
prob_window.add(prob_up)

prob_zscore = (prob_up - prob_window.mean()) / prob_window.std()
# > 2: prob_up muito alto vs histórico recente
# < -2: prob_up muito baixo vs histórico recente
```
**Relevância**: ALTA — detecta "esticado demais"

### 3.4 Previous Window Outcome (NOVO)
```python
# Guardar resultado da janela anterior
previous_window = {
    "window_start": 1234567890,
    "final_prob_up": 0.95,
    "outcome": "UP",  # quem ganhou
    "was_flip": False  # favorito perdeu?
}

# Se teve flip recente, mais cautela
flip_penalty = 0.2 if previous_window["was_flip"] else 0
```
**Relevância**: MÉDIA — contexto histórico

## Arquivos a Criar
```
indicators/signals/
└── state.py  # StateTracker, RollingWindow, etc.
```

---

# FASE 4 — Scorer & Sinais

## Objetivo
Combinar todos os indicadores em um **score final** e gerar decisão ENTER/NO_ENTER.

## Lógica do Score

### 4.1 Normalização dos Indicadores
```python
def normalize(value, min_val, max_val):
    """Normaliza para 0-1"""
    return max(0, min(1, (value - min_val) / (max_val - min_val)))

# Exemplos:
imbalance_norm = normalize(imbalance, -0.5, 0.5)  # -0.5 a 0.5 → 0 a 1
vol_norm = normalize(rv_5m, 0, 1.0)               # 0% a 100% → 0 a 1
spread_norm = normalize(spread_pct, 0, 0.03)      # 0% a 3% → 0 a 1
```

### 4.2 Score Composto
```python
# Pesos do analista (ajustáveis)
WEIGHTS = {
    "imbalance": 0.25,
    "microprice_edge": 0.15,
    "imbalance_delta": 0.10,
    "momentum": 0.10,       # taker_buy_sell_ratio da Binance
    "vol": -0.20,           # negativo: alta vol = ruim
    "spread": -0.10,        # negativo: alto spread = ruim
    "impact": -0.05,        # negativo: alto impact = ruim
    "persistence": 0.05,
}

score = sum(WEIGHTS[k] * indicators[k] for k in WEIGHTS)
```

### 4.3 Zonas de Probabilidade
```python
def get_zone(prob_up):
    """Classifica a zona de risco"""
    underdog_prob = min(prob_up, 1 - prob_up)

    if underdog_prob < 0.02:
        return "danger"   # azarão < 2% = muito arriscado
    elif underdog_prob < 0.05:
        return "caution"  # azarão 2-5% = cuidado
    elif underdog_prob < 0.15:
        return "safe"     # azarão 5-15% = zona ideal
    else:
        return "neutral"  # azarão > 15% = sem edge claro
```

### 4.4 Decisão Final
```python
def decide(gates, score, persistence, zone, regime):
    # Gates são obrigatórios
    if not gates['all_passed']:
        return "NO_ENTER", "gates_failed"

    # Zona perigosa
    if zone == "danger":
        return "NO_ENTER", "zone_danger"

    # Vol muito alta
    if regime == "muito_alta":
        return "NO_ENTER", "high_vol"

    # Persistência mínima
    if persistence < 20:
        return "NO_ENTER", "no_persistence"

    # Score threshold
    if score < 0.5:
        return "NO_ENTER", "low_score"

    # Tudo ok!
    side = "UP" if prob_up > 0.5 else "DOWN"
    confidence = "high" if score > 0.7 else "medium"
    return "ENTER", f"{side}_{confidence}"
```

## Saída JSONL
```json
{
  "ts_ms": 1770340757554,
  "market": "BTC15m",
  "time_remaining_s": 180,

  "gates": {
    "time": true,
    "liquidity": true,
    "spread": true,
    "stability": true,
    "latency": true,
    "all_passed": true
  },

  "indicators": {
    "imbalance": 0.15,
    "imbalance_delta": 0.02,
    "microprice": 0.42,
    "microprice_vs_mid": 0.005,
    "spread_pct": 0.8,
    "price_impact": 0.003,
    "rv_5m": 0.35,
    "regime": "normal",
    "prob_zscore": 1.2
  },

  "probability": {
    "prob_up": 0.92,
    "underdog": "DOWN",
    "underdog_price": 0.08,
    "zone": "safe"
  },

  "decision": {
    "persistence_s": 45,
    "score": 0.72,
    "action": "ENTER",
    "side": "UP",
    "confidence": "high",
    "reason": "all_conditions_met"
  }
}
```

## Arquivos a Criar
```
indicators/signals/
├── scorer.py    # Normalização e cálculo do score
├── decision.py  # Lógica de decisão
├── recorder.py  # Monta JSONL
└── main.py      # Loop principal
```

---

# FASE 5 — Backtester

## Objetivo
**Testar a estratégia** nos dados históricos para validar antes de operar real.

## Funcionalidades

### 5.1 Carregar Dados Históricos
```python
def load_window(date, window_start):
    """Carrega dados de uma janela 15min específica"""
    books = load_jsonl(f"data/raw/books/BTC15m_{date}.jsonl")
    vol = load_jsonl(f"data/raw/volatility/BTCUSDT_{date}.jsonl")

    # Filtrar pela janela
    window_books = [r for r in books if r['window_start'] == window_start]
    window_vol = [r for r in vol if in_window(r['ts_ms'], window_start)]

    return merge_by_timestamp(window_books, window_vol)
```

### 5.2 Simular Decisões
```python
def backtest_window(data):
    """Simula as decisões em uma janela"""
    state = StateTracker()
    signals = []

    for tick in data:
        gates = evaluate_gates(tick)
        indicators = calculate_indicators(tick)
        persistence = state.update(gates['all_passed'], tick['ts_ms'])
        decision = decide(gates, indicators, persistence)

        signals.append({
            "ts": tick['ts_ms'],
            "decision": decision,
            "prob_up": tick['prob_up']
        })

    return signals
```

### 5.3 Calcular Resultado
```python
def calculate_pnl(signals, outcome):
    """Calcula P&L de uma janela"""
    entries = [s for s in signals if s['decision'][0] == "ENTER"]

    if not entries:
        return {"entered": False, "pnl": 0}

    # Pegar primeira entrada
    entry = entries[0]
    entry_price = entry['prob_up'] if entry['decision'][1].startswith("UP") else (1 - entry['prob_up'])

    # Resultado: 1.0 se acertou, 0.0 se errou
    won = (outcome == "UP" and entry['decision'][1].startswith("UP")) or \
          (outcome == "DOWN" and entry['decision'][1].startswith("DOWN"))

    pnl = (1.0 - entry_price) if won else (-entry_price)

    return {"entered": True, "entry_price": entry_price, "won": won, "pnl": pnl}
```

### 5.4 Métricas de Performance
```python
def calculate_metrics(results):
    """Calcula métricas agregadas"""
    entered = [r for r in results if r['entered']]

    return {
        "total_windows": len(results),
        "entries": len(entered),
        "entry_rate": len(entered) / len(results),
        "wins": sum(1 for r in entered if r['won']),
        "win_rate": sum(1 for r in entered if r['won']) / len(entered) if entered else 0,
        "total_pnl": sum(r['pnl'] for r in entered),
        "avg_pnl": sum(r['pnl'] for r in entered) / len(entered) if entered else 0,
        "sharpe": ...,  # calcular
    }
```

## Arquivos a Criar
```
backtest/
├── __init__.py
├── loader.py     # Carregar dados históricos
├── simulator.py  # Simular decisões
├── metrics.py    # Calcular métricas
└── run.py        # Script principal
```

---

# FASE 6 — Execução (Bot Real)

## Objetivo
**Integrar com API de trading** para executar ordens reais.

## ⚠️ ATENÇÃO
Esta fase requer:
- API keys da Polymarket
- Carteira com fundos
- Testes exaustivos em paper trading primeiro

## Componentes

### 6.1 Cliente de Execução
```python
class PolymarketTrader:
    def __init__(self, api_key, secret):
        self.client = PolymarketClient(api_key, secret)

    async def place_order(self, token_id, side, size, price):
        """Coloca ordem limite"""
        order = await self.client.create_order(
            token_id=token_id,
            side=side,
            size=size,
            price=price,
            order_type="LIMIT"
        )
        return order

    async def cancel_order(self, order_id):
        """Cancela ordem"""
        return await self.client.cancel_order(order_id)
```

### 6.2 Gestão de Posição
```python
class PositionManager:
    def __init__(self, max_position=10, max_risk_pct=0.02):
        self.max_position = max_position
        self.max_risk_pct = max_risk_pct
        self.current_position = 0

    def calculate_size(self, confidence, bankroll):
        """Kelly criterion simplificado"""
        if confidence == "high":
            kelly = 0.05
        else:
            kelly = 0.02

        size = bankroll * kelly * self.max_risk_pct
        return min(size, self.max_position - self.current_position)
```

### 6.3 Loop de Execução
```python
async def run_bot():
    signal_generator = SignalGenerator()
    trader = PolymarketTrader(API_KEY, SECRET)
    position_manager = PositionManager()

    while True:
        # Gerar sinal
        signal = await signal_generator.get_signal()

        if signal['action'] == "ENTER":
            size = position_manager.calculate_size(
                signal['confidence'],
                get_bankroll()
            )

            if size > 0:
                await trader.place_order(
                    token_id=signal['token_id'],
                    side="BUY",
                    size=size,
                    price=signal['entry_price']
                )

        await asyncio.sleep(1)
```

## Arquivos a Criar
```
bot/
├── __init__.py
├── trader.py        # Cliente de execução
├── position.py      # Gestão de posição
├── risk.py          # Limites de risco
└── main.py          # Loop do bot
```

---

# Resumo: Ordem de Implementação

| Prioridade | Fase | Tempo | Pré-requisito |
|------------|------|-------|---------------|
| 1 | Fase 1: Gates | 4h | Fase 0 (feita) |
| 2 | Fase 2: Microestrutura | 6h | Fase 1 |
| 3 | Fase 3: Estado | 4h | Fase 2 |
| 4 | Fase 4: Scorer | 6h | Fase 3 |
| 5 | Fase 5: Backtester | 8h | Fase 4 |
| 6 | Fase 6: Execução | 10h | Fase 5 + testes |

**Recomendação**: Implementar Fases 1-4, depois rodar Fase 5 com pelo menos 1 semana de dados antes de considerar Fase 6.

---

# Checklist de Dados Necessários

## ✅ Já Coletando
- [x] Order book Polymarket (50 níveis)
- [x] Imbalance, spread, mid, prob_up
- [x] window_start, latency
- [x] Volatilidade Binance (RV, ATR, CVI)
- [x] Regime de volatilidade
- [x] Funding, OI, L/S ratio

## ⏳ Precisa Acumular
- [ ] ~1 semana de dados para backtest inicial
- [ ] ~1 mês para backtest robusto

## 📊 Métricas de Sucesso
- Win rate > 60%
- Sharpe > 1.5
- Max drawdown < 20%
- Entry rate 10-30% das janelas
