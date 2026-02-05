# PLAN — Polymarket Book Recorder 1Hz (v3 — revisado)

## 1. Objetivo

Servico que a cada segundo captura e persiste:

- Snapshot do order book (top N niveis) dos tokens YES e NO
- Best bid, best ask, mid e spread de cada token
- Overround (vig) entre YES e NO
- Probabilidade implicita (mid do YES token)
- Imbalance e depth

Mercados-alvo: BTC 15m, ETH 15m, SOL 15m, XRP 15m

Saida: JSONL com timestamp do sistema, um arquivo por mercado por dia.

---

## 2. Arquitetura

### Fase 1 — REST polling (este plano)

- Poll 1x/segundo usando batch endpoint `GET /books` da CLOB API
- 2 requests/segundo: 1 para 4 tokens YES, 1 para 4 tokens NO (ou 1 unico se o batch aceitar 8)
- Fallback para requests individuais se batch falhar

### Fase 2 — WebSocket (futuro)

- Manter book em memoria via `wss://ws-subscriptions-clob.polymarket.com/ws/`
- Canal `market` (sem auth), subscribe com `assets_ids`
- Snapshot a cada 1 segundo do book em memoria
- A interface do recorder.py sera identica — so muda a source

---

## 3. APIs utilizadas

| Servico     | Base URL                                   | Uso                        |
|-------------|-------------------------------------------|----------------------------|
| CLOB API    | `https://clob.polymarket.com`              | Order book, precos, /time  |
| Gamma API   | `https://gamma-api.polymarket.com`         | Descoberta de mercado      |

### Endpoints usados

| Endpoint                              | API   | Finalidade                              |
|---------------------------------------|-------|-----------------------------------------|
| `GET /book?token_id=X`               | CLOB  | Book de um token (fallback)             |
| `GET /books` (body: BookParams[])     | CLOB  | Books em batch (primario)               |
| `GET /time`                           | CLOB  | Sincronizacao de relogio                |
| `GET /events/slug/{slug}`             | Gamma | Descoberta do mercado ativo por slug    |

### Rate limits relevantes (por 10 segundos)

| Endpoint   | Limite   |
|------------|----------|
| `/book`    | 1,500    |
| `/books`   | 500      |
| Gamma geral| 4,000    |
| `/events`  | 500      |

A 1Hz com batch: ~1-2 req/s = ~10-20 req/10s. Bem dentro dos limites.

---

## 4. Estrutura de pastas

```
polymarket-book-recorder/
├── src/
│   ├── config.py            # Configuracoes e constantes
│   ├── clob_client.py       # Wrapper da CLOB API (book, time)
│   ├── market_discovery.py  # Calcula slug, busca token IDs via Gamma
│   ├── recorder.py          # Loop 1Hz, calcula metricas, monta rows
│   ├── writer.py            # Escrita JSONL com rotacao diaria
│   └── main.py              # Entry point, signal handling, orquestracao
├── data/
│   └── raw/
│       └── books/            # BTC15m_2026-02-04.jsonl etc.
├── requirements.txt
└── .env
```

---

## 5. Configuracao

### .env
```
CLOB_BASE_URL=https://clob.polymarket.com
GAMMA_BASE_URL=https://gamma-api.polymarket.com
POLL_HZ=1
DEPTH_LEVELS=50
REQUEST_TIMEOUT_S=2.0
MAX_RETRIES=2
COINS=btc,eth,sol,xrp
WINDOW=15m
OUT_DIR=data/raw/books
```

