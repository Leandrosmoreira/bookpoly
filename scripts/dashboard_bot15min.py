#!/usr/bin/env python3
"""
Dashboard LIVE v2 do bot 15min — terminal com cores, P&L, win rate e barra de progresso.

USO:
    python scripts/dashboard_bot15min.py
"""

import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Carregar .env (mesmo que o bot) para POLYMARKET_PRIVATE_KEY / POLYMARKET_FUNDER
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

try:
    import httpx
except ImportError:
    httpx = None

LOG_DIR = Path(__file__).parent.parent / "logs"
GAMMA_HOST = os.getenv("GAMMA_BASE_URL", "https://gamma-api.polymarket.com")
CLOB_HOST = os.getenv("CLOB_BASE_URL", "https://clob.polymarket.com")
ASSETS = ["btc", "eth", "sol", "xrp"]
ENTRY_WINDOW_START = 240   # 4 min antes da expiração
ENTRY_WINDOW_END = 60      # 1 min antes (hard stop)
WINDOW_SECONDS = 900        # 1 ciclo = 15 min (atualizar saldo por ciclo)

# ─── Cores ANSI ──────────────────────────────────────────────────────────────

class C:
    """Cores ANSI para terminal."""
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    BLINK   = "\033[5m"
    # Foreground
    RED     = "\033[91m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    BLUE    = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN    = "\033[96m"
    WHITE   = "\033[97m"
    GRAY    = "\033[90m"
    # Background
    BG_RED    = "\033[41m"
    BG_GREEN  = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_BLUE   = "\033[44m"

# Desabilitar cores se não suportar
if os.name == "nt":
    try:
        os.system("")  # Habilita ANSI no Windows 10+
    except Exception:
        pass

NO_COLOR = os.getenv("NO_COLOR")  # Padrão https://no-color.org/
if NO_COLOR:
    for attr in dir(C):
        if not attr.startswith("_"):
            setattr(C, attr, "")


# ─── Preços CLOB ao vivo ─────────────────────────────────────────────────────

def _to_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _clob_price(c, token_id: str) -> float | None:
    """Preço ao vivo: /midpoint, fallback /price."""
    base = CLOB_HOST.rstrip("/")
    for path, key in [("/midpoint", "mid"), ("/price", "price")]:
        try:
            r = c.get(base + path, params={"token_id": token_id})
            if r.status_code == 200:
                data = r.json()
                p = data.get(key) or data.get("value")
                if p is not None:
                    val = _to_float(p)
                    if val is not None and 0 <= val <= 1:
                        return val
        except Exception:
            continue
    return None


def _book_spread(c, token_id: str) -> tuple:
    """Retorna (mid, spread) do orderbook."""
    try:
        r = c.get(f"{CLOB_HOST}/book", params={"token_id": token_id})
        if r.status_code != 200:
            return None, None
        book = r.json()
        bids = book.get("bids", [])
        asks = book.get("asks", [])
        best_bid = _to_float(bids[0].get("price") or bids[0].get("p")) if bids else None
        best_ask = _to_float(asks[0].get("price") or asks[0].get("p")) if asks else None
        mid = None
        spread = None
        if best_bid is not None and best_ask is not None:
            mid = round((best_bid + best_ask) / 2, 4)
            spread = round(best_ask - best_bid, 4)
        elif best_bid is not None:
            mid = best_bid
        elif best_ask is not None:
            mid = best_ask
        return mid, spread
    except Exception:
        return None, None


def fetch_live_prices() -> dict:
    """Preço % atual (YES mid, NO mid, YES spread) ao vivo por ativo."""
    out = {a: {"yes": None, "no": None, "spread": None} for a in ASSETS}
    if not httpx:
        return out
    now = int(time.time())
    window_ts = (now // 900) * 900
    t0 = time.monotonic()
    try:
        with httpx.Client(timeout=8) as c:
            for asset in ASSETS:
                for wts in [window_ts, window_ts - 900]:
                    slug = f"{asset}-updown-15m-{wts}"
                    r = c.get(f"{GAMMA_HOST}/events/slug/{slug}")
                    if r.status_code != 200:
                        continue
                    event = r.json()
                    markets = event.get("markets", [])
                    if not markets:
                        continue
                    raw = markets[0].get("clobTokenIds")
                    tokens = json.loads(raw) if isinstance(raw, str) else (raw or [])
                    if len(tokens) < 2:
                        continue
                    yes_token, no_token = tokens[0], tokens[1]
                    yes_p = _clob_price(c, yes_token)
                    no_p = _clob_price(c, no_token)
                    _, spread = _book_spread(c, yes_token)
                    if yes_p is None:
                        mid, sp = _book_spread(c, yes_token)
                        if mid is not None:
                            yes_p = mid
                        if spread is None:
                            spread = sp
                    if no_p is None:
                        mid, _ = _book_spread(c, no_token)
                        if mid is not None:
                            no_p = mid
                    out[asset] = {"yes": yes_p, "no": no_p, "spread": spread}
                    break  # Encontrou mercado ativo
    except Exception:
        pass
    latency = round((time.monotonic() - t0) * 1000)
    return out, latency


# ─── Portfolio / saldo (atualizado por ciclo 15 min) ──────────────────────────

DATA_API = os.getenv("POLYMARKET_DATA_API", "https://data-api.polymarket.com")

def _balance_wallet_address() -> str | None:
    """Wallet cujo saldo mostrar: signer (conta Polymarket) primeiro, senão FUNDER."""
    try:
        from eth_account import Account
        pk = (os.getenv("POLYMARKET_PRIVATE_KEY") or "").strip()
        if pk:
            if not pk.startswith("0x"):
                pk = "0x" + pk
            return Account.from_key(pk).address
    except Exception:
        pass
    funder = (os.getenv("POLYMARKET_FUNDER") or "").strip()
    if funder:
        return funder if funder.startswith("0x") else "0x" + funder
    return None


def _get_proxy_wallet(eoa: str) -> str | None:
    """Na Polymarket o USDC fica na proxy wallet, não na EOA. Busca proxy na Gamma API."""
    if not httpx or not eoa:
        return None
    try:
        r = httpx.get(
            f"{GAMMA_HOST.rstrip('/')}/public-profile",
            params={"address": eoa},
            timeout=8,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        proxy = (data.get("proxyWallet") or "").strip()
        if proxy and len(proxy) == 42 and proxy.startswith("0x"):
            return proxy
    except Exception:
        pass
    return None


def _parse_value(raw) -> float | None:
    """Extrai valor numérico (número, string ou dict com value)."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw.replace(",", ".").strip())
        except ValueError:
            return None
    if isinstance(raw, dict):
        return _parse_value(raw.get("value") or raw.get("totalValue") or raw.get("balance"))
    return None


def _fetch_portfolio_from_api(wallet: str) -> tuple[float | None, float | None]:
    """(portfolio_total, available_to_trade). GET data-api.polymarket.com/value?user=."""
    if not httpx:
        return None, None
    try:
        r = httpx.get(f"{DATA_API.rstrip('/')}/value", params={"user": wallet}, timeout=10)
        if r.status_code != 200:
            return None, None
        data = r.json()
        avail_keys = ("available", "availableToTrade", "available_to_trade", "cash", "balance")
        value_keys = ("value", "totalValue", "total_value", "balance")
        v, a = None, None
        if isinstance(data, list) and len(data) > 0:
            o = data[0]
            if isinstance(o, dict):
                v = next((_parse_value(o.get(k)) for k in value_keys if o.get(k) is not None), None)
                a = next((_parse_value(o.get(k)) for k in avail_keys if o.get(k) is not None), None)
            else:
                v = _parse_value(o)
        elif isinstance(data, dict):
            v = next((_parse_value(data.get(k)) for k in value_keys if data.get(k) is not None), None)
            a = next((_parse_value(data.get(k)) for k in avail_keys if data.get(k) is not None), None)
        else:
            v = _parse_value(data)
            a = v
        if v is not None:
            return (v, a if a is not None else v)
    except Exception:
        pass
    return None, None


def _fetch_usdc_on_chain(wallet: str) -> float | None:
    """Saldo USDC on-chain (Polygon) como fallback."""
    try:
        from web3 import Web3
    except ImportError:
        return None
    usdc_address = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
    abi = [{"constant": True, "inputs": [{"name": "account", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}], "type": "function"}]
    try:
        from polygon_rpc import get_web3_with_fallback, get_polygon_rpc_list
        urls = get_polygon_rpc_list()
        w3 = get_web3_with_fallback(timeout=5) if get_web3_with_fallback else None
    except ImportError:
        urls = [os.getenv("POLYGON_RPC", "https://polygon-rpc.com")]
        w3 = None
    if w3:
        try:
            usdc = w3.eth.contract(address=Web3.to_checksum_address(usdc_address), abi=abi)
            raw = usdc.functions.balanceOf(Web3.to_checksum_address(wallet)).call()
            return raw / 1e6
        except Exception:
            pass
    for rpc in urls:
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 5}))
            usdc = w3.eth.contract(address=Web3.to_checksum_address(usdc_address), abi=abi)
            raw = usdc.functions.balanceOf(Web3.to_checksum_address(wallet)).call()
            return raw / 1e6
        except Exception:
            continue
    return None


def fetch_usdc_balance() -> tuple[float | None, float | None]:
    """(portfolio_total, available_to_trade). Data API com EOA ou proxy; fallback on-chain."""
    eoa = _balance_wallet_address()
    if not eoa:
        return None, None
    # Tentar Data API com EOA (conta conectada no site) e depois com proxy
    for w in (eoa, _get_proxy_wallet(eoa)):
        if not w:
            continue
        portfolio, available = _fetch_portfolio_from_api(w)
        if portfolio is not None:
            return (portfolio, available if available is not None else portfolio)
    proxy_or_eoa = _get_proxy_wallet(eoa) or eoa
    on_chain = _fetch_usdc_on_chain(proxy_or_eoa)
    if on_chain is not None:
        return (on_chain, on_chain)
    return None, None


# ─── Log ──────────────────────────────────────────────────────────────────────

def get_log_path():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return LOG_DIR / f"bot_15min_{today}.jsonl"


def load_all_historical_events(max_files: int = 31, max_lines_total: int = 50000) -> list[dict]:
    """Carrega eventos de todos os logs bot_15min_*.jsonl para cálculo de histórico total."""
    import glob
    events: list[dict] = []
    pattern = str(LOG_DIR / "bot_15min_*.jsonl")
    for path in sorted(glob.glob(pattern), reverse=True)[:max_files]:
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        except Exception:
            pass
        if len(events) >= max_lines_total:
            break
    if len(events) > max_lines_total:
        events = events[-max_lines_total:]
    # Ordem cronológica (arquivos lidos do mais novo ao mais antigo)
    events.reverse()
    return events


def load_events(path: Path, max_lines: int = 5000) -> list[dict]:
    if not path.exists():
        return []
    events = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except Exception:
        pass
    return events[-max_lines:] if len(events) > max_lines else events


def format_ts(ts: int | None) -> str:
    if ts is None:
        return "--:--:--"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M:%S")


def format_expiry(seconds: int) -> str:
    """Formata tempo até expiração: 618 -> '10min 18s', 45 -> '45s'."""
    if seconds <= 0:
        return "0s"
    m = seconds // 60
    s = seconds % 60
    if m == 0:
        return f"{s}s"
    return f"{m}min {s}s"


# ─── Estatísticas ────────────────────────────────────────────────────────────

def _median_ms(lst: list[float]) -> int | None:
    """Mediana da lista em ms. Retorna None se vazia."""
    if not lst:
        return None
    s = sorted(lst)
    n = len(s)
    if n % 2 == 1:
        return int(s[n // 2])
    return int((s[n // 2 - 1] + s[n // 2]) / 2)


def compute_order_latencies_ms(events: list[dict]) -> list[float]:
    """Retorna lista de latências em ms (PLACING_ORDER -> ORDER_PLACED/ORDER_FAILED) por (market, cycle)."""
    pending: dict[tuple[str, int], int] = {}
    out: list[float] = []
    for e in events:
        market = (e.get("market") or "").lower()
        cycle = e.get("cycle_end_ts") or e.get("end_ts")
        action = e.get("action", "")
        ts = e.get("ts")
        if action == "PLACING_ORDER" and cycle is not None and ts is not None:
            pending[(market, cycle)] = ts
        if action in ("ORDER_PLACED", "ORDER_FAILED"):
            key = (market, cycle)
            if key in pending and ts is not None:
                out.append((ts - pending[key]) * 1000)
            if key in pending:
                del pending[key]
    return out


def compute_stats(events: list[dict]) -> dict:
    """Calcula P&L, win rate, contagens a partir dos eventos do dia."""
    stats = {
        "total_cycles": 0,
        "entered_cycles": 0,
        "filled_count": 0,
        "failed_count": 0,
        "skipped_count": 0,
        "pnl_usdc": 0.0,
        "trades_by_market": defaultdict(lambda: {"filled": 0, "failed": 0, "pnl": 0.0}),
        "last_balance": None,
        "order_latencies_ms": [],
        "position_results": [],  # lista de {"win": bool, "pnl": float} para Win Rate real
    }

    seen_cycles = set()  # (market, cycle_end_ts)
    entered_cycles = set()

    stats["order_latencies_ms"] = compute_order_latencies_ms(events)

    for e in events:
        market = (e.get("market") or "").lower()
        cycle = e.get("cycle_end_ts") or e.get("end_ts")
        action = e.get("action", "")

        if action == "NEW_CYCLE" and cycle:
            seen_cycles.add((market, cycle))

        if action == "PLACING_ORDER" and cycle:
            entered_cycles.add((market, cycle))

        if action == "FILLED":
            stats["filled_count"] += 1
            price = _to_float(e.get("price"))
            if price is not None:
                # Lucro estimado: (1.00 - preço_entrada) × shares
                # Quando o mercado resolve a favor, recebe $1 por share
                size = _to_float(e.get("size")) or 6
                profit = (1.0 - price) * size
                stats["pnl_usdc"] += profit
                stats["trades_by_market"][market]["pnl"] += profit
            stats["trades_by_market"][market]["filled"] += 1

        if action in ("ORDER_FAILED", "TIMEOUT_CANCEL", "CANCEL_HARD_STOP"):
            stats["failed_count"] += 1
            stats["trades_by_market"][market]["failed"] += 1

        if action == "POSITION_RESULT":
            win = e.get("win")
            pnl = _to_float(e.get("pnl"))
            if win is not None:
                stats["position_results"].append({"win": bool(win), "pnl": pnl or 0.0})

        if action == "SKIP_PRICE_OOR":
            stats["skipped_count"] += 1

        if "balance" in e and e["balance"] is not None:
            stats["last_balance"] = _to_float(e["balance"])

    stats["total_cycles"] = len(seen_cycles)
    stats["entered_cycles"] = len(entered_cycles)

    return stats


# ─── Barra de Progresso ──────────────────────────────────────────────────────

def progress_bar(elapsed: int, total: int, width: int = 30) -> str:
    """Barra de progresso visual: [████████░░░░] 63%"""
    if total <= 0:
        return "[" + "░" * width + "]   0%"
    pct = min(1.0, max(0.0, elapsed / total))
    filled = int(width * pct)
    bar = "█" * filled + "░" * (width - filled)

    # Cor baseada no progresso
    if pct >= 0.93:  # Últimos segundos
        color = C.RED + C.BOLD
    elif pct >= 0.80:  # Janela de entrada (4min-1min)
        color = C.YELLOW
    elif pct >= 0.67:  # Perto da janela
        color = C.CYAN
    else:
        color = C.BLUE

    return f"{color}[{bar}]{C.RESET} {pct*100:3.0f}%"


# ─── Colorir estado ──────────────────────────────────────────────────────────

def color_state(state: str) -> str:
    s = state.upper()
    if s == "HOLDING":
        return f"{C.GREEN}{C.BOLD}{s}{C.RESET}"
    if s == "ORDER_PLACED":
        return f"{C.YELLOW}{s}{C.RESET}"
    if s in ("DONE", "FILLED"):
        return f"{C.GREEN}{s}{C.RESET}"
    if s in ("SKIPPED", "ORDER_FAILED"):
        return f"{C.RED}{s}{C.RESET}"
    if s == "IDLE":
        return f"{C.GRAY}{s}{C.RESET}"
    return s


def color_action(action: str) -> str:
    a = action.upper()
    if a == "FILLED":
        return f"{C.GREEN}{C.BOLD}{a}{C.RESET}"
    if a in ("ORDER_FAILED", "CANCEL_HARD_STOP"):
        return f"{C.RED}{a}{C.RESET}"
    if a == "TIMEOUT_CANCEL":
        return f"{C.YELLOW}{a}{C.RESET}"
    if a in ("PLACING_ORDER", "ORDER_PLACED"):
        return f"{C.CYAN}{a}{C.RESET}"
    if a == "NEW_CYCLE":
        return f"{C.BLUE}{a}{C.RESET}"
    if a.startswith("SKIP"):
        return f"{C.GRAY}{a}{C.RESET}"
    return a


def color_result(result: str) -> str:
    r = result.upper()
    if r == "FILLED":
        return f"{C.GREEN}{C.BOLD}FILLED{C.RESET}"
    if r in ("ORDER_FAILED", "CANCEL_HARD_STOP"):
        return f"{C.RED}{r}{C.RESET}"
    if r == "TIMEOUT_CANCEL":
        return f"{C.YELLOW}{r}{C.RESET}"
    return r


def color_price(price_str: str, is_entry: bool = False) -> str:
    """Colore preço: verde se >= 93%, vermelho se fora do range."""
    try:
        val = float(price_str.replace("$", "").replace("%", "")) / 100
        if val >= 0.93:
            return f"{C.GREEN}{price_str}{C.RESET}"
        elif val >= 0.80:
            return f"{C.YELLOW}{price_str}{C.RESET}"
        else:
            return f"{C.GRAY}{price_str}{C.RESET}"
    except (ValueError, AttributeError):
        return price_str


# ─── Dashboard principal ─────────────────────────────────────────────────────

def build_dashboard(events: list[dict], live_data: tuple | None = None) -> str:
    now = int(time.time())
    window_start = (now // WINDOW_SECONDS) * WINDOW_SECONDS
    window_end = window_start + WINDOW_SECONDS
    elapsed = now - window_start
    time_to_expiry = window_end - now
    in_entry_window = ENTRY_WINDOW_END <= time_to_expiry <= ENTRY_WINDOW_START

    live_balance = None
    live_available = None
    if live_data is not None:
        live_prices = live_data[0]
        latency_ms = live_data[1]
        if len(live_data) >= 3:
            live_balance = live_data[2]
        if len(live_data) >= 4:
            live_available = live_data[3]
    else:
        live_prices = {a: {"yes": None, "no": None, "spread": None} for a in ASSETS}
        latency_ms = 0

    # Estatísticas do dia
    stats = compute_stats(events)

    # Por mercado: último estado no ciclo atual
    current_cycle = window_end
    by_market = defaultdict(lambda: {"state": "—", "action": "—", "side": "—", "price": "—", "result": "—", "ts": None})
    order_events = []

    for e in events:
        market = e.get("market", "").upper()
        cycle = e.get("cycle_end_ts") or e.get("end_ts")
        action = e.get("action", "")
        state = e.get("state", "")
        ts = e.get("ts")

        if cycle != current_cycle:
            continue
        by_market[market]["state"] = state
        by_market[market]["action"] = action
        by_market[market]["ts"] = ts
        if "side" in e:
            by_market[market]["side"] = e.get("side", "—")
        if "price" in e and e["price"] is not None:
            by_market[market]["price"] = f"${float(e['price']):.2f}"
        if action in ("FILLED", "TIMEOUT_CANCEL", "CANCEL_HARD_STOP", "ORDER_FAILED", "EXPIRED"):
            by_market[market]["result"] = action

        if action in ("PLACING_ORDER", "ORDER_PLACED", "FILLED", "TIMEOUT_CANCEL", "CANCEL_HARD_STOP", "ORDER_FAILED"):
            order_events.append(e)

    last_orders = order_events[-10:]

    # ─── Renderizar ───────────────────────────────────────────────────────

    W = 88  # largura total
    lines = []

    # Header
    lines.append("")
    lines.append(f"{C.BOLD}{C.CYAN}{'═' * W}{C.RESET}")
    lines.append(f"{C.BOLD}{C.CYAN}  BOT 15MIN — DASHBOARD LIVE v2{C.RESET}")
    lines.append(f"{C.BOLD}{C.CYAN}{'═' * W}{C.RESET}")

    # Info janela + Portfolio e dinheiro para trade (atualizados a cada 10s)
    utc_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    portfolio_val = live_balance if live_balance is not None else stats.get("last_balance")
    portfolio_str = f"${portfolio_val:.2f}" if portfolio_val is not None else "—"
    available_val = live_available if live_available is not None else portfolio_val
    available_str = f"${available_val:.2f}" if available_val is not None else "—"
    expiry_str = format_expiry(time_to_expiry)
    lines.append(f"  {C.DIM}UTC:{C.RESET} {utc_now}    "
                 f"{C.DIM}Janela:{C.RESET} {format_ts(window_start)}—{format_ts(window_end)}    "
                 f"{C.DIM}Expira em:{C.RESET} {C.BOLD}{expiry_str}{C.RESET}    "
                 f"{C.DIM}Portfolio:{C.RESET} {C.BOLD}{portfolio_str}{C.RESET}    "
                 f"{C.DIM}Dinheiro para trade:{C.RESET} {C.BOLD}{available_str}{C.RESET}")

    # Barra de progresso
    bar = progress_bar(elapsed, WINDOW_SECONDS, width=40)
    if in_entry_window:
        entry_tag = f"  {C.BG_YELLOW}{C.BOLD} JANELA DE ENTRADA (4min→1min) {C.RESET}"
    elif time_to_expiry < ENTRY_WINDOW_END:
        entry_tag = f"  {C.BG_RED}{C.BOLD} HARD STOP (<1min) {C.RESET}"
    elif time_to_expiry > ENTRY_WINDOW_START:
        entry_tag = f"  {C.DIM}Aguardando janela de entrada...{C.RESET}"
    else:
        entry_tag = ""
    lines.append(f"  {bar}{entry_tag}")

    # ─── Painel de estatísticas ───────────────────────────────────────────
    lines.append("")
    lines.append(f"  {C.BOLD}{'─' * 42} STATS DO DIA {'─' * 32}{C.RESET}")

    total_trades = stats["filled_count"] + stats["failed_count"]
    exec_rate = (stats["filled_count"] / total_trades * 100) if total_trades > 0 else 0
    results = stats.get("position_results") or []
    wins = sum(1 for r in results if r.get("win"))
    total_closed = len(results)
    win_rate = (wins / total_closed * 100) if total_closed > 0 else None
    pnl = stats["pnl_usdc"]
    pnl_color = C.GREEN if pnl >= 0 else C.RED
    # Saldo: preferir valor atualizado por ciclo 15 min (live_balance), senão do log
    balance = live_balance if live_balance is not None else stats["last_balance"]
    bal_str = f"${balance:.2f}" if balance is not None else "—"
    if live_balance is not None:
        bal_str = f"{C.BOLD}{bal_str}{C.RESET} {C.DIM}(atualizado por ciclo){C.RESET}"

    lines.append(
        f"  {C.DIM}Ciclos:{C.RESET} {stats['total_cycles']:>3}   "
        f"{C.DIM}Entradas:{C.RESET} {stats['entered_cycles']:>3}   "
        f"{C.DIM}Fills:{C.RESET} {C.GREEN}{stats['filled_count']}{C.RESET}   "
        f"{C.DIM}Falhas:{C.RESET} {C.RED}{stats['failed_count']}{C.RESET}   "
        f"{C.DIM}Execucao:{C.RESET} {C.BOLD}{exec_rate:.0f}%{C.RESET}   "
        f"{C.DIM}Win Rate:{C.RESET} {C.BOLD}{f'{win_rate:.0f}%' if win_rate is not None else '—'}{C.RESET}  {C.DIM}({total_closed} fechados){C.RESET}   "
        f"{C.DIM}P&L (est):{C.RESET} {pnl_color}{C.BOLD}${pnl:+.2f}{C.RESET}"
    )
    lines.append(f"  {C.DIM}Saldo USDC:{C.RESET} {bal_str}")

    # P&L por mercado (mini-tabela inline)
    pnl_parts = []
    for asset in ASSETS:
        m_stats = stats["trades_by_market"].get(asset, {"filled": 0, "pnl": 0.0})
        m_pnl = m_stats["pnl"]
        m_fills = m_stats["filled"]
        if m_fills > 0:
            pc = C.GREEN if m_pnl >= 0 else C.RED
            pnl_parts.append(f"{asset.upper()}:{pc}${m_pnl:+.2f}{C.RESET}({m_fills})")
        else:
            pnl_parts.append(f"{asset.upper()}:{C.GRAY}$0.00{C.RESET}(0)")
    lines.append(f"  {C.DIM}P&L por ativo:{C.RESET} {'  '.join(pnl_parts)}")

    # ─── Tabela de mercados ───────────────────────────────────────────────
    lines.append("")
    lines.append(f"  {C.BOLD}{'─' * 42} MERCADOS {'─' * 36}{C.RESET}")
    # Header da tabela
    lines.append(
        f"  {C.BOLD}{'ATIVO':<6}│{'YES':>6} {'NO':>6} {'SPRD':>6} │"
        f" {'ESTADO':<14}│ {'ACAO':<18}│ {'LADO':<5}│ {'PRECO':<7}│ {'RESULTADO':<16}{C.RESET}"
    )
    lines.append(f"  {'─' * 6}┼{'─' * 20}┼{'─' * 15}┼{'─' * 19}┼{'─' * 6}┼{'─' * 8}┼{'─' * 16}")

    for asset in ASSETS:
        m = by_market[asset.upper()]
        lp = live_prices.get(asset, {"yes": None, "no": None, "spread": None})
        yes_v = lp.get("yes")
        no_v = lp.get("no")
        spread_v = lp.get("spread")

        yes_str = f"{yes_v*100:.0f}%" if yes_v is not None else " — "
        no_str = f"{no_v*100:.0f}%" if no_v is not None else " — "
        spread_str = f"{spread_v*100:.1f}c" if spread_v is not None else " — "

        # Colorir preço YES/NO baseado no range 93-98%
        def _color_pct(val, s):
            if val is None:
                return f"{C.GRAY}{s:>6}{C.RESET}"
            if 0.93 <= val <= 0.98:
                return f"{C.GREEN}{C.BOLD}{s:>6}{C.RESET}"
            elif val > 0.98:
                return f"{C.RED}{s:>6}{C.RESET}"
            elif val >= 0.80:
                return f"{C.YELLOW}{s:>6}{C.RESET}"
            else:
                return f"{C.GRAY}{s:>6}{C.RESET}"

        yes_col = _color_pct(yes_v, yes_str)
        no_col = _color_pct(no_v, no_str)
        spread_col = f"{C.DIM}{spread_str:>6}{C.RESET}" if spread_v is not None else f"{C.GRAY}{spread_str:>6}{C.RESET}"

        state_col = color_state(m["state"])
        action_col = color_action(m["action"])
        result_col = color_result(m["result"]) if m["result"] != "—" else f"{C.GRAY}—{C.RESET}"

        # Pad to fixed widths (ANSI chars don't count for display)
        lines.append(
            f"  {C.BOLD}{asset.upper():<6}{C.RESET}│"
            f"{yes_col} {no_col} {spread_col} │"
            f" {state_col:<{14 + 9}}│"  # +9 for ANSI escape chars
            f" {action_col:<{18 + 9}}│"
            f" {str(m['side'])[:5]:<5}│"
            f" {str(m['price'])[:7]:<7}│"
            f" {result_col}"
        )

    # ─── API Latência e latência de envio de ordem (mediana) ───────────────
    lat_color = C.GREEN if latency_ms < 2000 else (C.YELLOW if latency_ms < 5000 else C.RED)
    lines.append(f"\n  {C.DIM}API latencia:{C.RESET} {lat_color}{latency_ms}ms{C.RESET}")
    order_lat = stats.get("order_latencies_ms") or []
    mediana_hoje = _median_ms(order_lat)
    if mediana_hoje is not None:
        order_lat_color = C.GREEN if mediana_hoje < 1000 else (C.YELLOW if mediana_hoje < 3000 else C.RED)
        lines.append(f"  {C.DIM}Envio ordem (mediana):{C.RESET} {order_lat_color}{mediana_hoje}ms{C.RESET}  {C.DIM}({len(order_lat)} ordens hoje){C.RESET}")
    else:
        lines.append(f"  {C.DIM}Envio ordem (mediana):{C.RESET} {C.GRAY}— (nenhuma ordem hoje){C.RESET}")

    # ─── Últimas ordens ──────────────────────────────────────────────────
    lines.append("")
    lines.append(f"  {C.BOLD}{'─' * 42} ULTIMAS ORDENS {'─' * 30}{C.RESET}")
    if not last_orders:
        lines.append(f"  {C.GRAY}Nenhuma ordem neste ciclo.{C.RESET}")
    for e in last_orders:
        ts_str = format_ts(e.get("ts"))
        market = (e.get("market") or "").upper()
        action = e.get("action", "")
        side = e.get("side", "—")
        price = e.get("price")
        price_str = f"${float(price):.2f}" if price is not None else "—"
        oid = e.get("order_id") or ""
        if isinstance(oid, str) and len(oid) > 12:
            oid = oid[:10] + ".."

        action_col = color_action(action)
        side_col = f"{C.GREEN}{side}{C.RESET}" if side == "YES" else (f"{C.RED}{side}{C.RESET}" if side == "NO" else side)

        lines.append(f"  {C.DIM}[{ts_str}]{C.RESET} {C.BOLD}{market:<4}{C.RESET} {action_col:<{18+9}} "
                     f"{side_col:<{5+9}} {price_str:<7} {C.DIM}{oid}{C.RESET}")

    # Footer
    lines.append("")
    lines.append(f"  {C.DIM}Log: {get_log_path().name}  |  Atualiza: 10s  |  Ctrl+C para sair{C.RESET}")
    lines.append("")

    return "\n".join(lines)


# ─── Main loop ────────────────────────────────────────────────────────────────

def main():
    log_path = get_log_path()
    clear_cmd = "clear" if os.name != "nt" else "cls"
    events = []
    try:
        if not log_path.exists():
            print(f"{C.YELLOW}Aguardando log do dia: {log_path.name}{C.RESET}")
            while not log_path.exists():
                time.sleep(1)
        events = load_events(log_path)
        with open(log_path, "r") as f:
            f.seek(0, 2)
            last_pos = f.tell()
            while True:
                # Ler novas linhas
                f.seek(last_pos)
                while True:
                    line = f.readline()
                    if not line:
                        break
                    line = line.strip()
                    if line:
                        try:
                            events.append(json.loads(line))
                            if len(events) > 5000:
                                events = events[-4000:]
                        except json.JSONDecodeError:
                            pass
                    last_pos = f.tell()

                # Verificar se mudou de dia
                new_path = get_log_path()
                if new_path != log_path:
                    log_path = new_path
                    if log_path.exists():
                        events = load_events(log_path)
                        f.close()
                        f = open(log_path, "r")
                        f.seek(0, 2)
                        last_pos = f.tell()

                live_prices, latency_ms = fetch_live_prices()
                portfolio, available = fetch_usdc_balance()
                live_data = (live_prices, latency_ms, portfolio, available)
                os.system(clear_cmd)
                print(build_dashboard(events, live_data))
                time.sleep(10)
    except KeyboardInterrupt:
        print(f"\n{C.YELLOW}Dashboard encerrado.{C.RESET}")
        sys.exit(0)


if __name__ == "__main__":
    main()
