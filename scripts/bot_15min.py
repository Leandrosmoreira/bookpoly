#!/usr/bin/env python3
"""
Bot 24/7 para mercados 15min do Polymarket (BTC, ETH, SOL, XRP).

Estratégia:
- Detecta ciclos de 15min automaticamente
- Entra quando YES ou NO estiver entre 93%-98%
- Janela de entrada: 4min a 1min antes da expiração
- Stop-loss: vende (SELL FOK) se prob do lado cair abaixo de 70%
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
from guardrails import GuardrailsPro, GuardrailAction

load_dotenv(Path(__file__).parent.parent / ".env")

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import OrderArgs, OrderType, ApiCreds, BalanceAllowanceParams, AssetType
    from py_clob_client.order_builder.constants import BUY, SELL
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
ENTRY_WINDOW_START = 240   # Segundos antes da expiração (4min)
ENTRY_WINDOW_END = 60      # Hard stop (1min)
FILL_TIMEOUT = 5           # Segundos para aguardar fill por tentativa
MAX_FILL_ATTEMPTS = 3      # Tentativas de ordem (1 inicial + 2 reenvios 1 tick abaixo) antes de SKIPPED
MIN_SHARES = 8             # Quantidade por ordem
MIN_PRICE = 0.93           # Preço mínimo para entrada (93%) — NÃO ALTERAR, definido pelo dono
MAX_PRICE = 0.98           # Preço máximo para entrada
MIN_BALANCE_USDC = 8.2     # Saldo mínimo (USDC) para 8 shares @ 98%
ORDER_FAIL_RETRY_DELAY = 2 # Segundos antes de reenviar após falha
ORDER_FAIL_MAX_RETRIES = 2 # Tentativas de place_order antes de desistir
MAX_RETRY_PRICE_DELTA = float(os.getenv("MAX_RETRY_PRICE_DELTA", "0.04"))  # Max centavos acima do preco original no retry

# Stop-loss
STOP_PROB = float(os.getenv("BL_STOP_PROB", "0.40"))           # Vende se prob do lado cair abaixo

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
    skip_retried: bool = False  # True após dar uma nova chance após SKIPPED no mesmo ciclo
    yes_price: Optional[float] = None  # Probabilidade YES atual (CLOB)
    no_price: Optional[float] = None   # Probabilidade NO atual (CLOB)
    # Stop-loss
    stop_executed: bool = False
    stop_price: Optional[float] = None
    stop_size: Optional[int] = None
    stop_ts: Optional[int] = None
    stop_order_id: Optional[str] = None
    stop_pnl: Optional[float] = None


# ==============================================================================
# CLIENTE GLOBAL
# ==============================================================================

_client: Optional[ClobClient] = None
_http: Optional[httpx.Client] = None
_running = True
_usdc_balance_cache: Optional[float] = None
_usdc_balance_cache_ts: float = 0
BALANCE_CACHE_TTL = 60  # Cache saldo por 60 segundos

# Fila de resultados pendentes (posicoes cujo outcome nao foi obtido na transicao)
# Chave: "asset:cycle_end_ts", Valor: dict com dados da posicao
_pending_results: dict = {}


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
# LOGGING (persistent file handle + flush a cada write)
# ==============================================================================

class _LogWriter:
    """File handle persistente com flush() a cada write — evita perda de dados em crash."""

    def __init__(self):
        self._file = None
        self._date: Optional[str] = None

    def write(self, event: dict):
        today = datetime.now().strftime("%Y-%m-%d")
        if self._date != today or self._file is None:
            self.close()
            LOGS_DIR.mkdir(exist_ok=True)
            self._file = open(LOGS_DIR / f"bot_15min_{today}.jsonl", "a", encoding="utf-8")
            self._date = today
        try:
            self._file.write(json.dumps(event) + "\n")
            self._file.flush()  # CRITICO: garante que dados vão pro disco
        except Exception as e:
            print(f"[LOG ERROR] {e}")

    def close(self):
        if self._file is not None:
            try:
                self._file.flush()
                self._file.close()
            except Exception:
                pass
            self._file = None
            self._date = None


_log_writer = _LogWriter()


def log_event(action: str, asset: str, ctx: MarketContext, **extra):
    """Grava evento no log JSONL via _log_writer (flush garantido)."""
    now = int(time.time())
    event = {
        "ts": now,
        "ts_iso": datetime.now().isoformat(),
        "market": asset,
        "cycle_end_ts": ctx.cycle_end_ts,
        "state": ctx.state.value,
        "action": action,
        "order_id": ctx.order_id,
        "yes_price": ctx.yes_price,
        "no_price": ctx.no_price,
        **extra,
    }

    _log_writer.write(event)

    # Também exibe no console
    time_str = datetime.now().strftime("%H:%M:%S")
    state_str = ctx.state.value.ljust(12)

    # Probabilidades CLOB (sempre visíveis)
    yp = ctx.yes_price
    np_ = ctx.no_price
    clob_str = f"yes={yp:.2f} no={np_:.2f}" if yp is not None and np_ is not None else ""

    # Status P&L em tempo real (só durante HOLDING com posição ativa)
    pos_str = ""
    if (ctx.state == MarketState.HOLDING
            and ctx.entered_side and ctx.entered_price is not None
            and yp is not None and np_ is not None):
        side_now, winning, pnl = calc_clob_pnl(
            ctx.entered_side, ctx.entered_price,
            yp, np_, ctx.entered_size or MIN_SHARES
        )
        emoji = "\u2705" if winning else "\u274c"
        label = "WINNING" if winning else "LOSING"
        pos_str = f"{ctx.entered_side}@{ctx.entered_price:.2f} now={side_now:.2f} {emoji}{label} ${pnl:+.2f}"

    # Montar linha final
    if pos_str:
        print(f"[{time_str}] {asset.upper().ljust(4)} | {state_str} | {pos_str} | {clob_str} | {action} {extra if extra else ''}")
    else:
        print(f"[{time_str}] {asset.upper().ljust(4)} | {state_str} | {clob_str} | {action} {extra if extra else ''}")


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


def _get_resolved_outcome(asset: str, cycle_end_ts: int, retries: int = 3, delay: float = 3.0) -> Optional[str]:
    """Retorna qual outcome venceu ('YES' ou 'NO') após resolução.

    Faz até `retries` tentativas com `delay` segundos entre cada,
    pois a Gamma API pode demorar para refletir a resolução.
    Retorna None apenas se todas as tentativas falharem.
    """
    http = get_http()
    window_start = cycle_end_ts - 900
    slug = f"{asset}-updown-15m-{window_start}"

    for attempt in range(retries):
        try:
            r = http.get(f"{GAMMA_HOST}/events/slug/{slug}")
            if r.status_code != 200:
                if attempt < retries - 1:
                    time.sleep(delay)
                continue
            event = r.json()
            markets = event.get("markets", [])
            if not markets:
                if attempt < retries - 1:
                    time.sleep(delay)
                continue
            market = markets[0]
            # outcomePrices após resolução: "1,0" = YES venceu, "0,1" = NO venceu
            raw = market.get("outcomePrices")
            if raw is None:
                if attempt < retries - 1:
                    time.sleep(delay)
                continue
            if isinstance(raw, str):
                raw = [s.strip() for s in raw.split(",")] if "," in raw else [raw]
            if not isinstance(raw, (list, tuple)) or len(raw) < 2:
                if attempt < retries - 1:
                    time.sleep(delay)
                continue
            try:
                p0 = float(raw[0])
                p1 = float(raw[1])
            except (TypeError, ValueError):
                if attempt < retries - 1:
                    time.sleep(delay)
                continue
            if p0 >= 0.99 and p1 <= 0.01:
                return "YES"
            if p1 >= 0.99 and p0 <= 0.01:
                return "NO"
            # outcomePrices existe mas nao e 1,0 ou 0,1 — mercado nao resolveu ainda
            if attempt < retries - 1:
                time.sleep(delay)
                continue
        except Exception:
            if attempt < retries - 1:
                time.sleep(delay)
                continue
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


def get_best_bid(token_id: str) -> Optional[float]:
    """Best bid do book CLOB."""
    try:
        http = get_http()
        r = http.get(f"{CLOB_HOST.rstrip('/')}/book", params={"token_id": token_id})
        if r.status_code == 200:
            book = r.json()
            bids = book.get("bids", [])
            if bids:
                return _float_price(bids[0].get("price") or bids[0].get("p"))
    except Exception:
        pass
    return None


def calc_clob_pnl(entered_side: str, entered_price: float,
                  yes_price: float, no_price: float, shares: float) -> tuple:
    """P&L em tempo real baseado na probabilidade CLOB do lado posicionado.
    Retorna (side_now_price, is_winning, potential_pnl).
    """
    side_now = yes_price if entered_side == "YES" else no_price
    winning = side_now > entered_price
    if winning:
        pnl = round((1.0 - entered_price) * shares, 2)
    else:
        pnl = round(-entered_price * shares, 2)
    return side_now, winning, pnl


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
    """Saldo USDC on-chain (Polygon). Cacheado por 60s. Tenta vários RPCs em caso de falha/rate limit."""
    global _usdc_balance_cache, _usdc_balance_cache_ts
    now = time.time()
    if _usdc_balance_cache is not None and (now - _usdc_balance_cache_ts) < BALANCE_CACHE_TTL:
        return _usdc_balance_cache
    result = _fetch_usdc_balance()
    if result is not None:
        _usdc_balance_cache = result
        _usdc_balance_cache_ts = now
    return result


def _fetch_usdc_balance() -> Optional[float]:
    """Busca saldo USDC disponível para trade.

    Fonte primária: CLOB API get_balance_allowance(COLLATERAL).
    Na Polymarket, USDC depositado vira collateral no CTF Exchange —
    balanceOf on-chain retorna 0. A CLOB API retorna o saldo real.

    Fallback: USDC ERC-20 on-chain (para wallets com USDC não-depositado).
    """
    # 1. CLOB API autenticada — saldo real no exchange (retorna em raw units, 6 decimais)
    try:
        client = get_client()
        params = BalanceAllowanceParams(
            asset_type=AssetType.COLLATERAL,
            signature_type=1,  # POLY_PROXY
        )
        result = client.get_balance_allowance(params)
        if isinstance(result, dict):
            raw_balance = result.get("balance", "0")
            return int(raw_balance) / 10**6
    except Exception:
        pass

    # 2. Fallback: USDC ERC-20 on-chain
    wallet = _get_balance_wallet_address()
    if not wallet:
        return None
    return _fetch_usdc_onchain(wallet)


def _fetch_usdc_onchain(wallet: str) -> Optional[float]:
    """Fallback: saldo USDC ERC-20 on-chain (Polygon)."""
    try:
        from web3 import Web3
    except ImportError:
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
    """Envia ordem LIMIT POST_ONLY (maker)."""
    try:
        client = get_client()
        order_args = OrderArgs(token_id=token_id, price=price, size=size, side=BUY)
        signed_order = client.create_order(order_args)
        resp = client.post_order(signed_order, OrderType.GTC, post_only=True)
        if resp.get("success"):
            return resp.get("orderID")
        else:
            print(f"[ERRO] place_order: {resp}")
            return None
    except Exception as e:
        print(f"[ERRO] place_order: {e}")
        return None


def place_sell_order(token_id: str, price: float, size: float) -> Optional[str]:
    """Envia ordem de SELL FOK (taker, fill imediato)."""
    try:
        client = get_client()
        order_args = OrderArgs(token_id=token_id, price=price, size=size, side=SELL)
        signed_order = client.create_order(order_args)
        resp = client.post_order(signed_order, OrderType.FOK, post_only=False)
        if resp.get("success"):
            return resp.get("orderID")
        else:
            print(f"[ERRO] place_sell_order: {resp}")
            return None
    except Exception as e:
        print(f"[ERRO] place_sell_order: {e}")
        return None


def evaluate_stop_loss(
    ctx: MarketContext,
    yes_price: float,
    no_price: float,
) -> Optional[dict]:
    """Avalia se deve executar stop-loss. Retorna dict ou None."""
    if ctx.state != MarketState.HOLDING:
        return None
    if ctx.stop_executed:
        return None
    if ctx.entered_side is None or ctx.entered_price is None or ctx.entered_size is None:
        return None

    # Prob atual do nosso lado
    our_price = yes_price if ctx.entered_side == "YES" else no_price

    if our_price >= STOP_PROB:
        return None  # Ainda acima do threshold

    # Token que estamos segurando (vamos VENDER ele)
    our_token = ctx.yes_token_id if ctx.entered_side == "YES" else ctx.no_token_id

    return {
        "token_id": our_token,
        "side": ctx.entered_side,
        "size": ctx.entered_size,
        "our_price": round(our_price, 4),
        "trigger": STOP_PROB,
    }


def execute_stop_loss(ctx: MarketContext, stop: dict) -> bool:
    """Executa SELL FOK do token posicionado a mercado (aceita qualquer bid)."""
    best_bid = get_best_bid(stop["token_id"])
    if best_bid is None:
        log_event("STOP_SKIPPED", ctx.asset, ctx, reason="no_bid")
        return False

    # Venda a mercado: price minimo 0.01 para garantir fill no melhor bid disponivel
    sell_price = 0.01

    order_id = place_sell_order(stop["token_id"], sell_price, stop["size"])
    if not order_id:
        log_event("STOP_NOT_FILLED", ctx.asset, ctx,
            sell_price=sell_price, size=stop["size"], reason="order_rejected")
        return False

    # FOK = fill imediato, verificar
    filled = check_order_filled(order_id)
    if filled:
        # PnL do stop: best_bid e o preco real de execucao (sell_price=0.01 e so o minimo aceito)
        exec_price = best_bid  # FOK preenche no melhor bid disponivel
        stop_pnl = round((exec_price - ctx.entered_price) * stop["size"], 4)
        ctx.stop_executed = True
        ctx.stop_price = exec_price
        ctx.stop_size = stop["size"]
        ctx.stop_ts = int(time.time())
        ctx.stop_order_id = order_id
        ctx.stop_pnl = stop_pnl
        ctx.state = MarketState.DONE  # Encerra posicao
        log_event("STOP_EXECUTED", ctx.asset, ctx,
            sell_price=exec_price, size=stop["size"],
            stop_pnl=stop_pnl, our_price=stop["our_price"],
            trigger=STOP_PROB, order_id=order_id)
        return True
    else:
        log_event("STOP_NOT_FILLED", ctx.asset, ctx,
            sell_price=sell_price, size=stop["size"], reason="FOK_not_matched")
        return False


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
    ctx.skip_retried = False
    ctx.yes_price = None
    ctx.no_price = None
    ctx.stop_executed = False
    ctx.stop_price = None
    ctx.stop_size = None
    ctx.stop_ts = None
    ctx.stop_order_id = None
    ctx.stop_pnl = None


# ==============================================================================
# SIGNAL HANDLER
# ==============================================================================

def signal_handler(signum, frame):
    """Graceful shutdown com flush de logs."""
    global _running
    print(f"\n[SHUTDOWN] Sinal {signum} recebido, flushing logs...")
    _running = False
    _log_writer.close()


import atexit
atexit.register(_log_writer.close)


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
    print(f"STOP-LOSS: prob < {STOP_PROB*100:.0f}%")
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
    guardrails = {asset: GuardrailsPro(asset=asset) for asset in ASSETS}

    print("Iniciando loop principal... (Ctrl+C para parar)")
    print()

    while _running:
        now = int(time.time())

        # ── Resolver resultados pendentes (posicoes cujo outcome nao foi obtido na transicao)
        if _pending_results:
            resolved_keys = []
            for key, pdata in list(_pending_results.items()):
                age = now - pdata.get("added_ts", now)
                # Max age: 10 minutos — se nao resolveu, logar UNKNOWN e limpar
                if age > 600:
                    tmp_ctx = MarketContext(asset=pdata["asset"])
                    tmp_ctx.cycle_end_ts = pdata["cycle_end_ts"]
                    log_event("POSITION_RESULT_UNKNOWN", pdata["asset"], tmp_ctx,
                        side=pdata["side"],
                        entry_price=pdata["entry_price"],
                        size=pdata["size"],
                        stop_executed=pdata.get("stop_executed", False),
                        stop_pnl=pdata.get("stop_pnl"),
                        age_s=age,
                        reason="max_pending_age_exceeded")
                    resolved_keys.append(key)
                    continue
                outcome = _get_resolved_outcome(pdata["asset"], pdata["cycle_end_ts"], retries=5, delay=5.0)
                if outcome is not None:
                    if pdata.get("stop_executed") and pdata.get("stop_pnl") is not None:
                        pnl = pdata["stop_pnl"]
                        win = pnl > 0
                    else:
                        win = pdata["side"] == outcome
                        pnl = (1.0 - pdata["entry_price"]) * pdata["size"] if win else -pdata["entry_price"] * pdata["size"]
                    # Usar contexto temporario para log
                    tmp_ctx = MarketContext(asset=pdata["asset"])
                    tmp_ctx.cycle_end_ts = pdata["cycle_end_ts"]
                    log_event("POSITION_RESULT_RESOLVED", pdata["asset"], tmp_ctx,
                        outcome_winner=outcome,
                        side=pdata["side"],
                        entry_price=pdata["entry_price"],
                        size=pdata["size"],
                        win=win,
                        pnl=round(pnl, 2),
                        stop_executed=pdata.get("stop_executed", False),
                        stop_pnl=pdata.get("stop_pnl"))
                    resolved_keys.append(key)
            for key in resolved_keys:
                del _pending_results[key]

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
            guardrails[asset].update(float(now), yes_price, no_price)
            yes_token = market["yes_token"]
            no_token = market["no_token"]

            # Atualizar token IDs
            ctx.yes_token_id = yes_token
            ctx.no_token_id = no_token

            # Atualizar probabilidades CLOB no contexto (para log_event)
            ctx.yes_price = round(yes_price, 4)
            ctx.no_price = round(no_price, 4)

            # 2. Detectar novo ciclo
            if ctx.cycle_end_ts != end_ts:
                old_cycle = ctx.cycle_end_ts
                # Gravar resultado da posição do ciclo anterior ANTES de resetar
                if ctx.state in (MarketState.HOLDING, MarketState.DONE) and ctx.entered_side and ctx.entered_price is not None and old_cycle is not None:
                    outcome_winner = _get_resolved_outcome(asset, old_cycle, retries=5, delay=5.0)
                    size = ctx.entered_size if ctx.entered_size is not None else MIN_SHARES

                    if ctx.stop_executed and ctx.stop_pnl is not None:
                        # Stop-loss ja vendeu — PnL = stop_pnl
                        log_event("POSITION_RESULT", asset, ctx,
                            outcome_winner=outcome_winner or "N/A",
                            side=ctx.entered_side,
                            entry_price=ctx.entered_price,
                            size=size,
                            pnl=ctx.stop_pnl,
                            stop_executed=True,
                            stop_price=ctx.stop_price)
                    elif outcome_winner is not None:
                        win = ctx.entered_side == outcome_winner
                        pnl = (1.0 - ctx.entered_price) * size if win else -ctx.entered_price * size
                        log_event("POSITION_RESULT", asset, ctx,
                            outcome_winner=outcome_winner,
                            side=ctx.entered_side,
                            entry_price=ctx.entered_price,
                            size=size,
                            win=win,
                            pnl=round(pnl, 2),
                            stop_executed=False)
                    else:
                        # API nao retornou resultado apos retries — salvar para resolver depois
                        pending_key = f"{asset}:{old_cycle}"
                        _pending_results[pending_key] = {
                            "asset": asset,
                            "cycle_end_ts": old_cycle,
                            "side": ctx.entered_side,
                            "entry_price": ctx.entered_price,
                            "size": size,
                            "stop_executed": ctx.stop_executed,
                            "stop_pnl": ctx.stop_pnl,
                            "added_ts": now,
                        }
                        log_event("POSITION_RESULT_PENDING", asset, ctx,
                            side=ctx.entered_side,
                            entry_price=ctx.entered_price,
                            size=size,
                            reason="outcome_not_resolved_after_retries")
                reset_context(ctx)
                guardrails[asset].reset()
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

            # 5b. SKIPPED mas ainda na janela (5min–1min) e preço no range 93%–98%? Uma nova chance no mesmo ciclo.
            if ctx.state == MarketState.SKIPPED and not ctx.skip_retried:
                if (MIN_PRICE <= yes_price <= MAX_PRICE) or (MIN_PRICE <= no_price <= MAX_PRICE):
                    ctx.state = MarketState.IDLE
                    ctx.trade_attempts = 0
                    ctx.skip_retried = True
                    log_event("RE_ENTRY_AFTER_SKIP", asset, ctx, time_to_expiry=time_to_expiry, yes_price=round(yes_price, 2), no_price=round(no_price, 2))

            # 6. Ciclo encerrado?
            if ctx.state in (MarketState.DONE, MarketState.SKIPPED):
                continue

            # 6a. HOLDING — stop-loss por probabilidade
            if ctx.state == MarketState.HOLDING:
                if not ctx.stop_executed:
                    stop = evaluate_stop_loss(ctx, yes_price, no_price)
                    if stop is not None:
                        log_event("STOP_SIGNAL", asset, ctx,
                            our_price=stop["our_price"],
                            trigger=STOP_PROB,
                            size=stop["size"],
                            time_left=time_to_expiry)
                        execute_stop_loss(ctx, stop)
                continue  # HOLDING nao entra na logica de entrada

            if ctx.trade_attempts >= 1:
                continue  # ja deu fill neste ciclo — nao reenvia

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

            # 7a. Guardrails PRO — filtro de entrada inteligente
            # Skip guardrails na re-entry: a entrada ja foi aprovada na 1a tentativa.
            # Apos fill loop bloqueante (~12s), o guardrail perde samples e bloqueia
            # por insufficient_data. Bypass evita esse falso bloqueio.
            if not ctx.skip_retried:
                gr_decision = guardrails[asset].evaluate(side, float(now))
                log_event("GUARDRAIL_DECISION", asset, ctx,
                    gr_action=gr_decision.action.value, side=side,
                    risk_score=gr_decision.risk_score,
                    pump=gr_decision.pump_score,
                    pump_thr=gr_decision.pump_threshold,
                    stability=gr_decision.stability_score,
                    time_in_band=gr_decision.time_in_band_s,
                    momentum=gr_decision.momentum_score,
                    momentum_thr=gr_decision.momentum_threshold,
                    t_remaining=time_to_expiry,
                    reason=gr_decision.reason)
                if gr_decision.action == GuardrailAction.BLOCK:
                    log_event("GUARDRAIL_BLOCK", asset, ctx,
                        side=side, risk_score=gr_decision.risk_score,
                        reason=gr_decision.reason)
                    continue

                # CAUTION tambem bloqueia — so ALLOW permite entrada
                if gr_decision.action == GuardrailAction.CAUTION:
                    log_event("GUARDRAIL_CAUTION_BLOCK", asset, ctx,
                        side=side, risk_score=gr_decision.risk_score,
                        reason=gr_decision.reason)
                    continue
            else:
                log_event("GUARDRAIL_SKIP_REENTRY", asset, ctx,
                    side=side, reason="skip_retried_bypass")

            # 7b. Verificar saldo USDC antes de enviar ordem
            balance = get_usdc_balance()
            if balance is not None and balance < MIN_BALANCE_USDC:
                log_event("SKIP_INSUFFICIENT_BALANCE", asset, ctx, balance=round(balance, 2), required=MIN_BALANCE_USDC)
                continue

            # 8. Enviar ordem e aguardar fill — até MAX_FILL_ATTEMPTS tentativas (15s cada)
            current_price = price
            filled = False
            for attempt in range(MAX_FILL_ATTEMPTS):
                is_retry = attempt > 0
                log_event("PLACING_ORDER", asset, ctx, side=side, price=current_price, size=MIN_SHARES, time_to_expiry=time_to_expiry, retry=is_retry)
                order_id = place_order_with_retry(token_id, current_price, MIN_SHARES)
                if not order_id:
                    ctx.trade_attempts += 1
                    ctx.state = MarketState.SKIPPED
                    log_event("ORDER_FAILED", asset, ctx)
                    break
                ctx.state = MarketState.ORDER_PLACED
                ctx.order_id = order_id
                log_event("ORDER_PLACED", asset, ctx, side=side, price=current_price, order_id=order_id)
                filled = wait_for_fill(order_id, timeout=FILL_TIMEOUT)
                if filled:
                    ctx.trade_attempts += 1
                    ctx.state = MarketState.HOLDING
                    ctx.entered_side = side
                    ctx.entered_price = current_price
                    ctx.entered_size = MIN_SHARES
                    ctx.entered_ts = now
                    ctx.order_id = None
                    log_event("FILLED", asset, ctx, side=side, price=current_price)
                    break
                cancel_order(order_id)
                ctx.order_id = None
                log_event("TIMEOUT_CANCEL", asset, ctx, side=side, price=current_price)
                if attempt + 1 >= MAX_FILL_ATTEMPTS:
                    ctx.trade_attempts += 1
                    ctx.state = MarketState.SKIPPED
                    break
                best_ask = get_best_ask(token_id)
                if best_ask is None:
                    ctx.trade_attempts += 1
                    ctx.state = MarketState.SKIPPED
                    break
                current_price = max(0.01, min(MAX_PRICE, round(best_ask - 0.01, 2)))
                if current_price > price + MAX_RETRY_PRICE_DELTA:
                    log_event("RETRY_PRICE_TOO_HIGH", asset, ctx,
                        original_price=price, retry_price=current_price,
                        delta=round(current_price - price, 2),
                        max_delta=MAX_RETRY_PRICE_DELTA)
                    ctx.trade_attempts += 1
                    ctx.state = MarketState.SKIPPED
                    break
                if current_price < MIN_PRICE:
                    ctx.trade_attempts += 1
                    ctx.state = MarketState.SKIPPED
                    break

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
