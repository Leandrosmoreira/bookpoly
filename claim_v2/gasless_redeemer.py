"""
Gasless Redeemer — Usa Polymarket Relayer para resgatar posições sem pagar gas.

Dependências oficiais:
  - py-builder-relayer-client (Polymarket)
  - py-builder-signing-sdk (Polymarket)
"""
import logging
import time
from dataclasses import dataclass
from typing import Optional

from eth_abi import encode as abi_encode
from web3 import Web3

from .config import ClaimV2Config, CTF_ADDRESS, NEG_RISK_ADAPTER, USDC_ADDRESS

log = logging.getLogger(__name__)

# Zero bytes32 (parentCollectionId é sempre zero no Polymarket)
ZERO_BYTES32 = b"\x00" * 32

# Function selector: redeemPositions(address,bytes32,bytes32,uint256[])
REDEEM_SELECTOR = bytes.fromhex("a8e0b2a0")  # keccak256 dos 4 primeiros bytes


@dataclass
class GaslessResult:
    success: bool
    tx_hash: str = ""
    tx_id: str = ""
    error: str = ""
    method: str = "gasless"


class GaslessRedeemer:
    """Resgata posições via Polymarket Relayer (0 gas).

    IMPORTANTE:
    - Mesmo que o relayer responda OK, validamos on-chain se o token foi queimado.
      Se ainda houver saldo do token, consideramos falha e deixamos o executor
      cair para o fallback on-chain.
    """

    def __init__(self, config: ClaimV2Config):
        self.config = config
        self._client = None
        self._safe_address: Optional[str] = None
        self._initialized = False
        # Web3 / contrato para checar saldo após o gasless
        self._w3: Optional[Web3] = None
        self._contract = None

    def _init_client(self):
        """Inicializa RelayClient com Builder API keys."""
        if self._initialized:
            return

        from py_builder_relayer_client.client import RelayClient
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

        self._client = RelayClient(
            self.config.relayer_url,
            self.config.chain_id,
            pk,
            builder_config,
        )

        self._initialized = True
        log.info("RelayClient inicializado (gasless)")

    def ensure_safe_deployed(self) -> bool:
        """Verifica se a Safe wallet está deployed. Se não, faz deploy."""
        self._init_client()

        try:
            safe_address = self._client.get_expected_safe()
            self._safe_address = safe_address
            log.info(f"Safe address: {safe_address}")

            deployed = self._client.get_deployed(safe_address)
            if deployed:
                log.info("Safe já deployed")
                return True

            log.info("Deploying Safe wallet...")
            response = self._client.deploy()
            result = response.wait()
            log.info(f"Safe deployed! TX: {result}")
            return True

        except Exception as e:
            log.error(f"Erro ao verificar/deploy Safe: {e}")
            return False

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

    # ─── Verificação on-chain após gasless ────────────────────────────────

    def _get_w3(self) -> Web3:
        """Conecta a um RPC da lista para checar saldo do token."""
        if self._w3 is not None:
            return self._w3
        # Import tardio para evitar ciclo
        from .config import CTF_ABI  # type: ignore

        for rpc_url in self.config.rpc_urls:
            try:
                request_kwargs = {"timeout": 10}
                try:
                    from polygon_rpc import get_request_kwargs_for_rpc  # type: ignore

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
        raise RuntimeError("Cannot connect to any Polygon RPC for gasless verify")

    def _has_balance(self, token_id: str) -> bool:
        """Verifica se ainda existe saldo desse token após o gasless."""
        try:
            w3 = self._get_w3()
            if not self._contract:
                return True
            balance = self._contract.functions.balanceOf(
                Web3.to_checksum_address(self.config.wallet_address),
                int(token_id),
            ).call()
            return balance > 0
        except Exception as e:
            # Em dúvida, não confiamos no gasless e deixamos fallback decidir.
            log.warning(f"  Erro ao verificar saldo após gasless: {e}")
            return True

    def redeem(self, position) -> GaslessResult:
        """Resgata uma posição via Relayer."""
        try:
            self._init_client()

            from py_builder_relayer_client.models import SafeTransaction, OperationType

            # Codificar chamada
            call_data = self._encode_redeem_data(
                position.condition_id,
                position.outcome_index,
            )

            # Determinar contrato alvo (CTF ou NegRiskAdapter)
            # Para mercados 15min padrão, usar CTF direto
            target = Web3.to_checksum_address(CTF_ADDRESS)

            tx = SafeTransaction(
                to=target,
                operation=OperationType.Call,
                data=call_data,
                value="0",
            )

            log.info(f"  Enviando via Relayer: {position.market_slug} ({position.outcome})...")

            response = self._client.execute([tx])
            tx_id = getattr(response, "transaction_id", "") or ""
            tx_hash = getattr(response, "transaction_hash", "") or ""

            log.info(f"  Relayer TX ID: {tx_id[:20]}...")

            # Aguardar confirmação
            result = response.wait()

            # Verificar se o relayer reportou estado ruim (quando disponível)
            state = ""
            try:
                state = getattr(result, "state", "") or (result.get("state") if isinstance(result, dict) else "")
            except Exception:
                state = ""
            if state and state not in ("STATE_CONFIRMED", "STATE_MINED"):
                raise RuntimeError(f"Gasless tx state={state}")

            # Verificação extra: saldo on-chain do token deve ir a zero
            if self._has_balance(position.token_id):
                raise RuntimeError("Gasless não queimou o token (saldo on-chain ainda > 0)")

            log.info("  Gasless redeem OK!")

            return GaslessResult(
                success=True,
                tx_hash=str(tx_hash),
                tx_id=str(tx_id),
            )

        except Exception as e:
            error_msg = str(e)
            log.warning(f"  Gasless redeem falhou: {error_msg}")
            return GaslessResult(
                success=False,
                error=error_msg,
            )
