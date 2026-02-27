#!/usr/bin/env python3
"""
Bot 24/7 para mercados 4h do Polymarket (BTC, ETH, SOL, XRP).

Estratégia:
- Detecta ciclos de 4h automaticamente
- Entra quando YES ou NO estiver entre 95%-99%
- Janela de entrada: 30min a 5min antes da expiração
- Stop-loss: vende (SELL FOK) se prob do lado cair abaixo de 70%
- Máximo 1 trade por ciclo por mercado

USO:
    python scripts/bot_4h.py
"""

import json
import os
import signal
import sys
import time
import argparse
import atexit
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional, Any

import httpx
from guardrails import GuardrailsPro, GuardrailAction

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

# ==============================================================================
# CONSTANTES
# ==============================================================================

CLOB_HOST = os.getenv("CLOB_BASE_URL", "https://clob.polymarket.com")
GAMMA_HOST = os.getenv("GAMMA_BASE_URL", "https://gamma-api.polymarket.com")
CHAIN_ID = 137

WINDOW_SECONDS = 14400  # 4 horas

# Configurações do bot
POLL_SECONDS = 1              # Intervalo do loop principal
ENTRY_WINDOW_START = 1800     # 30min antes da expiração
ENTRY_WINDOW_END = 300        # Hard stop 5min
FILL_TIMEOUT = 5              # Segundos para aguardar fill por tentativa
MAX_FILL_ATTEMPTS = 3         # Tentativas de ordem antes de SKIPPED
MIN_SHARES = 5                # Quantidade por ordem
MIN_PRICE = 0.95              # Preço mínimo para entrada (95%)
MAX_PRICE = 0.99              # Preço máximo para entrada (99%)
MIN_BALANCE_USDC = 5.2        # Saldo mínimo (USDC) para 5 shares @ 99%
ORDER_FAIL_RETRY_DELAY = 2    # Segundos antes de reenviar após falha
ORDER_FAIL_MAX_RETRIES = 2    # Tentativas de place_order antes de desistir
MAX_RETRY_PRICE_DELTA = float(os.getenv("MAX_RETRY_PRICE_DELTA", "0.04"))

# Stop-loss
STOP_PROB = float(os.getenv("BL_STOP_PROB", "0.70"))  # Vende se prob do lado cair abaixo

# Mercados
ASSETS = ["btc", "eth", "sol", "xrp"]

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
    skip_retried: bool = False
    yes_price: Optional[float] = None
    no_price: Optional[float] = None
    last_status_log_ts: int = 0
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

_client: Any = None
_http: Optional[httpx.Client] = None
_running = True
_usdc_balance_cache: Optional[float] = None
_usdc_balance_cache_ts: float = 0
BALANCE_CACHE_TTL = 60

_pending_results: dict = {}


def get_client() -> Any:
    global _client
    if _client is None:
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import ApiCreds
        except ImportError as e:
            raise RuntimeError("ERRO: pip install py-clob-client") from e

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

    def write(self, data: dict):
        today = datetime.now().strftime("%Y-%m-%d")
        if self._date != today or self._file is None:
            self.close()
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            self._file = open(LOGS_DIR / f"bot_4h_{today}.jsonl", "a", encoding="utf-8")
            self._date = today
        try:
            self._file.write(json.dumps(data, ensure_ascii=False) + "\n")
            self._file.flush()
        except Exception as e:
            print(f"[LOG ERROR] {e}")

    def close(self):
        if self._file:
            try:
                self._file.flush()
                self._file.close()
            except Exception:
                pass
            self._file = None
            self._date = None


_log_writer = LogWriter()
atexit.register(_log_writer.close)


