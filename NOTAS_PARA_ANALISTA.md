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

---

## 5. CLAIM v2 — Problema: Gasless não funciona (holder mismatch)

**Arquivos:** `claim_v2/`, especialmente `gasless_redeemer.py`, `executor.py`, `debug/`

### Problema identificado

O `claim_v2` está tentando fazer **gasless redeem via Builder Relayer**, mas:

1. **Gasless sempre falha** com `failed onchain, transaction_hash: !` (mesmo quando o SDK retorna `STATE_CONFIRMED/STATE_MINED`).
2. **Fallback on-chain funciona** (tx entra, bloco confirmado), mas às vezes o saldo só atualiza quando o usuário clica manualmente no site.
3. **Holder mismatch confirmado**: diagnóstico mostra que os tokens estão na **proxy wallet** (`0x0e958bbabdd7d1d8d7397e86668d6ee95db693ba`), não na Safe do Builder (`0xf5Ad9068c7145caC62551452Ba7bEC67Afde9722`).

### Evidências técnicas

- **Holder detector** (`claim_v2/debug/holder_detector.py`):
  - Para cada `token_id`, consulta `balanceOf` no CTF para EOA, proxy e Safe.
  - Resultado consistente: `proxy_balance > 0`, `eoa_balance = 0`, `safe_balance = 0`.
  - Conclusão: **holder real = proxy wallet**, não Safe.

- **SDK Python limitação**:
  - O `py-builder-relayer-client` **não expõe** `RelayerTxType` (SAFE vs PROXY).
  - O client TypeScript oficial (`@polymarket/builder-relayer-client`) tem `RelayerTxType.PROXY` e funciona.
  - O SDK Python só permite usar o modo padrão (SAFE implícito), que opera sobre uma Safe diferente da proxy onde estão os tokens.

- **Effect verifier** (`claim_v2/debug/effect_verifier.py`):
  - Snapshot antes/depois de USDC + token CTF.
  - Quando gasless "passa" mas não tem efeito → `NO_EFFECT` (token não queimado, USDC não creditado).

### O que foi implementado

1. **Módulos de debug** (`claim_v2/debug/`):
   - `holder_detector.py`: identifica holder real (EOA/proxy/safe) via `balanceOf`.
   - `effect_verifier.py`: compara saldos antes/depois para provar se teve efeito.
   - `relayer_raw_logger.py`: persiste respostas cruas do relayer (sem secrets) em JSONL.

2. **Executor com roteamento inteligente** (`claim_v2/executor.py`):
   - Detecta holder antes de tentar redeem.
   - Se holder = proxy/safe → tenta gasless primeiro.
   - Se holder = EOA → vai direto on-chain.
   - Se gasless não tiver efeito → marca como falha e não conta como sucesso.

3. **Flags de debug** (`claim_v2/main.py`):
   - `--debug-holder`: loga holder probe (EOA/proxy/safe balances).
   - `--debug-verify`: snapshot antes/depois e classifica efeito (SUCCESS_EFFECT/NO_EFFECT/PARTIAL_EFFECT).
   - `--debug-raw-relayer`: salva payload completo do relayer em `logs/relayer_raw_*.jsonl`.

### Por que não funciona (hipótese principal)

**Holder mismatch**: o gasless está tentando redimir via **Safe do Builder** (`0xf5Ad...`), mas os tokens estão na **proxy wallet** (`0x0e95...`). Como `redeemPositions` redime o que o `msg.sender` possui, e o `msg.sender` do gasless é a Safe (que não tem tokens), o redeem não tem efeito.

**Solução necessária**:
- Usar `RelayerTxType.PROXY` no SDK Python (mas ele não expõe isso).
- **OU** migrar para TypeScript (`@polymarket/builder-relayer-client`) onde `RelayerTxType.PROXY` existe.
- **OU** usar apenas on-chain direto (que funciona, mas paga POL).

### Arquivos gerados (quando roda com debug)

- `logs/claim_v2_redeemables_<timestamp>.json`: dump das posições encontradas.
- `logs/relayer_raw_<timestamp>.jsonl`: eventos do relayer (execute, wait, deploy).
- `logs/CLAIM_V2_DEBUG_REPORT.md`: resumo do run.

### Comandos para diagnóstico

```bash
# Debug completo (lento, mas gera evidência)
python -m claim_v2.main --debug-holder --debug-verify --debug-raw-relayer

# Só verificar holder (rápido)
python -m claim_v2.main --debug-holder

# Uso normal (sem debug, rápido)
python -m claim_v2.main
```

### Próximos passos sugeridos

1. Validar se o SDK Python oficial tem como usar PROXY (ou se precisa de fork/wrapper).
2. Considerar migrar para TypeScript se PROXY for essencial para gasless funcionar.
3. Manter fallback on-chain como está (funciona, só paga POL).

### Referências

- Docs Builder: https://docs.polymarket.com/developers/builders/relayer-client
- Repo TypeScript: https://github.com/Polymarket/builder-relayer-client
- Repo Python: https://github.com/Polymarket/py-builder-relayer-client
