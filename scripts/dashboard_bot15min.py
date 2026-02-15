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

try:
    import httpx
except ImportError:
    httpx = None

LOG_DIR = Path(__file__).parent.parent / "logs"
GAMMA_HOST = os.getenv("GAMMA_BASE_URL", "https://gamma-api.polymarket.com")
CLOB_HOST = os.getenv("CLOB_BASE_URL", "https://clob.polymarket.com")
ASSETS = ["btc", "eth", "sol", "xrp"]
ENTRY_WINDOW_START = 300   # 5 min antes da expiração
ENTRY_WINDOW_END = 60      # 1 min antes (hard stop)

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


# ─── Log ──────────────────────────────────────────────────────────────────────

def get_log_path():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return LOG_DIR / f"bot_15min_{today}.jsonl"


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


# ─── Estatísticas ────────────────────────────────────────────────────────────

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
    }

    seen_cycles = set()  # (market, cycle_end_ts)
    entered_cycles = set()

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
                size = _to_float(e.get("size")) or 5
                profit = (1.0 - price) * size
                stats["pnl_usdc"] += profit
                stats["trades_by_market"][market]["pnl"] += profit
            stats["trades_by_market"][market]["filled"] += 1

        if action in ("ORDER_FAILED", "TIMEOUT_CANCEL", "CANCEL_HARD_STOP"):
            stats["failed_count"] += 1
            stats["trades_by_market"][market]["failed"] += 1

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
    elif pct >= 0.80:  # Janela de entrada (5min-1min)
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
    window_start = (now // 900) * 900
    window_end = window_start + 900
    elapsed = now - window_start
    time_to_expiry = window_end - now
    in_entry_window = ENTRY_WINDOW_END <= time_to_expiry <= ENTRY_WINDOW_START

    if live_data is not None:
        live_prices, latency_ms = live_data
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

    # Info janela
    utc_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    lines.append(f"  {C.DIM}UTC:{C.RESET} {utc_now}    "
                 f"{C.DIM}Janela:{C.RESET} {format_ts(window_start)}—{format_ts(window_end)}    "
                 f"{C.DIM}Expira em:{C.RESET} {C.BOLD}{time_to_expiry}s{C.RESET}")

    # Barra de progresso
    bar = progress_bar(elapsed, 900, width=40)
    if in_entry_window:
        entry_tag = f"  {C.BG_YELLOW}{C.BOLD} JANELA DE ENTRADA (5min→1min) {C.RESET}"
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
    win_rate = (stats["filled_count"] / total_trades * 100) if total_trades > 0 else 0
    pnl = stats["pnl_usdc"]
    pnl_color = C.GREEN if pnl >= 0 else C.RED
    bal_str = f"${stats['last_balance']:.2f}" if stats["last_balance"] is not None else "—"

    lines.append(
        f"  {C.DIM}Ciclos:{C.RESET} {stats['total_cycles']:>3}   "
        f"{C.DIM}Entradas:{C.RESET} {stats['entered_cycles']:>3}   "
        f"{C.DIM}Fills:{C.RESET} {C.GREEN}{stats['filled_count']}{C.RESET}   "
        f"{C.DIM}Falhas:{C.RESET} {C.RED}{stats['failed_count']}{C.RESET}   "
        f"{C.DIM}Win Rate:{C.RESET} {C.BOLD}{win_rate:.0f}%{C.RESET}   "
        f"{C.DIM}P&L (est):{C.RESET} {pnl_color}{C.BOLD}${pnl:+.2f}{C.RESET}   "
        f"{C.DIM}Saldo:{C.RESET} {bal_str}"
    )

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

    # ─── API Latência ─────────────────────────────────────────────────────
    lat_color = C.GREEN if latency_ms < 2000 else (C.YELLOW if latency_ms < 5000 else C.RED)
    lines.append(f"\n  {C.DIM}API latencia:{C.RESET} {lat_color}{latency_ms}ms{C.RESET}")

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

                live_data = fetch_live_prices()
                os.system(clear_cmd)
                print(build_dashboard(events, live_data))
                time.sleep(10)
    except KeyboardInterrupt:
        print(f"\n{C.YELLOW}Dashboard encerrado.{C.RESET}")
        sys.exit(0)


if __name__ == "__main__":
    main()