def log_event(action: str, asset: str, ctx: MarketContext, **extra):
    now = int(time.time())
    payload = {
        "ts": now,
        "ts_iso": datetime.now().isoformat(),
        "action": action,
        "market": asset,
        "state": ctx.state.value if ctx.state else None,
        "cycle_end_ts": ctx.cycle_end_ts,
        "order_id": ctx.order_id,
        "entered_side": ctx.entered_side,
        "entered_price": ctx.entered_price,
        "entered_size": ctx.entered_size,
        "entered_ts": ctx.entered_ts,
        "yes_price": ctx.yes_price,
        "no_price": ctx.no_price,
        **extra,
    }
    _log_writer.write(payload)

    time_str = datetime.now().strftime("%H:%M:%S")
    state_str = ctx.state.value if ctx.state else "?"
    clob_str = ""
    if ctx.yes_price is not None and ctx.no_price is not None:
        clob_str = f"YES={ctx.yes_price:.3f} NO={ctx.no_price:.3f}"

    pos_str = ""
    if (ctx.state == MarketState.HOLDING
            and ctx.entered_side and ctx.entered_price is not None
            and ctx.yes_price is not None and ctx.no_price is not None):
        side_now = ctx.yes_price if ctx.entered_side == "YES" else ctx.no_price
        winning = side_now > ctx.entered_price
        shares = ctx.entered_size or MIN_SHARES
        pnl = round((1.0 - ctx.entered_price) * shares, 2) if winning else round(-ctx.entered_price * shares, 2)
        label = "WINNING" if winning else "LOSING"
        pos_str = f"{ctx.entered_side}@{ctx.entered_price:.2f} now={side_now:.2f} {label} ${pnl:+.2f}"

    if pos_str:
        print(f"[{time_str}] {asset.upper().ljust(4)} | {state_str.ljust(12)} | {pos_str} | {clob_str} | {action} {extra if extra else ''}".rstrip())
    else:
        extra_str = f"{extra}" if extra else ""
        print(f"[{time_str}] {asset.upper().ljust(4)} | {state_str} | {clob_str} | {action} {extra_str}".rstrip())


# ==============================================================================
# SLUG 4H — formato: {asset}-updown-4h-{window_start_ts}
# ==============================================================================

def slug_4h(asset: str, window_ts: int) -> str:
    return f"{asset}-updown-4h-{window_ts}"


# ==============================================================================
# FUNÇÕES DE MERCADO
# ==============================================================================

