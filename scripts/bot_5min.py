#!/usr/bin/env python3
"""
Bot 24/7 para mercados 5min do Polymarket (BTC, ETH, SOL, XRP).

Baseado no bot 15min, mas com parâmetros:
- Mercado: 5 minutos (window = 300s)
- Entrada: 40s até 15s restantes
- Range: 96% a 99%
- Ordem: LIMIT post-only (igual bot_15min)

USO:
    python scripts/bot_5min.py
"""

from __future__ import annotations

from datetime import datetime
import importlib.util
import sys
from pathlib import Path
from types import ModuleType


WINDOW_SECONDS = 300
SLUG_INTERVAL = "5m"
LOG_PREFIX = "bot_5min"


def _load_bot_15min_module() -> ModuleType:
    """
    Carrega o bot_15min diretamente do arquivo, sem depender de `import scripts...`
    (systemd executa como script e pode não ter `scripts/` como pacote importável).
    """
    module_name = "_bot_15min_base"
    if module_name in sys.modules:
        return sys.modules[module_name]

    bot_path = Path(__file__).resolve().parent / "bot_15min.py"
    spec = importlib.util.spec_from_file_location(module_name, bot_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Falha ao carregar spec do bot base: {bot_path}")

    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _patch_bot_15min():
    # Importa o bot base (mantém toda a lógica de execução/guardrails/stop-loss)
    base = _load_bot_15min_module()

    # Parâmetros solicitados
    base.ENTRY_WINDOW_START = 40  # segundos antes da expiração
    base.ENTRY_WINDOW_END = 15    # hard stop (não entra faltando menos que isso)
    base.MIN_PRICE = 0.96
    base.MAX_PRICE = 0.99
    base.MIN_SHARES = 5
    base.MIN_BALANCE_USDC = 5.2

    # --- Logging: escrever em logs/bot_5min_YYYY-MM-DD.jsonl ---
    def _log_write_5m(self, event: dict):  # noqa: ANN001
        today = datetime.now().strftime("%Y-%m-%d")
        if self._date != today or self._file is None:
            self.close()
            base.LOGS_DIR.mkdir(exist_ok=True)
            self._file = open(base.LOGS_DIR / f"{LOG_PREFIX}_{today}.jsonl", "a", encoding="utf-8")
            self._date = today
        try:
            self._file.write(base.json.dumps(event) + "\n")
            self._file.flush()
        except Exception as e:
            print(f"[LOG ERROR] {e}")

    base._LogWriter.write = _log_write_5m  # type: ignore[attr-defined]
    try:
        base._log_writer.close()  # type: ignore[attr-defined]
    except Exception:
        pass
    base._log_writer = base._LogWriter()  # type: ignore[attr-defined]

    # --- Slug/market discovery: mudar de updown-15m para updown-5m e 300s ---
    def fetch_market_status(asset: str):  # noqa: ANN001
        try:
            http = base.get_http()
            now = int(base.time.time())
            current_window = int(now // WINDOW_SECONDS) * WINDOW_SECONDS
            for window_ts in (current_window, current_window - WINDOW_SECONDS):
                slug = f"{asset}-updown-{SLUG_INTERVAL}-{window_ts}"
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

    def _get_resolved_outcome(asset: str, cycle_end_ts: int, retries: int = 3, delay: float = 3.0):  # noqa: ANN001
        http = base.get_http()
        window_start = cycle_end_ts - WINDOW_SECONDS
        slug = f"{asset}-updown-{SLUG_INTERVAL}-{window_start}"
        for attempt in range(retries):
            try:
                r = http.get(f"{base.GAMMA_HOST}/events/slug/{slug}")
                if r.status_code != 200:
                    if attempt < retries - 1:
                        base.time.sleep(delay)
                    continue
                event = r.json()
                markets = event.get("markets", [])
                if not markets:
                    if attempt < retries - 1:
                        base.time.sleep(delay)
                    continue
                market = markets[0]
                raw = market.get("outcomePrices")
                if raw is None:
                    if attempt < retries - 1:
                        base.time.sleep(delay)
                    continue
                if isinstance(raw, str):
                    raw = [s.strip() for s in raw.split(",")] if "," in raw else [raw]
                if not isinstance(raw, (list, tuple)) or len(raw) < 2:
                    if attempt < retries - 1:
                        base.time.sleep(delay)
                    continue
                try:
                    p0 = float(raw[0])
                    p1 = float(raw[1])
                except (TypeError, ValueError):
                    if attempt < retries - 1:
                        base.time.sleep(delay)
                    continue
                if p0 >= 0.99 and p1 <= 0.01:
                    return "YES"
                if p1 >= 0.99 and p0 <= 0.01:
                    return "NO"
                if attempt < retries - 1:
                    base.time.sleep(delay)
                    continue
            except Exception:
                if attempt < retries - 1:
                    base.time.sleep(delay)
                    continue
        return None

    def _fetch_market_by_slug(http, asset: str, slug: str):  # noqa: ANN001
        try:
            r = http.get(f"{base.GAMMA_HOST}/events/slug/{slug}")
            if r.status_code != 200:
                return None
            event = r.json()
            markets = event.get("markets", [])
            if not markets:
                return None
            market = markets[0]

            raw = market.get("clobTokenIds")
            tokens = base.json.loads(raw) if isinstance(raw, str) else (raw or [])
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

            yes_price = base.get_best_price(yes_token)
            no_price = base.get_best_price(no_token)
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

    # aplica monkeypatches no módulo base
    base.fetch_market_status = fetch_market_status
    base._get_resolved_outcome = _get_resolved_outcome
    base._fetch_market_by_slug = _fetch_market_by_slug

    return base


def main():
    base = _patch_bot_15min()
    base.main()


if __name__ == "__main__":
    main()

