# Análise Completa dos Order Books - Insights para o Bot

**Data da Análise:** 2026-02-07  
**Período:** 3 dias (05/02 a 07/02)  
**Total de Registros:** ~911,000 registros  
**Mercados Analisados:** BTC15m, ETH15m, SOL15m, XRP15m

---

## 📊 Resumo Executivo

### Estatísticas Gerais por Mercado

| Mercado | Registros | Windows | Depth Médio | Spread P95 | Prob Danger |
|---------|-----------|---------|-------------|------------|-------------|
| **BTC15m** | 227,746 | 255 | $104,294 | 40.0% | 13.4% |
| **ETH15m** | 227,762 | 255 | $68,706 | 40.0% | 12.7% |
| **SOL15m** | 227,780 | 255 | $45,553 | 46.2% | 14.8% |
| **XRP15m** | 227,805 | 255 | $33,707 | 57.1% | 14.7% |

---

## 🔍 Análise Detalhada

### 1. Spread (Bid-Ask Spread)

**Problema Crítico Identificado:**
- **Spread P95 muito alto** em todos os mercados (40-57%)
- Spread mediano é baixo (2-2.3%), mas há picos extremos
- **XRP15m** tem o pior spread (P95=57.1%)

**Implicações:**
- O bot atual usa `max_spread_pct=10%`, mas 5% dos casos têm spread >40%
- Muitos trades podem estar sendo rejeitados por spread alto
- Ou o bot está entrando em condições ruins quando spread é alto

**Recomendações:**
1. ✅ **Manter `max_spread_pct=10%`** (já está adequado)
2. ⚠️ **Adicionar filtro de spread P95**: Rejeitar se spread > 5% (mais conservador)
3. 📊 **Monitorar spread por hora**: Evitar horários com spread alto

---

### 2. Depth (Liquidez)

**Análise:**
- **BTC15m** tem melhor liquidez: Depth médio $104k, P25=$79k
- **XRP15m** tem menor liquidez: Depth médio $34k, P25=$9.6k ⚠️
- Depth P25 de XRP ($9.6k) está abaixo do `min_depth=$300` atual (OK)

**Implicações:**
- Liquidez suficiente para trades de $5-10
- XRP pode ter problemas em horários de baixa liquidez

**Recomendações:**
1. ✅ **Manter `min_depth=$300`** (adequado)
2. 📈 **Considerar aumentar para $500-1000** para XRP em horários específicos
3. 🕐 **Evitar operar XRP em horários de baixa liquidez** (ver seção Horários)

---

### 3. Distribuição de Probabilidade

**Zonas de Probabilidade:**

| Zona | BTC15m | ETH15m | SOL15m | XRP15m | Total |
|------|--------|--------|--------|--------|-------|
| **Danger** (<2% ou >98%) | 13.4% | 12.7% | 14.8% | 14.7% | **13.9%** |
| **Caution** (2-5% ou 95-98%) | 7.3% | 8.6% | 9.5% | 10.0% | **8.9%** |
| **Safe** (5-15% ou 85-95%) | 17.1% | 15.9% | 15.4% | 17.1% | **16.4%** |
| **Neutral** (15-85%) | 62.2% | 62.7% | 60.0% | 58.0% | **60.6%** |

**Insights:**
- **13.9% dos registros estão em zona Danger** (forçada entry)
- **16.4% estão em zona Safe** (melhor para operar)
- **60.6% estão em Neutral** (sem edge claro)

**Implicações:**
- O bot está entrando principalmente em zonas Danger e Safe
- Win rate baixo (18.5%) sugere que as entradas em Danger não estão funcionando
- Zona Safe pode ser melhor, mas precisa de mais dados

**Recomendações:**
1. ⚠️ **Revisar estratégia de Forced Entry** (prob≥95% + ≤2min)
   - Win rate atual: 18.5% sugere que forced entry não está funcionando
   - Considerar aumentar threshold para prob≥98%
2. ✅ **Focar em zona Safe** (5-15% ou 85-95%)
   - Melhor relação risco/retorno
   - Mais oportunidades (16.4% dos registros)
3. 🚫 **Evitar zona Neutral** quando possível
   - Sem edge claro
   - 60% dos registros, mas baixa expectativa

---

### 4. Análise por Hora do Dia

**Melhores Horários para Liquidez (Top 3 por mercado):**

| Mercado | Hora 1 | Hora 2 | Hora 3 |
|---------|--------|--------|--------|
| **BTC15m** | 19h ($120k) | 20h ($118k) | 21h ($118k) |
| **ETH15m** | 19h ($91k) | 08h ($90k) | 13h ($82k) |
| **SOL15m** | 19h ($64k) | 08h ($61k) | 11h ($60k) |
| **XRP15m** | 19h ($47k) | 17h ($47k) | 21h ($45k) |

