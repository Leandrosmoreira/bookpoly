#!/usr/bin/env python3
"""
Verifica o saldo da conta Polymarket.

Mostra:
- Saldo USDC (on-chain)
- Saldo POL para gas (on-chain)
- Posicoes abertas (via Data API)
- Valor total estimado

SEGURANCA:
- Usa web3.py para consulta on-chain (trustless)
- Usa httpx para Data API publica
- NAO precisa de API keys para consultar saldo

DEPENDENCIAS:
    pip install web3 httpx python-dotenv

USO:
    python scripts/check_balance.py
"""

import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

try:
    from web3 import Web3
    from eth_account import Account
except ImportError:
    print("ERRO: web3 ou eth-account nao instalado")
    print("Execute: pip install web3 eth-account")
    sys.exit(1)


# Configuracoes
POLYGON_RPC = "https://polygon-rpc.com"
DATA_API = "https://data-api.polymarket.com"

# Contratos (Polygon)
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

# ABI minimo para balanceOf
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    }
]


def get_wallet_address() -> str:
    """Obtem endereco da carteira do .env."""
    # Tentar POLYMARKET_WALLET primeiro
    wallet = os.getenv("POLYMARKET_WALLET")
    if wallet:
        return Web3.to_checksum_address(wallet)

    # Tentar derivar de POLYMARKET_PRIVATE_KEY
    private_key = os.getenv("POLYMARKET_PRIVATE_KEY")
    if private_key:
        if not private_key.startswith("0x"):
            private_key = f"0x{private_key}"
        account = Account.from_key(private_key)
        return account.address

    # Tentar POLYMARKET_FUNDER
    funder = os.getenv("POLYMARKET_FUNDER")
    if funder:
        return Web3.to_checksum_address(funder)

    return None


def get_usdc_balance(w3: Web3, wallet: str) -> float:
    """Consulta saldo USDC on-chain."""
    usdc = w3.eth.contract(
        address=Web3.to_checksum_address(USDC_ADDRESS),
        abi=ERC20_ABI,
    )
    balance_raw = usdc.functions.balanceOf(wallet).call()
    # USDC tem 6 decimais
    return balance_raw / 10**6


def get_pol_balance(w3: Web3, wallet: str) -> float:
    """Consulta saldo POL (nativo) on-chain."""
    balance_wei = w3.eth.get_balance(wallet)
    return w3.from_wei(balance_wei, "ether")


def get_positions(wallet: str) -> list:
    """Consulta posicoes via Data API publica."""
    try:
        with httpx.Client(timeout=30) as client:
            response = client.get(
                f"{DATA_API}/positions",
                params={
                    "user": wallet.lower(),
                    "sizeThreshold": 0.01,
                    "limit": 100,
                }
            )

            if response.status_code != 200:
                print(f"  Aviso: Erro ao buscar posicoes ({response.status_code})")
                return []

            return response.json()
    except Exception as e:
        print(f"  Aviso: Erro ao buscar posicoes - {e}")
        return []


def format_usd(value: float) -> str:
    """Formata valor em USD."""
    if value >= 1000:
        return f"${value:,.2f}"
    return f"${value:.2f}"


def main():
    print("=" * 60)
    print("POLYMARKET - VERIFICAR SALDO")
    print("=" * 60)
    print()

    # Carregar .env
    env_path = Path(__file__).parent.parent / ".env"
    load_dotenv(env_path)

    # Obter endereco da carteira
    wallet = get_wallet_address()

    if not wallet:
        print("ERRO: Carteira nao encontrada no .env")
        print()
        print("Adicione ao seu .env:")
        print("  POLYMARKET_WALLET=0x...")
        print("  ou")
        print("  POLYMARKET_PRIVATE_KEY=0x...")
        sys.exit(1)

    print(f"Carteira: {wallet}")
    print()

    # Conectar ao Polygon
    print("Conectando ao Polygon...")
    w3 = Web3(Web3.HTTPProvider(POLYGON_RPC))

    if not w3.is_connected():
        print("ERRO: Nao foi possivel conectar ao Polygon RPC")
        sys.exit(1)

    print()
    print("-" * 60)
    print("SALDOS ON-CHAIN")
    print("-" * 60)

    # Saldo USDC
    usdc_balance = get_usdc_balance(w3, wallet)
    print(f"  USDC:  {format_usd(usdc_balance)}")

    # Saldo POL
    pol_balance = get_pol_balance(w3, wallet)
    print(f"  POL:   {pol_balance:.4f} POL")

    if pol_balance < 0.1:
        print(f"  [!] POL baixo - pode precisar para gas")

    print()
    print("-" * 60)
    print("POSICOES ABERTAS")
    print("-" * 60)

    positions = get_positions(wallet)

    if not positions:
        print("  Nenhuma posicao encontrada")
        total_positions_value = 0.0
    else:
        total_positions_value = 0.0
        for pos in positions:
            market_title = pos.get("market", {}).get("question", "Mercado desconhecido")
            outcome = pos.get("outcome", "?")
            size = float(pos.get("size", 0))
            price = float(pos.get("price", 0))
            value = size * price

            # Limitar titulo a 40 chars
            if len(market_title) > 40:
                market_title = market_title[:37] + "..."

            print(f"  {outcome}: {size:.2f} shares @ ${price:.2f} = {format_usd(value)}")
            print(f"      {market_title}")

            total_positions_value += value

        print()
        print(f"  Total em posicoes: {format_usd(total_positions_value)}")

    print()
    print("=" * 60)
    print("RESUMO")
    print("=" * 60)

    total_value = usdc_balance + total_positions_value

    print(f"  USDC disponivel:    {format_usd(usdc_balance)}")
    print(f"  Valor em posicoes:  {format_usd(total_positions_value)}")
    print(f"  --------------------------")
    print(f"  VALOR TOTAL:        {format_usd(total_value)}")
    print()
    print("=" * 60)


if __name__ == "__main__":
    main()
