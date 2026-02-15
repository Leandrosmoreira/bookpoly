#!/usr/bin/env python3
"""
Bot 24/7 para mercados 15min do Polymarket (BTC, ETH, SOL, XRP).

Estratégia:
- Detecta ciclos de 15min automaticamente
- Entra quando YES ou NO estiver entre 95%-98%
- Janela de entrada: 5min a 1min antes da expiração
- Timeout de fill: 10s
- Máximo 1 trade por ciclo por mercado

USO:
    python scripts/bot_15min.py
"""

import json
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import OrderArgs, ApiCreds
    from py_clob_client.order_builder.constants import BUY
except ImportError:
    print("ERRO: pip install py-clob-client")
    sys.exit(1)

# ==============================================================================
# CONSTANTES
# ==============================================================================

CLOB_HOST = os.getenv("CLOB_BASE_URL", "https://clob.polymarket.com")
GAMMA_HOST = os.getenv("GAMMA_BASE_URL", "https://gamma-api.polymarket.com")
CHAIN_ID = 137

# Configurações do bot
POLL_SECONDS = 1           # Intervalo do loop principal
ENTRY_WINDOW_START = 300   # Segundos antes da expiração (5min)
ENTRY_WINDOW_END = 60      # Hard stop (1min)
FILL_TIMEOUT = 10          # Segundos para aguardar fill
MIN_SHARES = 5             # Quantidade por ordem
MIN_PRICE = 0.95           # Preço mínimo para entrada
MAX_PRICE = 0.98           # Preço máximo para entrada
MIN_BALANCE_USDC = 5.5     # Saldo mínimo (USDC) para enviar ordem
ORDER_FAIL_RETRY_DELAY = 2 # Segundos antes de reenviar após falha
ORDER_FAIL_MAX_RETRIES = 2 # Tentativas de place_order antes de desistir

# Mercados
ASSETS = ['btc', 'eth', 'sol', 'xrp']

# Diretório de logs
LOGS_DIR = Path(__file__).parent.parent / "logs"


# ==============================================================================
# ESTADOS E CONTEXTO
# ==============================================================================

class MarketState(Enum):
    IDLE = "IDLE"
    ORDER_PLACED = "ORDER_PLACED"
    HOLDING = "HOLDING"
    DONE = "DONE"
    SKIPPED = "SKIPPED"


@dataclass
class MarketContext:
    asset: str
    cycle_end_ts: Optional[int] = None
    state: MarketState = MarketState.IDLE
    trade_attempts: int = 0
    order_id: Optional[str] = None
    entered_side: Optional[str] = None
    entered_price: Optional[float] = None
    entered_size: Optional[float] = None
    entered_ts: Optional[int] = None
    yes_token_id: Optional[str] = None
    no_token_id: Optional[str] = None


# ==============================================================================
# CLIENTE GLOBAL
# ==============================================================================

_client: Optional[ClobClient] = None
_http: Optional[httpx.Client] = None
_running = True


def get_client() -> ClobClient:
    """Retorna cliente CLOB configurado."""
    global _client
    if _client is None:
        pk = os.getenv("POLYMARKET_PRIVATE_KEY", "")
        funder = os.getenv("POLYMARKET_FUNDER", "")

        if not pk or not funder:
            raise RuntimeError("Configure POLYMARKET_PRIVATE_KEY e POLYMARKET_FUNDER")

        if not pk.startswith("0x"):
            pk = f"0x{pk}"

        _client = ClobClient(
            CLOB_HOST,
            chain_id=CHAIN_ID,
            key=pk,
            signature_type=1,
            funder=funder,
        )

        api_key = os.getenv("POLYMARKET_API_KEY", "")
        api_secret = os.getenv("POLYMARKET_API_SECRET", "")
        api_pass = os.getenv("POLYMARKET_PASSPHRASE", "")

        if api_key and api_secret and api_pass:
            _client.set_api_creds(ApiCreds(api_key, api_secret, api_pass))
        else:
            _client.set_api_creds(_client.create_or_derive_api_creds())

    return _client