**Padrões Identificados:**
- **19h UTC** é o melhor horário para todos os mercados
- **08h UTC** é bom para ETH e SOL
- **Madrugada (00h-06h)** tem menor liquidez (não mostrado, mas inferido)

**Recomendações:**
1. 🕐 **Priorizar operações entre 08h-21h UTC**
2. ⚠️ **Evitar madrugada (00h-06h UTC)** - menor liquidez
3. 📊 **Ajustar Time Gate** se necessário para focar em horários melhores

---

### 5. Latency

**Análise:**
- Latency média: **188ms** (todos os mercados)
- Latency mediana: **178ms**
- Latency P95: **260ms**

**Implicações:**
- Latency está dentro do aceitável (<500ms)
- Não é um fator limitante atual

**Recomendações:**
1. ✅ **Manter `max_latency_ms=500`** (adequado)

---

## 🎯 Recomendações Prioritárias para o Bot

### 🔴 Prioridade ALTA

1. **Revisar Threshold de Spread**
   - **Atual:** `max_spread_pct=10%`
   - **Recomendado:** Manter 10%, mas adicionar filtro adicional:
     - Se spread > 5%: Rejeitar (mais conservador)
     - Se spread > 3%: Reduzir confidence

2. **Revisar Estratégia de Forced Entry**
   - **Problema:** Win rate de 18.5% sugere que forced entry não funciona
   - **Recomendado:**
     - Aumentar threshold: `prob≥98%` (em vez de 95%)
     - Reduzir janela: `≤90s` (em vez de 120s)
     - Ou **desabilitar forced entry** temporariamente

3. **Focar em Zona Safe**
   - **Atual:** Bot entra em Danger e Safe
   - **Recomendado:**
     - Priorizar zona Safe (5-15% ou 85-95%)
     - Aumentar score mínimo para zona Safe
     - Reduzir score mínimo para zona Danger (ou evitar)

### 🟡 Prioridade MÉDIA

4. **Ajustar por Mercado**
   - **XRP15m** tem pior spread (P95=57%)
   - **Recomendado:**
     - Reduzir `max_spread_pct` para XRP: 5-7%
     - Aumentar `min_depth` para XRP: $500-1000

5. **Otimizar Horários**
   - **Recomendado:**
     - Priorizar 08h-21h UTC
     - Considerar reduzir frequência em madrugada

6. **Melhorar Score Threshold**
   - **Atual:** `score_threshold=0.35`
   - **Recomendado:**
     - Para zona Safe: `score≥0.40`
     - Para zona Danger: `score≥0.50` (mais conservador)
     - Para zona Neutral: `score≥0.45` (evitar quando possível)

### 🟢 Prioridade BAIXA

7. **Monitoramento**
   - Adicionar métricas de performance por zona
   - Adicionar métricas de performance por hora
   - Adicionar alertas para spread alto

---

## 📈 Parâmetros Recomendados

### Configuração Atual vs Recomendada

| Parâmetro | Atual | Recomendado | Justificativa |
|-----------|-------|--------------|---------------|
| `max_spread_pct` | 10% | **5-7%** | Spread P95 é muito alto (40-57%) |
| `min_depth` | $300 | **$500** (XRP: $1000) | Melhor margem de segurança |
| `forced_entry_prob` | ≥95% | **≥98%** | Win rate baixo (18.5%) |
| `forced_entry_window` | ≤120s | **≤90s** | Mais conservador |
| `score_threshold` | 0.35 | **0.40-0.50** | Melhorar qualidade das entradas |
| `time_gate_start` | 30s | **30s** | OK |
| `time_gate_end` | 240s | **180s** | Evitar entradas muito tardias |

### Por Zona de Probabilidade

| Zona | Score Mínimo | Spread Máx | Depth Mín | Prioridade |
|------|--------------|------------|-----------|------------|
| **Danger** | 0.50 | 3% | $1000 | ⚠️ Baixa (ou desabilitar) |
| **Caution** | 0.45 | 5% | $500 | 🟡 Média |
| **Safe** | 0.40 | 7% | $300 | ✅ Alta |
| **Neutral** | 0.45 | 5% | $500 | 🟡 Média (evitar) |

---

## 🔬 Próximos Passos

1. **Implementar mudanças prioritárias**
   - Ajustar thresholds de spread
   - Revisar forced entry
   - Focar em zona Safe

2. **Coletar mais dados**
   - Analisar correlação entre score e win rate
   - Analisar performance por zona
   - Analisar performance por hora

3. **Backtesting**
   - Testar novas configurações em dados históricos
   - Comparar win rate antes/depois

4. **Monitoramento contínuo**
   - Acompanhar métricas em tempo real
   - Ajustar parâmetros conforme necessário

---

## 📊 Arquivos Gerados

- `analysis_books_complete.json` - Dados completos em JSON
- `analysis_output.txt` - Saída completa da análise

---

**Última atualização:** 2026-02-07 17:40 UTC

