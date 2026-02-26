#!/usr/bin/env python3
"""
Bot 24/7 para mercados 1h do Polymarket (BTC, ETH, SOL, XRP).

Estratégia:
- Detecta ciclos de 1h automaticamente
- Entra quando YES ou NO estiver entre 96%-99%
- Janela de entrada: 15min a 4min antes da expiração
- Timeout de fill: 5s
- Máximo 1 trade por ciclo por mercado

USO:
    python scripts/bot_1h.py
"""

import json
import os
import signal
import sys
import time
import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional, Any
from zoneinfo import ZoneInfo

import httpx
from guardrails import GuardrailsPro, GuardrailAction
from post_defense import (
    PostDefenseEngine,
    PostDefenseConfig,
    PositionMeta,
    DefenseStateTracker,
    evaluate_defense as pd_evaluate,
)
from post_defense.hedge import get_opposite_token

try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

# ==============================================================================
# CONSTANTES
# ==============================================================================

CLOB_HOST = os.getenv("CLOB_BASE_URL", "https://clob.polymarket.com")
GAMMA_HOST = os.getenv("GAMMA_BASE_URL", "https://gamma-api.polymarket.com")
CHAIN_ID = 137

WINDOW_SECONDS = 3600
ET = ZoneInfo("America/New_York")
COIN_FULL_NAME_1H = {"btc": "bitcoin", "eth": "ethereum", "sol": "solana", "xrp": "xrp"}

# Configurações do bot
POLL_SECONDS = 1            # Intervalo do loop principal
ENTRY_WINDOW_START = 900    # Segundos antes da expiração (15min)
ENTRY_WINDOW_END = 240      # Hard stop (4min)
FILL_TIMEOUT = 5            # Segundos para aguardar fill por tentativa
MAX_FILL_ATTEMPTS = 3       # Tentativas de ordem antes de SKIPPED
MIN_SHARES = 5              # Quantidade por ordem
MIN_PRICE = 0.96            # Preço mínimo para entrada (96%)
MAX_PRICE = 0.99            # Preço máximo para entrada (99%)
MIN_BALANCE_USDC = 5.2      # Saldo mínimo (USDC) para 5 shares @ 99%
ORDER_FAIL_RETRY_DELAY = 2  # Segundos antes de reenviar após falha
ORDER_FAIL_MAX_RETRIES = 2  # Tentativas de place_order antes de desistir
MAX_RETRY_PRICE_DELTA = float(os.getenv("MAX_RETRY_PRICE_DELTA", "0.04"))  # Max centavos acima do preco original no retry

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
    defense_tracker: Optional[DefenseStateTracker] = None
    yes_price: Optional[float] = None
    no_price: Optional[float] = None
    last_status_log_ts: int = 0


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
            from py_clob_client.client import ClobClient  # type: ignore
        except ImportError as e:
            raise RuntimeError("ERRO: pip install py-clob-client") from e

        pk = os.getenv("POLYMARKET_PRIVATE_KEY", "")
        if not pk:
            raise RuntimeError("Configure POLYMARKET_PRIVATE_KEY")
        if not pk.startswith("0x"):
            pk = f"0x{pk}"

        _client = ClobClient(
            CLOB_HOST,
            chain_id=CHAIN_ID,
            key=pk,
            signature_type=1,
        )
    return _client


def get_http() -> httpx.Client:
    global _http
    if _http is None:
        _http = httpx.Client(timeout=30)
    return _http


class LogWriter:
    def __init__(self):
        self._file = None
        self._open()

    def _open(self):
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        self._file = open(LOGS_DIR / f"bot_1h_{today}.jsonl", "a", encoding="utf-8")

    def write(self, data: dict):
        if not self._file:
            self._open()
        self._file.write(json.dumps(data, ensure_ascii=False) + "\n")
        self._file.flush()

    def close(self):
        if self._file:
            try:
                self._file.flush()
            except Exception:
                pass
            try:
                self._file.close()
            except Exception:
                pass
            self._file = None