### config.py
```python
import os
from dataclasses import dataclass

@dataclass(frozen=True)
class Config:
    clob_base: str = os.getenv("CLOB_BASE_URL", "https://clob.polymarket.com")
    gamma_base: str = os.getenv("GAMMA_BASE_URL", "https://gamma-api.polymarket.com")
    poll_hz: int = int(os.getenv("POLL_HZ", "1"))
    depth_levels: int = int(os.getenv("DEPTH_LEVELS", "50"))
    request_timeout: float = float(os.getenv("REQUEST_TIMEOUT_S", "2.0"))
    max_retries: int = int(os.getenv("MAX_RETRIES", "2"))
    coins: list[str] = None  # populated in __post_init__
    window: str = os.getenv("WINDOW", "15m")
    out_dir: str = os.getenv("OUT_DIR", "data/raw/books")

    def __post_init__(self):
        raw = os.getenv("COINS", "btc,eth,sol,xrp")
        object.__setattr__(self, "coins", [c.strip().lower() for c in raw.split(",")])
```

---

## 6. Descoberta de mercado (market_discovery.py)

### Logica do slug

```python
def current_slug(coin: str, server_time_s: float) -> str:
    """Calcula o slug do mercado 15m ativo agora."""
    ts = int(server_time_s // 900) * 900
    return f"{coin}-updown-15m-{ts}"
```

### Fluxo

1. No startup, chamar `GET /time` da CLOB para obter server_time
2. Para cada coin, calcular slug e buscar `GET /events/slug/{slug}` na Gamma
3. Extrair do response:
   - `condition_id` = market["conditionId"]
   - `yes_token` = market["clobTokenIds"][0]
   - `no_token` = market["clobTokenIds"][1]
4. Cachear resultado com TTL = proximo limite de 15 min

### Quando redescobrir

- A cada transicao de janela de 15 min (i.e., quando `time.time() // 900` mudar)
- Se um fetch do book retornar 404 (mercado resolvido/fechado)
- Retry com backoff se Gamma retornar erro

### Transicao de mercado

Durante a transicao (~primeiros 5-15s de uma nova janela):
- O novo mercado pode nao existir ainda na Gamma
- Retry com backoff exponencial curto (0.5s, 1s, 2s)
- Enquanto nao descobrir, registrar row com `error: "market_not_found"`
- Nunca travar o loop por causa de discovery

---

## 7. Captura do book (clob_client.py)

### Fetch batch (primario)

```python
async def fetch_books(session, token_ids: list[str]) -> dict[str, OrderBook]:
    """Busca books de multiplos tokens via GET /books."""
    # Monta query params conforme API
    # Retorna dict de token_id -> {bids, asks}
```

### Normalizacao

```python
def normalize_book(raw: dict, depth: int) -> dict:
    bids = sorted(raw["bids"], key=lambda x: float(x["price"]), reverse=True)[:depth]
    asks = sorted(raw["asks"], key=lambda x: float(x["price"]))[:depth]
    return {
        "bids": [{"price": float(b["price"]), "size": float(b["size"])} for b in bids],
        "asks": [{"price": float(a["price"]), "size": float(a["size"])} for a in asks],
    }
```

Nota: a API retorna price/size como strings. Converter para float na normalizacao.

---

## 8. Metricas por tick (recorder.py)

Para cada mercado, a cada segundo, calcular a partir do book YES e book NO:

### 8.1 Por token (YES e NO separados)

```python
best_bid = bids[0]["price"] if bids else None   # melhor compra
best_ask = asks[0]["price"] if asks else None   # melhor venda
spread = best_ask - best_bid if both else None  # spread do token
mid = (best_bid + best_ask) / 2 if both else None
```

### 8.2 Metricas combinadas do mercado

```python
# Probabilidade implicita (usa mid do YES token)
prob_up = mid_yes                          # e.g. 0.41 = 41%
prob_down = 1.0 - prob_up                  # e.g. 0.59 = 59%

# Overround (vig do market maker)
# Se voce comprar YES ao ask + NO ao ask, paga mais que $1
overround = best_ask_yes + best_ask_no - 1.0   # e.g. 0.42 + 0.60 - 1.0 = 0.02 (2 centavos de vig)

# Spread sintetico para quem opera YES
# Comprar YES ao ask, vender ao bid
synthetic_spread_yes = best_ask_yes - best_bid_yes  # spread real para operar

# Depth e imbalance do YES token
total_bid_depth = sum(b["size"] for b in bids_yes)
total_ask_depth = sum(a["size"] for a in asks_yes)
imbalance = (total_bid_depth - total_ask_depth) / (total_bid_depth + total_ask_depth) if denom > 0 else 0
```