def get_http() -> httpx.Client:
    """Retorna cliente HTTP reutilizável."""
    global _http
    if _http is None:
        _http = httpx.Client(timeout=30)
    return _http


# ==============================================================================
# LOGGING
# ==============================================================================

def get_log_file() -> Path:
    """Retorna path do arquivo de log do dia."""
    LOGS_DIR.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    return LOGS_DIR / f"bot_15min_{today}.jsonl"


def log_event(action: str, asset: str, ctx: MarketContext, **extra):
    """Grava evento no log JSONL."""
    now = int(time.time())
    event = {
        "ts": now,
        "ts_iso": datetime.now().isoformat(),
        "market": asset,
        "cycle_end_ts": ctx.cycle_end_ts,
        "state": ctx.state.value,
        "action": action,
        "order_id": ctx.order_id,
        **extra,
    }

    log_file = get_log_file()
    with open(log_file, "a") as f:
        f.write(json.dumps(event) + "\n")

    # Também exibe no console
    time_str = datetime.now().strftime("%H:%M:%S")
    state_str = ctx.state.value.ljust(12)
    print(f"[{time_str}] {asset.upper().ljust(4)} | {state_str} | {action} {extra if extra else ''}")


# ==============================================================================
# FUNÇÕES DE MERCADO
# ==============================================================================

