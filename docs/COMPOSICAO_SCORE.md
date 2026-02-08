# 🧮 Composição do Score - Explicação Detalhada

## 📊 O que é o Score?

O **score** é um número de **0.0 a 1.0** que representa a **qualidade do sinal de trading**. Ele combina múltiplos indicadores do mercado em um único valor.

---

## 🔢 Como o Score é Calculado?

O score é a **soma ponderada** de 9 indicadores diferentes, cada um com seu próprio **peso** (importância).

### Fórmula Geral:
```
score = Σ (peso × indicador_normalizado)
```

Depois, o resultado é **normalizado** para ficar entre 0.0 e 1.0.

---

## 📈 Componentes do Score

### ✅ **Fatores Positivos** (aumentam o score)

#### 1. **Imbalance** (Peso: 25% = 0.25)
- **O que é:** Desequilíbrio do order book
- **Cálculo:** `(bid_depth - ask_depth) / (bid_depth + ask_depth)`
- **Range:** -1.0 a +1.0
- **Normalização:** -0.5 a +0.5 → 0 a 1
- **Interpretação:**
  - **Imbalance positivo** (mais bids) = **score maior** ✅
  - **Imbalance negativo** (mais asks) = score menor
- **Por quê:** Mais compradores = pressão de alta = sinal melhor

#### 2. **Microprice Edge** (Peso: 15% = 0.15)
- **O que é:** Diferença entre microprice e mid price
- **Cálculo:** `microprice - mid`
- **Range:** -0.02 a +0.02 (aproximadamente)
- **Normalização:** -0.02 a +0.02 → 0 a 1
- **Interpretação:**
  - **Edge positivo** (microprice > mid) = **score maior** ✅
  - **Edge negativo** (microprice < mid) = score menor
- **Por quê:** Microprice acima do mid indica pressão de compra = sinal melhor

#### 3. **Imbalance Delta** (Peso: 10% = 0.10)
- **O que é:** Mudança no desequilíbrio desde o tick anterior
- **Cálculo:** `imbalance_atual - imbalance_anterior`
- **Range:** -0.2 a +0.2 (aproximadamente)
- **Normalização:** -0.2 a +0.2 → 0 a 1
- **Interpretação:**
  - **Delta positivo** (imbalance aumentando) = **score maior** ✅
  - **Delta negativo** (imbalance diminuindo) = score menor
- **Por quê:** Imbalance aumentando = momentum de compra = sinal melhor

#### 4. **Momentum (Taker Ratio)** (Peso: 10% = 0.10)
- **O que é:** Razão taker buy/sell da Binance
- **Fonte:** Dados de klines da Binance
- **Range:** 0.4 a 0.6 (normalmente)
- **Normalização:** 0.4 a 0.6 → 0 a 1
- **Interpretação:**
  - **Ratio > 0.5** (mais compras) = **score maior** ✅
  - **Ratio < 0.5** (mais vendas) = score menor
- **Por quê:** Mais taker buys = momentum de alta = sinal melhor

#### 5. **Persistence** (Peso: 5% = 0.05)
- **O que é:** Tempo que os gates estão passando consecutivamente
- **Cálculo:** Segundos desde que todos os gates começaram a passar
- **Range:** 0 a 120 segundos
- **Normalização:** 0 a 120s → 0 a 1
- **Interpretação:**
  - **Mais persistência** = **score maior** ✅
  - **Menos persistência** = score menor
- **Por quê:** Sinal que persiste = mais confiável = sinal melhor

---

### ❌ **Fatores Negativos** (diminuem o score)

#### 6. **Volatility** (Peso: -20% = -0.20)
- **O que é:** Volatilidade anualizada do ativo (Binance)
- **Fonte:** Dados de klines da Binance (RV 5min)
- **Range:** 0 a 1.0 (0% a 100%)
- **Normalização:** 0 a 1.0 → 0 a 1 (mas peso é negativo!)
- **Interpretação:**
  - **Volatilidade alta** = **score menor** ❌
  - **Volatilidade baixa** = score maior ✅
- **Por quê:** Alta volatilidade = mais risco = sinal pior

#### 7. **Spread** (Peso: -10% = -0.10)
- **O que é:** Spread percentual do token YES
- **Cálculo:** `(best_ask - best_bid) / mid * 100`
- **Range:** 0% a 3% (normalmente)
- **Normalização:** 0% a 3% → 0 a 1 (mas peso é negativo!)
- **Interpretação:**
  - **Spread alto** = **score menor** ❌
  - **Spread baixo** = score maior ✅
- **Por quê:** Spread alto = custo maior = sinal pior

