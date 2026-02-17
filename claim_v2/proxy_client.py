"""
ProxyRelayClient -- Wrapper que adiciona suporte PROXY ao py-builder-relayer-client.

O SDK Python oficial (v0.0.1) so suporta SAFE mode. Este modulo implementa o
flow PROXY identico ao SDK TypeScript (@polymarket/builder-relayer-client),
reutilizando a infra HTTP, auth e polling do SDK Python.

Flow PROXY (corrigido conforme referencia qualiaenjoyer/polymarket-apis):
  1. GET /nonce?address={proxy_wallet}&type=PROXY  -> nonce (int)
  2. Encode proxy call data (ABI encode ProxyWalletFactory.proxy(calls))
  3. Build struct hash: keccak256("rlx:" + from + to + data + fee + gasPrice + gasLimit + nonce + relayHub + relay)
  4. Sign struct hash (personal_sign via encode_defunct)
  5. POST /submit com type="PROXY"
  6. Poll ate STATE_CONFIRMED/STATE_MINED

Contratos Polygon Mainnet:
  ProxyFactory: 0xaB45c5A4B0c941a2F231C04C3f49182e1A254052
  RelayHub:     0xD216153c06E857cD7f72665E0aF1d7D82172F494
  CTFExchange:  0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E

Referencia: https://github.com/Polymarket/py-clob-client/issues/117
            https://github.com/qualiaenjoyer/polymarket-apis (web3_client.py)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

from eth_abi import encode as abi_encode
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import keccak, to_checksum_address, to_bytes
from web3 import Web3

# Reutilizar infra do SDK existente
from py_builder_relayer_client.client import RelayClient
from py_builder_relayer_client.http_helpers.helpers import get as http_get, post as http_post
from py_builder_relayer_client.response import ClientRelayerTransactionResponse
from py_builder_relayer_client.exceptions import RelayerClientException
from py_builder_relayer_client.utils.utils import prepend_zx

log = logging.getLogger("claim_v2.proxy_client")

# -----------------------------------------------
#  Constantes Polygon Mainnet (Chain ID 137)
# -----------------------------------------------

PROXY_FACTORY = "0xaB45c5A4B0c941a2F231C04C3f49182e1A254052"
RELAY_HUB     = "0xD216153c06E857cD7f72665E0aF1d7D82172F494"

# FIX BUG 3: Relay address HARDCODED (identico a referencia)
# Este e o endereco do relay contract, NAO do relay worker (que muda).
# Ref: qualiaenjoyer/polymarket-apis web3_client.py
RELAY_ADDRESS = "0x7db63fe6d62eb73fb01f8009416f4c2bb4fbda6a"

# CTFExchange -- usado para getPolyProxyWalletAddress() on-chain (FIX BUG 1)
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"

# ABI minima para buscar proxy wallet on-chain
EXCHANGE_ABI_PROXY = [
    {
        "type": "function",
        "name": "getPolyProxyWalletAddress",
        "inputs": [{"name": "_addr", "type": "address"}],
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view"
    }
]

# Default gas params
DEFAULT_GAS_PRICE = "0"
DEFAULT_RELAYER_FEE = "0"

# -- Gas Limit Calculation (fallback estatico) --
# Ref: https://github.com/Polymarket/magic-proxy-builder-example
BASE_GAS_PER_TX     = 150_000
RELAY_HUB_PADDING   = 3_450_000
OVERHEAD_BUFFER     = 450_000
INTRINSIC_COST      = 30_000
MIN_EXECUTION_BUFFER = 500_000


def calculate_gas_limit(transaction_count: int) -> str:
    """
    Calcula gasLimit para o struct hash PROXY (fallback estatico).

    Formula oficial da Polymarket (magic-proxy-builder-example):
      txGas = count * 150K
      relayerWillSend = txGas + 3.45M
      maxSignable = relayerWillSend - 30K - 450K
      executionNeeds = txGas + 500K
      gasLimit = min(maxSignable, max(executionNeeds, 3M))

    Para 1 tx: min(3_120_000, max(650_000, 3_000_000)) = 3_000_000
    """
    tx_gas = transaction_count * BASE_GAS_PER_TX
    relayer_will_send = tx_gas + RELAY_HUB_PADDING
    max_signable = relayer_will_send - INTRINSIC_COST - OVERHEAD_BUFFER
    execution_needs = tx_gas + MIN_EXECUTION_BUFFER
    return str(min(max_signable, max(execution_needs, 3_000_000)))


# ABI minima do ProxyWalletFactory.proxy(tuple[])
# Cada tuple e (address to, uint8 typeCode, bytes data, uint256 value)
PROXY_FUNCTION_SELECTOR = Web3.keccak(text="proxy((address,uint8,bytes,uint256)[])")[0:4]


# -----------------------------------------------
#  Models
# -----------------------------------------------

class CallType(Enum):
    Invalid = 0
    Call = 1
    DelegateCall = 2


@dataclass
class ProxyTransaction:
    """Transacao individual para executar via ProxyWalletFactory."""
    to: str
    type_code: CallType
    data: str   # hex com ou sem 0x
    value: str


# -----------------------------------------------
#  Encoding -- ProxyWalletFactory.proxy(calls)
# -----------------------------------------------

def encode_proxy_transaction_data(txns: List[ProxyTransaction]) -> str:
    """
    ABI-encode uma chamada a ProxyWalletFactory.proxy(tuple[]).

    Cada tuple: (address to, uint8 typeCode, bytes data, uint256 value)

    Retorna: hex string com 0x prefix (selector + encoded args).
    """
    calls = []
    for tx in txns:
        to_addr = to_checksum_address(tx.to)
        data_bytes = bytes.fromhex(tx.data.replace("0x", ""))
        calls.append((
            to_addr,
            tx.type_code.value,
            data_bytes,
            int(tx.value),
        ))

    # ABI encode: proxy((address,uint8,bytes,uint256)[])
    encoded_args = abi_encode(
        ["(address,uint8,bytes,uint256)[]"],
        [calls],
    )

    return prepend_zx(PROXY_FUNCTION_SELECTOR.hex() + encoded_args.hex())


# -----------------------------------------------
#  Struct Hash -- "rlx:" prefix signing
# -----------------------------------------------

def _pad_32(value: int) -> bytes:
    """Converte int para bytes32 (big-endian, 32 bytes)."""
    return value.to_bytes(32, byteorder="big")


def create_proxy_struct_hash(
    from_addr: str,
    to_addr: str,
    data: str,
    tx_fee: str,
    gas_price: str,
    gas_limit: str,
    nonce: str,
    relay_hub: str,
    relay_address: str,
) -> bytes:
    """
    Cria o struct hash para assinatura PROXY.

    hash = keccak256(concat(
        "rlx:",
        from,        // signer address hex (sem 0x, como bytes)
        to,          // proxy factory hex
        data,        // encoded proxy call data hex
        pad32(fee),
        pad32(gasPrice),
        pad32(gasLimit),
        pad32(nonce),
        relayHub,    // hex
        relay        // hex (HARDCODED relay address)
    ))

    Identico a referencia: qualiaenjoyer/polymarket-apis helpers.py
    """
    parts = []

    # "rlx:" prefix (como bytes ASCII)
    parts.append(b"rlx:")

    # Enderecos como raw bytes (20 bytes cada)
    parts.append(bytes.fromhex(from_addr.replace("0x", "")))
    parts.append(bytes.fromhex(to_addr.replace("0x", "")))

    # Data como raw bytes
    parts.append(bytes.fromhex(data.replace("0x", "")))

    # Integers como bytes32
    parts.append(_pad_32(int(tx_fee)))
    parts.append(_pad_32(int(gas_price)))
    parts.append(_pad_32(int(gas_limit)))
    parts.append(_pad_32(int(nonce)))

    # Mais enderecos
    parts.append(bytes.fromhex(relay_hub.replace("0x", "")))
    parts.append(bytes.fromhex(relay_address.replace("0x", "")))

    concatenated = b"".join(parts)
    return keccak(concatenated)


# -----------------------------------------------
#  Assinatura
# -----------------------------------------------

def sign_proxy_struct_hash(private_key: str, struct_hash: bytes) -> str:
    """
    Assina o struct hash PROXY via personal_sign (EIP-191).

    No TypeScript: signer.signMessage(structHash)
    Em Python: Account.sign_message(encode_defunct(hash), pk)
    """
    msg = encode_defunct(struct_hash)
    sig = Account.sign_message(msg, private_key)
    return prepend_zx(sig.signature.hex())


def split_and_pack_proxy_sig(sig_hex: str) -> str:
    """
    Divide a assinatura em (r, s, v) e re-empacota.
    Ajuste de v: mesmo padrao do SDK (v in 0,1 -> +27).
    """
    sig_bytes = bytes.fromhex(sig_hex.replace("0x", ""))
    if len(sig_bytes) != 65:
        raise ValueError(f"Assinatura invalida: {len(sig_bytes)} bytes (esperado 65)")

    r = int.from_bytes(sig_bytes[0:32], "big")
    s = int.from_bytes(sig_bytes[32:64], "big")
    v = sig_bytes[64]

    # Normalizar v
    if v in (0, 1):
        v += 27

    packed = abi_encode(["uint256", "uint256", "uint8"], [r, s, v])
    return prepend_zx(packed.hex())


# -----------------------------------------------
#  ProxyRelayClient -- Classe principal
# -----------------------------------------------

class ProxyRelayClient:
    """
    Client que executa transacoes via Polymarket Relayer no modo PROXY.

    Usa a mesma infra de auth (Builder API keys) do RelayClient oficial,
    mas implementa o flow PROXY que o SDK Python nao expoe.

    Corrigido conforme referencia qualiaenjoyer/polymarket-apis:
    - Proxy address via on-chain getPolyProxyWalletAddress() ou override
    - Nonce via GET /nonce?address=...&type=PROXY
    - Relay address HARDCODED (nao dinamico)
    - Gas limit via estimate_gas() * 1.3 + 100000 (com fallback estatico)
    """

    def __init__(
        self,
        relayer_url: str,
        chain_id: int,
        private_key: str,
        builder_config=None,
        proxy_wallet_override: str = None,
        rpc_url: str = "",
    ):
        self.relayer_url = relayer_url.rstrip("/")
        self.chain_id = chain_id
        self.private_key = private_key
        self.builder_config = builder_config
        self.rpc_url = rpc_url

        # Derivar enderecos
        self.account = Account.from_key(private_key)
        self.signer_address = self.account.address

        # Web3 para chamadas on-chain (estimate_gas, getPolyProxyWalletAddress)
        self.w3: Optional[Web3] = None
        self.exchange_contract = None
        if rpc_url:
            try:
                request_kwargs = {"timeout": 15}
                try:
                    from polygon_rpc import get_request_kwargs_for_rpc
                    request_kwargs.update(get_request_kwargs_for_rpc(rpc_url, timeout=15))
                except ImportError:
                    pass
                self.w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs=request_kwargs))
                if self.w3.is_connected():
                    self.exchange_contract = self.w3.eth.contract(
                        address=Web3.to_checksum_address(CTF_EXCHANGE),
                        abi=EXCHANGE_ABI_PROXY,
                    )
                    log.info(f"  Web3 conectado: {rpc_url[:40]}...")
                else:
                    log.warning(f"  Web3 nao conectou: {rpc_url[:40]}...")
                    self.w3 = None
            except Exception as e:
                log.warning(f"  Web3 init falhou: {e}")
                self.w3 = None

        # FIX BUG 1: Proxy wallet -- usar override (Funder) ou buscar on-chain
        if proxy_wallet_override:
            self.proxy_wallet = to_checksum_address(proxy_wallet_override)
            log.info(f"  Proxy Wallet:  {self.proxy_wallet} (override / Funder)")
        elif self.exchange_contract:
            try:
                self.proxy_wallet = self.get_poly_proxy_address()
                log.info(f"  Proxy Wallet:  {self.proxy_wallet} (on-chain via Exchange)")
            except Exception as e:
                log.warning(f"  getPolyProxyWalletAddress falhou: {e}")
                log.warning(f"  AVISO: Sem proxy_wallet_override e sem Web3 -- relay vai falhar!")
                self.proxy_wallet = ""
        else:
            log.warning(f"  AVISO: Sem proxy_wallet_override e sem Web3 -- relay vai falhar!")
            self.proxy_wallet = ""

        log.info(f"ProxyRelayClient inicializado")
        log.info(f"  Signer:        {self.signer_address}")
        log.info(f"  ProxyFactory:  {PROXY_FACTORY}")
        log.info(f"  RelayHub:      {RELAY_HUB}")
        log.info(f"  RelayAddress:  {RELAY_ADDRESS} (hardcoded)")

    # -- FIX BUG 1: Buscar proxy wallet on-chain --

    def get_poly_proxy_address(self) -> str:
        """
        Busca proxy wallet via chamada on-chain ao CTFExchange.

        exchange.functions.getPolyProxyWalletAddress(eoa).call()

        Ref: qualiaenjoyer/polymarket-apis web3_client.py
        """
        if not self.exchange_contract:
            raise RuntimeError("Web3/Exchange contract nao inicializado")
        result = self.exchange_contract.functions.getPolyProxyWalletAddress(
            Web3.to_checksum_address(self.signer_address)
        ).call()
        return Web3.to_checksum_address(result)

    # -- FIX BUG 2: Nonce via /nonce endpoint --

    def get_relay_nonce(self) -> int:
        """
        GET /nonce?address={proxy_wallet}&type=PROXY

        Retorna: nonce (int)

        Ref: qualiaenjoyer/polymarket-apis web3_client.py _get_relay_nonce()
        Antigo: get_relay_payload() usava /relay-payload (ERRADO -- retornava relay worker address)
        """
        import requests as req
        url = f"{self.relayer_url}/nonce?address={self.proxy_wallet}&type=PROXY"
        headers = self._generate_headers("GET", "/nonce") or {}
        resp = req.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        # Resposta e texto puro com o nonce
        nonce_text = resp.text.strip().strip('"')
        return int(nonce_text)

    # -- FIX BUG 4: Estimativa de gas on-chain --

    def _estimate_gas(self, encoded_data: str) -> int:
        """
        Estima gas on-chain como na referencia.

        estimated_gas = w3.eth.estimate_gas({from: proxy, to: factory, data: ...})
        gas_limit = estimated_gas * 1.3 + 100000

        Ref: qualiaenjoyer/polymarket-apis web3_client.py _build_proxy_relay_transaction()
        Fallback: calculate_gas_limit() estatico se Web3 nao disponivel.
        """
        if not self.w3:
            fallback = int(calculate_gas_limit(1))
            log.info(f"  Gas estimate: usando fallback estatico {fallback} (sem Web3)")
            return fallback

        try:
            estimated = self.w3.eth.estimate_gas({
                "from": self.proxy_wallet,
                "to": Web3.to_checksum_address(PROXY_FACTORY),
                "data": encoded_data,
            })
            gas_limit = int(estimated * 1.3 + 100_000)
            log.info(f"  Gas estimate: {estimated} on-chain -> {gas_limit} (x1.3 + 100K)")
            return gas_limit
        except Exception as e:
            fallback = int(calculate_gas_limit(1))
            log.warning(f"  Gas estimate falhou ({e}), usando fallback {fallback}")
            return fallback

    # -- API: submit --

    def _submit(self, payload: dict) -> dict:
        """POST /submit com auth headers."""
        import requests as req
        import json
        url = f"{self.relayer_url}/submit"
        headers = self._generate_headers("POST", "/submit", body=payload) or {}
        headers["Content-Type"] = "application/json"
        resp = req.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()

    # -- API: poll transaction --

    def poll_until_done(
        self,
        transaction_id: str,
        max_polls: int = 30,
        poll_freq_s: float = 2.0,
    ) -> Optional[dict]:
        """Poll GET /transaction?id={id} ate terminal state."""
        import requests as req
        for i in range(max_polls):
            headers = self._generate_headers("GET", "/transaction") or {}
            try:
                url = f"{self.relayer_url}/transaction?id={transaction_id}"
                resp = req.get(url, headers=headers, timeout=15)
                resp.raise_for_status()
                data = resp.json()
                txns = data if isinstance(data, list) else [data]
                for tx in txns:
                    state = tx.get("state", "")
                    if state in ("STATE_CONFIRMED", "STATE_MINED"):
                        return tx
                    if state in ("STATE_FAILED", "STATE_INVALID"):
                        log.warning(f"  Proxy tx {transaction_id}: {state}")
                        return tx
            except Exception as e:
                log.warning(f"  Poll {i+1}/{max_polls} erro: {e}")
            time.sleep(poll_freq_s)
        log.warning(f"  Proxy tx {transaction_id}: timeout apos {max_polls} polls")
        return None

    # -- Executar transacoes PROXY --

    def execute(
        self,
        transactions: List[ProxyTransaction],
        metadata: str = "",
    ) -> dict:
        """
        Executa transacoes via Relayer no modo PROXY.

        Corrigido conforme referencia (4 bugs):
        - Nonce via /nonce endpoint (nao /relay-payload)
        - Relay address HARDCODED (nao dinamico)
        - Gas limit via estimate_gas on-chain
        - Proxy wallet via override ou on-chain

        Args:
            transactions: lista de ProxyTransaction (to, typeCode, data, value)
            metadata: string opcional

        Returns:
            {"transaction_id": "...", "transaction_hash": "...", "state": "..."}
        """
        if not self.proxy_wallet:
            raise RuntimeError("proxy_wallet nao definido -- configure proxy_wallet_override ou rpc_url")

        # 1. FIX BUG 2: Get nonce via /nonce endpoint (nao /relay-payload)
        nonce = self.get_relay_nonce()
        log.info(f"  Nonce: {nonce} (via /nonce endpoint)")

        # 2. FIX BUG 3: Relay address HARDCODED (nao dinamico)
        relay_address = RELAY_ADDRESS
        log.info(f"  Relay address: {relay_address} (hardcoded)")

        # 3. Encode proxy call data
        encoded_data = encode_proxy_transaction_data(transactions)
        log.info(f"  Encoded data: {len(encoded_data)} chars")

        # 4. FIX BUG 4: Gas limit via estimate_gas on-chain (com fallback)
        gas_limit = str(self._estimate_gas(encoded_data))
        log.info(f"  Gas limit: {gas_limit}")

        # 5. Create struct hash
        struct_hash = create_proxy_struct_hash(
            from_addr=self.signer_address,
            to_addr=PROXY_FACTORY,
            data=encoded_data,
            tx_fee=DEFAULT_RELAYER_FEE,
            gas_price=DEFAULT_GAS_PRICE,
            gas_limit=gas_limit,
            nonce=str(nonce),
            relay_hub=RELAY_HUB,
            relay_address=relay_address,
        )

        # 6. Sign
        signature = sign_proxy_struct_hash(self.private_key, struct_hash)
        log.info(f"  Assinatura: {signature[:20]}...")

        # 7. Build request payload
        payload = {
            "type": "PROXY",
            "from": self.signer_address,
            "to": PROXY_FACTORY,
            "proxyWallet": self.proxy_wallet,
            "data": encoded_data,
            "nonce": str(nonce),
            "signature": signature,
            "signatureParams": {
                "gasPrice": DEFAULT_GAS_PRICE,
                "gasLimit": gas_limit,
                "relayerFee": DEFAULT_RELAYER_FEE,
                "relayHub": RELAY_HUB,
                "relay": relay_address,
            },
            "metadata": metadata,
        }

        # 8. Submit
        log.info(f"  Submetendo tx PROXY ao relayer...")
        resp = self._submit(payload)
        tx_id = resp.get("transactionID", "") or resp.get("transaction_id", "")
        tx_hash = resp.get("transactionHash", "") or resp.get("transaction_hash", "")

        log.info(f"  Resposta: tx_id={tx_id}, tx_hash={tx_hash[:20] if tx_hash else '(vazio)'}...")

        # 9. Poll ate concluir
        if tx_id:
            result = self.poll_until_done(tx_id)
            if result:
                state = result.get("state", "UNKNOWN")
                final_hash = result.get("transactionHash", tx_hash)
                return {
                    "transaction_id": tx_id,
                    "transaction_hash": final_hash,
                    "state": state,
                    "success": state in ("STATE_CONFIRMED", "STATE_MINED"),
                }

        return {
            "transaction_id": tx_id,
            "transaction_hash": tx_hash,
            "state": "UNKNOWN",
            "success": False,
        }

    # -- Auth headers (reutiliza builder_config do SDK) --

    def _generate_headers(
        self, method: str, request_path: str, body: dict = None
    ) -> Optional[dict]:
        """Gera headers de autenticacao Builder (HMAC)."""
        if not self.builder_config:
            return None
        result = self.builder_config.generate_builder_headers(
            method, request_path, body
        )
        # SDK retorna BuilderHeaderPayload object, nao dict
        if hasattr(result, "to_dict"):
            return result.to_dict()
        if hasattr(result, "__dict__"):
            return result.__dict__
        return result
