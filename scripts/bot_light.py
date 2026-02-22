#!/usr/bin/env python3
"""
Bot Light — Bot determinístico simplificado para mercados 15min do Polymarket.

SEM guardrails, SEM post-defense, SEM indicadores, SEM IA.
Apenas regras matemáticas + flip/hedge para reversão de posição.

Estratégia:
- Detecta ciclos de 15min automaticamente
- Entra quando YES ou NO estiver entre 93%-98% (escolhe o lado com maior prob.)
- Janela de entrada: 4min a 1min antes da expiração
- Flip direcional se o mercado reverter (preço do nosso lado < 0.50)

USO:
    python scripts/bot_light.py
"""

import json
import math
import os
import signal
import sys
import time
import atexit
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import OrderArgs, OrderType, ApiCreds, BalanceAllowanceParams, AssetType
    from py_clob_client.order_builder.constants import BUY
except ImportError:
    print("ERRO: pip install py-clob-client")
    sys.exit(1)


# ==============================================================================
# BLOCK 1: CONFIG
# ==============================================================================

CLOB_HOST = os.getenv("CLOB_BASE_URL", "https://clob.polymarket.com")
GAMMA_HOST = os.getenv("GAMMA_BASE_URL", "https://gamma-api.polymarket.com")
CHAIN_ID = 137

# Assets
ASSETS = [a.strip() for a in os.getenv("BL_ASSETS", "btc,eth,sol,xrp").split(",")]

# Timing
POLL_INTERVAL = int(os.getenv("BL_POLL_INTERVAL", "3"))
ENTRY_WINDOW_START = int(os.getenv("BL_ENTRY_WINDOW_START", "240"))
ENTRY_WINDOW_END = int(os.getenv("BL_ENTRY_WINDOW_END", "60"))

# Pricing
MIN_PRICE = float(os.getenv("BL_MIN_PRICE", "0.93"))
MAX_PRICE = float(os.getenv("BL_MAX_PRICE", "0.98"))

# Order sizing
MIN_SHARES = int(os.getenv("BL_MIN_SHARES", "6"))
FILL_TIMEOUT = int(os.getenv("BL_FILL_TIMEOUT", "5"))
MAX_FILL_ATTEMPTS = int(os.getenv("BL_MAX_FILL_ATTEMPTS", "3"))
MAX_RETRY_PRICE_DELTA = float(os.getenv("BL_MAX_RETRY_PRICE_DELTA", "0.02"))
ORDER_FAIL_RETRY_DELAY = int(os.getenv("BL_ORDER_FAIL_RETRY_DELAY", "2"))
ORDER_FAIL_MAX_RETRIES = int(os.getenv("BL_ORDER_FAIL_MAX_RETRIES", "2"))
MIN_BALANCE_USDC = float(os.getenv("BL_MIN_BALANCE_USDC", "6.5"))

# Flip
FLIP_ENABLED = os.getenv("BL_FLIP_ENABLED", "true").lower() in ("true", "1", "yes")
FLIP_TRIGGER_PRICE = float(os.getenv("BL_FLIP_TRIGGER_PRICE", "0.50"))
FLIP_MIN_TIME_LEFT = int(os.getenv("BL_FLIP_MIN_TIME_LEFT", "120"))
FLIP_MIN_SHARES = int(os.getenv("BL_FLIP_MIN_SHARES", "6"))
MAX_FLIP_RATIO = float(os.getenv("BL_MAX_FLIP_RATIO", "3.0"))

# Logs
LOGS_DIR = Path(__file__).parent.parent / "logs"


# ==============================================================================
# BLOCK 2: CLIENT
# ==============================================================================

_client: Optional[ClobClient] = None
_http: Optional[httpx.Client] = None
_running = True
_usdc_balance_cache: Optional[float] = None
_usdc_balance_cache_ts: float = 0
BALANCE_CACHE_TTL = 60


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


