#!/usr/bin/env python3
"""
Bot Market-Maker BTC 15min — Polymarket.

Estratégia:
- Detecta ciclos de 15min (BTC only)
- Entra quando YES ou NO entre 93%-96% (POST_ONLY maker)
- Até 3 entradas de 5 shares cada (max 15 shares)
- Ladder de saída: SELL POST_ONLY a +1/+2/+3 ticks acima do avg_price
- Stop-loss: prob < 40% → market sell tudo (SELL FOK)
- Hard exit < 60s: spread bom → market sell, spread ruim → hold to resolution
- GuardrailsPro ativo para filtro de entrada

Diferença vs bots existentes:
  Bots normais: compra e segura até resolução
  Este bot: compra e tenta vender com lucro ANTES da resolução via ladder

USO:
    python scripts/botmmbtc15min.py
    python scripts/botmmbtc15min.py --dry-run
    python scripts/botmmbtc15min.py --once
"""

import argparse
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

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv:
    load_dotenv(Path(__file__).parent.parent / ".env")

from guardrails import GuardrailsPro, GuardrailAction

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

# Timing
WINDOW_SECONDS = 900           # 15 minutos
POLL_SECONDS = 0.5             # Loop 500ms (mais rápido para MM)
ENTRY_WINDOW_START = 240       # 4 min antes da expiração
ENTRY_WINDOW_END = 60          # 1 min hard stop

# Entrada
MIN_PRICE = 0.93               # Mínimo para entrada
MAX_PRICE = 0.96               # Máximo para entrada (garante 3 ticks de ladder)
ENTRY_SIZE = 5                 # Shares por entrada
MAX_SHARES = 15                # Posição máxima (3 × 5)
MAX_FILLS = 3                  # Máximo de fills por ciclo
MIN_BALANCE_USDC = 15.5        # Saldo mínimo (15 shares @ 0.96 + margem)

# Ordens
ORDER_TTL = 10                 # Cancelar entry não preenchida após 10s
MAX_CANCELS = 5                # Max cancels consecutivos → abortar
TICK_SIZE = 0.01
CANCEL_RETRY = 3
POST_CANCEL_COOLDOWN = 0.5

# Ladder de saída
LADDER_MAX_PRICE = 0.99       # Cap do CLOB

# Stop-loss
STOP_PROB = float(os.getenv("BL_STOP_PROB", "0.40"))

# Hard exit (<60s)
HARD_EXIT_SPREAD_MAX = 0.02   # Spread ≤ 2 ticks → market sell
HARD_EXIT_MIN_BID_SIZE = 5    # Liquidez mínima no bid

# Asset
ASSET = "btc"

# Logs
LOGS_DIR = Path(__file__).parent.parent / "logs"


# ==============================================================================
# ESTADOS E CONTEXTO
# ==============================================================================

class MMState(Enum):
    IDLE = "IDLE"
    ENTRY_PENDING = "ENTRY_PENDING"
    ACTIVE = "ACTIVE"
    STOP_SELLING = "STOP_SELLING"
    HOLD_TO_RESOLUTION = "HOLD_TO_RESOLUTION"
    DONE = "DONE"


@dataclass
class MMContext:
    asset: str = "btc"
    cycle_end_ts: Optional[int] = None
    state: MMState = MMState.IDLE

    # Tokens
    yes_token_id: Optional[str] = None
    no_token_id: Optional[str] = None
    yes_price: Optional[float] = None
    no_price: Optional[float] = None

    # Posição acumulada
    active_side: Optional[str] = None
    active_token_id: Optional[str] = None
    position_shares: float = 0
    position_avg_price: float = 0
    fills_count: int = 0

    # Ordem de entrada atual
    entry_order_id: Optional[str] = None
    entry_order_ts: Optional[float] = None
    entry_order_price: Optional[float] = None
    consecutive_cancels: int = 0

    # Ladder de saída
    ladder_orders: list = field(default_factory=list)
    ladder_realized_pnl: float = 0
    ladder_shares_sold: float = 0

    # Stop / resultado
    stop_executed: bool = False
    stop_pnl: Optional[float] = None
    hold_pnl: Optional[float] = None

    # Flags
    aborted: bool = False


# ==============================================================================
# CLIENTE GLOBAL
# ==============================================================================

_client = None  # type: ignore
_http: Optional[httpx.Client] = None
_running = True
_usdc_balance_cache: Optional[float] = None
_usdc_balance_cache_ts: float = 0
BALANCE_CACHE_TTL = 60


