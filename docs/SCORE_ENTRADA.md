# 🎯 Score e Decisão de Entrada

## 📊 O que é o Score?

O **score** é um indicador composto (0.0 a 1.0) que avalia a **qualidade do sinal de trading**. Ele combina múltiplos fatores do mercado em um único número.

## 🔍 Como o Score é Calculado?

O score combina **9 indicadores** com pesos diferentes:

### ✅ Fatores Positivos (aumentam o score):
- **Imbalance** (25%): Desequilíbrio do book (mais bids = melhor)
- **Microprice Edge** (15%): Vantagem do microprice vs mid
- **Imbalance Delta** (10%): Mudança no desequilíbrio
- **Momentum** (10%): Razão taker buy/sell (Binance)
- **Persistence** (5%): Tempo que os gates estão passando

### ❌ Fatores Negativos (diminuem o score):
- **Volatility** (-20%): Volatilidade alta = score menor
- **Spread** (-10%): Spread alto = score menor
- **Impact** (-5%): Impacto de preço = score menor

**Score final:** Soma normalizada de todos os componentes (0.0 a 1.0)

---

## 🚪 Como o Score é Usado na Decisão?

### **Com a Estratégia Atual (Entrada Forçada):**

O score é usado como **filtro de segurança** na entrada forçada:

```python
# Linha 145 de decision.py
and score >= config.score_low  # ✅ OBRIGATÓRIO: Score mínimo
```

**Condição:** `score >= 0.35` (score_low padrão)

### **Thresholds de Score:**

```python
score_high: float = 0.70   # Alta confiança
score_medium: float = 0.50  # Média confiança  
score_low: float = 0.35    # Mínimo para considerar entrada
```

---

## ✅ Resposta Direta

**SIM, o score tem indicação para entrada, mas é um FILTRO, não o fator principal.**

### **Na Estratégia Atual:**

1. **Fator Principal:** Probabilidade >= 95% (qualquer lado)
2. **Fator Secundário:** Últimos 4 minutos (240s >= remaining >= 30s)
3. **Filtro de Segurança:** Score >= 0.35 (mínimo)

### **O que isso significa:**

- ✅ **Score >= 0.35:** Pode entrar (se outras condições forem atendidas)
- ❌ **Score < 0.35:** **NÃO pode entrar** (mesmo com prob >= 95%)

---

## 📈 Interpretação do Score

| Score | Interpretação | Pode Entrar? |
|-------|---------------|--------------|
| ≥ 0.70 | **Muito Forte** | ✅ Sim (alta confiança) |
| ≥ 0.50 | **Forte** | ✅ Sim (média confiança) |
| ≥ 0.35 | **Fraco** | ✅ Sim (mínimo aceitável) |
| < 0.35 | **Muito Fraco** | ❌ **NÃO** (bloqueado) |

---

## 🎯 Exemplo Prático

**Cenário 1: Score Alto**
```
prob=95.5% ✅
remain=200s ✅
score=0.65 ✅ (>= 0.35)
→ ENTER (todos os critérios atendidos)
```

**Cenário 2: Score Baixo**
```
prob=95.5% ✅
remain=200s ✅
score=0.25 ❌ (< 0.35)
→ NO_ENTER (score muito baixo, bloqueado)
```

**Cenário 3: Score Mínimo**
```
prob=95.5% ✅
remain=200s ✅
score=0.35 ✅ (= 0.35, mínimo)
→ ENTER (passou no mínimo)
```

---

## 🔧 Ajustar Threshold de Score

Se quiser ser mais restritivo, pode aumentar o `score_low`:

```python
# Em decision.py, linha 39
score_low: float = 0.40  # Mais restritivo (antes: 0.35)
```

Ou mais permissivo:

```python
score_low: float = 0.30  # Mais permissivo (antes: 0.35)
```

---

## 📊 Resumo

**O score é um FILTRO de qualidade:**
- ✅ **Score >= 0.35:** Sinal tem qualidade mínima → pode entrar
- ❌ **Score < 0.35:** Sinal muito fraco → **bloqueado**

**Mas não é o fator principal:**
- O fator principal é **probabilidade >= 95%**
- O score apenas **valida a qualidade** do sinal

**Na prática:**
- Score alto (0.6+) = sinal muito bom
- Score médio (0.4-0.6) = sinal razoável
- Score baixo (0.35-0.4) = sinal fraco, mas aceitável
- Score muito baixo (< 0.35) = **bloqueado**

