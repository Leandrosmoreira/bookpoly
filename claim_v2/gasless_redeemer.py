"""
Gasless Redeemer — Usa Polymarket Relayer (modo PROXY) para resgatar posições sem gas.

O SDK Python oficial (py-builder-relayer-client) só suporta SAFE mode.
Este módulo usa o ProxyRelayClient (wrapper local) que implementa PROXY mode
idêntico ao SDK TypeScript (@polymarket/builder-relayer-client).

Dependências oficiais:
  - py-builder-relayer-client (Polymarket) — infra HTTP/auth
  - py-builder-signing-sdk (Polymarket) — HMAC headers

Dependência local:
  - claim_v2.proxy_client — ProxyRelayClient (PROXY mode wrapper)
"""
import logging
import time
from dataclasses import dataclass
from typing import Optional

from eth_abi import encode as abi_encode
from web3 import Web3

from .config import ClaimV2Config, CTF_ADDRESS, NEG_RISK_ADAPTER, USDC_ADDRESS
from .debug.relayer_raw_logger import RelayerRawLogger
from .proxy_client import ProxyRelayClient, ProxyTransaction, CallType

log = logging.getLogger(__name__)

# Zero bytes32 (parentCollectionId é sempre zero no Polymarket)
ZERO_BYTES32 = b"\x00" * 32

# Function selector: redeemPositions(address,bytes32,bytes32,uint256[])
REDEEM_SELECTOR = bytes.fromhex("a8e0b2a0")


@dataclass
class GaslessResult:
    success: bool
    tx_hash: str = ""
    tx_id: str = ""
    error: str = ""
    method: str = "gasless_proxy"