def get_client():  # type: ignore[return]
    global _client
    if _client is None:
        pk = os.getenv("POLYMARKET_PRIVATE_KEY", "")
        funder = os.getenv("POLYMARKET_FUNDER", "")
        if not pk or not funder:
            raise RuntimeError("Configure POLYMARKET_PRIVATE_KEY e POLYMARKET_FUNDER")
        if not pk.startswith("0x"):
            pk = f"0x{pk}"
        _client = ClobClient(CLOB_HOST, chain_id=CHAIN_ID, key=pk, signature_type=1, funder=funder)
        api_key = os.getenv("POLYMARKET_API_KEY", "")
        api_secret = os.getenv("POLYMARKET_API_SECRET", "")
        api_pass = os.getenv("POLYMARKET_PASSPHRASE", "")
        if api_key and api_secret and api_pass:
            _client.set_api_creds(ApiCreds(api_key, api_secret, api_pass))
        else:
            _client.set_api_creds(_client.create_or_derive_api_creds())
    return _client


def get_http() -> httpx.Client:
    global _http
    if _http is None:
        _http = httpx.Client(timeout=30)
    return _http


# ==============================================================================
# LOGGING
# ==============================================================================

class LogWriter:
    def __init__(self):
        self._file = None
        self._date: Optional[str] = None

    def write(self, event: dict):
        today = datetime.now().strftime("%Y-%m-%d")
        if self._date != today or self._file is None:
            self.close()
            LOGS_DIR.mkdir(exist_ok=True)
            self._file = open(LOGS_DIR / f"botmm_btc15min_{today}.jsonl", "a", encoding="utf-8")
            self._date = today
        try:
            self._file.write(json.dumps(event, ensure_ascii=False) + "\n")
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


_log_writer = LogWriter()


def log_event(action: str, ctx: MMContext, **extra):
    now = int(time.time())
    event = {
        "ts": now,
        "ts_iso": datetime.now().isoformat(),
        "market": ctx.asset,
        "cycle_end_ts": ctx.cycle_end_ts,
        "state": ctx.state.value,
        "action": action,
        "side": ctx.active_side,
        "position_shares": ctx.position_shares,
        "avg_price": ctx.position_avg_price,
        "fills": ctx.fills_count,
        "ladder_pnl": ctx.ladder_realized_pnl,
        "yes_price": ctx.yes_price,
        "no_price": ctx.no_price,
        **extra,
    }
    _log_writer.write(event)

    # Console
    time_str = datetime.now().strftime("%H:%M:%S")
    state_str = ctx.state.value.ljust(18)
    pos_str = ""
    if ctx.position_shares > 0 and ctx.active_side:
        our_price = ctx.yes_price if ctx.active_side == "YES" else ctx.no_price
        if our_price is not None and ctx.position_avg_price > 0:
            unrealized = round((our_price - ctx.position_avg_price) * ctx.position_shares, 2)
            total_pnl = round(ctx.ladder_realized_pnl + unrealized, 2)
            pos_str = (f"{ctx.active_side}@{ctx.position_avg_price:.2f} "
                       f"pos={ctx.position_shares:.0f} "
                       f"now={our_price:.2f} "
                       f"uPnL=${unrealized:+.2f} "
                       f"lPnL=${ctx.ladder_realized_pnl:+.2f} "
                       f"tPnL=${total_pnl:+.2f}")

    yp = ctx.yes_price
    np_ = ctx.no_price
    clob_str = f"yes={yp:.2f} no={np_:.2f}" if yp is not None and np_ is not None else ""

    if pos_str:
        print(f"[{time_str}] BTC  | {state_str} | {pos_str} | {action} {extra if extra else ''}")
    else:
        print(f"[{time_str}] BTC  | {state_str} | {clob_str} | {action} {extra if extra else ''}")


# ==============================================================================
# FUNÇÕES DE MERCADO
# ==============================================================================

def _float_price(v) -> Optional[float]:
    if v is None:
        return None
    try:
        p = float(v)
        return p if 0 <= p <= 1 else None
    except (TypeError, ValueError):
        return None


def get_best_price(token_id: str) -> Optional[float]:
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


def get_best_bid(token_id: str) -> Optional[float]:
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


def get_best_ask(token_id: str) -> Optional[float]:
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


