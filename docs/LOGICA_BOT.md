# Lógica Completa do Bot Polymarket

> Bot que aposta **contra o azarão** nos mercados de 15 minutos do Polymarket (BTC, ETH, SOL, XRP).

---

## Índice

1. [Time Gate - Quando Pode Entrar](#1-time-gate---quando-pode-entrar)
2. [Gates - Filtros Obrigatórios](#2-gates---filtros-obrigatórios)
3. [Zonas de Probabilidade](#3-zonas-de-probabilidade)
4. [Microestrutura - Indicadores do Book](#4-microestrutura---indicadores-do-book)
5. [Indicadores Binance](#5-indicadores-binance)
6. [Score - Pontuação Composta](#6-score---pontuação-composta)
7. [Persistência](#7-persistência)
8. [Forced Entry - Entrada Forçada](#8-forced-entry---entrada-forçada)
9. [Resumo - Quando Entra?](#9-resumo---quando-entra)
10. [Gestão de Risco](#10-gestão-de-risco)
11. [Fluxo Visual](#11-fluxo-visual)

---

## 1. Time Gate - Quando Pode Entrar

O bot só pode entrar nos **últimos 4 minutos** de cada janela de 15 minutos:

```
Janela de 15 minutos:
├─ 00:00 - 11:00  → ❌ BLOQUEADO (cedo demais)
├─ 11:00 - 14:30  → ✅ PODE ENTRAR (últimos 4min)
└─ 14:30 - 15:00  → ❌ BLOQUEADO (tarde demais)

Tempo restante válido: 30s ≤ remaining ≤ 240s
```

### Por que esperar até o final?

| Momento | Probabilidade | Situação |
|---------|---------------|----------|
| Início (00:00) | ~50% | Muito incerto, pode ir para qualquer lado |
| Meio (07:30) | ~65% | Começando a definir |
| Final (11:00+) | ~85-95% | **Quase certo** - hora de entrar! |

**Estratégia**: Esperar até o final quando já sabe quem vai ganhar, mas ainda consegue comprar por menos de $1.00.

---

## 2. Gates - Filtros Obrigatórios

**Todos os 5 gates precisam passar!**

| Gate | O que verifica | Valor para PASSAR |
|------|----------------|-------------------|
| **Time** | Tempo restante na janela | 30s a 240s |
| **Liquidity** | Profundidade do book (bid + ask) | ≥ $300 |
| **Spread** | Diferença bid/ask em % | ≤ 10% |
| **Volatility** | Volatilidade Binance (anualizada) | ≤ 100% |
| **Latency** | Tempo de resposta da API | ≤ 500ms |

### Detalhes de cada Gate:

#### Time Gate
```
Só permite entrada entre 30s e 240s restantes.
- Antes de 240s: muito cedo, resultado incerto
- Depois de 30s: muito tarde, pode não executar a ordem
```

#### Liquidity Gate
```
Verifica se há liquidez suficiente no book.
- Soma: bid_depth + ask_depth
- Mínimo: $300 para garantir que a ordem será executada
```

#### Spread Gate
```
Verifica se o spread não está muito alto.
- Fórmula: (ask - bid) / mid × 100
- Máximo: 10% (antes era 2%, mas era muito restritivo)
```

#### Volatility Gate
```
Verifica se o BTC não está muito volátil.
- Usa: RV (Realized Volatility) de 5 minutos da Binance
- Máximo: 100% anualizada (crypto é naturalmente volátil)
```

#### Latency Gate
```
Verifica se a API está respondendo rápido.
- Máximo: 500ms
- Se a latência estiver alta, os dados podem estar desatualizados
```

---

## 3. Zonas de Probabilidade

As zonas classificam a **probabilidade do AZARÃO** (o lado que está perdendo):

| Zona | Prob do Azarão | Decisão |
|------|----------------|---------|
| 🔴 **danger** | < 2% | ❌ NÃO ENTRA (muito arriscado) |
| 🟡 **caution** | 2% - 5% | ⚠️ Cuidado |
| 🟢 **safe** | 5% - 15% | ✅ **Ideal para entrar** |
| ⚪ **neutral** | > 15% | ➖ Pouco edge |

### Explicação Detalhada:

#### 🔴 DANGER (Azarão < 2%)

```
Exemplo: prob_up = 99%, azarão = 1%

┌─────────────────────────────────────────────────┐
│ UP (favorito): 99%  ████████████████████████░   │
│ DOWN (azarão):  1%  ░                           │
└─────────────────────────────────────────────────┘

Você paga: $0.99
Você ganha: $1.00
Lucro se acertar: $0.01 (1%)

MAS se errar (1% chance):
Você perde: $0.99

⚠️ 1 erro apaga 99 acertos!
```

#### 🟡 CAUTION (Azarão 2% - 5%)

```
Exemplo: prob_up = 96%, azarão = 4%

┌─────────────────────────────────────────────────┐
│ UP (favorito): 96%  ████████████████████████    │
│ DOWN (azarão):  4%  ██                          │
└─────────────────────────────────────────────────┘

Você paga: $0.96
Lucro se acertar: $0.04 (4%)
Para ser lucrativo: precisa acertar 96%+ das vezes

⚠️ Margem de erro muito pequena!
```

#### 🟢 SAFE (Azarão 5% - 15%)

```
Exemplo: prob_up = 90%, azarão = 10%

┌─────────────────────────────────────────────────┐
│ UP (favorito): 90%  ██████████████████████      │
│ DOWN (azarão): 10%  █████                       │
└─────────────────────────────────────────────────┘

Você paga: $0.90
Lucro se acertar: $0.10 (11%)
Para ser lucrativo: precisa acertar 90% das vezes

✅ Bom equilíbrio entre probabilidade e lucro!
```

#### ⚪ NEUTRAL (Azarão > 15%)

```
Exemplo: prob_up = 80%, azarão = 20%

┌─────────────────────────────────────────────────┐
│ UP (favorito): 80%  ████████████████████        │
│ DOWN (azarão): 20%  ██████████                  │
└─────────────────────────────────────────────────┘

Você paga: $0.80
Lucro se acertar: $0.20 (25%)

Problema: 20% de erro é muito frequente!
Em 10 trades: ~2 erros = empate

➖ Não tem "edge" claro.
```

### Tabela Risco vs Recompensa:

| Zona | Azarão | Você Paga | Lucro/Trade | Precisão Necessária | Veredicto |
|------|--------|-----------|-------------|---------------------|-----------|
| 🔴 danger | < 2% | $0.98+ | $0.02 | 98%+ | ❌ Ruim |
| 🟡 caution | 2-5% | $0.95-0.98 | $0.02-0.05 | 95-98% | ⚠️ Arriscado |
| 🟢 safe | 5-15% | $0.85-0.95 | $0.05-0.15 | 85-95% | ✅ **Ideal** |
| ⚪ neutral | > 15% | < $0.85 | > $0.15 | < 85% | ➖ Incerto |

---

## 4. Microestrutura - Indicadores do Book

Indicadores calculados a partir do order book do Polymarket:

| Indicador | O que mede | Bom para entrar |
|-----------|------------|-----------------|
| **Imbalance** | (bids - asks) / total | Positivo = mais compradores |
| **Imbalance Delta** | Mudança do imbalance | Aumentando = momentum |
| **Microprice** | VWAP do topo do book | Perto do mid = estável |
| **Price Impact** | Slippage para $100 | Baixo = boa liquidez |
| **Spread %** | spread / mid | Baixo = melhor preço |

### Fórmulas:

```python
# Imbalance
imbalance = (bid_depth - ask_depth) / (bid_depth + ask_depth)
# Varia de -1 (só asks) a +1 (só bids)

# Microprice (VWAP do topo)
microprice = (bid_price × ask_qty + ask_price × bid_qty) / (bid_qty + ask_qty)

# Microprice vs Mid
microprice_edge = microprice - mid_price
# Positivo = pressão de compra

# Price Impact
# Quanto o preço move se você comprar/vender $100
```

---

## 5. Indicadores Binance

Indicadores externos da Binance para contexto de mercado:

| Indicador | O que mede | Bom para entrar |
|-----------|------------|-----------------|
| **RV 5min** | Volatilidade realizada recente | ≤ 100% |
| **Regime** | Classificação da volatilidade | ≠ "muito_alta" |
| **Taker Ratio** | Compradores vs Vendedores | > 0.5 = mais compradores |
| **Funding Rate** | Custo de posições long | Baixo = menos alavancagem |

### Regimes de Volatilidade:

| Regime | RV Típica | Comportamento |
|--------|-----------|---------------|
| baixa | < 30% | Mercado calmo, previsível |
| media | 30-60% | Normal |
| alta | 60-100% | Volátil mas ainda ok |
| muito_alta | > 100% | ❌ Bloqueia entrada |

---

## 6. Score - Pontuação Composta

O score combina todos os indicadores em uma nota de 0 a 1:

```python
score = (
    +0.25 × imbalance_norm          # Peso maior - mais importante
    +0.15 × microprice_edge_norm
    +0.10 × imbalance_delta_norm
    +0.10 × taker_ratio_norm        # Momentum da Binance
    -0.20 × volatility_norm         # Penaliza vol alta
    -0.10 × spread_norm             # Penaliza spread alto
    -0.05 × impact_norm
    +0.05 × persistence_norm        # Bonus por estabilidade
)
```

### Decisão baseada no Score:

| Score | Confiança | Decisão |
|-------|-----------|---------|
| ≥ 0.70 | HIGH | ✅ ENTRA (alta confiança) |
| ≥ 0.50 | MEDIUM | ✅ ENTRA (média confiança) |
| ≥ 0.35 | LOW | ✅ ENTRA (baixa confiança) |
| < 0.35 | - | ❌ NÃO ENTRA |

---

## 7. Persistência

O sinal precisa se manter estável por pelo menos **20 segundos**:

```
├─ 10:11:00 - Gates passaram
├─ 10:11:05 - Gates passaram (5s)
├─ 10:11:10 - Gates passaram (10s)
├─ 10:11:15 - Gates passaram (15s)
├─ 10:11:20 - Gates passaram (20s) ✅ AGORA pode entrar!
└─ 10:11:25 - ENTRADA PERMITIDA

Se em qualquer momento os gates falharem, o contador reseta.
```

### Por que persistência?

- Evita entrar em **sinais falsos** (picos momentâneos)
- Garante que a condição é **estável**
- Reduz **whipsaws** (entrar e logo depois o sinal inverter)

---

## 8. Forced Entry - Entrada Forçada

**Nova regra que ignora todos os filtros!**

```
SE:
  • Probabilidade do favorito ≥ 95%
  • Tempo restante ≤ 2 minutos

ENTÃO:
  ✅ ENTRA SEMPRE (ignora gates, score, persistence, etc.)
```

### Lógica:

```
Situação: Faltam 90 segundos, prob = 97%

Sem Forced Entry:
├─ Gates: ❌ (spread alto)
├─ Score: 0.30 (baixo)
└─ Decisão: NO_ENTER ❌

Com Forced Entry:
├─ Prob ≥ 95%? ✅ (97%)
├─ Remaining ≤ 120s? ✅ (90s)
└─ Decisão: ENTER! ✅ (ignora o resto)
```

### Configuração:

```python
force_entry_enabled: bool = True
force_entry_min_prob: float = 0.95      # 95%
force_entry_max_remaining_s: float = 120.0  # 2 minutos
```

---

## 9. Resumo - Quando Entra?

### Entrada Normal (todos os critérios):

```
✅ Time Gate: 30s ≤ remaining ≤ 240s
✅ Liquidity Gate: depth ≥ $300
✅ Spread Gate: spread ≤ 10%
✅ Volatility Gate: RV ≤ 100%
✅ Latency Gate: latency ≤ 500ms
✅ Zona: ≠ "danger"
✅ Regime: ≠ "muito_alta"
✅ Persistência: ≥ 20 segundos
✅ Score: ≥ 0.35
```

### Entrada Forçada (bypass):

```
✅ Probabilidade ≥ 95%
✅ Tempo restante ≤ 2 minutos
(ignora todos os outros filtros)
```

---

## 10. Gestão de Risco

| Parâmetro | Valor | Descrição |
|-----------|-------|-----------|
| **Bankroll** | $100 | Capital inicial para testes |
| **Tamanho por trade** | $5 (fixo) | Mínimo do Polymarket |
| **Max trades/dia** | 20 | Limite diário |
| **Max perda/dia** | $25 (25%) | Stop loss diário |
| **Max posições abertas** | 3 | Simultâneas |
| **Tempo entre trades** | 10s mínimo | Evita overtrading |
| **Max perdas consecutivas** | 5 | Depois para por 1 hora |

### Circuit Breaker:

```
Se perder 5 trades seguidos:
├─ Trading PAUSADO por 1 hora
├─ Motivo: "Consecutive losses: 5"
└─ Após 1h: Volta ao normal
```

---

## 11. Fluxo Visual

```
DADOS POLYMARKET + BINANCE
         ↓
    [FORCED ENTRY?] ─── sim ───→ ✅ ENTER (prob≥95%, ≤2min)
         ↓ não
    [GATES] ─── falhou ───→ ❌ NO_ENTER
         ↓ passou
    [ZONA] ─── danger ────→ ❌ NO_ENTER
         ↓ ok
    [REGIME] ─ muito_alta ─→ ❌ NO_ENTER
         ↓ ok
    [PERSISTENCE] ─ <20s ──→ ❌ NO_ENTER
         ↓ ≥20s
    [SCORE] ─── <0.35 ────→ ❌ NO_ENTER
         ↓ ≥0.35
    [RISK CHECK] ─ blocked ─→ ❌ NO_ENTER
         ↓ ok
    ✅ ENTER!
```

---

## Apêndice: Exemplo Completo

```
=== JANELA 10:00 - 10:15 (BTC15m) ===

10:11:30 - Dados recebidos:
├─ prob_up: 92%
├─ prob_down: 8% (azarão)
├─ zona: "safe" ✅
├─ spread: 5% ✅
├─ depth: $450 ✅
├─ RV: 75% ✅
├─ regime: "alta" ✅
├─ remaining: 210s ✅
└─ latency: 120ms ✅

Gates: TODOS PASSARAM ✅
Persistence: 35s ✅
Score: 0.62 (MEDIUM) ✅

→ DECISÃO: ENTER UP!
→ Comprar YES @ $0.92
→ Size: $5 (fixo)

10:15:00 - Resultado:
├─ BTC subiu
├─ YES vale $1.00
├─ Lucro: $1.00 - $0.92 = $0.08 por share
└─ Com $5: lucro de ~$0.43 ✅
```

---

*Documentação gerada automaticamente. Última atualização: 2025.*