_log_writer = LogWriter()


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
        "yes_token_id": ctx.yes_token_id,
        "no_token_id": ctx.no_token_id,
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
    extra_str = f"{extra}" if extra else ""
    print(f"[{time_str}] {asset.upper().ljust(4)} | {state_str} | {clob_str} | {action} {extra_str}".rstrip())


# ==============================================================================
# SLUG 1H
# ==============================================================================

def slug_1h(asset: str, window_ts: int) -> str:
    name = COIN_FULL_NAME_1H.get(asset.lower(), asset.lower())
    dt_utc = datetime.fromtimestamp(window_ts, tz=timezone.utc)
    dt_et = dt_utc.astimezone(ET)
    month = dt_et.strftime("%B").lower()
    day = dt_et.day
    hour_12 = dt_et.hour % 12 or 12
    am_pm = "am" if dt_et.hour < 12 else "pm"
    return f"{name}-up-or-down-{month}-{day}-{hour_12}{am_pm}-et"


# ==============================================================================
# FUNÇÕES DE MERCADO
# ==============================================================================

def fetch_market_status(asset: str) -> Optional[dict]:
    try:
        http = get_http()
        now = int(time.time())
        current_window = int(now // WINDOW_SECONDS) * WINDOW_SECONDS

        for window_ts in [current_window, current_window - WINDOW_SECONDS]:
            slug = slug_1h(asset, window_ts)
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
    slug = slug_1h(asset, window_start)

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


def fetch_book(token_id: str) -> Optional[dict]:
    http = get_http()
    base = CLOB_HOST.rstrip("/")
    try:
        r = http.get(f"{base}/book", params={"token_id": token_id})
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def get_usdc_balance() -> Optional[float]:
    global _usdc_balance_cache, _usdc_balance_cache_ts
    now = time.time()
    if _usdc_balance_cache is not None and (now - _usdc_balance_cache_ts) < BALANCE_CACHE_TTL:
        return _usdc_balance_cache

    try:
        client = get_client()
        resp = client.get_balance_allowance()
        if not isinstance(resp, dict):
            return None
        balances = resp.get("balances") or []
        for b in balances:
            if (b.get("asset_type") or "").lower() == "usdc":
                bal = float(b.get("available") or 0)
                _usdc_balance_cache = bal
                _usdc_balance_cache_ts = now
                return bal
    except Exception:
        return None
    return None


def place_order_with_retry(token_id: str, price: float, size: float) -> Optional[str]:
    for attempt in range(ORDER_FAIL_MAX_RETRIES):
        order_id = place_order(token_id, price, size)
        if order_id:
            return order_id
        if attempt < ORDER_FAIL_MAX_RETRIES - 1:
            time.sleep(ORDER_FAIL_RETRY_DELAY)
    return None


def place_order(token_id: str, price: float, size: float) -> Optional[str]:
    try:
        from py_clob_client.clob_types import OrderArgs, OrderType  # type: ignore
        from py_clob_client.order_builder.constants import BUY  # type: ignore

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


def place_hedge_order(token_id: str, price: float, size: float) -> Optional[str]:
    try:
        from py_clob_client.clob_types import OrderArgs, OrderType  # type: ignore
        from py_clob_client.order_builder.constants import BUY  # type: ignore

        client = get_client()
        order_args = OrderArgs(token_id=token_id, price=price, size=size, side=BUY)
        signed_order = client.create_order(order_args)
        resp = client.post_order(signed_order, OrderType.FOK, post_only=False)
        if isinstance(resp, dict) and resp.get("orderID"):
            return resp.get("orderID")
        print(f"[ERRO] place_hedge_order: {resp}")
    except Exception as e:
        print(f"[ERRO] place_hedge_order: {e}")
    return None


def cancel_order(order_id: str) -> bool:
    try:
        client = get_client()
        resp = client.cancel(order_id)
        return bool(resp)
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
    ctx.defense_tracker = None


def signal_handler(signum, frame):
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

    parser = argparse.ArgumentParser(description="Bot 1h Polymarket (BTC, ETH, SOL, XRP)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Não envia ordens; apenas descobre mercados, imprime preços e registra decisões.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Executa uma iteração (um fetch por asset) e encerra. Útil para smoke-test.",
    )
    args = parser.parse_args()
    dry_run = bool(args.dry_run or os.getenv("DRY_RUN", "").strip() == "1")

    print("=" * 60)
    print("BOT 1H - POLYMARKET")
    print(f"MERCADOS: {', '.join(a.upper() for a in ASSETS)}")
    print(f"JANELA: {ENTRY_WINDOW_START}s a {ENTRY_WINDOW_END}s antes da expiração")
    print(f"RANGE: {MIN_PRICE*100:.0f}% a {MAX_PRICE*100:.0f}%")
    print(f"SHARES: {MIN_SHARES}")
    if dry_run:
        print("MODO: DRY-RUN (sem ordens)")
    print("=" * 60)
    print()

    if not dry_run:
        try:
            get_client()
            from eth_account import Account  # type: ignore

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
    pd_config = PostDefenseConfig()
    pd_engines = {asset: PostDefenseEngine(asset, pd_config) for asset in ASSETS}

    print("Iniciando loop principal... (Ctrl+C para parar)")
    print()

    while _running:
        now = int(time.time())

        if _pending_results:
            resolved_keys = []
            for key, pdata in list(_pending_results.items()):
                age = now - pdata.get("added_ts", now)
                if age > 600:
                    tmp_ctx = MarketContext(asset=pdata["asset"])
                    tmp_ctx.cycle_end_ts = pdata["cycle_end_ts"]
                    log_event(
                        "POSITION_RESULT_UNKNOWN",
                        pdata["asset"],
                        tmp_ctx,
                        side=pdata["side"],
                        entry_price=pdata["entry_price"],
                        size=pdata["size"],
                        hedge_shares=pdata["hedge_shares"],
                        defense_phase=pdata["defense_phase"],
                        age_s=age,
                        reason="max_pending_age_exceeded",
                    )
                    resolved_keys.append(key)
                    continue
                outcome = _get_resolved_outcome(pdata["asset"], pdata["cycle_end_ts"], retries=5, delay=5.0)
                if outcome is not None:
                    win = pdata["side"] == outcome
                    pnl = (1.0 - pdata["entry_price"]) * pdata["size"] if win else -pdata["entry_price"] * pdata["size"]
                    tmp_ctx = MarketContext(asset=pdata["asset"])
                    tmp_ctx.cycle_end_ts = pdata["cycle_end_ts"]
                    log_event(
                        "POSITION_RESULT_RESOLVED",
                        pdata["asset"],
                        tmp_ctx,
                        outcome_winner=outcome,
                        side=pdata["side"],
                        entry_price=pdata["entry_price"],
                        size=pdata["size"],
                        win=win,
                        pnl=round(pnl, 2),
                        hedge_shares=pdata["hedge_shares"],
                        defense_phase=pdata["defense_phase"],
                        entered_side_price_final=pdata["entered_side_price_final"],
                    )
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
            if ctx.state != MarketState.HOLDING:
                pd_engines[asset].update(float(now), yes_price, time_to_expiry)
            yes_token = market["yes_token"]
            no_token = market["no_token"]

            ctx.yes_token_id = yes_token
            ctx.no_token_id = no_token
            ctx.yes_price = round(yes_price, 4)
            ctx.no_price = round(no_price, 4)

            in_entry_window = (ENTRY_WINDOW_END <= time_to_expiry <= ENTRY_WINDOW_START)
            status_interval_s = 1 if in_entry_window else 60
            if (now - ctx.last_status_log_ts) >= status_interval_s:
                ctx.last_status_log_ts = now
                log_event(
                    "STATUS",
                    asset,
                    ctx,
                    time_to_expiry=time_to_expiry,
                    in_entry_window=in_entry_window,
                    slug=market.get("slug"),
                )

            if ctx.cycle_end_ts != end_ts:
                old_cycle = ctx.cycle_end_ts
                if ctx.state == MarketState.HOLDING and ctx.entered_side and ctx.entered_price is not None and old_cycle is not None:
                    outcome_winner = _get_resolved_outcome(asset, old_cycle, retries=5, delay=5.0)
                    size = ctx.entered_size if ctx.entered_size is not None else MIN_SHARES
                    hedge_total = ctx.defense_tracker.total_hedge_shares if ctx.defense_tracker else 0
                    final_phase = ctx.defense_tracker.phase.value if ctx.defense_tracker else "NONE"
                    entered_side_price_final = round(yes_price if ctx.entered_side == "YES" else no_price, 4)
                    if outcome_winner is not None:
                        win = ctx.entered_side == outcome_winner
                        pnl = (1.0 - ctx.entered_price) * size if win else -ctx.entered_price * size
                        log_event(
                            "POSITION_RESULT",
                            asset,
                            ctx,
                            outcome_winner=outcome_winner,
                            side=ctx.entered_side,
                            entry_price=ctx.entered_price,
                            size=size,
                            win=win,
                            pnl=round(pnl, 2),
                            hedge_shares=hedge_total,
                            defense_phase=final_phase,
                            entered_side_price_final=entered_side_price_final,
                        )
                    else:
                        pending_key = f"{asset}:{old_cycle}"
                        _pending_results[pending_key] = {
                            "asset": asset,
                            "cycle_end_ts": old_cycle,
                            "side": ctx.entered_side,
                            "entry_price": ctx.entered_price,
                            "size": size,
                            "hedge_shares": hedge_total,
                            "defense_phase": final_phase,
                            "entered_side_price_final": entered_side_price_final,
                            "added_ts": now,
                        }
                        log_event(
                            "POSITION_RESULT_PENDING",
                            asset,
                            ctx,
                            side=ctx.entered_side,
                            entry_price=ctx.entered_price,
                            size=size,
                            hedge_shares=hedge_total,
                            defense_phase=final_phase,
                            reason="outcome_not_resolved_after_retries",
                        )
                reset_context(ctx)
                guardrails[asset].reset()
                pd_engines[asset].clear_position()
                ctx.cycle_end_ts = end_ts
                log_event("NEW_CYCLE", asset, ctx, end_ts=end_ts, title=market["title"], slug=market.get("slug"))

            if time_to_expiry <= 0:
                if ctx.state not in (MarketState.DONE, MarketState.SKIPPED):
                    ctx.state = MarketState.DONE
                    log_event("EXPIRED", asset, ctx)
                continue

            if time_to_expiry < ENTRY_WINDOW_END:
                if ctx.state == MarketState.ORDER_PLACED:
                    cancel_order(ctx.order_id)
                    ctx.state = MarketState.SKIPPED
                    log_event("CANCEL_HARD_STOP", asset, ctx, time_to_expiry=time_to_expiry)
                elif ctx.state == MarketState.IDLE:
                    ctx.state = MarketState.SKIPPED
                continue

            if time_to_expiry > ENTRY_WINDOW_START:
                continue

            if ctx.state == MarketState.SKIPPED and not ctx.skip_retried:
                if (MIN_PRICE <= yes_price <= MAX_PRICE) or (MIN_PRICE <= no_price <= MAX_PRICE):
                    ctx.state = MarketState.IDLE
                    ctx.trade_attempts = 0
                    ctx.skip_retried = True
                    log_event(
                        "RE_ENTRY_AFTER_SKIP",
                        asset,
                        ctx,
                        time_to_expiry=time_to_expiry,
                        yes_price=round(yes_price, 2),
                        no_price=round(no_price, 2),
                    )

            if ctx.state in (MarketState.DONE, MarketState.SKIPPED):
                continue

            if ctx.state == MarketState.HOLDING:
                if pd_config.enabled and ctx.defense_tracker is not None:
                    try:
                        entered_token = ctx.yes_token_id if ctx.entered_side == "YES" else ctx.no_token_id
                        book_json = fetch_book(entered_token) if entered_token else None
                        snap = pd_engines[asset].update(float(now), yes_price, time_to_expiry, book_json)
                        opp_token, _ = get_opposite_token(ctx.entered_side, ctx.yes_token_id, ctx.no_token_id)
                        best_ask_opp = get_best_ask(opp_token) if opp_token else None
                        decision = pd_evaluate(
                            tracker=ctx.defense_tracker,
                            snap=snap,
                            entered_side=ctx.entered_side,
                            entered_shares=ctx.entered_size or MIN_SHARES,
                            yes_token_id=ctx.yes_token_id,
                            no_token_id=ctx.no_token_id,
                            best_ask_opposite=best_ask_opp,
                            config=pd_config,
                            now_ts=float(now),
                        )
                        if decision.should_hedge and decision.hedge_shares > 0:
                            hedge_cost = decision.hedge_price * decision.hedge_shares
                            if hedge_cost > 0:
                                log_event("HEDGE_PLACING", asset, ctx, shares=decision.hedge_shares, price=decision.hedge_price)
                                hedge_id = place_hedge_order(decision.hedge_token_id, decision.hedge_price, decision.hedge_shares)
                                if hedge_id:
                                    filled = check_order_filled(hedge_id)
                                    if filled:
                                        ctx.defense_tracker.total_hedge_shares += decision.hedge_shares
                                        ctx.defense_tracker.last_hedge_ts = float(now)
                                        ctx.defense_tracker.hedge_order_id = hedge_id
                                        log_event("HEDGE_FILLED", asset, ctx, shares=decision.hedge_shares, price=decision.hedge_price, total_hedged=ctx.defense_tracker.total_hedge_shares)
                                    else:
                                        log_event("HEDGE_NOT_FILLED", asset, ctx, shares=decision.hedge_shares, reason="FOK_not_matched")
                                else:
                                    log_event("HEDGE_FAILED", asset, ctx, reason="order_rejected")
                    except Exception as e:
                        print(f"[ERRO] post_defense({asset}): {e}")
                continue

            if ctx.trade_attempts >= 1:
                continue

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
                log_event("SKIP_PRICE_OOR", asset, ctx, yes_price=round(yes_price, 2), no_price=round(no_price, 2), time_to_expiry=time_to_expiry)
                continue

            if not ctx.skip_retried:
                gr_decision = guardrails[asset].evaluate(side, float(now))
                log_event(
                    "GUARDRAIL_DECISION",
                    asset,
                    ctx,
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
                    reason=gr_decision.reason,
                )
                if gr_decision.action in (GuardrailAction.BLOCK, GuardrailAction.CAUTION):
                    log_event("GUARDRAIL_BLOCK", asset, ctx, side=side, risk_score=gr_decision.risk_score, reason=gr_decision.reason)
                    continue
            else:
                log_event("GUARDRAIL_SKIP_REENTRY", asset, ctx, side=side, reason="skip_retried_bypass")

            if dry_run:
                ctx.trade_attempts += 1
                ctx.state = MarketState.SKIPPED
                log_event(
                    "DRYRUN_WOULD_PLACE_ORDER",
                    asset,
                    ctx,
                    side=side,
                    token_id=token_id,
                    would_price=price,
                    shares=MIN_SHARES,
                    time_to_expiry=time_to_expiry,
                    slug=market.get("slug"),
                )
                continue

            balance = get_usdc_balance()
            if balance is not None and balance < MIN_BALANCE_USDC:
                log_event("SKIP_INSUFFICIENT_BALANCE", asset, ctx, balance=round(balance, 2), required=MIN_BALANCE_USDC)
                continue

            current_price = price
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
                    if pd_config.enabled:
                        vol_s, vol_l, z_v = pd_engines[asset].snapshot_regime()
                        meta = PositionMeta(
                            market_id=asset,
                            side=side,
                            entry_price=current_price,
                            entry_time_s=float(now),
                            position_shares=MIN_SHARES,
                            vol_entry_short=vol_s,
                            vol_entry_long=vol_l,
                            z_vol_entry=z_v,
                        )
                        pd_engines[asset].start_position(meta)
                        ctx.defense_tracker = DefenseStateTracker()
                        log_event("DEFENSE_STARTED", asset, ctx, vol_entry_short=round(vol_s, 6), vol_entry_long=round(vol_l, 6))
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
                    log_event("RETRY_PRICE_TOO_HIGH", asset, ctx, original_price=price, retry_price=current_price, delta=round(current_price - price, 2), max_delta=MAX_RETRY_PRICE_DELTA)
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

