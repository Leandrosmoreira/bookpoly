#!/usr/bin/env python3
"""
Dashboard LIVE do bot 15min no terminal.
Atualiza em tempo real assim que o bot grava uma nova linha no log (estilo tail -f).

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


def _to_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _book_mid(book: dict) -> float | None:
    """Preço mid: (best_bid + best_ask)/2. Aceita 'price' ou 'p' como string/número."""
    bids = book.get("bids", [])
    asks = book.get("asks", [])
    best_bid = _to_float(bids[0].get("price") or bids[0].get("p")) if bids else None
    best_ask = _to_float(asks[0].get("price") or asks[0].get("p")) if asks else None
    if best_bid is not None and best_ask is not None:
        return round((best_bid + best_ask) / 2, 2)
    return best_bid if best_bid is not None else best_ask


def _clob_price(c, token_id: str) -> float | None:
    """Preço ao vivo: tenta /midpoint (mid), depois /price, depois mid do book."""
    base = CLOB_HOST.rstrip("/")
    for path, key in [("/midpoint", "mid"), ("/price", "price")]:
        endpoint = base + path
        try:
            r = c.get(endpoint, params={"token_id": token_id})
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


def fetch_live_prices() -> dict:
    """Preço % atual (YES, NO) ao vivo: CLOB /midpoint ou /price, fallback book mid (10s)."""
    out = {a: ("—", "—") for a in ASSETS}
    if not httpx:
        return out
    now = int(time.time())
    window_ts = (now // 900) * 900
    try:
        with httpx.Client(timeout=8) as c:
            for asset in ASSETS:
                slug = f"{asset}-updown-15m-{window_ts}"
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
                if yes_p is None or no_p is None:
                    rb_yes = c.get(f"{CLOB_HOST}/book", params={"token_id": yes_token})
                    rb_no = c.get(f"{CLOB_HOST}/book", params={"token_id": no_token})
                    if yes_p is None and rb_yes.status_code == 200:
                        yes_p = _book_mid(rb_yes.json())
                    if no_p is None and rb_no.status_code == 200:
                        no_p = _book_mid(rb_no.json())
                if yes_p is not None or no_p is not None:
                    out[asset] = (f"{yes_p*100:.0f}%" if yes_p is not None else "—", f"{no_p*100:.0f}%" if no_p is not None else "—")
    except Exception:
        pass
    return out


def get_log_path():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return LOG_DIR / f"bot_15min_{today}.jsonl"


def load_events(path: Path, max_lines: int = 2000) -> list[dict]:
    """Carrega últimas linhas do log (eventos mais recentes no final)."""
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
        return "--"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M:%S")


def build_dashboard(events: list[dict], live_prices: dict | None = None) -> str:
    now = int(time.time())
    window_start = (now // 900) * 900
    window_end = window_start + 900
    time_to_expiry = window_end - now

    # Por mercado: último estado no ciclo atual (cycle_end_ts = window_end)
    current_cycle = window_end
    by_market = defaultdict(lambda: {"state": "—", "action": "—", "side": "—", "price": "—", "result": "—", "ts": None})
    order_events = []

    for e in events:
        market = e.get("market", "").upper()
        cycle = e.get("cycle_end_ts") or e.get("end_ts")
        action = e.get("action", "")
        state = e.get("state", "")
        ts = e.get("ts")

        # Considerar apenas ciclo atual (janela que expira em window_end)
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

    if live_prices is None:
        live_prices = {a: ("—", "—") for a in ASSETS}

    # Últimos N eventos de ordem (mais recentes no final)
    last_orders = order_events[-12:]

    lines = []
    lines.append("")
    lines.append("╔══════════════════════════════════════════════════════════════════════════════════════╗")
    lines.append("║  BOT 15MIN — DASHBOARD LIVE                                                        ║")
    lines.append("╠══════════════════════════════════════════════════════════════════════════════════════╣")
    in_entry_window = ENTRY_WINDOW_END <= time_to_expiry <= ENTRY_WINDOW_START
    lines.append("║  Agora (UTC): %s                                                                     ║" % datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("║  Janela: %s — %s   (expira em %4ds)                                                    ║" % (format_ts(window_start), format_ts(window_end), time_to_expiry))
    if in_entry_window:
        lines.append("║  TIMEFRAME — janela de entrada (5min a 1min)                                          ║")
    else:
        lines.append("║                                                                                    ║")
    lines.append("╚══════════════════════════════════════════════════════════════════════════════════════╝")
    lines.append("")
    lines.append("  MERCADO   │ PREÇO % (10s) │ ESTADO        │ ÚLTIMA AÇÃO      │ LADO  │ PREÇO  │ RESULTADO")
    lines.append("            │ YES%   NO%    │               │                  │       │        │")
    lines.append("  ──────────┼───────────────┼───────────────┼──────────────────┼───────┼────────┼──────────────────")
    for asset in ASSETS:
        m = by_market[asset.upper()]
        yes_p, no_p = live_prices.get(asset, ("—", "—"))
        price_col = f"{yes_p:>4} {no_p:>4}"
        lines.append("  %-9s │ %-13s │ %-13s │ %-16s │ %-5s │ %-6s │ %s" % (
            asset.upper(),
            price_col,
            m["state"][:13],
            m["action"][:16],
            str(m["side"])[:5],
            str(m["price"])[:6],
            m["result"][:16],
        ))
    lines.append("")
    lines.append("  ─── ÚLTIMAS ORDENS (entraram ou não) ───")
    for e in last_orders:
        ts_str = format_ts(e.get("ts"))
        market = (e.get("market") or "").upper()
        action = e.get("action", "")
        side = e.get("side", "—")
        price = e.get("price")
        price_str = f"${float(price):.2f}" if price is not None else "—"
        oid = e.get("order_id") or "—"
        if isinstance(oid, str) and len(oid) > 18:
            oid = oid[:10] + "…"
        lines.append("  [%s] %s %s side=%s price=%s %s" % (ts_str, market, action, side, price_str, oid))
    lines.append("")
    lines.append("  Log: %s  (Ctrl+C para sair)" % get_log_path().name)
    lines.append("")
    return "\n".join(lines)


def main():
    log_path = get_log_path()
    clear_cmd = "clear" if os.name != "nt" else "cls"
    events = []
    try:
        if not log_path.exists():
            print("Aguardando log do dia:", log_path.name)
            while not log_path.exists():
                time.sleep(1)
        events = load_events(log_path)
        with open(log_path, "r") as f:
            f.seek(0, 2)
            last_pos = f.tell()
            while True:
                # Ler novas linhas (se houver)
                f.seek(last_pos)
                while True:
                    line = f.readline()
                    if not line:
                        break
                    line = line.strip()
                    if line:
                        try:
                            events.append(json.loads(line))
                            if len(events) > 3000:
                                events = events[-2000:]
                        except json.JSONDecodeError:
                            pass
                    last_pos = f.tell()
                live_prices = fetch_live_prices()
                os.system(clear_cmd)
                print(build_dashboard(events, live_prices))
                time.sleep(10)  # relógio, preço % e "expira em Xs" atualizam a cada 10s
    except KeyboardInterrupt:
        print("\nDashboard encerrado.")
        sys.exit(0)


if __name__ == "__main__":
    main()
