# 📊 Análise de Indicadores

Script para validar e otimizar os indicadores coletados.

## 🎯 Objetivos

1. **Correlação com Outcomes**: Verificar se os indicadores realmente preveem resultados
2. **Distribuição**: Entender os valores típicos dos indicadores
3. **Sensibilidade de Thresholds**: Encontrar valores ótimos para os parâmetros
4. **Performance por Zona**: Qual zona de probabilidade tem melhor win rate
5. **Análise Temporal**: Há horários melhores para operar?

## 📦 Instalação

```bash
# Instalar dependências opcionais (para análises mais avançadas)
pip install pandas numpy
```

## 🚀 Uso Básico

```bash
# Análise dos últimos 7 dias
python analyze_indicators.py

# Análise dos últimos 30 dias
python analyze_indicators.py --days 30

# Salvar relatório em arquivo
python analyze_indicators.py --days 7 --output report.txt
```

## 📊 Exemplos de Perguntas Respondidas

### 1. Distribuição dos Indicadores

**Pergunta**: Qual o range típico do imbalance?

**Resposta** (exemplo):
```
IMBALANCE:
  Média: 0.31
  Mediana: 0.32
  Min: -1.00 | Max: 0.89
  P25: 0.17 | P75: 0.51
```

**Interpretação**: 
- Imbalance típico está entre 0.17 e 0.51 (positivo = mais compradores)
- Valores extremos (-1.0 a 0.89) indicam momentos de alta pressão

### 2. Correlação com Outcomes

**Pergunta**: Imbalance positivo → mais vitórias UP?

**Resposta** (quando houver outcomes):
```
IMBALANCE:
  up_win_rate: 65.2%
  down_win_rate: 58.1%
```

**Interpretação**: 
- Quando imbalance > 0 (mais compradores), UP ganha 65% das vezes
- Quando imbalance < 0 (mais vendedores), DOWN ganha 58% das vezes
- ✅ Indicador tem poder preditivo!

### 3. Sensibilidade de Thresholds

**Pergunta**: Qual o melhor `min_depth`?

**Resposta**:
```
min_depth_100:
  Passou: 1500 trades
  Win Rate: 58.2%

min_depth_300:
  Passou: 1200 trades
  Win Rate: 61.5%  ← MELHOR!

min_depth_500:
  Passou: 800 trades
  Win Rate: 60.1%
```

**Recomendação**: Usar `min_depth=300` (melhor win rate com volume suficiente)

### 4. Performance por Zona

**Pergunta**: Qual zona tem melhor performance?

**Resposta**:
```
DANGER (< 2%):
  Win Rate: 45.2%  ← EVITAR!

CAUTION (2-5%):
  Win Rate: 52.1%

SAFE (5-15%):
  Win Rate: 61.8%  ← MELHOR!

NEUTRAL (> 15%):
  Win Rate: 50.3%
```

**Recomendação**: Focar em trades na zona "safe" (5-15% de probabilidade do underdog)

### 5. Análise Temporal

**Pergunta**: Há horários melhores?

**Resposta**:
```
madrugada_00-06:
  Win Rate: 58.2%

manha_06-12:
  Win Rate: 61.5%  ← MELHOR!

tarde_12-18:
  Win Rate: 59.1%

noite_18-24:
  Win Rate: 57.8%
```

**Recomendação**: Manhã (6h-12h UTC) tem melhor performance

## 🔧 Parâmetros Testados

O script testa automaticamente:

| Parâmetro | Valores Testados | Atual |
|-----------|------------------|-------|
| `min_depth` | $100, $300, $500, $1000 | $300 |
| `max_spread_pct` | 1%, 2%, 3%, 5% | 2% |
| `max_volatility` | 30%, 50%, 70% | 50% |
| `min_persistence_s` | 10s, 20s, 30s, 60s | 20s |

## 📈 Próximos Passos

1. **Coletar Outcomes**: 
   - Implementar coleta de outcomes reais dos mercados
   - Ou usar API do Polymarket para verificar resultados

2. **Análise Avançada**:
   - Correlação cruzada entre múltiplos indicadores
   - Machine Learning para otimização de thresholds
   - Backtesting com diferentes estratégias

3. **Monitoramento Contínuo**:
   - Rodar análise semanalmente
   - Ajustar thresholds baseado em resultados
   - Alertas quando performance cair

## ⚠️ Limitações Atuais

- **Outcomes**: Por enquanto, outcomes precisam ser coletados separadamente
- **Dados Históricos**: Análise depende de dados já coletados
- **Correlação ≠ Causalidade**: Correlação não garante que vai funcionar no futuro

## 💡 Dicas

1. **Comece com 7 dias**: Dados suficientes sem ser muito lento
2. **Compare períodos**: Veja se performance muda ao longo do tempo
3. **Valide com backtest**: Use `backtest/run.py` para testar thresholds
4. **Documente mudanças**: Quando ajustar thresholds, documente o motivo

## 📝 Exemplo Completo

```bash
# 1. Análise básica
python analyze_indicators.py --days 7

# 2. Salvar relatório
python analyze_indicators.py --days 7 --output analysis_$(date +%Y%m%d).txt

# 3. Comparar com período anterior
python analyze_indicators.py --days 14 --output analysis_2weeks.txt
```

## 🔗 Relacionado

- `backtest/run.py` - Backtesting com dados históricos
- `indicators/signals/scorer.py` - Sistema de pontuação
- `bot/main.py` - Execução do bot

