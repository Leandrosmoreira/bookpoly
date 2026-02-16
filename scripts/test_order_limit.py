#!/usr/bin/env python3
"""
Script de teste: Ordem Limit POST-ONLY no Polymarket.

Envia uma ordem de 5 shares no mercado BTC 15min ativo.
POST-ONLY significa que a ordem so pode ser maker (adiciona ao book).

SEGURANCA:
- Usa apenas httpx (HTTP seguro)
- Assinatura HMAC local
- Nenhuma biblioteca terceira suspeita

USO:
    python scripts/test_order_limit.py

REQUISITOS:
    - POLYMARKET_API_KEY, POLYMARKET_API_SECRET, POLYMARKET_PASSPHRASE no .env
    - POLYMARKET_PRIVATE_KEY e POLYMARKET_FUNDER no .env (para ordem assinada)
    - pip install py-clob-client (criar/assinar ordem no formato CLOB)
    - Saldo USDC na conta
"""

import os
import sys
import time
import hmac
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv


# Configuracoes
CLOB_HOST = "https://clob.polymarket.com"
GAMMA_HOST = "https://gamma-api.polymarket.com"

# Minimo de shares
MIN_SHARES = 5


# Janela 15min em segundos
WINDOW_SECONDS = 900


def _current_window_ts() -> int:
    """Timestamp do inicio da janela 15min atual."""
    return int(time.time() // WINDOW_SECONDS) * WINDOW_SECONDS


def _get_signer_address() -> str:
    """Endereco do signer (EOA) para headers L2. CLOB exige POLY_ADDRESS."""
    from eth_account import Account
    pk = os.getenv("POLYMARKET_PRIVATE_KEY", "")
    if not pk:
        return ""
    if not pk.startswith("0x"):
        pk = f"0x{pk}"
    return Account.from_key(pk).address


def sign_request(api_secret: str, method: str, path: str, body: str = "", poly_address: str | None = None) -> dict:
    """
    Assina request com HMAC-SHA256 (L2), igual ao py-clob-client.
    Secret em base64 url-safe; assinatura retornada em base64 url-safe.
    Timestamp em segundos (como no place_order_limit_clob).
    Para signature_type=1 (magic email): poly_address deve ser o FUNDER (proxy).
    """
    import base64
    timestamp = str(int(time.time()))
    message = timestamp + method + path
    if body:
        message += body.replace("'", '"')

    try:
        secret_bytes = base64.urlsafe_b64decode(api_secret)
    except Exception:
        secret_bytes = api_secret.encode()

    h = hmac.new(secret_bytes, message.encode("utf-8"), hashlib.sha256)
    signature_b64 = base64.urlsafe_b64encode(h.digest()).decode("utf-8")

    address = (poly_address or _get_signer_address()).strip()
    if address and not address.startswith("0x"):
        address = f"0x{address}" if len(address) == 40 else address

    headers = {
        "POLY_ADDRESS": address,
        "POLY_API_KEY": os.getenv("POLYMARKET_API_KEY"),
        "POLY_TIMESTAMP": timestamp,
        "POLY_SIGNATURE": signature_b64,
        "POLY_PASSPHRASE": os.getenv("POLYMARKET_PASSPHRASE", ""),
        "Content-Type": "application/json",
    }
    return headers


def find_btc_15min_market() -> dict | None:
    """
    Busca o mercado BTC 15min ativo via slug (mesmo metodo do recorder).
    Slug: btc-updown-15m-{window_ts}
    """
    window_ts = _current_window_ts()
    slug = f"btc-updown-15m-{window_ts}"
    print(f"Buscando mercado BTC 15min (slug: {slug})...")

    with httpx.Client(timeout=30) as client:
        response = client.get(f"{GAMMA_HOST}/events/slug/{slug}")

        if response.status_code == 404:
            print("Mercado ainda nao existe nesta janela (404). Tente em alguns segundos.")
            return None
        if response.status_code != 200:
            print(f"Erro Gamma: {response.status_code}")
            return None

        event = response.json()
        markets = event.get("markets", [])
        if not markets:
            print("Evento sem markets")
            return None

        market = markets[0]
        raw = market.get("clobTokenIds")
        if isinstance(raw, str):
            import json
            try:
                tokens = json.loads(raw)
            except json.JSONDecodeError:
                print("clobTokenIds invalido")
                return None
        else:
            tokens = raw or []
        if len(tokens) < 2:
            print("Mercado sem 2 tokens")
        return None

        # Retornar no formato esperado pelo main: token_id YES = primeiro
        print(f"Mercado encontrado: {event.get('title', slug)}")
        return {
            "question": event.get("title", slug),
            "tokens": [
                {"token_id": tokens[0], "outcome": "YES"},
                {"token_id": tokens[1], "outcome": "NO"},
            ],
        }


def get_orderbook(token_id: str) -> dict | None:
    """
    Busca o orderbook para um token.
    """
    with httpx.Client(timeout=30) as client:
        response = client.get(
            f"{CLOB_HOST}/book",
            params={"token_id": token_id}
        )

        if response.status_code != 200:
            print(f"Erro ao buscar orderbook: {response.status_code}")
            return None

        return response.json()


def place_limit_order(
    private_key: str,
    funder: str,
    api_key: str,
    api_secret: str,
    api_passphrase: str,
    token_id: str,
    side: str,
    size: float,
    price: float,
) -> dict | None:
    """
    Envia ordem limit POST-ONLY usando ordem assinada (formato CLOB).

    A API exige body: { order, owner, orderType, postOnly } com order assinado.
    Para signature_type=1 (magic email): L2 usa POLY_ADDRESS=funder (proxy).
    """
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import OrderArgs, OrderType, ApiCreds
        from py_clob_client.order_builder.constants import BUY
        from py_clob_client.utilities import order_to_json
    except ImportError:
        print("ERRO: py-clob-client nao instalado. Execute: pip install py-clob-client")
        return None

    if not private_key.startswith("0x"):
        private_key = f"0x{private_key}"
    funder_addr = funder.strip() if funder else ""
    if funder_addr and not funder_addr.startswith("0x"):
        funder_addr = f"0x{funder_addr}"

    sig_type = int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "1"))
    client = ClobClient(
        CLOB_HOST,
        chain_id=137,
        key=private_key,
        signature_type=sig_type,
        funder=funder_addr,
    )
    client.set_api_creds(ApiCreds(api_key=api_key, api_secret=api_secret, api_passphrase=api_passphrase))

    # GTC: expiration deve ser 0 (servidor rejeita outro valor)
    order_args = OrderArgs(price=price, size=size, side=BUY, token_id=token_id)
    signed_order = client.create_order(order_args)
    body_dict = order_to_json(signed_order, api_key, OrderType.GTC, post_only=True)
    body = json.dumps(body_dict, separators=(",", ":"), ensure_ascii=False)

    path = "/order"
    # Type 1 (magic email): tentar signer; se 400 invalid signature, tentar POLY_ADDRESS=funder
    attempts = [("signer", None)]
    if sig_type == 1 and funder_addr:
        attempts.append(("funder", funder_addr))
    for poly_addr_label, poly_addr in attempts:
        headers = sign_request(api_secret, "POST", path, body, poly_address=poly_addr)

        print(f"\nEnviando ordem (POLY_ADDRESS={poly_addr_label}):")
    print(f"  Token: {token_id[:20]}...")
    print(f"  Side: {side}")
    print(f"  Size: {size} shares")
    print(f"  Price: ${price}")
        print(f"  PostOnly: True (type 1)")

        with httpx.Client(timeout=30) as client_http:
            response = client_http.post(
            f"{CLOB_HOST}{path}",
            headers=headers,
            content=body,
        )
        print(f"\nResposta: {response.status_code}")
        if response.status_code == 200:
            result = response.json()
            print(f"Ordem criada: {json.dumps(result, indent=2)}")
            return result
        err_text = response.text
        if response.status_code == 400 and "invalid signature" in err_text.lower() and poly_addr_label == "signer" and funder_addr:
            print("  (type 1: tentando com POLY_ADDRESS=funder...)")
            continue
        if response.status_code == 401 and poly_addr_label == "funder":
            print("\n  Para type 1 (magic email), a API key deve estar associada ao funder.")
            print("  Gere as keys de novo: echo 1 | python scripts/generate_api_keys.py")
        if response.status_code == 400 and "invalid signature" in err_text.lower():
            print("\n  Dica: confira POLYMARKET_PRIVATE_KEY e POLYMARKET_FUNDER (proxy).")
            print("  Para type 1, keys geradas com generate_api_keys.py ficam no signer (EOA).")
        print(f"Erro: {err_text}")
        return None
            return None