def get_book_snapshot(token_id: str) -> Optional[dict]:
    """Busca orderbook completo — usado para hard exit decision."""
    try:
        http = get_http()
        r = http.get(f"{CLOB_HOST.rstrip('/')}/book", params={"token_id": token_id})
        if r.status_code == 200:
            book = r.json()
            bids = book.get("bids", [])
            asks = book.get("asks", [])
            best_bid = _float_price(bids[0].get("price") or bids[0].get("p")) if bids else None
            best_ask = _float_price(asks[0].get("price") or asks[0].get("p")) if asks else None
            bid_top_size = float(bids[0].get("size") or bids[0].get("s") or 0) if bids else 0
            ask_top_size = float(asks[0].get("size") or asks[0].get("s") or 0) if asks else 0
            spread = round(best_ask - best_bid, 4) if best_bid and best_ask else None
            return {
                "best_bid": best_bid,
                "best_ask": best_ask,
                "bid_top_size": bid_top_size,
                "ask_top_size": ask_top_size,
                "spread": spread,
            }
    except Exception:
        pass
    return None


def fetch_market_status() -> Optional[dict]:
    """Busca status do mercado BTC 15min."""
    try:
        http = get_http()
        now = int(time.time())
        current_window = int(now // WINDOW_SECONDS) * WINDOW_SECONDS
        for window_ts in [current_window, current_window - WINDOW_SECONDS]:
            slug = f"{ASSET}-updown-15m-{window_ts}"
            result = _fetch_market_by_slug(http, slug)
            if result:
                end_ts = result["end_ts"]
                time_to_expiry = end_ts - now
                if time_to_expiry > -60:
                    return result
        return None
    except Exception as e:
        print(f"[ERRO] fetch_market_status: {e}")
        return None


def _fetch_market_by_slug(http, slug: str) -> Optional[dict]:
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
            end_ts = int(slug.split("-")[-1]) + WINDOW_SECONDS
        yes_price = get_best_price(yes_token)
        no_price = get_best_price(no_token)
        if yes_price is None or no_price is None:
            return None
        return {
            "asset": ASSET,
            "slug": slug,
            "end_ts": end_ts,
            "yes_token": yes_token,
            "no_token": no_token,
            "yes_price": float(yes_price),
            "no_price": float(no_price),
            "title": event.get("title", slug),
        }
    except Exception:
        return None


def _get_resolved_outcome(cycle_end_ts: int, retries: int = 3, delay: float = 3.0) -> Optional[str]:
    http = get_http()
    window_start = cycle_end_ts - WINDOW_SECONDS
    slug = f"{ASSET}-updown-15m-{window_start}"
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
        except Exception:
            if attempt < retries - 1:
                time.sleep(delay)
    return None


# ==============================================================================
# SALDO USDC
# ==============================================================================

def get_usdc_balance() -> Optional[float]:
    global _usdc_balance_cache, _usdc_balance_cache_ts
    now = time.time()
    if _usdc_balance_cache is not None and (now - _usdc_balance_cache_ts) < BALANCE_CACHE_TTL:
        return _usdc_balance_cache
    try:
        client = get_client()
        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=1)
        result = client.get_balance_allowance(params)
        if isinstance(result, dict):
            raw_balance = result.get("balance", "0")
            bal = int(raw_balance) / 10**6
            _usdc_balance_cache = bal
            _usdc_balance_cache_ts = now
            return bal
    except Exception:
        pass
    return None


# ==============================================================================
# FUNÇÕES DE ORDEM
# ==============================================================================

def place_order(token_id: str, price: float, size: float) -> Optional[str]:
    """BUY LIMIT POST_ONLY GTC (maker entry)."""
    try:
        client = get_client()
        order_args = OrderArgs(token_id=token_id, price=price, size=size, side=BUY)
        signed_order = client.create_order(order_args)
        resp = client.post_order(signed_order, OrderType.GTC, post_only=True)
        if resp.get("success"):
            return resp.get("orderID")
        else:
            print(f"[ERRO] place_order: {resp}")
    except Exception as e:
        print(f"[ERRO] place_order: {e}")
    return None


def place_sell_limit(token_id: str, price: float, size: float) -> Optional[str]:
    """SELL LIMIT POST_ONLY GTC (maker ladder exit)."""
    try:
        client = get_client()
        order_args = OrderArgs(token_id=token_id, price=price, size=size, side=SELL)
        signed_order = client.create_order(order_args)
        resp = client.post_order(signed_order, OrderType.GTC, post_only=True)
        if resp.get("success"):
            return resp.get("orderID")
        else:
            print(f"[ERRO] place_sell_limit: {resp}")
    except Exception as e:
        print(f"[ERRO] place_sell_limit: {e}")
    return None


