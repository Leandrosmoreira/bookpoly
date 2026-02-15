"""
Lista de RPCs gratuitos Polygon e fallback em caso de rate limit.

Polymarket NÃO fornece RPC próprio. Docs oficiais:
  https://docs.polymarket.com — indicam usar RPCs externos da Polygon.
  https://chainlist.org/chain/137 — lista de RPCs Polygon com latência.

Infura (MetaMask Developer): use API Key como Project ID. Se "Require API Key Secret"
  estiver ativo, defina também INFURA_API_SECRET no .env.

Uso:
  - INFURA_PROJECT_ID ou INFURA_API_KEY + opcional INFURA_API_SECRET
  - POLYGON_RPC_URLS no .env (opcional): URLs separadas por vírgula.
  - get_polygon_rpc_list() / get_web3_with_fallback() / get_request_kwargs_for_rpc(url)
"""
import base64
import os
from typing import Any, Dict, List, Optional

# RPCs públicos Polygon Mainnet (Chain ID 137) — ordem por disponibilidade/Chainlist
# Polymarket usa Polygon; não há RPC oficial Polymarket, só estes ou seu próprio nó.
POLYGON_RPC_URLS_DEFAULT: List[str] = [
    "https://polygon-rpc.com",           # oficial Polygon
    "https://polygon.drpc.org",         # dRPC, costuma ser rápido
    "https://rpc.ankr.com/polygon",      # Ankr
    "https://polygon-bor-rpc.publicnode.com",
    "https://1rpc.io/matic",
    "https://polygon-mainnet.public.blastapi.io",
    "https://matic-mainnet.chainstacklabs.com",  # Chainstack (Chainlist)
]


def get_polygon_rpc_list() -> List[str]:
    """Lista de RPCs: INFURA_PROJECT_ID, ou POLYGON_RPC_URLS (vírgula), ou padrão."""
    urls = []

    # Infura (MetaMask Developer): API Key = Project ID; opcional API Key Secret
    infura_id = os.getenv("INFURA_PROJECT_ID", "").strip() or os.getenv("INFURA_API_KEY", "").strip()
    if infura_id:
        urls.append(f"https://polygon-mainnet.infura.io/v3/{infura_id}")

    # Lista customizada (vírgula) ou padrão
    raw = os.getenv("POLYGON_RPC_URLS", "").strip()
    if raw:
        urls.extend([u.strip() for u in raw.split(",") if u.strip()])
    elif not urls:
        urls = list(POLYGON_RPC_URLS_DEFAULT)

    return urls


def get_request_kwargs_for_rpc(url: str, timeout: int = 10) -> Dict[str, Any]:
    """Para uso com HTTPProvider: timeout + Basic auth se for Infura com secret."""
    kwargs: Dict[str, Any] = {"timeout": timeout}
    if "infura.io" in url:
        secret = os.getenv("INFURA_API_SECRET", "").strip()
        if secret:
            key = os.getenv("INFURA_PROJECT_ID", "").strip() or os.getenv("INFURA_API_KEY", "").strip()
            if key:
                credentials = base64.b64encode(f"{key}:{secret}".encode()).decode()
                kwargs["headers"] = {"Authorization": f"Basic {credentials}"}
    return kwargs


def get_web3_with_fallback(timeout: int = 5):
    """Tenta conectar em cada RPC da lista; retorna o primeiro Web3 que conectar."""
    try:
        from web3 import Web3
    except ImportError:
        return None

    for url in get_polygon_rpc_list():
        try:
            req = get_request_kwargs_for_rpc(url, timeout=timeout)
            w3 = Web3(Web3.HTTPProvider(url, request_kwargs=req))
            if w3.is_connected():
                return w3
        except Exception:
            continue
    return None


def get_web3_next_rpc(current_url: Optional[str], exclude: Optional[List[str]] = None) -> Optional[str]:
    """Retorna o próximo RPC da lista (para trocar após rate limit). exclude = URLs que já falharam."""
    urls = get_polygon_rpc_list()
    skip = set(exclude or [])
    if current_url:
        skip.add(current_url.rstrip("/"))
    for u in urls:
        key = u.rstrip("/")
        if key not in skip:
            return u
    return None
