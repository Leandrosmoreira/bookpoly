"""
Configuration for secure claim module.

SECURITY: This module uses ONLY official Ethereum libraries.
No third-party polymarket wrappers that could leak credentials.
"""
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


# Polygon Mainnet Contract Addresses (official, verified on PolygonScan)
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

# Network
POLYGON_RPC = os.getenv("POLYGON_RPC", "https://polygon-rpc.com")
CHAIN_ID = 137

# Data API (public, no auth needed)
DATA_API_URL = "https://data-api.polymarket.com"


@dataclass
class ClaimConfig:
    """Configuration for claim operations."""

    # Wallet
    private_key: str = ""
    wallet_address: str = ""  # Proxy wallet (funder)

    # Network
    rpc_url: str = POLYGON_RPC
    chain_id: int = CHAIN_ID

    # Contracts
    ctf_address: str = CTF_ADDRESS
    usdc_address: str = USDC_ADDRESS

    # Options
    dry_run: bool = True
    gas_limit: int = 300000

    def __post_init__(self):
        # Load from environment
        if not self.private_key:
            self.private_key = os.getenv("POLYMARKET_PRIVATE_KEY", "")
        if not self.wallet_address:
            self.wallet_address = os.getenv("POLYMARKET_FUNDER", "")

    def validate(self) -> list[str]:
        """Validate configuration."""
        errors = []

        if not self.private_key:
            errors.append("POLYMARKET_PRIVATE_KEY not set")

        if not self.wallet_address:
            errors.append("POLYMARKET_FUNDER not set")

        if self.private_key and not self.private_key.startswith("0x"):
            if len(self.private_key) == 64:
                self.private_key = "0x" + self.private_key
            else:
                errors.append("Invalid private key format")

        return errors


# Minimal ABI for CTF contract - only redeemPositions function
CTF_ABI = [
    {
        "type": "function",
        "name": "redeemPositions",
        "inputs": [
            {"name": "collateralToken", "type": "address"},
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId", "type": "bytes32"},
            {"name": "indexSets", "type": "uint256[]"}
        ],
        "outputs": [],
        "stateMutability": "nonpayable"
    },
    {
        "type": "function",
        "name": "balanceOf",
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "id", "type": "uint256"}
        ],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view"
    }
]
