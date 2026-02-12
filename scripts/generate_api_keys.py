#!/usr/bin/env python3
"""
Gerador Seguro de API Keys do Polymarket CLOB.

SEGURANCA:
- Usa apenas eth-account (biblioteca oficial Ethereum Foundation)
- Usa httpx para requests HTTP (biblioteca segura)
- Private key NUNCA sai da maquina
- Nenhum servidor terceiro envolvido
- NAO usa py-clob-client ou polymarket-apis

DEPENDENCIAS:
    pip install eth-account httpx python-dotenv

USO:
    python scripts/generate_api_keys.py
"""

import os
import sys
import time
import httpx
from pathlib import Path
from dotenv import load_dotenv

try:
    from eth_account import Account
    from eth_account.messages import encode_typed_data
except ImportError:
    print("ERRO: eth-account nao instalado")
    print("Execute: pip install eth-account")
    sys.exit(1)


# Configuracoes
CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID = 137  # Polygon


def create_l1_auth_headers(private_key: str, nonce: int = 0) -> dict:
    """
    Cria headers de autenticacao L1 usando EIP-712.

    A assinatura e feita LOCALMENTE - a private key nunca sai da maquina.
    """
    account = Account.from_key(private_key)
    address = account.address
    timestamp = int(time.time())

    # Estrutura EIP-712 para autenticacao Polymarket
    typed_data = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
            ],
            "ClobAuth": [
                {"name": "address", "type": "address"},
                {"name": "timestamp", "type": "string"},
                {"name": "nonce", "type": "uint256"},
            ]
        },
        "primaryType": "ClobAuth",
        "domain": {
            "name": "ClobAuthDomain",
            "version": "1",
            "chainId": CHAIN_ID,
        },
        "message": {
            "address": address,
            "timestamp": str(timestamp),
            "nonce": nonce,
        }
    }

    # Assinar localmente com eth-account
    encoded = encode_typed_data(full_message=typed_data)
    signed = account.sign_message(encoded)
    signature = signed.signature.hex()

    # Headers L1
    return {
        "POLY_ADDRESS": address,
        "POLY_SIGNATURE": signature,
        "POLY_TIMESTAMP": str(timestamp),
        "POLY_NONCE": str(nonce),
    }


def derive_api_key(private_key: str, nonce: int = 0) -> dict:
    """
    Deriva API key do Polymarket CLOB.

    Usa o endpoint oficial /auth/derive-api-key que retorna
    credenciais deterministicas baseadas na assinatura.
    """
    headers = create_l1_auth_headers(private_key, nonce)
    headers["Content-Type"] = "application/json"

    with httpx.Client(timeout=30) as client:
        response = client.get(
            f"{CLOB_HOST}/auth/derive-api-key",
            headers=headers,
        )

        if response.status_code != 200:
            raise Exception(f"Erro ao derivar API key: {response.status_code} - {response.text}")

        return response.json()


def create_api_key(private_key: str, nonce: int = 0) -> dict:
    """
    Cria uma nova API key no Polymarket CLOB.

    Diferente de derive, cria uma nova key a cada chamada.
    """
    headers = create_l1_auth_headers(private_key, nonce)
    headers["Content-Type"] = "application/json"

    with httpx.Client(timeout=30) as client:
        response = client.post(
            f"{CLOB_HOST}/auth/api-key",
            headers=headers,
        )

        if response.status_code != 200:
            raise Exception(f"Erro ao criar API key: {response.status_code} - {response.text}")

        return response.json()


def main():
    print("=" * 60)
    print("GERADOR SEGURO DE API KEYS - POLYMARKET CLOB")
    print("=" * 60)
    print()
    print("SEGURANCA:")
    print("  - Usa apenas eth-account (oficial)")
    print("  - Private key assinada LOCALMENTE")
    print("  - Nenhum servidor terceiro envolvido")
    print()

    # Carregar .env
    env_path = Path(__file__).parent.parent / ".env"
    load_dotenv(env_path)

    private_key = os.getenv("POLYMARKET_PRIVATE_KEY")

    if not private_key:
        print("ERRO: POLYMARKET_PRIVATE_KEY nao encontrada no .env")
        print()
        print("Adicione ao seu .env:")
        print("  POLYMARKET_PRIVATE_KEY=0x...")
        sys.exit(1)

    # Validar formato
    if not private_key.startswith("0x"):
        private_key = f"0x{private_key}"

    try:
        account = Account.from_key(private_key)
        print(f"Carteira: {account.address}")
        print()
    except Exception as e:
        print(f"ERRO: Private key invalida - {e}")
        sys.exit(1)

    print("Escolha uma opcao:")
    print("  1. Derivar API key (deterministica, mesma key sempre)")
    print("  2. Criar nova API key (gera nova a cada vez)")
    print()

    choice = input("Opcao [1/2]: ").strip()

    if choice not in ["1", "2"]:
        choice = "1"

    print()
    print("Gerando credenciais...")
    print()

    try:
        if choice == "1":
            creds = derive_api_key(private_key)
        else:
            creds = create_api_key(private_key)

        api_key = creds.get("apiKey", creds.get("api_key", ""))
        api_secret = creds.get("secret", creds.get("api_secret", ""))
        api_passphrase = creds.get("passphrase", creds.get("api_passphrase", ""))

        print("=" * 60)
        print("CREDENCIAIS GERADAS COM SUCESSO!")
        print("=" * 60)
        print()
        print("Adicione ao seu .env:")
        print()
        print(f"POLYMARKET_API_KEY={api_key}")
        print(f"POLYMARKET_API_SECRET={api_secret}")
        print(f"POLYMARKET_PASSPHRASE={api_passphrase}")
        print()
        print("=" * 60)
        print("IMPORTANTE: Guarde essas credenciais em local seguro!")
        print("=" * 60)

    except Exception as e:
        print(f"ERRO: {e}")
        print()
        print("Possiveis causas:")
        print("  - Private key invalida")
        print("  - Carteira nao registrada no Polymarket")
        print("  - Problema de conexao com API")
        sys.exit(1)


if __name__ == "__main__":
    main()