class GaslessRedeemer:
    """Resgata posições via Polymarket Relayer usando modo PROXY (0 gas).

    Fluxo:
    1. Encode redeemPositions() como calldata
    2. Wrap em ProxyTransaction (type=Call, to=CTF)
    3. Enviar via ProxyRelayClient (PROXY mode no relayer)
    4. Verificar on-chain se o token foi queimado

    Se o token ainda existir após o gasless, considera falha
    e o executor cai para fallback on-chain.
    """

    def __init__(self, config: ClaimV2Config, raw_logger: Optional[RelayerRawLogger] = None):
        self.config = config
        self._client: Optional[ProxyRelayClient] = None
        self._initialized = False
        self.raw = raw_logger
        # Web3 para verificação on-chain
        self._w3: Optional[Web3] = None
        self._contract = None

    def _init_client(self):
        """Inicializa ProxyRelayClient com Builder API keys."""
        if self._initialized:
            return

        from py_builder_signing_sdk.config import BuilderConfig
        from py_builder_signing_sdk.sdk_types import BuilderApiKeyCreds

        creds = BuilderApiKeyCreds(
            key=self.config.builder_api_key,
            secret=self.config.builder_secret,
            passphrase=self.config.builder_passphrase,
        )
        builder_config = BuilderConfig(local_builder_creds=creds)

        pk = self.config.private_key
        if not pk.startswith("0x"):
            pk = "0x" + pk

        self._client = ProxyRelayClient(
            relayer_url=self.config.relayer_url,
            chain_id=self.config.chain_id,
            private_key=pk,
            builder_config=builder_config,
            proxy_wallet_override=self.config.funder_address,
        )

        self._initialized = True
        log.info(f"ProxyRelayClient inicializado (gasless PROXY mode)")
        log.info(f"  Proxy wallet: {self._client.proxy_wallet}")

    def _encode_redeem_data(self, condition_id: str, outcome_index: int) -> str:
        """Codifica chamada redeemPositions() como hex data."""
        if condition_id.startswith("0x"):
            condition_bytes = bytes.fromhex(condition_id[2:])
        else:
            condition_bytes = bytes.fromhex(condition_id)

        # indexSets: [1] para outcome 0, [2] para outcome 1
        index_sets = [1 << outcome_index]

        # ABI encode os parâmetros
        params = abi_encode(
            ["address", "bytes32", "bytes32", "uint256[]"],
            [
                Web3.to_checksum_address(USDC_ADDRESS),
                ZERO_BYTES32,
                condition_bytes,
                index_sets,
            ]
        )

        return "0x" + (REDEEM_SELECTOR + params).hex()

    # ─── Verificação on-chain após gasless ──────────────────────────────

    def _get_w3(self) -> Web3:
        """Conecta a um RPC para checar saldo do token."""
        if self._w3 is not None:
            return self._w3
        from .config import CTF_ABI

        for rpc_url in self.config.rpc_urls:
            try:
                request_kwargs = {"timeout": 10}
                try:
                    from polygon_rpc import get_request_kwargs_for_rpc
                    request_kwargs.update(get_request_kwargs_for_rpc(rpc_url, timeout=10))
                except ImportError:
                    pass
                w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs=request_kwargs))
                if w3.is_connected():
                    self._w3 = w3
                    self._contract = w3.eth.contract(
                        address=Web3.to_checksum_address(self.config.ctf_address),
                        abi=CTF_ABI,
                    )
                    return w3
            except Exception:
                continue
        raise RuntimeError("Cannot connect to any Polygon RPC")

    def _has_balance(self, token_id: str, holder: str = None) -> bool:
        """Verifica se ainda existe saldo desse token."""
        try:
            w3 = self._get_w3()
            if not self._contract:
                return True
            check_addr = holder or self.config.wallet_address
            balance = self._contract.functions.balanceOf(
                Web3.to_checksum_address(check_addr),
                int(token_id),
            ).call()
            return balance > 0
        except Exception as e:
            log.warning(f"  Erro ao verificar saldo: {e}")
            return True  # Em dúvida, não confiamos no gasless

    def redeem(self, position) -> GaslessResult:
        """Resgata uma posição via Relayer (modo PROXY)."""
        try:
            self._init_client()

            # 1. Encode redeemPositions()
            call_data = self._encode_redeem_data(
                position.condition_id,
                position.outcome_index,
            )

            # 2. Contrato alvo — CTF para mercados padrão
            target = Web3.to_checksum_address(CTF_ADDRESS)

            # 3. Criar ProxyTransaction
            proxy_tx = ProxyTransaction(
                to=target,
                type_code=CallType.Call,
                data=call_data,
                value="0",
            )

            log.info(f"  Enviando via Relayer PROXY: {position.market_slug} ({position.outcome})...")

            # 4. Executar via ProxyRelayClient
            resp = self._client.execute([proxy_tx])

            tx_id = resp.get("transaction_id", "")
            tx_hash = resp.get("transaction_hash", "")
            state = resp.get("state", "")
            success = resp.get("success", False)

            if self.raw:
                self.raw.log("proxy_execute_response", {
                    "tx_id": tx_id,
                    "tx_hash": tx_hash,
                    "state": state,
                    "success": success,
                    "position": {
                        "market_slug": position.market_slug,
                        "condition_id": position.condition_id,
                        "outcome_index": position.outcome_index,
                    },
                })

            log.info(f"  Relayer response: state={state}, success={success}")

            if not success:
                raise RuntimeError(f"Proxy tx state={state}, success=False")

            # 5. Verificação on-chain: token deve ter sido queimado
            # Checar na proxy wallet (onde os tokens estão)
            proxy_addr = self._client.proxy_wallet
            time.sleep(3)  # Aguardar indexação
            if self._has_balance(position.token_id, holder=proxy_addr):
                raise RuntimeError("Proxy gasless não queimou o token (saldo on-chain > 0)")

            log.info("  Gasless PROXY redeem OK!")
            return GaslessResult(
                success=True,
                tx_hash=str(tx_hash),
                tx_id=str(tx_id),
            )

        except Exception as e:
            error_msg = str(e)
            log.warning(f"  Gasless PROXY redeem falhou: {error_msg}")
            return GaslessResult(
                success=False,
                error=error_msg,
            )
