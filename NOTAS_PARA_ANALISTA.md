# Notas para o analista — validação das implementações

Pedido: subir para o git e que o analista valide se as implementações estão corretas.

---

## 1. Bot 15min — Reentrada no mesmo ciclo após SKIPPED

**Arquivo:** `scripts/bot_15min.py`

**O que foi feito:**
- Se o mercado está em estado **SKIPPED** (ex.: por ORDER_FAILED) e **ainda está na janela de entrada** (4 min a 1 min antes da expiração) e o **preço está no range 93%–98%**, o bot pode fazer **uma nova tentativa de ordem no mesmo ciclo**.
- Novo campo no contexto: **`skip_retried`** (bool). Ao dar essa segunda chance, `skip_retried = True`; no próximo ciclo é resetado em `reset_context()`.
- Novo evento no log: **`RE_ENTRY_AFTER_SKIP`** quando o bot volta a tentar após SKIPPED.
- Apenas **uma** reentrada desse tipo por ciclo (depois disso continua SKIPPED até o próximo ciclo).

**Trecho relevante:** entre “5. Fora da janela?” e “6. Já em posição?” foi inserido o bloco “5b” que, se `state == SKIPPED` e `not skip_retried` e preço no range, faz `state = IDLE`, `trade_attempts = 0`, `skip_retried = True` e registra `RE_ENTRY_AFTER_SKIP`.

**Validar:** lógica de reentrada (uma vez por ciclo, só na janela 4min–1min e com preço no range); reset de `skip_retried` no novo ciclo; não permitir múltiplas reentradas no mesmo ciclo.

---

## 2. Bot 15min — Janela 4 min e 6 shares

**Arquivos:** `scripts/bot_15min.py`, `scripts/dashboard_bot15min.py`, `scripts/status_bot15min_markets.py`

**O que foi feito:**
- **ENTRY_WINDOW_START:** 300 → **240** (janela de entrada passa a ser **4 min a 1 min** antes da expiração).
- **MIN_SHARES:** 5 → **6** (ordem de 6 shares).
- **MIN_BALANCE_USDC:** 5.5 → **6.5** (compatível com 6 × 0,98).
- Dashboard e status: textos e constantes atualizados para “4min” e janela 4min→1min.

**Validar:** uso consistente de 240s e 6 shares no bot; dashboard e status alinhados com o bot.

---

## 3. Recorder — BTC 5 min (bookpoly-recorder)

**Arquivos:** `src/config.py`, `src/market_discovery.py`, `src/main.py`, `src/recorder.py`

**O que foi feito:**
- **Config:** nova lista **`coins_5m`** (env `COINS_5M`, default `btc`).
- **Market discovery:** suporte a intervalo 5m (janela 300s), slug `{coin}-updown-5m-{window_ts}`, label `BTC5m`; cache por `(coin, interval)`; `discover_all` retorna dict por `market_label` (inclui BTC5m); `clear_cache_for_interval(interval)` na transição de janela.
- **Main:** detecta transição de janela 15m e 5m separadamente; chama `clear_cache_for_interval` e redescoberta; itera por `(label, info)` e usa `info["market_label"]` no writer.
- **Recorder:** `build_error_row` usa o primeiro argumento como label quando `market_info` é None (compatível com label tipo BTC5m).

**Validar:** descoberta e gravação do BTC5m; transição de janela 5m sem quebrar 15m; arquivos `BTC5m_YYYY-MM-DD.jsonl` no mesmo formato dos 15m.

---

## 4. Documentação e passo a passo

**Arquivos:** `PASSO_A_PASSO.md`, `docs/VERIFICAR_BTC5M_RECORDER.md`

- **PASSO_A_PASSO.md:** comandos para pull, restart bot, dashboard, claim (`claim.main`), status.
- **docs/VERIFICAR_BTC5M_RECORDER.md:** como testar e validar a gravação do BTC 5min (rodar recorder, checar arquivo, analisar com `src/analyze.py --market BTC5m`).

**Validar:** se os comandos e passos estão corretos para o ambiente atual.

---

## Checklist rápido para o analista

- [ ] Bot: reentrada após SKIPPED só uma vez por ciclo e só na janela 4min–1min com preço 93%–98%.
- [ ] Bot: MIN_SHARES=6, ENTRY_WINDOW_START=240, MIN_BALANCE_USDC=6.5 usados de forma consistente.
- [ ] Recorder: BTC5m descoberto e escrito; transição 15m/5m e cache por intervalo corretos.
- [ ] Docs: PASSO_A_PASSO e VERIFICAR_BTC5M coerentes com o uso do projeto.

Obrigado por validar.