### 8.3 Nota sobre o "spread de 1 centavo" do plano original

O calculo `up_cents + down_cents - 100 = 1` do plano original e o **overround**, nao o spread.
O spread de um token e `ask - bid` desse token. Sao conceitos diferentes:

- **Spread YES**: quanto custa para cruzar o book do YES token (friccao de entrada/saida)
- **Overround**: quanto o market maker cobra acima do fair value por completar o mercado

Ambos sao uteis, mas devem ser nomeados corretamente.

---

## 9. Loop 1Hz (main.py / recorder.py)

```python
async def run(config: Config):
    writer = Writer(config.out_dir)
    discovery = MarketDiscovery(config)
    client = ClobClient(config)

    # Sincronizar relogio
    server_offset = await client.get_time_offset()

    # Descoberta inicial
    markets = await discovery.discover_all(server_offset)

    seq = 0
    current_window = int(time.time() // 900)

    async with aiohttp.ClientSession() as session:
        while True:
            t0 = time.monotonic()
            ts_system = time.time()

            # Redescobrir se janela mudou
            new_window = int(ts_system // 900)
            if new_window != current_window:
                current_window = new_window
                markets = await discovery.discover_all(server_offset)

            # Fetch books (batch)
            all_token_ids = []
            for m in markets.values():
                all_token_ids.extend([m["yes_token"], m["no_token"]])

            try:
                books = await client.fetch_books(session, all_token_ids)
                latency_ms = (time.monotonic() - t0) * 1000
            except Exception as e:
                # Registrar erro, continuar
                for coin, info in markets.items():
                    writer.write_error_row(coin, seq, ts_system, str(e))
                seq += 1
                await sleep_until_next(t0, config.poll_hz)
                continue

            # Montar e escrever rows
            for coin, info in markets.items():
                yes_book = books.get(info["yes_token"])
                no_book = books.get(info["no_token"])
                row = build_row(coin, info, yes_book, no_book, seq, ts_system, latency_ms)
                writer.write(coin, row)

            seq += 1
            await sleep_until_next(t0, config.poll_hz)


async def sleep_until_next(t0: float, hz: int):
    """Dorme ate o proximo tick, compensando drift."""
    elapsed = time.monotonic() - t0
    sleep_time = (1.0 / hz) - elapsed
    if sleep_time > 0:
        await asyncio.sleep(sleep_time)
```

---

## 10. Writer (writer.py)

- Um arquivo JSONL por mercado por dia: `BTC15m_2026-02-04.jsonl`
- Abrir arquivo em modo append (`"a"`)
- Cachear file handle, rotacionar quando o dia mudar (UTC)
- Flush apos cada write (garante durabilidade)
- No shutdown, fechar todos os handles

```python
class Writer:
    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self._handles: dict[str, IO] = {}
        self._current_date: str = ""
        os.makedirs(base_dir, exist_ok=True)

    def write(self, market: str, row: dict):
        today = datetime.utcnow().strftime("%Y-%m-%d")
        if today != self._current_date:
            self._rotate(today)
        key = f"{market}_{today}"
        if key not in self._handles:
            path = os.path.join(self.base_dir, f"{market}_{today}.jsonl")
            self._handles[key] = open(path, "a", encoding="utf-8")
        f = self._handles[key]
        f.write(json.dumps(row, separators=(",", ":")) + "\n")
        f.flush()

    def close_all(self):
        for f in self._handles.values():
            f.close()
        self._handles.clear()
```

---

## 11. Modelo de dados JSONL (revisado)

