#!/usr/bin/env python3
"""
Debug completo do envio de ordem Type 1.
Mostra cada passo do processo para identificar o problema.
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
    from py_clob_client.clob_types import OrderArgs, ApiCreds
    from py_clob_client.order_builder.constants import BUY
    from eth_account import Account
except ImportError as e:
    print(f"ERRO: {e}")
    print("Execute: pip install py-clob-client eth-account")
    sys.exit(1)

CLOB_HOST = os.getenv("CLOB_BASE_URL", "https://clob.polymarket.com")
GAMMA_HOST = os.getenv("GAMMA_BASE_URL", "https://gamma-api.polymarket.com")
CHAIN_ID = 137


def step(num, msg):
    print(f"\n[STEP {num}] {msg}")
    print("-" * 50)


def main():
    print("=" * 60)
    print("DEBUG ORDEM TYPE 1 (Magic/Proxy)")
    print("=" * 60)

    # Step 1: Verificar configuracao
    step(1, "Verificando configuracao")

    pk = os.getenv("POLYMARKET_PRIVATE_KEY", "")
    funder = os.getenv("POLYMARKET_FUNDER", "")
    api_key = os.getenv("POLYMARKET_API_KEY", "")
    api_secret = os.getenv("POLYMARKET_API_SECRET", "")
    api_pass = os.getenv("POLYMARKET_PASSPHRASE", "")
    sig_type = int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "1"))

    if not pk.startswith("0x"):
        pk = f"0x{pk}"

    signer = Account.from_key(pk).address

    print(f"Signature Type: {sig_type}")
    print(f"Signer (EOA):   {signer}")
    print(f"Funder (Proxy): {funder}")
    print(f"API Key:        {api_key[:16]}..." if api_key else "API Key: NAO CONFIGURADA")

    # Step 2: Verificar se funder esta deployado
    step(2, "Verificando funder on-chain")

    from web3 import Web3
    w3 = Web3(Web3.HTTPProvider("https://polygon-rpc.com"))

    funder_checksum = Web3.to_checksum_address(funder)
    code = w3.eth.get_code(funder_checksum)

    if len(code) > 0:
        print(f"Funder DEPLOYADO: {len(code)} bytes")
    else:
        print("ERRO: Funder NAO deployado!")
        print("Faca login no polymarket.com primeiro")
        sys.exit(1)

    # Step 3: Buscar mercado
    step(3, "Buscando mercado BTC 15min")

    window_ts = int(time.time() // 900) * 900
    slug = f"btc-updown-15m-{window_ts}"

    with httpx.Client(timeout=30) as c:
        r = c.get(f"{GAMMA_HOST}/events/slug/{slug}")
        if r.status_code != 200:
            print(f"ERRO: Mercado nao encontrado ({r.status_code})")
            sys.exit(1)

        event = r.json()
        markets = event.get("markets", [])
        raw = markets[0].get("clobTokenIds")
        tokens = json.loads(raw) if isinstance(raw, str) else raw
        token_id = tokens[0]

    print(f"Mercado: {event.get('title')}")
    print(f"Token ID: {token_id[:50]}...")
    print(f"negRisk: {markets[0].get('negRisk')}")

    # Step 4: Buscar orderbook
    step(4, "Buscando orderbook")

    with httpx.Client(timeout=30) as c:
        r = c.get(f"{CLOB_HOST}/book", params={"token_id": token_id})
        book = r.json()
        bids = book.get("bids", [])
        asks = book.get("asks", [])

    best_bid = float(bids[0]["price"]) if bids else 0.01
    best_ask = float(asks[0]["price"]) if asks else 0.99

    print(f"Best Bid: ${best_bid:.4f}")
    print(f"Best Ask: ${best_ask:.4f}")

    order_price = max(0.01, round(best_bid - 0.01, 2))
    print(f"Order Price: ${order_price:.2f}")

    # Step 5: Criar cliente
    step(5, "Criando ClobClient")

    client = ClobClient(
        CLOB_HOST,
        chain_id=CHAIN_ID,
        key=pk,
        signature_type=sig_type,
        funder=funder,
    )

    print(f"Client criado")
    print(f"  - Host: {CLOB_HOST}")
    print(f"  - Chain: {CHAIN_ID}")
    print(f"  - Sig Type: {sig_type}")

    # Step 6: Configurar credenciais
    step(6, "Configurando API credentials")

    if api_key and api_secret and api_pass:
        client.set_api_creds(ApiCreds(api_key, api_secret, api_pass))
        print("Usando credenciais do .env")
    else:
        print("Derivando novas credenciais...")
        creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)
        print(f"Nova API Key: {creds.api_key}")

    # Step 7: Criar ordem (sem enviar)
    step(7, "Criando ordem assinada")

    order_args = OrderArgs(
        token_id=token_id,
        price=order_price,
        size=5.0,
        side=BUY,
    )

    try:
        signed_order = client.create_order(order_args)
        print("Ordem criada com sucesso!")

        # Mostrar todos os atributos da ordem
        order_dict = vars(signed_order) if hasattr(signed_order, '__dict__') else {}
        for k, v in order_dict.items():
            val_str = str(v)[:50] if len(str(v)) > 50 else str(v)
            print(f"  - {k}: {val_str}")

    except Exception as e:
        print(f"ERRO ao criar ordem: {e}")
        sys.exit(1)

    # Step 8: Enviar ordem
    step(8, "Enviando ordem para CLOB")

    confirm = input("\nEnviar ordem? [s/N]: ").strip().lower()
    if confirm != "s":
        print("Cancelado")
        sys.exit(0)

    try:
        resp = client.post_order(signed_order)
        print("\n" + "=" * 60)
        print("SUCESSO!")
        print("=" * 60)
        print(json.dumps(resp, indent=2))
    except Exception as e:
        print(f"\nERRO: {e}")

        # Extrair detalhes do erro
        error_str = str(e)
        if "invalid signature" in error_str.lower():
            print("\n" + "=" * 60)
            print("DIAGNOSTICO: invalid signature")
            print("=" * 60)
            print()
            print("Este erro indica que o CLOB rejeitou a assinatura EIP-712.")
            print()
            print("Possiveis causas:")
            print("  1. A conta Magic tem estado corrompido no servidor")
            print("  2. O signer nao esta autorizado no proxy")
            print("  3. Incompatibilidade de versao do cliente")
            print()
            print("Solucoes:")
            print("  A) Criar conta Magic NOVA no Polymarket")
            print("  B) Usar conta Type 0 (MetaMask)")

        sys.exit(1)


if __name__ == "__main__":
    main()