#### 8. **Impact** (Peso: -5% = -0.05)
- **O que é:** Impacto médio de preço para comprar/vender
- **Cálculo:** `(impact_buy + impact_sell) / 2`
- **Range:** 0 a 0.02 (aproximadamente)
- **Normalização:** 0 a 0.02 → 0 a 1 (mas peso é negativo!)
- **Interpretação:**
  - **Impacto alto** = **score menor** ❌
  - **Impacto baixo** = score maior ✅
- **Por quê:** Impacto alto = slippage maior = sinal pior

---

## 🧮 Cálculo Final

### Passo 1: Normalizar cada indicador (0 a 1)
Cada indicador é normalizado para ficar entre 0.0 e 1.0.

### Passo 2: Multiplicar pelo peso
```python
componente = peso × indicador_normalizado
```

### Passo 3: Somar todos os componentes
```python
raw_score = (
    + 0.25 × imbalance_norm
    + 0.15 × microprice_edge_norm
    + 0.10 × imbalance_delta_norm
    + 0.10 × momentum_norm
    + 0.05 × persistence_norm
    - 0.20 × volatility_norm    # Negativo!
    - 0.10 × spread_norm        # Negativo!
    - 0.05 × impact_norm        # Negativo!
)
```

### Passo 4: Normalizar o resultado final
```python
# raw_score pode variar de -0.35 a +0.65
final_score = normalize(raw_score, min_val=-0.35, max_val=0.65)
# Resultado: 0.0 a 1.0
```

---

## 📊 Exemplo Prático

### Cenário: Score = 0.61

**Componentes positivos:**
- Imbalance: 0.8 (normalizado) → 0.25 × 0.8 = **+0.20**
- Microprice Edge: 0.7 → 0.15 × 0.7 = **+0.105**
- Imbalance Delta: 0.6 → 0.10 × 0.6 = **+0.06**
- Momentum: 0.55 → 0.10 × 0.55 = **+0.055**
- Persistence: 0.5 → 0.05 × 0.5 = **+0.025**

**Componentes negativos:**
- Volatility: 0.4 (normalizado) → -0.20 × 0.4 = **-0.08**
- Spread: 0.3 → -0.10 × 0.3 = **-0.03**
- Impact: 0.2 → -0.05 × 0.2 = **-0.01**

**Soma:**
```
raw_score = 0.20 + 0.105 + 0.06 + 0.055 + 0.025 - 0.08 - 0.03 - 0.01
raw_score = 0.325
```

**Normalização final:**
```
score = normalize(0.325, min=-0.35, max=0.65)
score = (0.325 - (-0.35)) / (0.65 - (-0.35))
score = 0.675 / 1.0
score = 0.675 ≈ 0.68
```

---

## 🎯 Pesos Atuais (Configuráveis)

```python
@dataclass
class ScoreWeights:
    # Positivos
    imbalance: float = 0.25      # 25% - Mais importante!
    microprice_edge: float = 0.15  # 15%
    imbalance_delta: float = 0.10  # 10%
    momentum: float = 0.10        # 10%
    persistence: float = 0.05     # 5%
    
    # Negativos
    volatility: float = -0.20     # -20% - Mais penalizador!
    spread: float = -0.10         # -10%
    impact: float = -0.05        # -5%
```

**Total dos pesos positivos:** 0.65 (65%)  
**Total dos pesos negativos:** -0.35 (-35%)  
**Soma total:** 0.30 (30%)

---

## 📈 Interpretação do Score

| Score | Qualidade | Significado |
|-------|-----------|-------------|
| 0.8 - 1.0 | **Excelente** | Todos os indicadores muito favoráveis |
| 0.6 - 0.8 | **Bom** | Maioria dos indicadores favoráveis |
| 0.4 - 0.6 | **Médio** | Indicadores mistos |
| 0.35 - 0.4 | **Fraco** | Mínimo aceitável |
| 0.0 - 0.35 | **Muito Fraco** | Bloqueado (não entra) |

---

## 🔧 Ajustar Pesos

Se quiser dar mais importância a algum indicador, pode modificar os pesos em `scorer.py`:

```python
@dataclass
class ScoreWeights:
    imbalance: float = 0.30      # Aumentar de 0.25 para 0.30
    microprice_edge: float = 0.20  # Aumentar de 0.15 para 0.20
    # ... etc
```

---

## 📋 Resumo

**O score combina 9 indicadores:**
- **5 positivos** (imbalance, microprice, delta, momentum, persistence)
- **3 negativos** (volatility, spread, impact)

**Cada um tem um peso:**
- Imbalance: 25% (mais importante positivo)
- Volatility: -20% (mais penalizador negativo)

**Resultado final:** 0.0 a 1.0, onde:
- **≥ 0.35:** Pode entrar (mínimo)
- **< 0.35:** Bloqueado (não entra)