```json
{
  "v": 2,
  "ts_ms": 1738713600123,
  "ts_iso": "2026-02-04T23:00:00.123Z",
  "seq": 18422,

  "market": "BTC15m",
  "condition_id": "0xabc123...",
  "window_start": 1738713600,

  "yes": {
    "token_id": "0xyes...",
    "best_bid": 0.40,
    "best_ask": 0.42,
    "mid": 0.41,
    "spread": 0.02,
    "bid_depth": 520.0,
    "ask_depth": 430.0,
    "imbalance": 0.095,
    "bids": [
      {"p": 0.40, "s": 120.0},
      {"p": 0.39, "s": 80.0}
    ],
    "asks": [
      {"p": 0.42, "s": 90.0},
      {"p": 0.43, "s": 110.0}
    ]
  },

  "no": {
    "token_id": "0xno...",
    "best_bid": 0.57,
    "best_ask": 0.60,
    "mid": 0.585,
    "spread": 0.03,
    "bid_depth": 480.0,
    "ask_depth": 390.0,
    "imbalance": 0.103,
    "bids": [
      {"p": 0.57, "s": 95.0},
      {"p": 0.56, "s": 140.0}
    ],
    "asks": [
      {"p": 0.60, "s": 60.0},
      {"p": 0.61, "s": 75.0}
    ]
  },

  "derived": {
    "prob_up": 0.41,
    "prob_down": 0.59,
    "overround": 0.02,
    "mid_yes_cents": 41.0,
    "mid_no_cents": 58.5
  },

  "fetch": {
    "latency_ms": 142,
    "method": "batch_rest"
  },

  "err": null
}
```

### Diferencas vs plano original

| Campo                | Plano original              | Plano revisado                    | Motivo                                    |
|---------------------|-----------------------------|----------------------------------|-------------------------------------------|
| `schema_version`     | "1.0"                       | `"v": 2` (mais curto)            | Economiza bytes em JSONL                  |
| `prices.up_cents`    | Usava best_ask              | `yes.best_bid`, `yes.best_ask`, `yes.mid` | Separar bid/ask/mid e mais correto       |
| `prices.spread_cents`| `ask_yes + ask_no - 100`    | `yes.spread` = ask-bid do token  | O original era overround, nao spread      |
| overround           | Nao existia                 | `derived.overround`              | Agora explicito e nomeado corretamente    |
| Book structure      | `book.yes.bids/asks`        | `yes.bids/asks` (flat)           | Menos nesting, acesso mais rapido         |
| Keys do book        | `{"price":..., "size":...}` | `{"p":..., "s":...}`             | Economia ~40% em bytes por nivel          |
| `market_id`         | Campo generico              | `condition_id`                   | Nome correto da API                       |
| `window_start`      | Nao existia                 | Unix timestamp da janela 15min   | Util para agrupar/filtrar dados           |
| `poll_seq`          | Nome verboso                | `seq`                            | Mais curto                                |
| `fetch.source`      | "polymarket_rest"           | Removido (redundante)            | Metodo ja indica                          |
| `fetch.http_status` | 200                         | Removido                         | So interessa se erro, e ai vai em `err`   |

---

## 12. Graceful shutdown (main.py)

```python
import signal

shutdown_event = asyncio.Event()

def handle_signal(sig, frame):
    shutdown_event.set()

signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)

# No loop:
while not shutdown_event.is_set():
    # ... poll ...

# Cleanup
writer.close_all()
```

---

## 13. Dependencias (requirements.txt)

```
aiohttp>=3.9
python-dotenv>=1.0
```

Sem py-clob-client (nao precisamos de auth, e a lib adiciona complexidade desnecessaria para read-only).

---

## 14. Checklist de implementacao

1. [ ] config.py — dataclass com .env
2. [ ] clob_client.py — fetch_books (batch), fetch_book (individual), get_time_offset
3. [ ] market_discovery.py — calculo de slug, fetch via Gamma, cache com TTL
4. [ ] recorder.py — build_row com todas as metricas
5. [ ] writer.py — JSONL com rotacao diaria e flush
6. [ ] main.py — loop asyncio 1Hz, signal handling, orquestracao
7. [ ] Testar com 1 mercado antes de ligar os 4
8. [ ] Validar que rate limits nao sao atingidos (monitorar 429s)
