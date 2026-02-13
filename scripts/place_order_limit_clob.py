#!/usr/bin/env python3
"""
Envia uma ordem limit POST-ONLY (signature_type=1 = Poly Proxy/Magic).

Fluxo: (1) py-clob-client cria e assina a ordem (maker=funder, signer=EOA);
(2) POST /order com headers L2 (POLY_ADDRESS=signer para auth).

Nota: Para contas type 1 o CLOB pode responder 400 "invalid signature" na ordem.
Se isso ocorrer, confira no site (polymarket.com) se a conta consegue enviar
ordens; se sim, pode ser limitação do backend para API com type 1.

USO:
    python scripts/place_order_limit_clob.py
"""

import base64
import hmac
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

WINDOW_SECONDS = 900
GAMMA_HOST = os.getenv("GAMMA_BASE_URL", "https://gamma-api.polymarket.com")
CLOB_HOST = os.getenv("CLOB_BASE_URL", "https://clob.polymarket.com").rstrip("/")
CHAIN_ID = 137
MIN_SHARES = 5
POST_ORDER_PATH = "/order"


def current_window_ts():
    return int(time.time() // WINDOW_SECONDS) * WINDOW_SECONDS


def get_btc_15min_token_id():
    """Token YES do mercado BTC 15min atual (slug Gamma)."""
    window_ts = current_window_ts()
    slug = f"btc-updown-15m-{window_ts}"
    with httpx.Client(timeout=30) as client:
        r = client.get(f"{GAMMA_HOST}/events/slug/{slug}")
        if r.status_code != 200:
            return None, None
        event = r.json()
        markets = event.get("markets", [])
        if not markets:
            return None, None
        raw = markets[0].get("clobTokenIds")
        if isinstance(raw, str):
            tokens = json.loads(raw)
        else:
            tokens = raw or []
        if len(tokens) < 2:
            return None, None
        return tokens[0], event.get("title", slug)


def build_l2_headers(api_key: str, api_secret: str, passphrase: str, poly_address: str, method: str, path: str, body: str) -> dict:
    """Headers L2 com HMAC. poly_address = FUNDER para type 1."""
    timestamp = str(int(time.time()))  # segundos, como no py-clob-client
    message = timestamp + method + path
    if body:
        message += body.replace("'", '"')
    try:
        secret_bytes = base64.urlsafe_b64decode(api_secret)
    except Exception:
        secret_bytes = api_secret.encode()
    h = hmac.new(secret_bytes, message.encode("utf-8"), hashlib.sha256)
    sig_b64 = base64.urlsafe_b64encode(h.digest()).decode("utf-8")
    return {
        "POLY_ADDRESS": poly_address,
        "POLY_API_KEY": api_key,
        "POLY_TIMESTAMP": timestamp,
        "POLY_SIGNATURE": sig_b64,
        "POLY_PASSPHRASE": passphrase,
        "Content-Type": "application/json",
    }


def main():
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY
        from py_clob_client.utilities import order_to_json
    except ImportError:
        print("ERRO: pip install py-clob-client")
        sys.exit(1)

    key = os.getenv("POLYMARKET_PRIVATE_KEY")
    funder = os.getenv("POLYMARKET_FUNDER")
    if not key or not funder:
        print("ERRO: POLYMARKET_PRIVATE_KEY e POLYMARKET_FUNDER no .env")
        sys.exit(1)
    if not key.startswith("0x"):
        key = f"0x{key}"

    print("=" * 60)
    print("ORDEM LIMIT POST-ONLY (type 1 = POLY_ADDRESS = funder)")
    print("=" * 60)

    token_id, title = get_btc_15min_token_id()
    if not token_id:
        print("ERRO: Mercado BTC 15min nao encontrado")
        sys.exit(1)
    print(f"Mercado: {title}")
    print(f"Token YES: {token_id[:30]}...")

    sig_type = int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "1"))
    client = ClobClient(CLOB_HOST, chain_id=CHAIN_ID, key=key, signature_type=sig_type, funder=funder)
    api_key = os.getenv("POLYMARKET_API_KEY")
    api_secret = os.getenv("POLYMARKET_API_SECRET")
    api_pass = os.getenv("POLYMARKET_PASSPHRASE")
    if api_key and api_secret and api_pass:
        from py_clob_client.clob_types import ApiCreds
        client.set_api_creds(ApiCreds(api_key=api_key, api_secret=api_secret, api_passphrase=api_pass))
        print("Usando credenciais do .env")
    else:
        print("Derivando API creds (L1)...")
        client.set_api_creds(client.create_or_derive_api_creds())

    creds = client.creds
    price = 0.01
    size = float(MIN_SHARES)
    print(f"\nOrdem: BUY {size} @ ${price} (POST ONLY)")
    print(f"Custo max: ${price * size:.2f}")
    confirm = input("Enviar? [s/N]: ").strip().lower()
    if confirm != "s":
        print("Cancelado.")
        sys.exit(0)

    order_args = OrderArgs(price=price, size=size, side=BUY, token_id=token_id)
    signed_order = client.create_order(order_args)

    # Body igual ao client: order.dict(), owner, orderType, postOnly
    body_dict = order_to_json(signed_order, creds.api_key, OrderType.GTC, post_only=True)
    body_str = json.dumps(body_dict, separators=(",", ":"), ensure_ascii=False)

    # L2 auth: API key foi derivada com signer (EOA), entao POLY_ADDRESS = signer
    from eth_account import Account
    signer_addr = Account.from_key(key).address
    headers = build_l2_headers(
        creds.api_key, creds.api_secret, creds.api_passphrase,
        poly_address=signer_addr,
        method="POST", path=POST_ORDER_PATH, body=body_str,
    )

    resp = httpx.post(CLOB_HOST + POST_ORDER_PATH, headers=headers, content=body_str, timeout=30)

    if resp.status_code == 200:
        data = resp.json()
        print("\n" + "=" * 60)
        print("ORDEM ENVIADA")
        print("=" * 60)
        print(json.dumps(data, indent=2))
    else:
        print(f"Erro {resp.status_code}: {resp.text}")
        sys.exit(1)


if __name__ == "__main__":
    main()
