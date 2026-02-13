# Pedido de ajuda – envio de ordem limit (CLOB Polymarket)

**Para:** especialista  
**Assunto:** não foi possível enviar ordem limit via API (conta signature_type=1); pedido de ajuda com as dificuldades encontradas.

---

## Objetivo

Enviar uma **ordem limit POST-ONLY** no Polymarket CLOB usando a conta do usuário (login Magic/email, **signature_type=1**, funder = proxy).

## O que já funciona

- **Autenticação L1:** gerador de API keys (`scripts/generate_api_keys.py`) funciona (timestamp do servidor, EIP-712 com campo `message`, `sign_typed_data`).
- **Autenticação L2:** headers L2 (POLY_ADDRESS, POLY_API_KEY, POLY_TIMESTAMP, POLY_SIGNATURE em base64 url-safe, POLY_PASSPHRASE) estão corretos; endpoints que só usam L2 (ex.: auth) aceitam.
- **Saldo:** `scripts/check_balance.py` mostra o saldo correto usando **POLYMARKET_FUNDER** (proxy) como endereço para consulta.
- **Descoberta de mercado:** mercado BTC 15min é encontrado via slug Gamma (`btc-updown-15m-{window_ts}`); token_id e book estão corretos.
- **Criação da ordem:** o cliente (Python ou Node) monta e assina a ordem (maker=funder, signer=EOA, signatureType=1) sem erro.

## Dificuldades (onde trava)

Ao dar **POST /order** no CLOB com o order object assinado, o servidor responde sempre:

- **400 – "invalid signature"** (referente à **assinatura da ordem** EIP-712, não à autenticação L2).

Ou seja: a API aceita os headers L2, mas rejeita a assinatura da ordem para essa conta (type 1).

### O que já foi testado

1. **Script Python (especialista) – `test_order_limit.py`**  
   - Payload simplificado (tokenID, side, size, price) → CLOB exige **order object assinado** (não aceita esse payload).  
   - Ajustes feitos: descoberta de mercado via slug Gamma; L2 com POLY_ADDRESS, POLY_PASSPHRASE e assinatura HMAC em base64 url-safe. Auth L2 OK; para enviar ordem de verdade é preciso ordem assinada.

2. **Python + py-clob-client – `place_order_limit_clob.py`**  
   - Cliente cria e assina a ordem (create_order); envio com POST manual (mesmo body que o client usaria).  
   - Resultado: **400 "invalid signature"** na ordem.  
   - Tentativa com **POLY_ADDRESS = funder** (em vez de signer) → **401 Unauthorized** (a API key está associada ao signer para auth).

3. **Node + @polymarket/clob-client – `place_order_limit_node.mjs`**  
   - Cliente oficial em TypeScript; mesma conta (signature_type=1, funder).  
   - Resultado: mesmo **400 "invalid signature"** na ordem; além disso, `createApiKey` retornou 400 ("Could not create api key").

4. **Referência externa:**  
   - [py-clob-client issue #198](https://github.com/Polymarket/py-clob-client/issues/198): mesmo cenário (Magic/email, signature_type=1, funder) com 400 "invalid signature" na ordem.

## Ambiente da conta

- **POLYMARKET_SIGNATURE_TYPE=1** (Poly Proxy / Magic / email).
- **POLYMARKET_FUNDER:** proxy onde está o saldo (ex.: 0x843a86...).
- **POLYMARKET_PRIVATE_KEY:** chave exportada da conta Polymarket (signer/EOA).
- Credenciais L2 (apiKey, secret, passphrase) geradas e usadas nos headers; auth L2 funciona em outros endpoints.

## Pedido ao especialista

1. Se você já conseguiu enviar ordem via API com **signature_type=1** (Magic/email), qual cliente, versão e fluxo exato (incl. uso de funder/signer nos headers e no order)?
2. Existe algum detalhe conhecido para type 1 (ex.: EIP-1271, nonce do exchange, formato de assinatura da ordem) que devamos seguir?
3. Vale a pena abrir suporte com o Polymarket (ex.: “invalid signature” em POST /order para contas Poly Proxy) ou há workaround recomendado (ex.: usar outro tipo de conta para API)?

Qualquer dica ou exemplo de ordem enviada com sucesso (type 1) ajuda. O repositório está atualizado com os scripts e este documento.

---

*Documento gerado após tentativas com Python (py-clob-client + POST manual) e Node (@polymarket/clob-client). Repo: branch main.*