def fetch_market_status(asset: str) -> Optional[dict]:
    """Busca status do mercado que está na janela de entrada.

    Verifica tanto a janela atual quanto a anterior, retornando o mercado
    que está dentro da janela de operação (ENTRY_WINDOW_START a ENTRY_WINDOW_END).
    """
    try:
        http = get_http()
        now = int(time.time())
        current_window = int(now // 900) * 900

        # Tentar ambas as janelas e retornar a que está na janela de entrada
        for window_ts in [current_window, current_window - 900]:
            slug = f"{asset}-updown-15m-{window_ts}"
            result = _fetch_market_by_slug(http, asset, slug)
            if result:
                end_ts = result["end_ts"]
                time_to_expiry = end_ts - now
                # Retornar se estiver na janela de operação ou próximo dela
                if time_to_expiry > -60:  # Ainda não expirou (ou expirou há menos de 60s)
                    return result

        return None
    except Exception as e:
        print(f"[ERRO] fetch_market_status({asset}): {e}")
        return None


def _fetch_market_by_slug(http, asset: str, slug: str) -> Optional[dict]:
    """Busca dados de um mercado específico pelo slug."""
    try:

        r = http.get(f"{GAMMA_HOST}/events/slug/{slug}")
        if r.status_code != 200:
            return None

        event = r.json()
        markets = event.get("markets", [])
        if not markets:
            return None

        market = markets[0]

        # Token IDs
        raw = market.get("clobTokenIds")
        tokens = json.loads(raw) if isinstance(raw, str) else (raw or [])
        if len(tokens) < 2:
            return None

        yes_token = tokens[0]
        no_token = tokens[1]

        # End time (expiração)
        end_date = market.get("endDate") or event.get("endDate")
        if end_date:
            # Parse ISO date
            if end_date.endswith("Z"):
                end_date = end_date[:-1] + "+00:00"
            from datetime import datetime as dt
            end_ts = int(dt.fromisoformat(end_date).timestamp())
        else:
            # Fallback: assumir fim da janela
            end_ts = int(slug.split("-")[-1]) + 900

        # Preços ao vivo do CLOB (midpoint ou book) — só dados reais; sem default 0.50
        yes_price = get_best_price(yes_token)
        no_price = get_best_price(no_token)
        if yes_price is None or no_price is None:
            return None  # skip mercado sem preço real (evita dados fake)
        yes_price = float(yes_price)
        no_price = float(no_price)

        return {
            "asset": asset,
            "slug": slug,
            "end_ts": end_ts,
            "yes_token": yes_token,
            "no_token": no_token,
            "yes_price": yes_price,
            "no_price": no_price,
            "title": event.get("title", slug),
        }

    except Exception as e:
        return None


def _float_price(v) -> Optional[float]:
    if v is None:
        return None
    try:
        p = float(v)
        return p if 0 <= p <= 1 else None
    except (TypeError, ValueError):
        return None


def get_best_price(token_id: str) -> Optional[float]:
    """Preço real ao vivo: CLOB /midpoint (oficial) ou mid do book (bid+ask)/2. Sem Gamma. None = sem dado real."""
    http = get_http()
    base = CLOB_HOST.rstrip("/")
    # 1) Endpoint oficial Polymarket — midpoint
    try:
        r = http.get(f"{base}/midpoint", params={"token_id": token_id})
        if r.status_code == 200:
            data = r.json()
            mid = data.get("mid") or data.get("price")
            p = _float_price(mid)
            if p is not None:
                return p
    except Exception:
        pass
    # 2) Fallback: mid do orderbook (best_bid + best_ask) / 2
    try:
        r = http.get(f"{base}/book", params={"token_id": token_id})
        if r.status_code == 200:
            book = r.json()
            bids = book.get("bids", [])
            asks = book.get("asks", [])
            best_bid = _float_price(bids[0].get("price") or bids[0].get("p")) if bids else None
            best_ask = _float_price(asks[0].get("price") or asks[0].get("p")) if asks else None
            if best_bid is not None and best_ask is not None:
                return round((best_bid + best_ask) / 2, 2)
            if best_ask is not None:
                return best_ask
            if best_bid is not None:
                return best_bid
    except Exception:
        pass
    return None  # sem preço real — caller não deve usar 0.50


def get_best_ask(token_id: str) -> Optional[float]:
    """Best ask do book CLOB (para colocar ordem 1 tick abaixo)."""
    try:
        http = get_http()
        r = http.get(f"{CLOB_HOST.rstrip('/')}/book", params={"token_id": token_id})
        if r.status_code == 200:
            book = r.json()
            asks = book.get("asks", [])
            if asks:
                return _float_price(asks[0].get("price") or asks[0].get("p"))
    except Exception:
        pass
    return None


def get_outcome_prices(market: dict) -> tuple:
    """Extrai preços YES/NO do market data da Gamma API."""
    outcome_prices = market.get("outcomePrices")
    if outcome_prices:
        if isinstance(outcome_prices, str):
            import json as _json
            outcome_prices = _json.loads(outcome_prices)
        if len(outcome_prices) >= 2:
            return float(outcome_prices[0]), float(outcome_prices[1])
    return 0.50, 0.50


# ==============================================================================
# SALDO USDC (opcional: requer web3)
# ==============================================================================

def _get_balance_wallet_address() -> Optional[str]:
    """Endereço onde está o USDC (funder/proxy ou EOA)."""
    funder = os.getenv("POLYMARKET_FUNDER", "").strip()
    if funder:
        return funder if funder.startswith("0x") else f"0x{funder}"
    try:
        from eth_account import Account
        pk = os.getenv("POLYMARKET_PRIVATE_KEY", "")
        if not pk.startswith("0x"):
            pk = f"0x{pk}"
        if pk:
            return Account.from_key(pk).address
    except Exception:
        pass
    return None


def get_usdc_balance() -> Optional[float]:
    """Saldo USDC on-chain (Polygon). Tenta vários RPCs em caso de falha/rate limit."""
    try:
        from web3 import Web3
    except ImportError:
        return None
    wallet = _get_balance_wallet_address()
    if not wallet:
        return None
    try:
        from polygon_rpc import get_web3_with_fallback, get_polygon_rpc_list
    except ImportError:
        get_web3_with_fallback = None
        get_polygon_rpc_list = lambda: [os.getenv("POLYGON_RPC", "https://polygon-rpc.com")]
    usdc_address = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
    abi = [{"constant": True, "inputs": [{"name": "account", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}], "type": "function"}]
    urls = get_polygon_rpc_list()
    if get_web3_with_fallback:
        w3 = get_web3_with_fallback(timeout=5)
        if w3:
            try:
                usdc = w3.eth.contract(address=Web3.to_checksum_address(usdc_address), abi=abi)
                raw = usdc.functions.balanceOf(Web3.to_checksum_address(wallet)).call()
                return raw / 10**6
            except Exception:
                pass
    for rpc in urls:
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 5}))
            usdc = w3.eth.contract(address=Web3.to_checksum_address(usdc_address), abi=abi)
            raw = usdc.functions.balanceOf(Web3.to_checksum_address(wallet)).call()
            return raw / 10**6
        except Exception:
            continue
    return None


