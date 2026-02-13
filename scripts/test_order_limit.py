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
    - POLYMARKET_API_KEY no .env
    - POLYMARKET_API_SECRET no .env
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


def sign_request(api_secret: str, method: str, path: str, body: str = "") -> dict:
    """
    Assina request com HMAC-SHA256 (L2), igual ao py-clob-client.
    Secret em base64 url-safe; assinatura retornada em base64 url-safe.
    """
    import base64
    timestamp = str(int(time.time() * 1000))
    message = timestamp + method + path
    if body:
        message += body.replace("'", '"')

    try:
        secret_bytes = base64.urlsafe_b64decode(api_secret)
    except Exception:
        secret_bytes = api_secret.encode()

    h = hmac.new(secret_bytes, message.encode("utf-8"), hashlib.sha256)
    signature_b64 = base64.urlsafe_b64encode(h.digest()).decode("utf-8")

    headers = {
        "POLY_ADDRESS": _get_signer_address(),
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
    api_secret: str,
    token_id: str,
    side: str,
    size: float,
    price: float,
) -> dict | None:
    """
    Envia ordem limit POST-ONLY.

    POST-ONLY garante que a ordem so pode ser maker.
    Se fosse executar imediatamente (taker), ela e cancelada.
    """
    path = "/order"

    order_data = {
        "tokenID": token_id,
        "side": side,
        "size": str(size),
        "price": str(price),
        "type": "GTC",  # Good Till Cancelled
        "postOnly": True,  # IMPORTANTE: Apenas maker
    }

    body = json.dumps(order_data)
    headers = sign_request(api_secret, "POST", path, body)

    print(f"\nEnviando ordem:")
    print(f"  Token: {token_id[:20]}...")
    print(f"  Side: {side}")
    print(f"  Size: {size} shares")
    print(f"  Price: ${price}")
    print(f"  PostOnly: True")

    with httpx.Client(timeout=30) as client:
        response = client.post(
            f"{CLOB_HOST}{path}",
            headers=headers,
            content=body,
        )

        print(f"\nResposta: {response.status_code}")

        if response.status_code == 200:
            result = response.json()
            print(f"Ordem criada: {json.dumps(result, indent=2)}")
            return result
        else:
            print(f"Erro: {response.text}")
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

    # 5. Enviar ordem
    result = place_limit_order(
        api_secret=api_secret,
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
