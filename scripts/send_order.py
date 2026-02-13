#!/usr/bin/env python3
"""
Envia ordem limit POST-ONLY usando signature_type=1 (Magic/Proxy).

USO:
    python scripts/send_order.py
"""

import json
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import OrderArgs, OrderType, ApiCreds
    from py_clob_client.order_builder.constants import BUY
except ImportError:
    print("ERRO: pip install py-clob-client")
    sys.exit(1)

CLOB_HOST = os.getenv("CLOB_BASE_URL", "https://clob.polymarket.com")
GAMMA_HOST = os.getenv("GAMMA_BASE_URL", "https://gamma-api.polymarket.com")
CHAIN_ID = 137
MIN_SHARES = 5


def get_btc_15min_token():
    window_ts = int(time.time() // 900) * 900
    slug = f"btc-updown-15m-{window_ts}"
    print(f"Buscando: {slug}")

    with httpx.Client(timeout=30) as c:
        r = c.get(f"{GAMMA_HOST}/events/slug/{slug}")
        if r.status_code != 200:
            return None, None
        event = r.json()
        markets = event.get("markets", [])
        if not markets:
            return None, None
        raw = markets[0].get("clobTokenIds")
        tokens = json.loads(raw) if isinstance(raw, str) else (raw or [])
        if len(tokens) < 2:
            return None, None
        return tokens[0], event.get("title", slug)


def get_best_bid(token_id: str) -> float:
    with httpx.Client(timeout=30) as c:
        r = c.get(f"{CLOB_HOST}/book", params={"token_id": token_id})
        if r.status_code == 200:
            bids = r.json().get("bids", [])
            if bids:
                return float(bids[0].get("price", 0.01))
    return 0.01


def main():
    print("=" * 60)
    print("ORDEM LIMIT - TYPE 1 (Magic/Proxy)")
    print("=" * 60)

    pk = os.getenv("POLYMARKET_PRIVATE_KEY", "")
    funder = os.getenv("POLYMARKET_FUNDER", "")

    if not pk or not funder:
        print("ERRO: Configure POLYMARKET_PRIVATE_KEY e POLYMARKET_FUNDER")
        sys.exit(1)

    if not pk.startswith("0x"):
        pk = f"0x{pk}"

    from eth_account import Account
    signer = Account.from_key(pk).address

    print(f"Signer: {signer}")
    print(f"Funder: {funder}")

    token_id, title = get_btc_15min_token()
    if not token_id:
        print("ERRO: Mercado nao encontrado")
        sys.exit(1)

    print(f"Mercado: {title}")

    best_bid = get_best_bid(token_id)
    price = max(0.01, round(best_bid - 0.01, 2))

    print(f"Preco: ${price:.2f}")
    print(f"Shares: {MIN_SHARES}")

    # Cliente Type 1
    client = ClobClient(
        CLOB_HOST,
        chain_id=CHAIN_ID,
        key=pk,
        signature_type=1,
        funder=funder,
    )

    # Credenciais
    api_key = os.getenv("POLYMARKET_API_KEY", "")
    api_secret = os.getenv("POLYMARKET_API_SECRET", "")
    api_pass = os.getenv("POLYMARKET_PASSPHRASE", "")

    if api_key and api_secret and api_pass:
        client.set_api_creds(ApiCreds(api_key, api_secret, api_pass))
    else:
        client.set_api_creds(client.create_or_derive_api_creds())

    confirm = input("\nEnviar? [s/N]: ").strip().lower()
    if confirm != "s":
        print("Cancelado")
        sys.exit(0)

    print("\nEnviando...")

    try:
        resp = client.create_and_post_order(
            OrderArgs(token_id=token_id, price=price, size=float(MIN_SHARES), side=BUY),
            order_type=OrderType.GTC,
            neg_risk=False,
        )
        print("\nSUCESSO!")
        print(json.dumps(resp, indent=2))
    except Exception as e:
        print(f"\nERRO: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