def place_order_with_retry(token_id: str, price: float, size: float) -> Optional[str]:
    """Envia ordem com retry em caso de falha (até ORDER_FAIL_MAX_RETRIES)."""
    for attempt in range(ORDER_FAIL_MAX_RETRIES):
        order_id = place_order(token_id, price, size)
        if order_id:
            return order_id
        if attempt < ORDER_FAIL_MAX_RETRIES - 1:
            time.sleep(ORDER_FAIL_RETRY_DELAY)
    return None


# ==============================================================================
# FUNÇÕES DE ORDEM
# ==============================================================================

def place_order(token_id: str, price: float, size: float) -> Optional[str]:
    """Envia ordem LIMIT POST_ONLY."""
    try:
        client = get_client()
        resp = client.create_and_post_order(
            OrderArgs(token_id=token_id, price=price, size=size, side=BUY),
        )
        if resp.get("success"):
            return resp.get("orderID")
        else:
            print(f"[ERRO] place_order: {resp}")
            return None
    except Exception as e:
        print(f"[ERRO] place_order: {e}")
        return None


def cancel_order(order_id: str) -> bool:
    """Cancela uma ordem."""
    try:
        client = get_client()
        resp = client.cancel(order_id)
        return resp.get("canceled", False) or resp.get("success", False)
    except Exception as e:
        print(f"[ERRO] cancel_order: {e}")
        return False


def check_order_filled(order_id: str) -> bool:
    """Verifica se ordem foi preenchida."""
    try:
        client = get_client()
        order = client.get_order(order_id)
        if order:
            size_matched = float(order.get("size_matched", 0))
            return size_matched > 0
    except Exception as e:
        print(f"[ERRO] check_order_filled: {e}")
    return False


def wait_for_fill(order_id: str, timeout: int = FILL_TIMEOUT) -> bool:
    """Aguarda fill até timeout."""
    start = time.time()
    while time.time() - start < timeout:
        if check_order_filled(order_id):
            return True
        time.sleep(1)
    return False


# ==============================================================================
# RESET DE CONTEXTO
# ==============================================================================

def reset_context(ctx: MarketContext):
    """Reseta contexto para novo ciclo."""
    ctx.state = MarketState.IDLE
    ctx.trade_attempts = 0
    ctx.order_id = None
    ctx.entered_side = None
    ctx.entered_price = None
    ctx.entered_size = None
    ctx.entered_ts = None


# ==============================================================================
# SIGNAL HANDLER
# ==============================================================================

def signal_handler(signum, frame):
    """Graceful shutdown."""
    global _running
    print("\n[SHUTDOWN] Recebido sinal de parada...")
    _running = False


# ==============================================================================
# LOOP PRINCIPAL
# ==============================================================================