def place_sell_market(token_id: str, size: float) -> Optional[str]:
    """SELL FOK (taker market sell — stop-loss / hard exit)."""
    try:
        client = get_client()
        order_args = OrderArgs(token_id=token_id, price=0.01, size=size, side=SELL)
        signed_order = client.create_order(order_args)
        resp = client.post_order(signed_order, OrderType.FOK, post_only=False)
        if resp.get("success"):
            return resp.get("orderID")
        else:
            print(f"[ERRO] place_sell_market: {resp}")
    except Exception as e:
        print(f"[ERRO] place_sell_market: {e}")
    return None


def cancel_order(order_id: str) -> bool:
    try:
        client = get_client()
        resp = client.cancel(order_id)
        return resp.get("canceled", False) or resp.get("success", False)
    except Exception as e:
        print(f"[ERRO] cancel_order: {e}")
        return False


def check_order_filled(order_id: str) -> Optional[float]:
    """Retorna size_matched (float) ou None se erro. 0 = não preenchido."""
    try:
        client = get_client()
        order = client.get_order(order_id)
        if order:
            return float(order.get("size_matched", 0))
    except Exception as e:
        print(f"[ERRO] check_order_filled: {e}")
    return None


# ==============================================================================
# LADDER MANAGEMENT
# ==============================================================================

def build_ladder(ctx: MMContext) -> list:
    """Calcula níveis de venda baseado na posição atual.

    Regra: ENTRY_SIZE shares por nível, a avg_price + N ticks.
    - 5 shares → 1 nível  (+1 tick)
    - 10 shares → 2 níveis (+1, +2 ticks)
    - 15 shares → 3 níveis (+1, +2, +3 ticks)
    """
    if ctx.position_shares <= 0 or ctx.position_avg_price <= 0:
        return []

    remaining = ctx.position_shares
    levels = []
    tick_offset = 1

    while remaining > 0 and tick_offset <= 3:
        sell_price = round(ctx.position_avg_price + tick_offset * TICK_SIZE, 2)
        if sell_price > LADDER_MAX_PRICE:
            break
        size = min(ENTRY_SIZE, remaining)
        levels.append({"price": sell_price, "size": size})
        remaining -= size
        tick_offset += 1

    # Se sobrou shares (preço alto, poucos níveis disponíveis), colocar tudo no último nível
    if remaining > 0 and levels:
        levels[-1]["size"] += remaining

    return levels


def place_ladder(ctx: MMContext, levels: list, dry_run: bool = False):
    """Coloca ordens SELL POST_ONLY para cada nível do ladder."""
    if not ctx.active_token_id:
        return
    for level in levels:
        if dry_run:
            log_event("LADDER_DRY_RUN", ctx, price=level["price"], size=level["size"])
            ctx.ladder_orders.append({"order_id": "dry_run", "price": level["price"], "size": level["size"]})
            continue
        order_id = place_sell_limit(ctx.active_token_id, level["price"], level["size"])
        if order_id:
            ctx.ladder_orders.append({"order_id": order_id, "price": level["price"], "size": level["size"]})
            log_event("LADDER_PLACED", ctx, order_id=order_id, price=level["price"], size=level["size"])
        else:
            log_event("LADDER_PLACE_FAIL", ctx, price=level["price"], size=level["size"])
        time.sleep(0.1)  # Rate limit entre ordens


def cancel_all_ladder(ctx: MMContext):
    """Cancela todas as ordens do ladder."""
    for lo in ctx.ladder_orders:
        oid = lo.get("order_id")
        if oid and oid != "dry_run":
            for attempt in range(CANCEL_RETRY):
                if cancel_order(oid):
                    break
                time.sleep(POST_CANCEL_COOLDOWN)
    ctx.ladder_orders.clear()


def check_ladder_fills(ctx: MMContext) -> bool:
    """Verifica fills no ladder. Retorna True se houve mudança."""
    changed = False
    still_open = []

    for lo in ctx.ladder_orders:
        oid = lo.get("order_id")
        if oid == "dry_run":
            still_open.append(lo)
            continue
        filled = check_order_filled(oid)
        if filled is None:
            still_open.append(lo)  # Erro de API, manter
            continue
        if filled > 0:
            # Ordem preenchida!
            pnl = round((lo["price"] - ctx.position_avg_price) * filled, 4)
            ctx.ladder_realized_pnl += pnl
            ctx.ladder_shares_sold += filled
            ctx.position_shares = max(0, ctx.position_shares - filled)
            log_event("LADDER_FILLED", ctx,
                      order_id=oid, price=lo["price"], size=filled, pnl=pnl)
            changed = True
        else:
            still_open.append(lo)

    ctx.ladder_orders = still_open
    return changed


