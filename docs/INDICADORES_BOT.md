# 📊 Indicadores do Bot - Explicação Completa

## Formato do Log

```
[BTC15m] [T:✓L:✓S:✓V:✓N:✓] ALL:✓ | prob=95.5% zone=caution score=0.61 | spread=1.0% depth=$10336 vol=39% | persist=20s remain=219s | ★ ENTER UP
```

## 🔍 Indicadores dos Gates (Filtros)

### `[T:✓L:✓S:✓V:✓N:✓]`

Cada letra representa um **gate** (filtro de segurança). O símbolo `✓` significa que o gate **passou**, e `✗` significa que **falhou**.

#### **T** - Time Gate (Gate de Tempo)
- **O que verifica:** Se estamos no período correto da janela de 15 minutos
- **Condição:** Deve estar entre `time_window_start_s` e `time_window_end_s`
- **Padrão:** Últimos 4 minutos (660s a 870s da janela de 900s)
- **Por quê:** Evita entrar muito cedo ou muito tarde na janela

#### **L** - Liquidity Gate (Gate de Liquidez)
- **O que verifica:** Se há liquidez suficiente no mercado
- **Condição:** `(bid_depth + ask_depth) >= min_depth`
- **Padrão:** Mínimo $300 de depth total
- **Por quê:** Garante que há volume suficiente para executar o trade

#### **S** - Spread Gate (Gate de Spread)
- **O que verifica:** Se o spread está aceitável
- **Condição:** `spread_pct <= max_spread_pct`
- **Padrão:** Spread ≤ 10% do preço médio
- **Por quê:** Spread alto = custo maior para entrar/sair

#### **V** - Volatility Gate (Gate de Volatilidade)
- **O que verifica:** Se a volatilidade não está muito alta
- **Condição:** `volatility <= max_volatility` E regime não é "muito_alta"
- **Padrão:** Volatilidade ≤ 150% (anualizada)
- **Por quê:** Alta volatilidade = maior risco

#### **N** - Latency Gate (Gate de Latência)
- **O que verifica:** Se os dados estão atualizados
- **Condição:** `latency_ms <= max_latency_ms`
- **Padrão:** Latência ≤ 500ms
- **Por quê:** Dados antigos = decisões baseadas em informação desatualizada

### **ALL:✓** ou **ALL:✗**
- **O que significa:** Se **TODOS** os gates passaram
- **✓** = Todos os gates passaram → pode considerar entrada
- **✗** = Pelo menos um gate falhou → **NÃO pode entrar**

---

## 📈 Indicadores de Probabilidade

### **prob=95.5%**
- **O que é:** Probabilidade implícita de o evento ocorrer (UP)
- **Fonte:** Preço médio (`mid`) do token YES no Polymarket
- **Range:** 0% a 100%
- **Exemplo:** `prob=95.5%` = mercado acha que há 95.5% de chance de UP

### **zone=safe**
- **O que é:** Zona de probabilidade do azarão (underdog)
- **Zonas possíveis:**
  - `danger`: Azarão < 2% (muito improvável)
  - `caution`: Azarão 2-5% (improvável)
  - `safe`: Azarão 5-15% (razoável)
  - `neutral`: Azarão > 15% (sem edge claro)
- **Cálculo:** `underdog_prob = min(prob_up, 1 - prob_up)`

---

## 🎯 Indicadores de Score

### **score=0.61**
- **O que é:** Score composto (0.0 a 1.0) que avalia a qualidade do sinal
- **Componentes:**
  - Imbalance (desequilíbrio do book)
  - Microprice edge (vantagem do microprice vs mid)
  - Impact (impacto de compra/venda)
  - Spread (quanto menor, melhor)
- **Interpretação:**
  - `score >= 0.70`: Alta confiança
  - `score >= 0.50`: Média confiança
  - `score >= 0.35`: Baixa confiança (mínimo)
  - `score < 0.35`: Muito baixo, não entrar

---

## 💰 Indicadores de Mercado

### **spread=1.0%**
- **O que é:** Spread percentual do token YES
- **Cálculo:** `(best_ask - best_bid) / mid * 100`
- **Interpretação:** Quanto menor, melhor (menos custo)

### **depth=$10336**
- **O que é:** Depth total (liquidez) em dólares
- **Cálculo:** `bid_depth + ask_depth`
- **Interpretação:** Quanto maior, melhor (mais liquidez)

### **vol=39%**
- **O que é:** Volatilidade anualizada do ativo (Binance)
- **Fonte:** Dados de klines da Binance
- **Interpretação:** Quanto menor, melhor (menos risco)

---

## ⏱️ Indicadores Temporais

### **persist=20s**
- **O que é:** Quantos segundos os gates estão passando consecutivamente
- **Condição mínima:** `persist >= 20s` (configurável)
- **Por quê:** Evita entradas em sinais momentâneos

### **remain=219s**
- **O que é:** Segundos restantes na janela de 15 minutos
- **Range:** 900s (início) a 0s (fim)
- **Estratégia:** Entrar apenas nos últimos 4 minutos (240s >= remain >= 30s)

---

## 🎲 Decisão Final

### **★ ENTER UP** ou **○ NO_ENTER**
- **★ ENTER UP/DOWN:** Bot decidiu entrar no trade
- **○ NO_ENTER:** Bot decidiu não entrar
- **Razão:** Aparece no log completo (ex: `reason=all_conditions_met`)

---

## 📊 Exemplo Completo

```
[BTC15m] [T:✓L:✓S:✓V:✓N:✓] ALL:✓ | prob=95.5% zone=caution score=0.61 | spread=1.0% depth=$10336 vol=39% | persist=20s remain=219s | ★ ENTER UP
```

**Tradução:**
- **BTC15m:** Mercado Bitcoin 15 minutos
- **T:✓L:✓S:✓V:✓N:✓:** Todos os gates passaram (tempo, liquidez, spread, volatilidade, latência)
- **ALL:✓:** Todos os gates OK
- **prob=95.5%:** Probabilidade de UP é 95.5%
- **zone=caution:** Azarão está em 4.5% (zona de cautela)
- **score=0.61:** Score médio-alto (confiança média)
- **spread=1.0%:** Spread baixo (bom)
- **depth=$10336:** Boa liquidez
- **vol=39%:** Volatilidade moderada
- **persist=20s:** Gates passando há 20 segundos
- **remain=219s:** Faltam 3min39s para fechar a janela
- **★ ENTER UP:** Bot entrou comprando UP (mas deveria ser DOWN, pois prob >= 95%!)

---

## ⚠️ Nota Importante

Com a estratégia atual corrigida:
- **Se prob >= 95%:** Entrar **DOWN** (contra o favorito)
- **Se prob <= 5%:** Entrar **UP** (contra o favorito)
- **Sempre contra o azarão!**

