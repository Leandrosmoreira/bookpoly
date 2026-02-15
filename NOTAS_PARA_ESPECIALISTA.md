# Notas para o especialista — alterações e pendências

## Resumo

O assistente (analista) fez várias alterações no repositório para bot 15min, claim e RPC. **Parte das mudanças melhorou o comportamento, mas não foi possível chegar a soluções totalmente satisfatórias em dois pontos: claim (resgate) e uso de RPC.** Pedimos a ajuda do especialista para revisar e propor soluções melhores.

---

## O que foi alterado (código)

### Bot 15min (`scripts/bot_15min.py`)
- Preço real: uso de CLOB `/midpoint` e book (mid), sem default 0.50; mercado ignorado se não houver preço válido.
- Retry na ordem: ao falhar `place_order`, nova tentativa após 2s (até 2 tentativas).
- Verificação de saldo USDC antes de enviar ordem; evento `SKIP_INSUFFICIENT_BALANCE` se saldo < 5.5.
- Janela de entrada: 4 min → **5 min** antes da expiração.
- Retry após timeout (10s): reenvio 1 tick abaixo do best ask, com **teto em 98%** (não envia mais 0.99).
- Uso de lista de RPCs (polygon_rpc) para saldo.

### Claim (`claim/`)
- **Modo simulação removido:** claim sempre LIVE (sem `--dry-run`).
- **Lista de RPCs:** vários RPCs Polygon (incl. Infura via `INFURA_PROJECT_ID` + `INFURA_API_SECRET`); troca de RPC em rate limit.
- **Aguardar receipt:** retry com troca de RPC em rate limit; timeout 60s; em timeout, tenta obter receipt em outros RPCs (`_get_receipt_from_other_rpcs`).
- **Revert = já resgatado:** se a tx der revert, trata como "já resgatado (API desatualizada)" e não conta como falha.
- **get_pol_balance:** retry com troca de RPC em rate limit.
- Log reduzido (sem HTTP request do httpx; mensagens mais curtas).

### RPC (`polygon_rpc.py` — novo)
- Lista de RPCs públicos Polygon; suporte a `INFURA_PROJECT_ID` + `INFURA_API_SECRET` (Basic auth).
- `get_polygon_rpc_list()`, `get_web3_with_fallback()`, `get_request_kwargs_for_rpc(url)` para auth Infura.
- Usado pelo claim e pelo bot (saldo USDC).

### Outros
- Dashboard 15min: atualização 10s; texto "atualiza em tempo real" removido; coluna "PREÇO % (10s)".
- Serviço systemd e scripts de status/dashboard (bot15min.service, install_bot15min_service.sh, status_bot15min_markets.py, dashboard_bot15min.py).

---

## Onde as soluções NÃO foram satisfatórias — pedido de ajuda

### 1. Claim (resgate)

- **Problema:** A API da Polymarket (`/positions?redeemable=true`) às vezes continua listando posições **já resgatadas** (dados atrasados). O usuário vê "claim disponível" mas ao tentar a tx dá revert (já resgatado).
- **O que foi feito:** Tratar revert como "já resgatado" e não contar como falha; mensagem no log. Isso evita confusão mas **não evita** que a API mostre posições antigas.
- **Pedido:** Como **filtrar antes** (ex.: checagem on-chain de saldo da posição) para só listar/resgatar o que realmente ainda não foi resgatado? Ou outra forma de obter apenas "claims atuais" confiável.

### 2. RPC (Polygon)

- **Problema:** RPCs públicos dão **rate limit** com frequência; ao trocar de RPC (ex.: após enviar tx), o novo nó às vezes **não vê a tx** no tempo do timeout ("Transaction is not in the chain after 60 seconds"), e o script marca falha mesmo quando a tx foi minerada.
- **O que foi feito:** Lista de vários RPCs; troca em rate limit; em timeout, buscar receipt em outros RPCs; suporte a Infura (API Key + Secret). Ainda assim há casos de "1 success, 1 failed" em que o segundo resgate era na verdade minerado e o problema era só não receber o receipt a tempo.
- **Pedido:** Estratégia mais robusta para (a) reduzir rate limit (ex.: backoff, uso prioritário de Infura) e (b) confirmar tx minerada de forma confiável (ex.: polling em vários nós, tempo maior, ou outra abordagem recomendada).

---

## Arquivos não commitados (intencional)

- `.env` / `.env.polymarket` — credenciais (nunca subir).
- `logs/` — arquivos de log grandes (podem ser ignorados ou enviados por outro fluxo).

---

Obrigado por revisar e sugerir melhorias.
