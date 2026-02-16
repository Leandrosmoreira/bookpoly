"""
Configuração do Claim v2 — Contratos, ABIs, env vars (Builder + CTF).
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


# ─── Contratos (Polygon mainnet, oficiais) ────────────────────────────────────

CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
RELAYER_URL = "https://relayer-v2.polymarket.com/"
CHAIN_ID = 137

# Data API (público, sem auth)
DATA_API_URL = "https://data-api.polymarket.com"


# ─── ABIs mínimas ─────────────────────────────────────────────────────────────

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

# NegRiskAdapter tem mesma assinatura de redeemPositions que o CTF
NEG_RISK_ABI = CTF_ABI


# ─── Config dataclass ─────────────────────────────────────────────────────────

@dataclass
class ClaimV2Config:
    """Configuração para claim v2 (gasless + on-chain fallback)."""

    # Wallet
    private_key: str = ""
    wallet_address: str = ""  # Proxy wallet (funder)
    safe_address: str = ""    # Safe wallet (opcional, para diagnóstico)

    # Network
    rpc_url: str = ""
    rpc_urls: list = field(default_factory=get_polygon_rpc_list)
    chain_id: int = CHAIN_ID

    # Contracts
    ctf_address: str = CTF_ADDRESS
    neg_risk_adapter: str = NEG_RISK_ADAPTER
    usdc_address: str = USDC_ADDRESS

    # Relayer
    relayer_url: str = RELAYER_URL

    # Builder API keys (para gasless)
    builder_api_key: str = ""
    builder_secret: str = ""
    builder_passphrase: str = ""

    # On-chain fallback
    gas_limit: int = 300000

    def __post_init__(self):
        if not self.private_key:
            self.private_key = os.getenv("POLYMARKET_PRIVATE_KEY", "")
        if not self.wallet_address:
            self.wallet_address = os.getenv("POLYMARKET_FUNDER", "")
        if not self.rpc_url:
            urls = self.rpc_urls
            self.rpc_url = urls[0] if urls else "https://polygon-rpc.com"
        if not self.safe_address:
            self.safe_address = os.getenv("POLYMARKET_SAFE_ADDRESS", "")

        # Builder keys
        if not self.builder_api_key:
            self.builder_api_key = os.getenv("POLY_BUILDER_API_KEY", "")
        if not self.builder_secret:
            self.builder_secret = os.getenv("POLY_BUILDER_SECRET", "")
        if not self.builder_passphrase:
            self.builder_passphrase = os.getenv("POLY_BUILDER_PASSPHRASE", "")

    @property
    def has_builder_keys(self) -> bool:
        return bool(self.builder_api_key and self.builder_secret and self.builder_passphrase)

    def validate(self) -> list[str]:
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
