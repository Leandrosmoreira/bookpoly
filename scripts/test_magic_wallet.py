#!/usr/bin/env python3
"""
Script de diagnostico para conta Magic Link (POLY_PROXY / signature_type=1).

Verifica se a private key exportada corresponde ao funder address.

USO:
    python scripts/test_magic_wallet.py
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

try:
    from eth_account import Account
    from web3 import Web3
    import httpx
except ImportError:
    print("ERRO: Instale as dependencias:")
    print("  pip install eth-account web3 httpx")
    sys.exit(1)


CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID = 137


def main():
    print("=" * 60)
    print("DIAGNOSTICO MAGIC WALLET (POLY_PROXY)")
    print("=" * 60)

    pk = os.getenv("POLYMARKET_PRIVATE_KEY", "")
    funder = os.getenv("POLYMARKET_FUNDER", "")

    if not pk or not funder:
        print("\nERRO: Configure POLYMARKET_PRIVATE_KEY e POLYMARKET_FUNDER no .env")
        sys.exit(1)

    if not pk.startswith("0x"):
        pk = f"0x{pk}"

    # Derivar EOA
    account = Account.from_key(pk)
    eoa = account.address

    print(f"\nPrivate Key: {pk[:15]}...{pk[-6:]}")
    print(f"EOA (Signer): {eoa}")
    print(f"Funder (Proxy): {funder}")

    # Conectar ao Polygon
    w3 = Web3(Web3.HTTPProvider("https://polygon-rpc.com"))

    # 1. Verificar se proxy esta deployado
    print("\n--- Verificacao 1: Proxy deployado ---")
    funder_checksum = Web3.to_checksum_address(funder)
    code = w3.eth.get_code(funder_checksum)
    if len(code) > 0:
        print(f"OK: Proxy deployado ({len(code)} bytes)")
    else:
        print("ERRO: Proxy NAO deployado!")
        sys.exit(1)

    # 2. Verificar saldo USDC
    print("\n--- Verificacao 2: Saldo USDC ---")
    usdc = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
    balance_data = "0x70a08231" + funder[2:].lower().zfill(64)
    result = w3.eth.call({"to": usdc, "data": balance_data})
    usdc_balance = int(result.hex(), 16) / 1e6
    print(f"Saldo USDC: ${usdc_balance:.2f}")

    if usdc_balance < 5:
        print("AVISO: Saldo baixo para fazer ordens (minimo 5 USDC)")

    # 3. Testar L1 Auth
    print("\n--- Verificacao 3: L1 Auth (API) ---")
    try:
        with httpx.Client(timeout=10) as c:
            ts = int(float(c.get(f"{CLOB_HOST}/time").text.strip()))

        domain = {"name": "ClobAuthDomain", "version": "1", "chainId": CHAIN_ID}
        types = {
            "ClobAuth": [
                {"name": "address", "type": "address"},
                {"name": "timestamp", "type": "string"},
                {"name": "nonce", "type": "uint256"},
                {"name": "message", "type": "string"},
            ]
        }
        msg = {
            "address": eoa,
            "timestamp": str(ts),
            "nonce": 0,
            "message": "This message attests that I control the given wallet",
        }

        signed = Account.sign_typed_data(pk, domain, types, msg)
        sig = "0x" + signed.signature.hex()

        headers = {
            "POLY_ADDRESS": eoa,
            "POLY_SIGNATURE": sig,
            "POLY_TIMESTAMP": str(ts),
            "POLY_NONCE": "0",
        }

        with httpx.Client(timeout=15) as c:
            r = c.get(f"{CLOB_HOST}/auth/derive-api-key", headers=headers)

        if r.status_code == 200:
            creds = r.json()
            print(f"OK: L1 Auth funcionando")
            print(f"  API Key: {creds.get('apiKey', 'N/A')[:20]}...")
        else:
            print(f"ERRO: L1 Auth falhou ({r.status_code})")
            print(f"  {r.text[:100]}")
            print("\n*** A private key NAO esta vinculada a esta conta no Polymarket! ***")
            sys.exit(1)

    except Exception as e:
        print(f"ERRO: {e}")
        sys.exit(1)

    # 4. Testar criacao de ordem
    print("\n--- Verificacao 4: Criacao de Ordem ---")
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY

        creds = ApiCreds(
            api_key=os.getenv("POLYMARKET_API_KEY", creds.get("apiKey", "")),
            api_secret=os.getenv("POLYMARKET_API_SECRET", creds.get("secret", "")),
            api_passphrase=os.getenv("POLYMARKET_PASSPHRASE", creds.get("passphrase", "")),
        )

        client = ClobClient(
            host=CLOB_HOST,
            chain_id=CHAIN_ID,
            key=pk,
            creds=creds,
            signature_type=1,
            funder=funder,
        )

        print(f"Cliente configurado:")
        print(f"  Signer: {client.builder.signer.address()}")
        print(f"  Funder: {client.builder.funder}")
        print(f"  Sig Type: {client.builder.sig_type}")

        # Buscar mercado ativo
        import requests

        resp = requests.get(f"{CLOB_HOST}/sampling-markets?limit=50", timeout=15)
        markets = resp.json().get("data", [])

        token_id = None
        for m in markets:
            if not m.get("neg_risk") and m.get("accepting_orders"):
                tokens = m.get("tokens", [])
                if tokens:
                    token_id = tokens[0]["token_id"]
                    print(f"\nMercado de teste: {m.get('question', '')[:40]}...")
                    break

        if not token_id:
            print("AVISO: Nenhum mercado encontrado para teste")
            return

        # Criar ordem
        order_args = OrderArgs(token_id=token_id, price=0.01, size=5.0, side=BUY)
        signed_order = client.create_order(order_args)
        od = signed_order.dict()

        print(f"\nOrdem criada:")
        print(f"  Maker: {od.get('maker')}")
        print(f"  Signer: {od.get('signer')}")
        print(f"  SignatureType: {od.get('signatureType')}")

        # 5. Testar envio
        print("\n--- Verificacao 5: Envio de Ordem ---")
        try:
            resp = client.post_order(signed_order, OrderType.GTC)
            print(f"\n*** SUCESSO! Ordem enviada! ***")
            print(f"Resposta: {resp}")
        except Exception as e:
            error_msg = str(e)
            print(f"ERRO ao enviar: {error_msg}")

            if "invalid signature" in error_msg.lower():
                print("\n" + "=" * 60)
                print("DIAGNOSTICO: Invalid Signature")
                print("=" * 60)
                print("\nA assinatura foi rejeitada pelo servidor Polymarket.")
                print("Isso significa que:")
                print("  1. A private key exportada NAO corresponde ao proxy funder")
                print("  2. O mapeamento EOA -> Proxy esta incorreto no backend")
                print("\nSOLUCAO:")
                print("  1. Acesse https://reveal.magic.link/polymarket")
                print("  2. Faca login com o email da conta")
                print("  3. Exporte a private key por esse metodo")
                print("  4. Atualize POLYMARKET_PRIVATE_KEY no .env")
                print("  5. Execute este script novamente")

    except ImportError:
        print("AVISO: py-clob-client nao instalado, pulando teste de ordem")


if __name__ == "__main__":
    main()