def main():
    print("=" * 60)
    print("TESTE DE ORDEM LIMIT POST-ONLY - POLYMARKET")
    print("=" * 60)
    print()

    # Carregar .env
    env_path = Path(__file__).parent.parent / ".env"
    load_dotenv(env_path)

    api_key = os.getenv("POLYMARKET_API_KEY")
    api_secret = os.getenv("POLYMARKET_API_SECRET")
    api_passphrase = os.getenv("POLYMARKET_PASSPHRASE", "")
    private_key = os.getenv("POLYMARKET_PRIVATE_KEY")
    funder = os.getenv("POLYMARKET_FUNDER")

    if not api_key or not api_secret:
        print("ERRO: Credenciais nao encontradas no .env")
        print()
        print("Execute primeiro:")
        print("  python scripts/generate_api_keys.py")
        print()
        print("E adicione ao .env:")
        print("  POLYMARKET_API_KEY=...")
        print("  POLYMARKET_API_SECRET=...")
        sys.exit(1)
    if not private_key or not funder:
        print("ERRO: POLYMARKET_PRIVATE_KEY e POLYMARKET_FUNDER sao obrigatorios no .env (ordem assinada)")
        sys.exit(1)

    print(f"API Key: {api_key[:8]}...{api_key[-4:]}")
    print()

    # 1. Buscar mercado BTC 15min
    market = find_btc_15min_market()

    if not market:
        print("ERRO: Nenhum mercado BTC encontrado")
        sys.exit(1)

    # Pegar tokens (YES e NO)
    tokens = market.get("tokens", [])
    if len(tokens) < 2:
        print("ERRO: Mercado sem tokens")
        sys.exit(1)

    # Usar o token YES
    yes_token = None
    for token in tokens:
        if token.get("outcome", "").upper() == "YES":
            yes_token = token
            break

    if not yes_token:
        yes_token = tokens[0]

    token_id = yes_token.get("token_id")
    print(f"\nToken YES: {token_id[:30]}...")

    # 2. Buscar orderbook
    print("\nBuscando orderbook...")
    book = get_orderbook(token_id)

    if not book:
        print("ERRO: Nao conseguiu buscar orderbook")
        sys.exit(1)

    bids = book.get("bids", [])
    asks = book.get("asks", [])

    if bids:
        best_bid = float(bids[0].get("price", 0))
        print(f"Melhor bid: ${best_bid:.4f}")
    else:
        best_bid = 0.40
        print(f"Sem bids, usando: ${best_bid:.4f}")

    if asks:
        best_ask = float(asks[0].get("price", 0))
        print(f"Melhor ask: ${best_ask:.4f}")
    else:
        best_ask = 0.60
        print(f"Sem asks, usando: ${best_ask:.4f}")

    # 3. Calcular preco para ordem POST-ONLY
    # Para garantir que seja maker, colocar abaixo do best bid (se BUY)
    # ou acima do best ask (se SELL)

    # Vamos fazer uma ordem de COMPRA (BUY) 1 centavo abaixo do bid
    order_price = round(best_bid - 0.01, 2)
    if order_price < 0.01:
        order_price = 0.01

    print(f"\nPreco da ordem: ${order_price:.2f} (1c abaixo do bid)")
    print(f"Shares: {MIN_SHARES}")
    print(f"Custo maximo: ${order_price * MIN_SHARES:.2f}")

    # 4. Confirmar com usuario
    print()
    print("=" * 60)
    confirm = input("Enviar ordem? [s/N]: ").strip().lower()

    if confirm != "s":
        print("Cancelado pelo usuario")
        sys.exit(0)

    # 5. Enviar ordem (ordem assinada via py-clob-client)
    result = place_limit_order(
        private_key=private_key,
        funder=funder,
        api_key=api_key,
        api_secret=api_secret,
        api_passphrase=api_passphrase,
        token_id=token_id,
        side="BUY",
        size=MIN_SHARES,
        price=order_price,
    )

    if result:
        print()
        print("=" * 60)
        print("ORDEM ENVIADA COM SUCESSO!")
        print("=" * 60)
        print()
        print("A ordem POST-ONLY foi adicionada ao orderbook.")
        print("Como o preco esta abaixo do mercado, ela ficara pendente.")
        print()
        print("Voce pode verificar em: https://polymarket.com/portfolio")
    else:
        print()
        print("ERRO ao enviar ordem")
        sys.exit(1)


if __name__ == "__main__":
    main()
