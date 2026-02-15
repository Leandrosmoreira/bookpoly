# Verificar gravação BTC 5min (bookpoly-recorder)

Use estes passos para garantir que a adição do mercado 5min BTC não introduziu erros.

---

## 1. Rodar o recorder em primeiro plano (teste rápido)

Assim você vê no terminal se o BTC5m é descoberto e se aparecem erros.

```bash
cd /root/bookpoly
/root/bookpoly/venv/bin/python /root/bookpoly/src/main.py
```

**O que conferir nos logs:**
- Linha de config: deve mostrar `coins_5m=['btc']` (ou o valor de `COINS_5M`).
- Após "Discovered N markets": deve aparecer **BTC5m** na lista (ex.: `BTC15m, ETH15m, SOL15m, XRP15m, BTC5m`).
- Linha tipo: `Discovered BTC5m: btc-updown-5m-XXXXXXXX | YES=... NO=...`
- A cada tick, linhas `[BTC5m] seq=... mid_up=...` (sem erros de fetch).

Deixe rodar **30–60 segundos** e encerre com **Ctrl+C**.

**Se der 404 ou "Market not found" para BTC5m:** o slug 5min na Gamma pode ser outro; aí é preciso ajustar `make_slug(..., interval="5m")` em `src/market_discovery.py`.

---

## 2. Verificar se o arquivo BTC5m foi criado

```bash
ls -la /root/bookpoly/data/raw/books/BTC5m_*.jsonl
```

Deve existir pelo menos um arquivo `BTC5m_YYYY-MM-DD.jsonl` (data do dia em UTC).

Mostrar as últimas linhas (ex.: 3):

```bash
tail -3 /root/bookpoly/data/raw/books/BTC5m_*.jsonl
```

Cada linha deve ser um JSON com `"market":"BTC5m"`, `ts_ms`, `yes`, `no`, `derived`, etc.

---

## 3. Analisar com o script de análise

O `src/analyze.py` já aceita filtro por mercado. Use para checar se há dados válidos e sem erro para BTC5m:

```bash
cd /root/bookpoly
/root/bookpoly/venv/bin/python src/analyze.py --market BTC5m
```

**O que conferir na saída:**
- "Loaded N rows from M files" com N > 0 e arquivos que contenham BTC5m.
- Para o mercado BTC5m: número de ticks, erros (preferível 0), e janelas (windows) com dados.

Se quiser só a data de hoje (ajuste a data se necessário):

```bash
/root/bookpoly/venv/bin/python src/analyze.py --market BTC5m --date $(date -u +%Y-%m-%d)
```

---

## 4. Comparar com BTC15m (sanidade)

Para o mesmo dia, compare quantidade de ticks BTC15m vs BTC5m. Em 5min as janelas mudam 3x mais; em um mesmo período, BTC5m pode ter menos ticks por janela, mas deve ter dados contínuos:

```bash
/root/bookpoly/venv/bin/python src/analyze.py --market BTC15m --date 2026-02-15
/root/bookpoly/venv/bin/python src/analyze.py --market BTC5m --date 2026-02-15
```

Verifique se ambos mostram "ticks" e "windows" razoáveis, sem mensagens de erro.

---

## 5. Serviço em produção

Depois de validar em primeiro plano, reinicie o serviço para usar o código novo:

```bash
sudo systemctl restart bookpoly-recorder
sudo systemctl status bookpoly-recorder
```

Ver logs do serviço (incluindo possíveis erros de BTC5m):

```bash
sudo journalctl -u bookpoly-recorder -f -n 100
```

---

## Resumo rápido

| Passo | Comando / ação |
|-------|-----------------|
| Teste manual | `cd /root/bookpoly && /root/bookpoly/venv/bin/python src/main.py` (30–60s, depois Ctrl+C) |
| Arquivo criado? | `ls -la data/raw/books/BTC5m_*.jsonl` |
| Analisar BTC5m | `venv/bin/python src/analyze.py --market BTC5m` |
| Reiniciar serviço | `sudo systemctl restart bookpoly-recorder` |

Se em algum passo aparecer 404 ou "Market not found" só para BTC5m, o problema é o slug do mercado 5min na API; o resto do código está consistente com o modelo 15m.
