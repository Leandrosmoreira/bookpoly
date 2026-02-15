#!/usr/bin/env python3
"""
Envia ordens limit POST-ONLY usando signature_type=1 (Magic/Proxy).
Suporta múltiplos mercados 15min simultaneamente.

USO:
    python scripts/send_order.py
"""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
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

# Mercados 15min para testar
ASSETS = ['btc', 'eth', 'sol', 'xrp']


def get_15min_token(asset: str):
    """Busca token para qualquer ativo 15min."""
    window_ts = int(time.time() // 900) * 900
    slug = f"{asset}-updown-15m-{window_ts}"

    with httpx.Client(timeout=30) as c:
        r = c.get(f"{GAMMA_HOST}/events/slug/{slug}")
        if r.status_code != 200:
            return None, None, asset
        event = r.json()
        markets = event.get("markets", [])
        if not markets:
            return None, None, asset
        raw = markets[0].get("clobTokenIds")
        tokens = json.loads(raw) if isinstance(raw, str) else (raw or [])
        if len(tokens) < 2:
            return None, None, asset
        return tokens[0], event.get("title", slug), asset


def get_best_bid(token_id: str) -> float:
    with httpx.Client(timeout=30) as c:
        r = c.get(f"{CLOB_HOST}/book", params={"token_id": token_id})
        if r.status_code == 200:
            bids = r.json().get("bids", [])
            if bids:
                return float(bids[0].get("price", 0.01))
    return 0.01


def send_single_order(client, asset: str, token_id: str, price: float):
    """Envia uma ordem para um mercado específico."""
    try:
        resp = client.create_and_post_order(
            OrderArgs(token_id=token_id, price=price, size=float(MIN_SHARES), side=BUY),
        )
        return (asset.upper(), "SUCCESS", resp)
    except Exception as e:
        return (asset.upper(), "ERRO", str(e))


def main():
    print("=" * 60)
    print("ORDENS LIMIT - TYPE 1 (Magic/Proxy)")
    print(f"MERCADOS: {', '.join(a.upper() for a in ASSETS)}")
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
    print()

    # Buscar todos os tokens em paralelo
    print("Buscando mercados...")
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(get_15min_token, ASSETS))

    # Filtrar mercados encontrados
    markets = []
    for token_id, title, asset in results:
        if token_id:
            best_bid = get_best_bid(token_id)
            price = max(0.01, round(best_bid - 0.01, 2))
            markets.append({
                "asset": asset,
                "token_id": token_id,
                "title": title,
                "price": price,
            })
            print(f"  {asset.upper()}: {title} @ ${price:.2f}")
        else:
            print(f"  {asset.upper()}: NAO ENCONTRADO")

    if not markets:
        print("\nERRO: Nenhum mercado encontrado")
        sys.exit(1)

    print(f"\nTotal: {len(markets)} mercados, {MIN_SHARES} shares cada")

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

    print("\nEnviando ordens uma por uma...\n")

    success_count = 0
    for i, m in enumerate(markets, 1):
        print(f"--- Ordem {i}/{len(markets)}: {m['asset'].upper()} ---")
        print(f"Mercado: {m['title']}")
        print(f"Preco: ${m['price']:.2f}")
        print(f"Shares: {MIN_SHARES}")

        result = send_single_order(client, m["asset"], m["token_id"], m["price"])
        asset, status, info = result

        if status == "SUCCESS":
            print(f"Status: SUCCESS")
            print(json.dumps(info, indent=2))
            success_count += 1
        else:
            print(f"Status: ERRO")
            print(f"  {info}")

        print()
        time.sleep(1)  # Delay entre ordens

    print("=" * 60)
    print(f"TOTAL: {success_count}/{len(markets)} ordens enviadas")
    print("=" * 60)


if __name__ == "__main__":
    main()
