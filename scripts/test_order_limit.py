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


def sign_request(api_secret: str, method: str, path: str, body: str = "") -> dict:
    """
    Assina request com HMAC-SHA256.

    A assinatura e feita LOCALMENTE - nenhum dado sensivel sai da maquina.
    """
    timestamp = str(int(time.time() * 1000))
    message = f"{timestamp}{method}{path}{body}"

    signature = hmac.new(
        api_secret.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()

    return {
        "POLY_API_KEY": os.getenv("POLYMARKET_API_KEY"),
        "POLY_TIMESTAMP": timestamp,
        "POLY_SIGNATURE": signature,
        "Content-Type": "application/json",
    }


def find_btc_15min_market() -> dict | None:
    """
    Busca o mercado BTC 15min ativo no momento.

    Usa a API publica Gamma (nao precisa autenticacao).
    """
    print("Buscando mercado BTC 15min ativo...")

    with httpx.Client(timeout=30) as client:
        # Buscar mercados BTC ativos
        response = client.get(
            f"{GAMMA_HOST}/markets",
            params={
                "active": "true",
                "closed": "false",
            }
        )

        if response.status_code != 200:
            print(f"Erro ao buscar mercados: {response.status_code}")
            return None

        markets = response.json()

        # Filtrar por BTC 15min
        for market in markets:
            question = market.get("question", "").lower()

            # Procurar por mercados BTC 15 minutos
            if "bitcoin" in question or "btc" in question:
                if "15" in question and ("minute" in question or "min" in question):
                    print(f"Mercado encontrado: {market.get('question')}")
                    return market

        # Se nao encontrou 15min, tentar 5min
        for market in markets:
            question = market.get("question", "").lower()
            if "bitcoin" in question or "btc" in question:
                if "5" in question and ("minute" in question or "min" in question):
                    print(f"Mercado alternativo (5min): {market.get('question')}")
                    return market

        print("Nenhum mercado BTC de curto prazo encontrado")
        return None


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