def main():
    global _running

    print("=" * 60)
    print("BOT 15MIN - POLYMARKET")
    print(f"MERCADOS: {', '.join(a.upper() for a in ASSETS)}")
    print(f"JANELA: {ENTRY_WINDOW_START}s a {ENTRY_WINDOW_END}s antes da expiração")
    print(f"RANGE: {MIN_PRICE*100:.0f}% a {MAX_PRICE*100:.0f}%")
    print(f"SHARES: {MIN_SHARES}")
    print("=" * 60)
    print()

    # Verificar credenciais
    try:
        client = get_client()
        from eth_account import Account
        pk = os.getenv("POLYMARKET_PRIVATE_KEY", "")
        if not pk.startswith("0x"):
            pk = f"0x{pk}"
        signer = Account.from_key(pk).address
        funder = os.getenv("POLYMARKET_FUNDER", "")
        print(f"Signer: {signer}")
        print(f"Funder: {funder}")
        print()
    except Exception as e:
        print(f"ERRO: {e}")
        sys.exit(1)

    # Registrar signal handler
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Inicializar contextos
    contexts = {asset: MarketContext(asset=asset) for asset in ASSETS}

    print("Iniciando loop principal... (Ctrl+C para parar)")
    print()

    while _running:
        now = int(time.time())

        for asset in ASSETS:
            if not _running:
                break

            ctx = contexts[asset]

            # 1. Buscar status do mercado
            market = fetch_market_status(asset)
            if not market:
                continue

            end_ts = market["end_ts"]
            time_to_expiry = end_ts - now
            yes_price = market["yes_price"]
            no_price = market["no_price"]
            yes_token = market["yes_token"]
            no_token = market["no_token"]

            # Atualizar token IDs
            ctx.yes_token_id = yes_token
            ctx.no_token_id = no_token

            # 2. Detectar novo ciclo
            if ctx.cycle_end_ts != end_ts:
                old_cycle = ctx.cycle_end_ts
                reset_context(ctx)
                ctx.cycle_end_ts = end_ts
                log_event("NEW_CYCLE", asset, ctx, end_ts=end_ts, title=market["title"])

            # 3. Já expirou?
            if time_to_expiry <= 0:
                if ctx.state not in (MarketState.DONE, MarketState.SKIPPED):
                    ctx.state = MarketState.DONE
                    log_event("EXPIRED", asset, ctx)
                continue

            # 4. Menos de 60s? Hard stop
            if time_to_expiry < ENTRY_WINDOW_END:
                if ctx.state == MarketState.ORDER_PLACED:
                    cancel_order(ctx.order_id)
                    ctx.state = MarketState.SKIPPED
                    log_event("CANCEL_HARD_STOP", asset, ctx, time_to_expiry=time_to_expiry)
                elif ctx.state == MarketState.IDLE:
                    ctx.state = MarketState.SKIPPED
                continue

            # 5. Fora da janela de entrada?
            if time_to_expiry > ENTRY_WINDOW_START:
                continue

            # 6. Já em posição (FILLED) ou ciclo encerrado? Só 1 fill por mercado por ciclo.
            if ctx.state in (MarketState.HOLDING, MarketState.DONE, MarketState.SKIPPED):
                continue
            if ctx.trade_attempts >= 1:
                continue  # já executou uma ordem neste ciclo — não reenvia

            # 7. Verificar condição 95%-98%
            side, token_id, price = None, None, None

            if MIN_PRICE <= yes_price <= MAX_PRICE:
                side = "YES"
                token_id = yes_token
                price = max(0.01, round(yes_price - 0.01, 2))
            elif MIN_PRICE <= no_price <= MAX_PRICE:
                side = "NO"
                token_id = no_token
                price = max(0.01, round(no_price - 0.01, 2))

            if not side:
                # Na janela mas preço fora do range 95%-98% — log para diagnóstico
                log_event("SKIP_PRICE_OOR", asset, ctx, yes_price=round(yes_price, 2), no_price=round(no_price, 2), time_to_expiry=time_to_expiry)
                continue

            # 7b. Verificar saldo USDC antes de enviar ordem
            balance = get_usdc_balance()
            if balance is not None and balance < MIN_BALANCE_USDC:
                log_event("SKIP_INSUFFICIENT_BALANCE", asset, ctx, balance=round(balance, 2), required=MIN_BALANCE_USDC)
                continue

            # 8. Enviar ordem (com retry se falhar)
            log_event("PLACING_ORDER", asset, ctx, side=side, price=price, size=MIN_SHARES, time_to_expiry=time_to_expiry)

            order_id = place_order_with_retry(token_id, price, MIN_SHARES)
            if not order_id:
                ctx.state = MarketState.SKIPPED
                log_event("ORDER_FAILED", asset, ctx)
                continue

            ctx.state = MarketState.ORDER_PLACED
            ctx.order_id = order_id
            # trade_attempts só sobe quando FILLED — assim pode reenviar após timeout

            log_event("ORDER_PLACED", asset, ctx, side=side, price=price, order_id=order_id)

            # 9. Aguardar fill (até 10s)
            filled = wait_for_fill(order_id, timeout=FILL_TIMEOUT)

            if filled:
                ctx.trade_attempts += 1  # 1 fill por mercado por ciclo
                ctx.state = MarketState.HOLDING
                ctx.entered_side = side
                ctx.entered_price = price
                ctx.entered_size = MIN_SHARES
                ctx.entered_ts = now
                ctx.order_id = None
                log_event("FILLED", asset, ctx, side=side, price=price)
            else:
                cancel_order(order_id)
                ctx.order_id = None
                log_event("TIMEOUT_CANCEL", asset, ctx, side=side, price=price)
                # Reenviar 1 tick abaixo do best ask, respeitando teto 95%-98%
                best_ask = get_best_ask(token_id)
                if best_ask is not None:
                    retry_price = max(0.01, min(MAX_PRICE, round(best_ask - 0.01, 2)))
                    if retry_price < MIN_PRICE:
                        ctx.state = MarketState.IDLE
                        continue
                    log_event("PLACING_ORDER", asset, ctx, side=side, price=retry_price, size=MIN_SHARES, time_to_expiry=time_to_expiry, retry=True)
                    order_id = place_order_with_retry(token_id, retry_price, MIN_SHARES)
                    if order_id:
                        ctx.state = MarketState.ORDER_PLACED
                        ctx.order_id = order_id
                        log_event("ORDER_PLACED", asset, ctx, side=side, price=retry_price, order_id=order_id)
                        filled = wait_for_fill(order_id, timeout=FILL_TIMEOUT)
                        if filled:
                            ctx.trade_attempts += 1
                            ctx.state = MarketState.HOLDING
                            ctx.entered_side = side
                            ctx.entered_price = retry_price
                            ctx.entered_size = MIN_SHARES
                            ctx.entered_ts = now
                            ctx.order_id = None
                            log_event("FILLED", asset, ctx, side=side, price=retry_price)
                        else:
                            cancel_order(order_id)
                            ctx.state = MarketState.IDLE
                            ctx.order_id = None
                            log_event("TIMEOUT_CANCEL", asset, ctx, side=side, price=retry_price)
                    else:
                        ctx.state = MarketState.IDLE
                        log_event("ORDER_FAILED", asset, ctx)
                else:
                    ctx.state = MarketState.IDLE

        # Aguardar próximo ciclo
        time.sleep(POLL_SECONDS)

    # Cleanup
    print()
    print("[SHUTDOWN] Cancelando ordens abertas...")
    for asset, ctx in contexts.items():
        if ctx.state == MarketState.ORDER_PLACED and ctx.order_id:
            cancel_order(ctx.order_id)
            log_event("SHUTDOWN_CANCEL", asset, ctx)

    if _http:
        _http.close()

    print("[SHUTDOWN] Bot encerrado.")


if __name__ == "__main__":
    main()