def _float_price(v) -> Optional[float]:
    if v is None:
        return None
    try:
        p = float(v)
        return p if 0 <= p <= 1 else None
    except (TypeError, ValueError):
        return None


def get_best_price(token_id: str) -> Optional[float]:
    """Preço real: CLOB /midpoint ou mid do book. None = sem dado real."""
    http = get_http()
    base = CLOB_HOST.rstrip("/")
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
    return None


def get_best_ask(token_id: str) -> Optional[float]:
    """Best ask do book CLOB."""
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


def fetch_market_status(asset: str) -> Optional[dict]:
    """Busca status do mercado via Gamma API."""
    try:
        http = get_http()
        now = int(time.time())
        current_window = int(now // 900) * 900

        for window_ts in [current_window, current_window - 900]:
            slug = f"{asset}-updown-15m-{window_ts}"
            result = _fetch_market_by_slug(http, asset, slug)
            if result:
                end_ts = result["end_ts"]
                time_to_expiry = end_ts - now
                if time_to_expiry > -60:
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

        raw = market.get("clobTokenIds")
        tokens = json.loads(raw) if isinstance(raw, str) else (raw or [])
        if len(tokens) < 2:
            return None

        yes_token = tokens[0]
        no_token = tokens[1]

        end_date = market.get("endDate") or event.get("endDate")
        if end_date:
            if end_date.endswith("Z"):
                end_date = end_date[:-1] + "+00:00"
            from datetime import datetime as dt
            end_ts = int(dt.fromisoformat(end_date).timestamp())
        else:
            end_ts = int(slug.split("-")[-1]) + 900

        yes_price = get_best_price(yes_token)
        no_price = get_best_price(no_token)
        if yes_price is None or no_price is None:
            return None
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

    except Exception:
        return None


def _get_resolved_outcome(asset: str, cycle_end_ts: int, retries: int = 3, delay: float = 3.0) -> Optional[str]:
    """Retorna outcome vencedor ('YES' ou 'NO') após resolução."""
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
            if attempt < retries - 1:
                time.sleep(delay)
                continue
        except Exception:
            if attempt < retries - 1:
                time.sleep(delay)
                continue
    return None


def get_usdc_balance() -> Optional[float]:
    """Saldo USDC via CLOB API. Cacheado por 60s."""
    global _usdc_balance_cache, _usdc_balance_cache_ts
    now = time.time()
    if _usdc_balance_cache is not None and (now - _usdc_balance_cache_ts) < BALANCE_CACHE_TTL:
        return _usdc_balance_cache
    try:
        client = get_client()
        params = BalanceAllowanceParams(
            asset_type=AssetType.COLLATERAL,
            signature_type=1,
        )
        result = client.get_balance_allowance(params)
        if isinstance(result, dict):
            raw_balance = result.get("balance", "0")
            bal = int(raw_balance) / 10**6
            _usdc_balance_cache = bal
            _usdc_balance_cache_ts = now
            return bal
    except Exception:
        pass
    return _usdc_balance_cache


# ==============================================================================
# BLOCK 3: TIME ENGINE
# ==============================================================================

def calc_time_left(now: int, cycle_end_ts: int) -> int:
    return cycle_end_ts - now


def is_in_entry_window(time_left: int) -> bool:
    return ENTRY_WINDOW_START >= time_left >= ENTRY_WINDOW_END


# ==============================================================================
# BLOCK 4: STATE MEMORY
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
    entered_size: Optional[int] = None
    entered_ts: Optional[int] = None
    yes_token_id: Optional[str] = None
    no_token_id: Optional[str] = None
    yes_price: Optional[float] = None
    no_price: Optional[float] = None
    # Flip
    flip_executed: bool = False
    flip_side: Optional[str] = None
    flip_price: Optional[float] = None
    flip_size: Optional[int] = None
    flip_ts: Optional[int] = None
    flip_order_id: Optional[str] = None


_pending_results: dict = {}


def reset_context(ctx: MarketContext):
    """Reseta contexto para novo ciclo."""
    ctx.state = MarketState.IDLE
    ctx.trade_attempts = 0
    ctx.order_id = None
    ctx.entered_side = None
    ctx.entered_price = None
    ctx.entered_size = None
    ctx.entered_ts = None
    ctx.yes_price = None
    ctx.no_price = None
    ctx.flip_executed = False
    ctx.flip_side = None
    ctx.flip_price = None
    ctx.flip_size = None
    ctx.flip_ts = None
    ctx.flip_order_id = None


# ==============================================================================
# BLOCK 5: ENTRY ENGINE
# ==============================================================================

def evaluate_entry(
    ctx: MarketContext,
    yes_price: float,
    no_price: float,
    time_left: int,
) -> Optional[dict]:
    """Avaliação determinística de entrada. Retorna dict ou None."""
    if not is_in_entry_window(time_left):
        return None

    if ctx.trade_attempts >= 1:
        return None

    yes_in_range = MIN_PRICE <= yes_price <= MAX_PRICE
    no_in_range = MIN_PRICE <= no_price <= MAX_PRICE

    if yes_in_range and no_in_range:
        # Ambos em range: pega o lado com maior probabilidade
        if yes_price >= no_price:
            side = "YES"
            mid = yes_price
            token_id = ctx.yes_token_id
        else:
            side = "NO"
            mid = no_price
            token_id = ctx.no_token_id
    elif yes_in_range:
        side = "YES"
        mid = yes_price
        token_id = ctx.yes_token_id
    elif no_in_range:
        side = "NO"
        mid = no_price
        token_id = ctx.no_token_id
    else:
        return None

    # Preço maker: 1 centavo abaixo do midpoint
    entry_price = max(0.01, round(mid - 0.01, 2))

    return {
        "side": side,
        "token_id": token_id,
        "price": entry_price,
        "size": MIN_SHARES,
    }


# ==============================================================================
# BLOCK 6: EXECUTION ENGINE
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


def place_flip_order(token_id: str, price: float, size: float) -> Optional[str]:
    """Envia ordem de flip FOK (taker, fill imediato)."""
    try:
        client = get_client()
        order_args = OrderArgs(token_id=token_id, price=price, size=size, side=BUY)
        signed_order = client.create_order(order_args)
        resp = client.post_order(signed_order, OrderType.FOK, post_only=False)
        if resp.get("success"):
            return resp.get("orderID")
        else:
            print(f"[ERRO] place_flip_order: {resp}")
            return None
    except Exception as e:
        print(f"[ERRO] place_flip_order: {e}")
        return None


def place_order_with_retry(token_id: str, price: float, size: float) -> Optional[str]:
    """Envia ordem com retry (até ORDER_FAIL_MAX_RETRIES)."""
    for attempt in range(ORDER_FAIL_MAX_RETRIES):
        order_id = place_order(token_id, price, size)
        if order_id:
            return order_id
        if attempt < ORDER_FAIL_MAX_RETRIES - 1:
            time.sleep(ORDER_FAIL_RETRY_DELAY)
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


def execute_entry(ctx: MarketContext, entry: dict) -> bool:
    """Executa loop de tentativas de fill. Retorna True se preenchido."""
    current_price = entry["price"]
    original_price = entry["price"]
    token_id = entry["token_id"]
    side = entry["side"]

    for attempt in range(MAX_FILL_ATTEMPTS):
        is_retry = attempt > 0
        log_event("ORDER_PLACED", ctx.asset, ctx,
            side=side, price=current_price, size=MIN_SHARES,
            attempt=attempt + 1, is_retry=is_retry)

        order_id = place_order_with_retry(token_id, current_price, MIN_SHARES)
        if not order_id:
            ctx.trade_attempts += 1
            ctx.state = MarketState.SKIPPED
            log_event("ORDER_FAILED", ctx.asset, ctx, reason="place_order_returned_none")
            return False

        ctx.state = MarketState.ORDER_PLACED
        ctx.order_id = order_id

        filled = wait_for_fill(order_id, timeout=FILL_TIMEOUT)
        if filled:
            ctx.trade_attempts += 1
            ctx.state = MarketState.HOLDING
            ctx.entered_side = side
            ctx.entered_price = current_price
            ctx.entered_size = MIN_SHARES
            ctx.entered_ts = int(time.time())
            ctx.order_id = None
            log_event("ORDER_FILLED", ctx.asset, ctx,
                side=side, price=current_price, size=MIN_SHARES)
            return True

        cancel_order(order_id)
        ctx.order_id = None
        log_event("ORDER_TIMEOUT", ctx.asset, ctx,
            side=side, price=current_price, attempt=attempt + 1)

        if attempt + 1 >= MAX_FILL_ATTEMPTS:
            ctx.trade_attempts += 1
            ctx.state = MarketState.SKIPPED
            return False

        best_ask = get_best_ask(token_id)
        if best_ask is None:
            ctx.trade_attempts += 1
            ctx.state = MarketState.SKIPPED
            return False

        current_price = max(0.01, min(MAX_PRICE, round(best_ask - 0.01, 2)))
        if current_price > original_price + MAX_RETRY_PRICE_DELTA:
            log_event("RETRY_PRICE_TOO_HIGH", ctx.asset, ctx,
                original_price=original_price, retry_price=current_price,
                delta=round(current_price - original_price, 2),
                max_delta=MAX_RETRY_PRICE_DELTA)
            ctx.trade_attempts += 1
            ctx.state = MarketState.SKIPPED
            return False
        if current_price < MIN_PRICE:
            ctx.trade_attempts += 1
            ctx.state = MarketState.SKIPPED
            return False

    return False


# ==============================================================================
# BLOCK 7: POSITION ENGINE (Flip Logic)
# ==============================================================================

def evaluate_flip(
    ctx: MarketContext,
    yes_price: float,
    no_price: float,
    time_left: int,
) -> Optional[dict]:
    """Avalia se deve executar flip direcional. Retorna dict ou None."""
    if not FLIP_ENABLED:
        return None
    if ctx.state != MarketState.HOLDING:
        return None
    if ctx.flip_executed:
        return None
    if time_left < FLIP_MIN_TIME_LEFT:
        return None
    if ctx.entered_side is None or ctx.entered_price is None or ctx.entered_size is None:
        return None

    # Preço atual do nosso lado
    our_price = yes_price if ctx.entered_side == "YES" else no_price
    if our_price >= FLIP_TRIGGER_PRICE:
        return None  # Ainda a nosso favor

    # Preço do lado oposto
    opp_price = no_price if ctx.entered_side == "YES" else yes_price
    if opp_price <= 0.01 or opp_price >= 0.99:
        return None  # Extremos impraticáveis

    # Formula: Q2 = ceil((Q1 * P1) / (1 - P2))
    q1 = ctx.entered_size
    p1 = ctx.entered_price
    denominator = 1.0 - opp_price
    if denominator <= 0.01:
        return None

    q2_raw = (q1 * p1) / denominator
    q2 = math.ceil(q2_raw)

    if q2 < FLIP_MIN_SHARES:
        return None

    # Segurança: limitar Q2 relativo a Q1
    if q2 > q1 * MAX_FLIP_RATIO:
        return None

    flip_side = "NO" if ctx.entered_side == "YES" else "YES"
    flip_token = ctx.no_token_id if ctx.entered_side == "YES" else ctx.yes_token_id

    return {
        "side": flip_side,
        "token_id": flip_token,
        "price": opp_price,
        "size": q2,
        "q2_raw": round(q2_raw, 2),
        "our_price": round(our_price, 4),
    }


def execute_flip(ctx: MarketContext, flip: dict) -> bool:
    """Executa ordem de flip (FOK taker). Retorna True se preenchido."""
    # Preço fresco do best_ask para o FOK
    best_ask = get_best_ask(flip["token_id"])
    if best_ask is None:
        best_ask = flip["price"]

    # Markup de 1 centavo para garantir fill
    flip_price = min(0.99, round(best_ask + 0.01, 2))

    order_id = place_flip_order(flip["token_id"], flip_price, flip["size"])
    if not order_id:
        log_event("FLIP_NOT_FILLED", ctx.asset, ctx,
            flip_side=flip["side"], flip_price=flip_price,
            flip_size=flip["size"], reason="order_rejected")
        return False

    # FOK = fill imediato, verificar
    filled = check_order_filled(order_id)
    if filled:
        ctx.flip_executed = True
        ctx.flip_side = flip["side"]
        ctx.flip_price = flip_price
        ctx.flip_size = flip["size"]
        ctx.flip_ts = int(time.time())
        ctx.flip_order_id = order_id
        log_event("FLIP_EXECUTED", ctx.asset, ctx,
            flip_side=flip["side"], flip_price=flip_price,
            flip_size=flip["size"], flip_order_id=order_id,
            q2_raw=flip["q2_raw"])
        return True
    else:
        log_event("FLIP_NOT_FILLED", ctx.asset, ctx,
            flip_side=flip["side"], flip_price=flip_price,
            flip_size=flip["size"], reason="FOK_not_matched")
        return False


def calc_pnl(ctx: MarketContext, outcome_winner: str) -> float:
    """Calcula PnL total (leg1 + leg2 se flip)."""
    if ctx.entered_price is None or ctx.entered_size is None:
        return 0.0

    # Leg 1: entrada original
    leg1_cost = ctx.entered_price * ctx.entered_size
    leg1_payout = float(ctx.entered_size) if ctx.entered_side == outcome_winner else 0.0

    # Leg 2: flip (se executado)
    leg2_cost = 0.0
    leg2_payout = 0.0
    if ctx.flip_executed and ctx.flip_price is not None and ctx.flip_size is not None:
        leg2_cost = ctx.flip_price * ctx.flip_size
        leg2_payout = float(ctx.flip_size) if ctx.flip_side == outcome_winner else 0.0

    total_pnl = (leg1_payout + leg2_payout) - (leg1_cost + leg2_cost)
    return round(total_pnl, 2)


def calc_live_pnl(ctx: MarketContext) -> tuple:
    """P&L em tempo real baseado no midpoint do lado posicionado.
    Retorna (side_now_price, is_winning, potential_pnl).
    """
    if (ctx.entered_side is None or ctx.entered_price is None
            or ctx.yes_price is None or ctx.no_price is None):
        return 0.0, False, 0.0
    side_now = ctx.yes_price if ctx.entered_side == "YES" else ctx.no_price
    shares = ctx.entered_size or MIN_SHARES
    winning = side_now > ctx.entered_price
    if winning:
        pnl = round((1.0 - ctx.entered_price) * shares, 2)
    else:
        pnl = round(-ctx.entered_price * shares, 2)
    return side_now, winning, pnl


# ==============================================================================
# BLOCK 8: LOGGER
# ==============================================================================

class _LogWriter:
    """File handle persistente com flush() a cada write."""

    def __init__(self):
        self._file = None
        self._date: Optional[str] = None

    def write(self, event: dict):
        today = datetime.now().strftime("%Y-%m-%d")
        if self._date != today or self._file is None:
            self.close()
            LOGS_DIR.mkdir(exist_ok=True)
            self._file = open(LOGS_DIR / f"bot_light_{today}.jsonl", "a", encoding="utf-8")
            self._date = today
        try:
            self._file.write(json.dumps(event) + "\n")
            self._file.flush()
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
    """Grava evento no log JSONL + exibe no console."""
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

    # Console
    time_str = datetime.now().strftime("%H:%M:%S")
    state_str = ctx.state.value.ljust(12)

    yp = ctx.yes_price
    np_ = ctx.no_price
    clob_str = f"yes={yp:.2f} no={np_:.2f}" if yp is not None and np_ is not None else ""

    # P&L em tempo real durante HOLDING
    pos_str = ""
    if ctx.state == MarketState.HOLDING and ctx.entered_side and ctx.entered_price is not None:
        side_now, winning, pnl = calc_live_pnl(ctx)
        label = "WINNING" if winning else "LOSING"
        pos_str = f"{ctx.entered_side}@{ctx.entered_price:.2f} now={side_now:.2f} {label} ${pnl:+.2f}"
        if ctx.flip_executed:
            pos_str += f" [FLIP {ctx.flip_side}@{ctx.flip_price:.2f}x{ctx.flip_size}]"

    if pos_str:
        print(f"[{time_str}] {asset.upper().ljust(4)} | {state_str} | {pos_str} | {clob_str} | {action}")
    else:
        extras_str = ""
        # Mostrar campos-chave de alguns eventos no console
        for k in ("side", "price", "size", "pnl", "reason", "outcome_winner", "flip_side"):
            if k in extra:
                extras_str += f" {k}={extra[k]}"
        print(f"[{time_str}] {asset.upper().ljust(4)} | {state_str} | {clob_str} | {action}{extras_str}")


# ==============================================================================
# BLOCK 9: MAIN LOOP
# ==============================================================================

# Sessão PnL
_session_pnl = 0.0
_session_trades = 0
_session_wins = 0
_holding_tick_counter: dict = {}


def main():
    global _running, _session_pnl, _session_trades, _session_wins

    print("=" * 60)
    print("BOT LIGHT - POLYMARKET")
    print(f"MERCADOS: {', '.join(a.upper() for a in ASSETS)}")
    print(f"JANELA: {ENTRY_WINDOW_START}s a {ENTRY_WINDOW_END}s antes da expiracao")
    print(f"RANGE: {MIN_PRICE*100:.0f}% a {MAX_PRICE*100:.0f}%")
    print(f"SHARES: {MIN_SHARES}")
    print(f"FLIP: {'ON' if FLIP_ENABLED else 'OFF'} (trigger={FLIP_TRIGGER_PRICE:.2f}, min_time={FLIP_MIN_TIME_LEFT}s, max_ratio={MAX_FLIP_RATIO}x)")
    print(f"POLL: {POLL_INTERVAL}s")
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

    # Signal handlers
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # Contextos por asset
    contexts = {asset: MarketContext(asset=asset) for asset in ASSETS}

    print("Iniciando loop principal... (Ctrl+C para parar)")
    print()

    while _running:
        now = int(time.time())

        # -- Resolver resultados pendentes --
        if _pending_results:
            resolved_keys = []
            for key, pdata in list(_pending_results.items()):
                age = now - pdata.get("added_ts", now)
                if age > 300:
                    tmp_ctx = MarketContext(asset=pdata["asset"])
                    tmp_ctx.cycle_end_ts = pdata["cycle_end_ts"]
                    log_event("POSITION_RESULT_UNKNOWN", pdata["asset"], tmp_ctx,
                        side=pdata["side"],
                        entry_price=pdata["entry_price"],
                        size=pdata["size"],
                        flip_executed=pdata.get("flip_executed", False),
                        age_s=age,
                        reason="max_pending_age_exceeded")
                    resolved_keys.append(key)
                    continue
                outcome = _get_resolved_outcome(pdata["asset"], pdata["cycle_end_ts"], retries=3, delay=2.0)
                if outcome is not None:
                    # Recalcular PnL
                    tmp_ctx = MarketContext(asset=pdata["asset"])
                    tmp_ctx.cycle_end_ts = pdata["cycle_end_ts"]
                    tmp_ctx.entered_side = pdata["side"]
                    tmp_ctx.entered_price = pdata["entry_price"]
                    tmp_ctx.entered_size = pdata["size"]
                    tmp_ctx.flip_executed = pdata.get("flip_executed", False)
                    tmp_ctx.flip_side = pdata.get("flip_side")
                    tmp_ctx.flip_price = pdata.get("flip_price")
                    tmp_ctx.flip_size = pdata.get("flip_size")
                    pnl = calc_pnl(tmp_ctx, outcome)
                    win = pnl > 0
                    _session_pnl += pnl
                    _session_trades += 1
                    if win:
                        _session_wins += 1
                    log_event("POSITION_RESULT_RESOLVED", pdata["asset"], tmp_ctx,
                        outcome_winner=outcome,
                        side=pdata["side"],
                        entry_price=pdata["entry_price"],
                        size=pdata["size"],
                        win=win,
                        pnl=pnl,
                        flip_executed=pdata.get("flip_executed", False))
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
            time_left = end_ts - now
            yes_price = market["yes_price"]
            no_price = market["no_price"]

            # Atualizar contexto
            ctx.yes_token_id = market["yes_token"]
            ctx.no_token_id = market["no_token"]
            ctx.yes_price = round(yes_price, 4)
            ctx.no_price = round(no_price, 4)

            # 2. Detectar novo ciclo
            if ctx.cycle_end_ts != end_ts:
                old_cycle = ctx.cycle_end_ts

                # Resolver posição do ciclo anterior
                if (ctx.state == MarketState.HOLDING
                        and ctx.entered_side
                        and ctx.entered_price is not None
                        and old_cycle is not None):
                    outcome_winner = _get_resolved_outcome(asset, old_cycle)
                    if outcome_winner is not None:
                        pnl = calc_pnl(ctx, outcome_winner)
                        win = pnl > 0
                        _session_pnl += pnl
                        _session_trades += 1
                        if win:
                            _session_wins += 1
                        log_event("POSITION_RESULT", asset, ctx,
                            outcome_winner=outcome_winner,
                            side=ctx.entered_side,
                            entry_price=ctx.entered_price,
                            size=ctx.entered_size,
                            win=win,
                            pnl=pnl,
                            flip_executed=ctx.flip_executed,
                            flip_side=ctx.flip_side,
                            flip_price=ctx.flip_price,
                            flip_size=ctx.flip_size)
                    else:
                        pending_key = f"{asset}:{old_cycle}"
                        _pending_results[pending_key] = {
                            "asset": asset,
                            "cycle_end_ts": old_cycle,
                            "side": ctx.entered_side,
                            "entry_price": ctx.entered_price,
                            "size": ctx.entered_size,
                            "flip_executed": ctx.flip_executed,
                            "flip_side": ctx.flip_side,
                            "flip_price": ctx.flip_price,
                            "flip_size": ctx.flip_size,
                            "added_ts": now,
                        }
                        log_event("POSITION_RESULT_PENDING", asset, ctx,
                            side=ctx.entered_side,
                            entry_price=ctx.entered_price,
                            size=ctx.entered_size,
                            reason="outcome_not_resolved_after_retries")

                reset_context(ctx)
                ctx.cycle_end_ts = end_ts
                _holding_tick_counter[asset] = 0
                log_event("NEW_CYCLE", asset, ctx, end_ts=end_ts, title=market["title"])

            # 3. Expirou?
            if time_left <= 0:
                if ctx.state not in (MarketState.DONE, MarketState.SKIPPED):
                    ctx.state = MarketState.DONE
                    log_event("EXPIRED", asset, ctx)
                continue

            # 4. Hard stop < 60s
            if time_left < ENTRY_WINDOW_END:
                if ctx.state == MarketState.ORDER_PLACED:
                    cancel_order(ctx.order_id)
                    ctx.state = MarketState.SKIPPED
                    log_event("CANCEL_HARD_STOP", asset, ctx, time_left=time_left)
                elif ctx.state == MarketState.IDLE:
                    ctx.state = MarketState.SKIPPED
                continue

            # 5. Já finalizado?
            if ctx.state in (MarketState.DONE, MarketState.SKIPPED):
                continue

            # 6. HOLDING -> flip check
            if ctx.state == MarketState.HOLDING:
                # Log holding tick periódico (a cada 10 polls)
                _holding_tick_counter[asset] = _holding_tick_counter.get(asset, 0) + 1
                if _holding_tick_counter[asset] % 10 == 0:
                    log_event("HOLDING_TICK", asset, ctx, time_left=time_left)

                if FLIP_ENABLED and not ctx.flip_executed:
                    flip = evaluate_flip(ctx, yes_price, no_price, time_left)
                    if flip is not None:
                        # Verificar saldo
                        flip_cost = flip["size"] * flip["price"]
                        balance = get_usdc_balance()
                        log_event("FLIP_SIGNAL", asset, ctx,
                            our_price=flip["our_price"],
                            trigger_price=FLIP_TRIGGER_PRICE,
                            opp_price=flip["price"],
                            q1=ctx.entered_size, p1=ctx.entered_price,
                            q2=flip["size"], q2_raw=flip["q2_raw"],
                            flip_cost=round(flip_cost, 2),
                            time_left=time_left)

                        if balance is not None and balance < flip_cost:
                            log_event("FLIP_SKIPPED", asset, ctx,
                                reason="insufficient_balance",
                                balance=round(balance, 2),
                                needed=round(flip_cost, 2))
                        else:
                            execute_flip(ctx, flip)

                continue  # HOLDING nao entra na logica de entrada

            # 7. Fora da janela?
            if time_left > ENTRY_WINDOW_START:
                continue

            # 8. Já tentou trade?
            if ctx.trade_attempts >= 1:
                continue

            # 9. Avaliar entrada
            entry = evaluate_entry(ctx, yes_price, no_price, time_left)
            if entry is None:
                log_event("SKIP_PRICE_OOR", asset, ctx,
                    yes_price=round(yes_price, 2),
                    no_price=round(no_price, 2),
                    time_left=time_left)
                continue

            # 10. Verificar saldo
            balance = get_usdc_balance()
            if balance is not None and balance < MIN_BALANCE_USDC:
                log_event("SKIP_INSUFFICIENT_BALANCE", asset, ctx,
                    balance=round(balance, 2), required=MIN_BALANCE_USDC)
                continue

            # 11. Executar entrada
            log_event("ENTRY_SIGNAL", asset, ctx,
                side=entry["side"], price=entry["price"],
                size=entry["size"], time_left=time_left)
            execute_entry(ctx, entry)

        # Aguardar próximo ciclo
        time.sleep(POLL_INTERVAL)

    # -- Shutdown --
    _shutdown(contexts)


# ==============================================================================
# BLOCK 10: FINALIZATION
# ==============================================================================

def _signal_handler(signum, frame):
    """Graceful shutdown."""
    global _running
    print(f"\n[SHUTDOWN] Sinal {signum} recebido, flushing logs...")
    _running = False


atexit.register(_log_writer.close)


def _shutdown(contexts: dict):
    """Cleanup: cancelar ordens + resumo da sessão."""
    print()
    print("[SHUTDOWN] Cancelando ordens abertas...")
    for asset, ctx in contexts.items():
        if ctx.state == MarketState.ORDER_PLACED and ctx.order_id:
            cancel_order(ctx.order_id)
            log_event("SHUTDOWN_CANCEL", asset, ctx)

    print()
    print("=" * 60)
    print("SESSION SUMMARY")
    print(f"  Trades: {_session_trades}")
    print(f"  Wins:   {_session_wins}")
    if _session_trades > 0:
        print(f"  Win%:   {(_session_wins/_session_trades*100):.1f}%")
    else:
        print(f"  Win%:   N/A")
    print(f"  PnL:    ${_session_pnl:+.2f}")
    print("=" * 60)

    _log_writer.close()
    if _http:
        _http.close()

    print("[SHUTDOWN] Bot encerrado.")


if __name__ == "__main__":
    main()
