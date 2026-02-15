#!/usr/bin/env python3
"""
Mostra quais mercados 15min o bot está monitorando AGORA.

Usa a mesma lógica do bot_15min.py (slug = {asset}-updown-15m-{window_ts}).
Mostra preços Gamma (usados pelo bot) e CLOB ao vivo para comparar.

USO:
    python scripts/status_bot15min_markets.py
"""

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

GAMMA_HOST = os.getenv("GAMMA_BASE_URL", "https://gamma-api.polymarket.com")
CLOB_HOST = os.getenv("CLOB_BASE_URL", "https://clob.polymarket.com")
ASSETS = ["btc", "eth", "sol", "xrp"]
ENTRY_WINDOW_START = 240  # 4 min antes
ENTRY_WINDOW_END = 60     # 1 min antes


def get_clob_prices(yes_token: str, no_token: str) -> tuple:
    """Preços ao vivo do CLOB: best ask para YES e para NO (para compra)."""
    yes_ask, no_ask = None, None
    try:
        with httpx.Client(timeout=10) as c:
            for token_id, name in [(yes_token, "yes"), (no_token, "no")]:
                r = c.get(f"{CLOB_HOST}/book", params={"token_id": token_id})
                if r.status_code != 200:
                    continue
                book = r.json()
                asks = book.get("asks", [])
                if asks:
                    price = float(asks[0].get("price", 0.5))
                    if name == "yes":
                        yes_ask = price
                    else:
                        no_ask = price
    except Exception:
        pass
    return yes_ask, no_ask


def fetch_market(asset: str) -> dict:
    """Busca mercado atual (janela atual e anterior)."""
    now = int(time.time())
    current_window = (now // 900) * 900
    for window_ts in [current_window, current_window - 900]:
        slug = f"{asset}-updown-15m-{window_ts}"
        try:
            with httpx.Client(timeout=15) as c:
                r = c.get(f"{GAMMA_HOST}/events/slug/{slug}")
            if r.status_code != 200:
                continue
            event = r.json()
            markets = event.get("markets", [])
            if not markets:
                continue
            m = markets[0]
            raw = m.get("clobTokenIds")
            tokens = __import__("json").loads(raw) if isinstance(raw, str) else (raw or [])
            if len(tokens) < 2:
                continue
            yes_token = tokens[0]
            no_token = tokens[1]
            end_date = m.get("endDate") or event.get("endDate")
            if end_date:
                if end_date.endswith("Z"):
                    end_date = end_date[:-1] + "+00:00"
                end_ts = int(datetime.fromisoformat(end_date).timestamp())
            else:
                end_ts = window_ts + 900
            time_to_expiry = end_ts - now
            op = m.get("outcomePrices")
            if op:
                if isinstance(op, str):
                    op = __import__("json").loads(op)
                yes_p, no_p = float(op[0]), float(op[1])
            else:
                yes_p, no_p = 0.50, 0.50
            return {
                "asset": asset,
                "slug": slug,
                "title": event.get("title", slug),
                "end_ts": end_ts,
                "end_iso": datetime.fromtimestamp(end_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                "time_to_expiry": time_to_expiry,
                "yes_price": yes_p,
                "no_price": no_p,
                "yes_token": yes_token,
                "no_token": no_token,
            }
        except Exception as e:
            return {"asset": asset, "error": str(e)}
    return None


def status_label(ttl: int) -> str:
    if ttl <= 0:
        return "FECHADO (expirou)"
    if ttl < ENTRY_WINDOW_END:
        return "FECHANDO (<1min)"
    if ttl <= ENTRY_WINDOW_START:
        return "JANELA ENTRADA (operando)"
    return "AGUARDANDO (>4min)"


def main():
    now = int(time.time())
    window = (now // 900) * 900
    print("=" * 72)
    print("MERCADOS 15MIN QUE O BOT MONITORA AGORA")
    print("=" * 72)
    print(f"  Hora do servidor: {datetime.fromtimestamp(now, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  Janela UTC (início): {window} ({datetime.fromtimestamp(window, tz=timezone.utc).strftime('%H:%M UTC')})")
    print(f"  Ativos: {', '.join(ASSETS)}")
    print()

    for asset in ASSETS:
        info = fetch_market(asset)
        if info is None:
            print(f"  {asset.upper():4} | Nenhum mercado encontrado")
            continue
        if "error" in info:
            print(f"  {asset.upper():4} | ERRO: {info['error']}")
            continue
        ttl = info["time_to_expiry"]
        status = status_label(ttl)
        yes_clob, no_clob = get_clob_prices(info["yes_token"], info["no_token"])
        print(f"  {asset.upper():4} | {info['slug']}")
        print(f"        | Título: {info['title']}")
        print(f"        | Expira: {info['end_iso']} (em {ttl}s)")
        print(f"        | Status: {status}")
        if yes_clob is not None:
            print(f"        | YES: Gamma={info['yes_price']:.2f}  CLOB(ao vivo)={yes_clob:.2f}")
        else:
            print(f"        | YES: Gamma={info['yes_price']:.2f}  CLOB=--")
        if no_clob is not None:
            print(f"        | NO:  Gamma={info['no_price']:.2f}  CLOB(ao vivo)={no_clob:.2f}")
        else:
            print(f"        | NO:  Gamma={info['no_price']:.2f}  CLOB=--")
        print()

    print("=" * 72)
    print("Gamma = preços da API Gamma (podem estar atrasados). CLOB = orderbook ao vivo.")
    print("O BOT usa Gamma para a regra 95%-98%; se Gamma estiver errado, o bot pode nao entrar.")
    print("=" * 72)


if __name__ == "__main__":
    main()