def rebuild_ladder(ctx: MMContext, dry_run: bool = False):
    """Cancela ladder atual e reconstroi baseado na posição."""
    cancel_all_ladder(ctx)
    time.sleep(POST_CANCEL_COOLDOWN)
    levels = build_ladder(ctx)
    if levels:
        place_ladder(ctx, levels, dry_run=dry_run)
        log_event("LADDER_REBUILT", ctx, levels=len(levels))


# ==============================================================================
# STOP-LOSS & HARD EXIT
# ==============================================================================

def market_sell_all(ctx: MMContext, reason: str) -> bool:
    """Cancela tudo e vende posição inteira a mercado."""
    # 1. Cancelar ladder
    cancel_all_ladder(ctx)

    # 2. Cancelar entry pendente
    if ctx.entry_order_id:
        cancel_order(ctx.entry_order_id)
        ctx.entry_order_id = None

    # 3. Vender tudo
    if ctx.position_shares <= 0 or not ctx.active_token_id:
        return False

    best_bid = get_best_bid(ctx.active_token_id)
    order_id = place_sell_market(ctx.active_token_id, ctx.position_shares)
    if not order_id:
        log_event("MARKET_SELL_FAIL", ctx, reason=reason)
        return False

    filled = check_order_filled(order_id)
    if filled and filled > 0:
        exec_price = best_bid or 0
        sell_pnl = round((exec_price - ctx.position_avg_price) * filled, 4)
        total_pnl = round(ctx.ladder_realized_pnl + sell_pnl, 4)
        ctx.stop_executed = True
        ctx.stop_pnl = total_pnl
        ctx.position_shares = max(0, ctx.position_shares - filled)
        log_event("MARKET_SELL_DONE", ctx,
                  order_id=order_id, exec_price=exec_price,
                  sell_pnl=sell_pnl, total_pnl=total_pnl, reason=reason)
        return True
    else:
        log_event("MARKET_SELL_NOT_FILLED", ctx, reason="FOK_not_matched")
        return False


def evaluate_hard_exit(ctx: MMContext) -> str:
    """Avalia se deve fazer market sell ou hold to resolution."""
    if not ctx.active_token_id:
        return "HOLD"
    snap = get_book_snapshot(ctx.active_token_id)
    if not snap or snap["spread"] is None:
        return "HOLD"
    if snap["spread"] <= HARD_EXIT_SPREAD_MAX and snap["bid_top_size"] >= HARD_EXIT_MIN_BID_SIZE:
        return "MARKET_SELL"
    return "HOLD"


# ==============================================================================
# POSIÇÃO
# ==============================================================================

def update_position(ctx: MMContext, fill_price: float, fill_size: float):
    """Atualiza posição com novo fill (preço médio ponderado)."""
    old_total = ctx.position_shares * ctx.position_avg_price
    new_total = fill_size * fill_price
    ctx.position_shares += fill_size
    if ctx.position_shares > 0:
        ctx.position_avg_price = round((old_total + new_total) / ctx.position_shares, 4)
    ctx.fills_count += 1


def choose_side(yes_price: float, no_price: float) -> Optional[tuple]:
    """Escolhe lado para entrar. Retorna (side, price) ou None."""
    yes_ok = MIN_PRICE <= yes_price <= MAX_PRICE
    no_ok = MIN_PRICE <= no_price <= MAX_PRICE

    if yes_ok and no_ok:
        # Ambos no range: escolher o com mais espaço para ladder (menor preço = mais ticks)
        return ("YES", yes_price) if yes_price <= no_price else ("NO", no_price)
    elif yes_ok:
        return ("YES", yes_price)
    elif no_ok:
        return ("NO", no_price)
    return None


# ==============================================================================
# RESET
# ==============================================================================

def reset_context(ctx: MMContext):
    ctx.state = MMState.IDLE
    ctx.yes_token_id = None
    ctx.no_token_id = None
    ctx.yes_price = None
    ctx.no_price = None
    ctx.active_side = None
    ctx.active_token_id = None
    ctx.position_shares = 0
    ctx.position_avg_price = 0
    ctx.fills_count = 0
    ctx.entry_order_id = None
    ctx.entry_order_ts = None
    ctx.entry_order_price = None
    ctx.consecutive_cancels = 0
    ctx.ladder_orders.clear()
    ctx.ladder_realized_pnl = 0
    ctx.ladder_shares_sold = 0
    ctx.stop_executed = False
    ctx.stop_pnl = None
    ctx.hold_pnl = None
    ctx.aborted = False


