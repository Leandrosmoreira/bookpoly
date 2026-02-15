"""
Configuration for secure claim module.

SECURITY: This module uses ONLY official Ethereum libraries.
No third-party polymarket wrappers that could leak credentials.
"""
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()

try:
    from polygon_rpc import get_polygon_rpc_list
except ImportError:
    def get_polygon_rpc_list():
        return [os.getenv("POLYGON_RPC", "https://polygon-rpc.com")]


# Polygon Mainnet Contract Addresses (official, verified on PolygonScan)
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

# Network: lista de RPCs para fallback em rate limit
POLYGON_RPC_URLS = get_polygon_rpc_list()
POLYGON_RPC = POLYGON_RPC_URLS[0] if POLYGON_RPC_URLS else "https://polygon-rpc.com"
CHAIN_ID = 137

# Data API (public, no auth needed)
DATA_API_URL = "https://data-api.polymarket.com"


@dataclass
class ClaimConfig:
    """Configuration for claim operations."""

    # Wallet
    private_key: str = ""
    wallet_address: str = ""  # Proxy wallet (funder)

    # Network: primeiro RPC; lista completa em rpc_urls para fallback
    rpc_url: str = POLYGON_RPC
    rpc_urls: list = field(default_factory=get_polygon_rpc_list)
    chain_id: int = CHAIN_ID

    # Contracts
    ctf_address: str = CTF_ADDRESS
    usdc_address: str = USDC_ADDRESS

    # Options (sempre LIVE; dry_run removido)
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