def fetch_market_status(asset: str) -> Optional[dict]:
    try:
        http = get_http()
        now = int(time.time())
        current_window = int(now // WINDOW_SECONDS) * WINDOW_SECONDS

        for window_ts in [current_window, current_window - WINDOW_SECONDS]:
            slug = slug_4h(asset, window_ts)
            result = _fetch_market_by_slug(http, asset, slug, window_ts)
            if result:
                end_ts = result["end_ts"]
                time_to_expiry = end_ts - now
                if time_to_expiry > -60:
                    return result
        return None
    except Exception as e:
        print(f"[ERRO] fetch_market_status({asset}): {e}")
        return None


def _get_resolved_outcome(asset: str, cycle_end_ts: int, retries: int = 3, delay: float = 3.0) -> Optional[str]:
    http = get_http()
    window_start = cycle_end_ts - WINDOW_SECONDS
    slug = slug_4h(asset, window_start)

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


def _fetch_market_by_slug(http: httpx.Client, asset: str, slug: str, window_ts: int) -> Optional[dict]:
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
            end_ts = int(window_ts) + WINDOW_SECONDS

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
            if not bids or not asks:
                return None
            best_bid = _float_price(bids[0].get("price") if isinstance(bids[0], dict) else None)
            best_ask = _float_price(asks[0].get("price") if isinstance(asks[0], dict) else None)
            if best_bid is None or best_ask is None:
                return None
            return round((best_bid + best_ask) / 2, 4)
    except Exception:
        pass
    return None


def get_best_ask(token_id: str) -> Optional[float]:
    http = get_http()
    base = CLOB_HOST.rstrip("/")
    try:
        r = http.get(f"{base}/book", params={"token_id": token_id})
        if r.status_code == 200:
            book = r.json()
            asks = book.get("asks", [])
            if not asks:
                return None
            return _float_price(asks[0].get("price"))
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


# ==============================================================================
# SALDO USDC
# ==============================================================================

def get_usdc_balance() -> Optional[float]:
    """Saldo USDC disponível para trade (CLOB API)."""
    global _usdc_balance_cache, _usdc_balance_cache_ts
    now = time.time()
    if _usdc_balance_cache is not None and (now - _usdc_balance_cache_ts) < BALANCE_CACHE_TTL:
        return _usdc_balance_cache

    try:
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
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
    return None


# ==============================================================================
# FUNÇÕES DE ORDEM
# ==============================================================================

def place_order_with_retry(token_id: str, price: float, size: float) -> Optional[str]:
    for attempt in range(ORDER_FAIL_MAX_RETRIES):
        order_id = place_order(token_id, price, size)
        if order_id:
            return order_id
        if attempt < ORDER_FAIL_MAX_RETRIES - 1:
            time.sleep(ORDER_FAIL_RETRY_DELAY)
    return None


def place_order(token_id: str, price: float, size: float) -> Optional[str]:
    """Envia ordem LIMIT POST_ONLY (maker)."""
    try:
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY

        client = get_client()
        order_args = OrderArgs(token_id=token_id, price=price, size=size, side=BUY)
        signed_order = client.create_order(order_args)
        resp = client.post_order(signed_order, OrderType.GTC, post_only=True)
        if isinstance(resp, dict) and resp.get("orderID"):
            return resp.get("orderID")
        print(f"[ERRO] place_order: {resp}")
    except Exception as e:
        print(f"[ERRO] place_order: {e}")
    return None


def place_sell_order(token_id: str, price: float, size: float) -> Optional[str]:
    """Envia ordem de SELL FOK (taker, fill imediato)."""
    try:
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import SELL

        client = get_client()
        order_args = OrderArgs(token_id=token_id, price=price, size=size, side=SELL)
        signed_order = client.create_order(order_args)
        resp = client.post_order(signed_order, OrderType.FOK, post_only=False)
        if isinstance(resp, dict) and resp.get("orderID"):
            return resp.get("orderID")
        print(f"[ERRO] place_sell_order: {resp}")
    except Exception as e:
        print(f"[ERRO] place_sell_order: {e}")
    return None


def cancel_order(order_id: str) -> bool:
    try:
        client = get_client()
        resp = client.cancel(order_id)
        return resp.get("canceled", False) or resp.get("success", False)
    except Exception as e:
        print(f"[ERRO] cancel_order: {e}")
        return False


def check_order_filled(order_id: str) -> bool:
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
    start = time.time()
    while time.time() - start < timeout:
        if check_order_filled(order_id):
            return True
        time.sleep(1)
    return False


# ==============================================================================
# STOP-LOSS
# ==============================================================================

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

    our_price = yes_price if ctx.entered_side == "YES" else no_price

    if our_price >= STOP_PROB:
        return None

    our_token = ctx.yes_token_id if ctx.entered_side == "YES" else ctx.no_token_id

    return {
        "token_id": our_token,
        "side": ctx.entered_side,
        "size": ctx.entered_size,
        "our_price": round(our_price, 4),
        "trigger": STOP_PROB,
    }


def execute_stop_loss(ctx: MarketContext, stop: dict) -> bool:
    """Executa SELL FOK do token posicionado a mercado."""
    best_bid = get_best_bid(stop["token_id"])
    if best_bid is None:
        log_event("STOP_SKIPPED", ctx.asset, ctx, reason="no_bid")
        return False

    sell_price = 0.01  # Mercado: aceita qualquer bid

    order_id = place_sell_order(stop["token_id"], sell_price, stop["size"])
    if not order_id:
        log_event("STOP_NOT_FILLED", ctx.asset, ctx,
            sell_price=sell_price, size=stop["size"], reason="order_rejected")
        return False

    filled = check_order_filled(order_id)
    if filled:
        exec_price = best_bid
        stop_pnl = round((exec_price - ctx.entered_price) * stop["size"], 4)
        ctx.stop_executed = True
        ctx.stop_price = exec_price
        ctx.stop_size = stop["size"]
        ctx.stop_ts = int(time.time())
        ctx.stop_order_id = order_id
        ctx.stop_pnl = stop_pnl
        ctx.state = MarketState.DONE
        log_event("STOP_EXECUTED", ctx.asset, ctx,
            sell_price=exec_price, size=stop["size"],
            stop_pnl=stop_pnl, our_price=stop["our_price"],
            trigger=STOP_PROB, order_id=order_id)
        return True
    else:
        log_event("STOP_NOT_FILLED", ctx.asset, ctx,
            sell_price=sell_price, size=stop["size"], reason="FOK_not_matched")
        return False


# ==============================================================================
# RESET
# ==============================================================================

def reset_context(ctx: MarketContext):
    ctx.cycle_end_ts = None
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


def signal_handler(signum, frame):
    global _running
    print(f"\n[SHUTDOWN] Sinal {signum} recebido, flushing logs...")
    _running = False
    _log_writer.close()


# ==============================================================================
# LOOP PRINCIPAL
# ==============================================================================

def main():
    global _running

    parser = argparse.ArgumentParser(description="Bot 4h Polymarket (BTC, ETH, SOL, XRP)")
    parser.add_argument("--dry-run", action="store_true", help="Sem ordens reais.")
    parser.add_argument("--once", action="store_true", help="Executa uma iteração e encerra.")
    args = parser.parse_args()
    dry_run = bool(args.dry_run or os.getenv("DRY_RUN", "").strip() == "1")

    print("=" * 60)
    print("BOT 4H - POLYMARKET")
    print(f"MERCADOS: {', '.join(a.upper() for a in ASSETS)}")
    print(f"JANELA: {ENTRY_WINDOW_START}s a {ENTRY_WINDOW_END}s antes da expiração")
    print(f"RANGE: {MIN_PRICE*100:.0f}% a {MAX_PRICE*100:.0f}%")
    print(f"SHARES: {MIN_SHARES}")
    print(f"STOP-LOSS: prob < {STOP_PROB*100:.0f}%")
    if dry_run:
        print("MODO: DRY-RUN (sem ordens)")
    print("=" * 60)
    print()

    if not dry_run:
        try:
            get_client()
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

    contexts = {asset: MarketContext(asset=asset) for asset in ASSETS}
    guardrails = {asset: GuardrailsPro(asset=asset) for asset in ASSETS}

    print("Iniciando loop principal... (Ctrl+C para parar)")
    print()

    while _running:
        now = int(time.time())

        # ── Resolver resultados pendentes
        if _pending_results:
            resolved_keys = []
            for key, pdata in list(_pending_results.items()):
                age = now - pdata.get("added_ts", now)
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

            ctx.yes_token_id = yes_token
            ctx.no_token_id = no_token
            ctx.yes_price = round(yes_price, 4)
            ctx.no_price = round(no_price, 4)

            # Log de status (a cada 1s na janela, 60s fora)
            in_entry_window = (ENTRY_WINDOW_END <= time_to_expiry <= ENTRY_WINDOW_START)
            status_interval_s = 1 if in_entry_window else 60
            if (now - ctx.last_status_log_ts) >= status_interval_s:
                ctx.last_status_log_ts = now
                log_event("STATUS", asset, ctx,
                    time_to_expiry=time_to_expiry,
                    in_entry_window=in_entry_window,
                    slug=market.get("slug"))

            # Detectar novo ciclo
            if ctx.cycle_end_ts != end_ts:
                old_cycle = ctx.cycle_end_ts
                if ctx.state in (MarketState.HOLDING, MarketState.DONE) and ctx.entered_side and ctx.entered_price is not None and old_cycle is not None:
                    outcome_winner = _get_resolved_outcome(asset, old_cycle, retries=5, delay=5.0)
                    size = ctx.entered_size if ctx.entered_size is not None else MIN_SHARES

                    if ctx.stop_executed and ctx.stop_pnl is not None:
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
                log_event("NEW_CYCLE", asset, ctx, end_ts=end_ts, title=market["title"], slug=market.get("slug"))

            # Já expirou?
            if time_to_expiry <= 0:
                if ctx.state not in (MarketState.DONE, MarketState.SKIPPED):
                    ctx.state = MarketState.DONE
                    log_event("EXPIRED", asset, ctx)
                continue

            # Hard stop
            if time_to_expiry < ENTRY_WINDOW_END:
                if ctx.state == MarketState.ORDER_PLACED:
                    cancel_order(ctx.order_id)
                    ctx.state = MarketState.SKIPPED
                    log_event("CANCEL_HARD_STOP", asset, ctx, time_to_expiry=time_to_expiry)
                elif ctx.state == MarketState.IDLE:
                    ctx.state = MarketState.SKIPPED
                continue

            # Fora da janela de entrada
            if time_to_expiry > ENTRY_WINDOW_START:
                continue

            # Re-entry após SKIPPED
            if ctx.state == MarketState.SKIPPED and not ctx.skip_retried:
                if (MIN_PRICE <= yes_price <= MAX_PRICE) or (MIN_PRICE <= no_price <= MAX_PRICE):
                    ctx.state = MarketState.IDLE
                    ctx.trade_attempts = 0
                    ctx.skip_retried = True
                    log_event("RE_ENTRY_AFTER_SKIP", asset, ctx,
                        time_to_expiry=time_to_expiry,
                        yes_price=round(yes_price, 2),
                        no_price=round(no_price, 2))

            if ctx.state in (MarketState.DONE, MarketState.SKIPPED):
                continue

            # HOLDING — stop-loss por probabilidade
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
                continue

            if ctx.trade_attempts >= 1:
                continue

            # Avaliar entrada
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
                log_event("SKIP_PRICE_OOR", asset, ctx,
                    yes_price=round(yes_price, 2),
                    no_price=round(no_price, 2),
                    time_to_expiry=time_to_expiry)
                continue

            # Guardrails
            if not ctx.skip_retried:
                gr_decision = guardrails[asset].evaluate(side, float(now))
                log_event("GUARDRAIL_DECISION", asset, ctx,
                    gr_action=gr_decision.action.value,
                    side=side,
                    risk_score=gr_decision.risk_score,
                    pump=gr_decision.pump_score,
                    pump_thr=gr_decision.pump_threshold,
                    stability=gr_decision.stability_score,
                    time_in_band=gr_decision.time_in_band_s,
                    momentum=gr_decision.momentum_score,
                    momentum_thr=gr_decision.momentum_threshold,
                    t_remaining=time_to_expiry,
                    reason=gr_decision.reason)
                if gr_decision.action in (GuardrailAction.BLOCK, GuardrailAction.CAUTION):
                    log_event("GUARDRAIL_BLOCK", asset, ctx,
                        side=side, risk_score=gr_decision.risk_score, reason=gr_decision.reason)
                    continue
            else:
                log_event("GUARDRAIL_SKIP_REENTRY", asset, ctx, side=side, reason="skip_retried_bypass")

            # Dry-run
            if dry_run:
                ctx.trade_attempts += 1
                ctx.state = MarketState.SKIPPED
                log_event("DRYRUN_WOULD_PLACE_ORDER", asset, ctx,
                    side=side, token_id=token_id, would_price=price,
                    shares=MIN_SHARES, time_to_expiry=time_to_expiry,
                    slug=market.get("slug"))
                continue

            # Verificar saldo
            balance = get_usdc_balance()
            if balance is not None and balance < MIN_BALANCE_USDC:
                log_event("SKIP_INSUFFICIENT_BALANCE", asset, ctx,
                    balance=round(balance, 2), required=MIN_BALANCE_USDC)
                continue

            # Enviar ordem
            current_price = price
            for attempt in range(MAX_FILL_ATTEMPTS):
                is_retry = attempt > 0
                log_event("PLACING_ORDER", asset, ctx,
                    side=side, price=current_price, size=MIN_SHARES,
                    time_to_expiry=time_to_expiry, retry=is_retry)
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

        time.sleep(POLL_SECONDS)
        if args.once:
            break

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