# ==============================================================================
# SIGNAL HANDLER
# ==============================================================================

def signal_handler(signum, frame):
    global _running
    print(f"\n[SHUTDOWN] Sinal {signum} recebido...")
    _running = False
    _log_writer.close()


import atexit
atexit.register(_log_writer.close)


# ==============================================================================
# LOOP PRINCIPAL
# ==============================================================================

def main():
    global _running

    parser = argparse.ArgumentParser(description="Bot MM BTC 15min Polymarket")
    parser.add_argument("--dry-run", action="store_true", help="Sem ordens reais.")
    parser.add_argument("--once", action="store_true", help="Executa uma iteração e encerra.")
    args = parser.parse_args()
    dry_run = bool(args.dry_run or os.getenv("DRY_RUN", "").strip() == "1")

    print("=" * 60)
    print("BOT MARKET-MAKER BTC 15MIN - POLYMARKET")
    print(f"ENTRADA: {MIN_PRICE*100:.0f}%-{MAX_PRICE*100:.0f}% | {ENTRY_SIZE} shares × {MAX_FILLS} fills = {MAX_SHARES} max")
    print(f"LADDER: +1/+2/+3 ticks (cap {LADDER_MAX_PRICE})")
    print(f"STOP-LOSS: prob < {STOP_PROB*100:.0f}%")
    print(f"HARD EXIT: spread ≤ {HARD_EXIT_SPREAD_MAX} e bid ≥ {HARD_EXIT_MIN_BID_SIZE}")
    print(f"JANELA: {ENTRY_WINDOW_START}s a {ENTRY_WINDOW_END}s | ORDER_TTL={ORDER_TTL}s")
    if dry_run:
        print("*** DRY-RUN MODE ***")
    print("=" * 60)
    print()

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

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    ctx = MMContext(asset=ASSET)
    guardrail = GuardrailsPro(asset=ASSET)

    print("Iniciando loop principal... (Ctrl+C para parar)")
    print()

    while _running:
        now = time.time()
        now_int = int(now)

        # ── 1. Fetch market
        market = fetch_market_status()
        if not market:
            time.sleep(POLL_SECONDS)
            continue

        end_ts = market["end_ts"]
        time_remaining = end_ts - now_int
        yes_price = market["yes_price"]
        no_price = market["no_price"]
        guardrail.update(now, yes_price, no_price)

        ctx.yes_token_id = market["yes_token"]
        ctx.no_token_id = market["no_token"]
        ctx.yes_price = round(yes_price, 4)
        ctx.no_price = round(no_price, 4)

        # ── 2. Detectar novo ciclo → reset
        if ctx.cycle_end_ts != end_ts:
            old_cycle = ctx.cycle_end_ts

            # Resolver resultado do ciclo anterior
            if old_cycle is not None and ctx.position_shares > 0 and ctx.active_side:
                outcome = _get_resolved_outcome(old_cycle, retries=5, delay=5.0)
                if ctx.stop_executed and ctx.stop_pnl is not None:
                    log_event("CYCLE_RESULT", ctx, outcome=outcome or "N/A",
                              pnl=ctx.stop_pnl, method="stop/market_sell")
                elif ctx.hold_pnl is not None:
                    log_event("CYCLE_RESULT", ctx, outcome=outcome or "N/A",
                              pnl=ctx.hold_pnl, method="hold_to_resolution")
                elif outcome:
                    win = ctx.active_side == outcome
                    hold_shares = ctx.position_shares
                    hold_pnl = ((1.0 - ctx.position_avg_price) * hold_shares if win
                                else -ctx.position_avg_price * hold_shares)
                    total_pnl = round(ctx.ladder_realized_pnl + hold_pnl, 4)
                    log_event("CYCLE_RESULT", ctx, outcome=outcome, win=win,
                              hold_pnl=round(hold_pnl, 4), ladder_pnl=ctx.ladder_realized_pnl,
                              total_pnl=total_pnl, method="resolution")
                else:
                    log_event("CYCLE_RESULT_UNKNOWN", ctx, reason="outcome_not_resolved")

            # Cancelar ordens abertas do ciclo anterior
            if ctx.ladder_orders:
                cancel_all_ladder(ctx)
            if ctx.entry_order_id:
                cancel_order(ctx.entry_order_id)

            reset_context(ctx)
            guardrail.reset()
            ctx.cycle_end_ts = end_ts
            log_event("NEW_CYCLE", ctx, end_ts=end_ts, title=market["title"])

        # ── 3. Estado DONE → skip
        if ctx.state == MMState.DONE:
            time.sleep(POLL_SECONDS)
            continue

        # ── 4. RISK CHECK — stop-loss (qualquer estado com posição)
        if ctx.position_shares > 0 and ctx.active_side:
            our_prob = yes_price if ctx.active_side == "YES" else no_price
            if our_prob < STOP_PROB:
                log_event("STOP_TRIGGERED", ctx, our_prob=round(our_prob, 4), trigger=STOP_PROB)
                if not dry_run:
                    market_sell_all(ctx, reason="prob_below_stop")
                else:
                    log_event("STOP_DRY_RUN", ctx)
                ctx.state = MMState.DONE
                time.sleep(POLL_SECONDS)
                continue

        # ── 5. TIME CHECK — expirado
        if time_remaining <= 0:
            if ctx.state not in (MMState.DONE,):
                # Resolver posição mantida
                if ctx.position_shares > 0:
                    outcome = _get_resolved_outcome(end_ts, retries=5, delay=5.0)
                    if outcome and ctx.active_side:
                        win = ctx.active_side == outcome
                        hold_pnl = ((1.0 - ctx.position_avg_price) * ctx.position_shares if win
                                    else -ctx.position_avg_price * ctx.position_shares)
                        ctx.hold_pnl = round(ctx.ladder_realized_pnl + hold_pnl, 4)
                        log_event("RESOLVED", ctx, outcome=outcome, win=win,
                                  hold_pnl=round(hold_pnl, 4), total_pnl=ctx.hold_pnl)
                ctx.state = MMState.DONE
            time.sleep(POLL_SECONDS)
            continue

        # ── 6. TIME CHECK — hard exit zone (<60s)
        if time_remaining < ENTRY_WINDOW_END:
            # Cancelar entry pendente
            if ctx.entry_order_id:
                cancel_order(ctx.entry_order_id)
                ctx.entry_order_id = None

            if ctx.position_shares > 0 and ctx.state not in (MMState.HOLD_TO_RESOLUTION, MMState.DONE):
                # Cancelar ladder
                cancel_all_ladder(ctx)

                decision = evaluate_hard_exit(ctx)
                log_event("HARD_EXIT_EVAL", ctx, decision=decision, time_remaining=time_remaining)

                if decision == "MARKET_SELL":
                    if not dry_run:
                        market_sell_all(ctx, reason="hard_exit_good_spread")
                    ctx.state = MMState.DONE
                else:
                    log_event("HOLD_TO_RESOLUTION", ctx, time_remaining=time_remaining)
                    ctx.state = MMState.HOLD_TO_RESOLUTION

            elif ctx.position_shares == 0 and ctx.state != MMState.DONE:
                ctx.state = MMState.DONE

            time.sleep(POLL_SECONDS)
            continue

        # ── 7. Fora da janela de entrada
        if time_remaining > ENTRY_WINDOW_START:
            time.sleep(POLL_SECONDS)
            continue

        # ── 8. CHECK LADDER FILLS
        if ctx.ladder_orders:
            changed = check_ladder_fills(ctx)
            if changed and ctx.position_shares <= 0:
                log_event("LADDER_EXIT_COMPLETE", ctx,
                          total_pnl=ctx.ladder_realized_pnl,
                          shares_sold=ctx.ladder_shares_sold)
                ctx.state = MMState.DONE
                time.sleep(POLL_SECONDS)
                continue
            if changed and ctx.position_shares > 0:
                # Posição mudou, rebuild ladder
                rebuild_ladder(ctx, dry_run=dry_run)

        # ── 9. ENTRY LOGIC
        if ctx.aborted:
            time.sleep(POLL_SECONDS)
            continue

        # 9a. Entry pendente — check timeout
        if ctx.entry_order_id:
            age = now - (ctx.entry_order_ts or now)
            if age >= ORDER_TTL:
                # Verificar se teve fill parcial primeiro
                filled = check_order_filled(ctx.entry_order_id)
                cancel_order(ctx.entry_order_id)
                entry_price = ctx.entry_order_price

                if filled and filled > 0:
                    update_position(ctx, entry_price, filled)
                    log_event("ENTRY_PARTIAL_FILL", ctx,
                              order_id=ctx.entry_order_id,
                              filled=filled, price=entry_price)
                    ctx.entry_order_id = None
                    ctx.entry_order_ts = None
                    ctx.entry_order_price = None
                    ctx.state = MMState.ACTIVE
                    rebuild_ladder(ctx, dry_run=dry_run)
                else:
                    ctx.consecutive_cancels += 1
                    log_event("ENTRY_TIMEOUT_CANCEL", ctx,
                              order_id=ctx.entry_order_id,
                              cancels=ctx.consecutive_cancels)
                    ctx.entry_order_id = None
                    ctx.entry_order_ts = None
                    ctx.entry_order_price = None

                    if ctx.consecutive_cancels >= MAX_CANCELS:
                        log_event("ABORT_MAX_CANCELS", ctx)
                        ctx.aborted = True
                        if ctx.position_shares == 0:
                            ctx.state = MMState.DONE
                time.sleep(POST_CANCEL_COOLDOWN)
                time.sleep(POLL_SECONDS)
                continue

            # Check se fill enquanto esperava
            filled = check_order_filled(ctx.entry_order_id)
            if filled and filled > 0:
                entry_price = ctx.entry_order_price
                ctx.entry_order_id = None
                ctx.entry_order_ts = None
                ctx.entry_order_price = None
                ctx.consecutive_cancels = 0

                update_position(ctx, entry_price, filled)
                log_event("ENTRY_FILLED", ctx, price=entry_price, size=filled,
                          position=ctx.position_shares, avg_price=ctx.position_avg_price)
                ctx.state = MMState.ACTIVE
                rebuild_ladder(ctx, dry_run=dry_run)

            time.sleep(POLL_SECONDS)
            continue

        # 9b. Sem entry pendente — posso colocar nova?
        can_enter = (
            ctx.fills_count < MAX_FILLS
            and ctx.position_shares < MAX_SHARES
            and not ctx.aborted
            and ctx.state in (MMState.IDLE, MMState.ACTIVE)
        )

        if can_enter:
            choice = choose_side(yes_price, no_price)
            if choice:
                side, mid_price = choice

                # Se já tem posição, só entra no mesmo lado
                if ctx.active_side and ctx.active_side != side:
                    time.sleep(POLL_SECONDS)
                    continue

                # Guardrails (só na primeira entrada)
                if ctx.fills_count == 0:
                    gr = guardrail.evaluate(side, now)
                    log_event("GUARDRAIL", ctx, action=gr.action.value, side=side,
                              risk=gr.risk_score, reason=gr.reason)
                    if gr.action in (GuardrailAction.BLOCK, GuardrailAction.CAUTION):
                        time.sleep(POLL_SECONDS)
                        continue

                # Balance check (só na primeira entrada)
                if ctx.fills_count == 0:
                    balance = get_usdc_balance()
                    if balance is not None and balance < MIN_BALANCE_USDC:
                        log_event("SKIP_LOW_BALANCE", ctx, balance=round(balance, 2))
                        time.sleep(POLL_SECONDS)
                        continue

                # Calcular preço de entrada: 1 tick abaixo do mid
                entry_price = max(0.01, round(mid_price - TICK_SIZE, 2))

                if not dry_run:
                    token_id = ctx.yes_token_id if side == "YES" else ctx.no_token_id
                    order_id = place_order(token_id, entry_price, ENTRY_SIZE)
                    if order_id:
                        ctx.entry_order_id = order_id
                        ctx.entry_order_ts = now
                        ctx.entry_order_price = entry_price
                        ctx.active_side = side
                        ctx.active_token_id = token_id
                        if ctx.state == MMState.IDLE:
                            ctx.state = MMState.ENTRY_PENDING
                        log_event("ENTRY_PLACED", ctx, order_id=order_id,
                                  side=side, price=entry_price, size=ENTRY_SIZE)
                    else:
                        log_event("ENTRY_PLACE_FAIL", ctx, side=side, price=entry_price)
                else:
                    log_event("ENTRY_DRY_RUN", ctx, side=side, price=entry_price, size=ENTRY_SIZE)
                    # Simular fill em dry-run
                    token_id = ctx.yes_token_id if side == "YES" else ctx.no_token_id
                    ctx.active_side = side
                    ctx.active_token_id = token_id
                    update_position(ctx, entry_price, ENTRY_SIZE)
                    ctx.state = MMState.ACTIVE
                    rebuild_ladder(ctx, dry_run=True)

        if args.once:
            break

        time.sleep(POLL_SECONDS)

    # ── Cleanup
    print()
    print("[SHUTDOWN] Cancelando ordens abertas...")
    if ctx.entry_order_id:
        cancel_order(ctx.entry_order_id)
    cancel_all_ladder(ctx)
    if _http:
        _http.close()
    print("[SHUTDOWN] Bot encerrado.")


if __name__ == "__main__":
    main()
