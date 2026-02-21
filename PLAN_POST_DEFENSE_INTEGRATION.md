# PLAN: Integracao Post-Defense com bot_15min.py

## Resumo

Integrar o sistema de indicadores `post_defense/` (ja implementado) com o
bot_15min.py, adicionando: state machine de defesa, logica de decisao baseada
no RPI/severity, e execucao de hedge via ordens IOC (taker) no lado oposto.

**Estrategia de hedge**: manter posicao original + abrir hedge no lado oposto.
Nao tenta vender a posicao original (slippage em books iliquidos da Polymarket).

---

## 1. Arquivos NOVOS (3)

### 1.1 `scripts/post_defense/state_machine.py` (~120 linhas)

State machine com 5 fases:

```
NORMAL ──(severity > 0)──> ALERT
ALERT  ──(severity > alert_confirm_ticks)──> DEFENSE
ALERT  ──(severity == 0 por cooldown)──> NORMAL
DEFENSE ──(severity >= panic_threshold)──> PANIC
DEFENSE ──(severity == 0 por exit_cooldown)──> NORMAL
PANIC  ──(!allow_reversal OU time < 60s)──> EXIT
EXIT   ──(fim do ciclo)──> NORMAL
```

Estruturas:
```python
class DefensePhase(Enum):
    NORMAL = "NORMAL"       # Monitorando, sem acao
    ALERT = "ALERT"         # severity > 0 detectado, aguardando confirmacao
    DEFENSE = "DEFENSE"     # Hedge parcial colocado
    PANIC = "PANIC"         # Hedge maximo, situacao critica
    EXIT = "EXIT"           # Saindo da posicao (futuro)

@dataclass
class DefenseStateTracker:
    phase: DefensePhase = DefensePhase.NORMAL
    phase_entered_ts: float = 0.0        # Quando entrou na fase atual
    alert_ticks: int = 0                 # Ticks consecutivos com severity > 0
    last_hedge_ts: float = 0.0           # Timestamp do ultimo hedge
    total_hedge_shares: int = 0          # Total de shares hedgeadas neste ciclo
    hedge_order_id: Optional[str] = None # Ordem de hedge pendente
    hedge_side: Optional[str] = None     # Lado do hedge ("YES" ou "NO")
```

Funcoes:
```python
def evaluate_transition(
    tracker: DefenseStateTracker,
    snap: TickSnapshot,
    config: PostDefenseConfig,
    now_ts: float,
) -> tuple[DefensePhase, str]:
    """
    Avalia transicao de fase baseada no TickSnapshot.
    Retorna (nova_fase, razao).

    Regras:
    - NORMAL -> ALERT: severity > 0
    - ALERT -> DEFENSE: alert_ticks >= PD_ALERT_CONFIRM_TICKS (default: 3)
                        E allow_reversal == True
    - ALERT -> NORMAL: severity == 0 por PD_ALERT_COOLDOWN_S (default: 5s)
    - DEFENSE -> PANIC: severity >= PD_PANIC_THRESHOLD (default: 0.7)
                        E adverse_move > PD_PANIC_ADVERSE_MIN (default: 0.02)
    - DEFENSE -> NORMAL: severity == 0 por PD_DEFENSE_EXIT_S (default: 10s)
    - PANIC -> EXIT: !allow_reversal OU time_left < 60s
    - EXIT: sem transicao automatica (fim do ciclo reseta)
    """
```

### 1.2 `scripts/post_defense/hedge.py` (~100 linhas)

Calculo de sizing e helpers de execucao:

```python
def calc_hedge_shares(
    severity: float,
    position_shares: int,
    total_hedged: int,
    phase: DefensePhase,
    config: PostDefenseConfig,
) -> int:
    """
    Calcula quantas shares hedgear AGORA.

    Logica:
    - DEFENSE: hedge_pct = lerp(min_hedge, max_hedge, severity)
              = lerp(0.20, 0.80, severity)
    - PANIC: hedge_pct = max_hedge (0.80)
    - Subtrai total_hedged ja feito
    - Minimo PD_MIN_SHARES (5) ou 0 se ja hedgeou suficiente
    - Maximo: position_shares * max_hedge - total_hedged

    Retorna 0 se nao precisa hedgear mais.
    """

def calc_hedge_price(best_ask: float, phase: DefensePhase) -> float:
    """
    Preco para ordem de hedge (IOC taker).

    DEFENSE: best_ask (cruza spread, fill imediato)
    PANIC: best_ask + 0.01 (garante fill mesmo se book mover)

    Clampado em [0.01, 0.99].
    """

def get_opposite_token(ctx, entered_side: str) -> str:
    """
    Retorna token_id do lado oposto.
    YES entrou -> hedge com NO (ctx.no_token_id)
    NO entrou -> hedge com YES (ctx.yes_token_id)
    """
```

### 1.3 `scripts/post_defense/decision.py` (~80 linhas)

Logica de decisao central que junta state machine + hedge:

```python
@dataclass
class DefenseDecision:
    """Resultado da avaliacao de defesa a cada tick."""
    phase: DefensePhase
    prev_phase: DefensePhase
    phase_changed: bool
    reason: str

    # Acao
    should_hedge: bool = False
    hedge_shares: int = 0
    hedge_price: float = 0.0
    hedge_token_id: str = ""
    hedge_type: str = ""  # "FOK" para taker imediato

    # Diagnostico
    severity: float = 0.0
    rpi: float = 0.0
    rpi_threshold: float = 0.0
    adverse_move: Optional[float] = None
    time_left_s: int = 0

def evaluate_defense(
    tracker: DefenseStateTracker,
    snap: TickSnapshot,
    ctx,  # MarketContext
    config: PostDefenseConfig,
    now_ts: float,
) -> DefenseDecision:
    """
    Funcao principal chamada a cada tick durante HOLDING.

    1. Avalia transicao de fase (state_machine.evaluate_transition)
    2. Se fase mudou, loga transicao
    3. Se fase in (DEFENSE, PANIC):
       a. Verifica cooldown desde ultimo hedge (PD_HEDGE_COOLDOWN_S = 10s)
       b. Verifica saldo USDC
       c. Calcula hedge_shares
       d. Se hedge_shares > 0, preenche should_hedge=True
    4. Retorna DefenseDecision
    """
```

---

## 2. Arquivos ALTERADOS (3)

### 2.1 `scripts/post_defense/config.py` — Novos parametros

Adicionar ao PostDefenseConfig:

```python
# -- State Machine --
alert_confirm_ticks: int = int(os.getenv("PD_ALERT_CONFIRM_TICKS", "3"))
alert_cooldown_s: float = float(os.getenv("PD_ALERT_COOLDOWN_S", "5.0"))
panic_threshold: float = float(os.getenv("PD_PANIC_THRESHOLD", "0.70"))
panic_adverse_min: float = float(os.getenv("PD_PANIC_ADVERSE_MIN", "0.02"))
defense_exit_s: float = float(os.getenv("PD_DEFENSE_EXIT_S", "10.0"))

# -- Hedge Execution --
hedge_cooldown_s: float = float(os.getenv("PD_HEDGE_COOLDOWN_S", "10.0"))
hedge_panic_markup: float = float(os.getenv("PD_HEDGE_PANIC_MARKUP", "0.01"))
```

### 2.2 `scripts/post_defense/__init__.py` — Novos exports

```python
from .state_machine import DefensePhase, DefenseStateTracker, evaluate_transition
from .hedge import calc_hedge_shares, calc_hedge_price, get_opposite_token
from .decision import DefenseDecision, evaluate_defense
```

### 2.3 `scripts/bot_15min.py` — Integracao principal

#### 2.3.1 Novos imports (topo do arquivo, ~linha 29)

```python
from post_defense import (
    PostDefenseEngine, PostDefenseConfig, PositionMeta,
    DefensePhase, DefenseStateTracker, DefenseDecision,
    evaluate_defense as pd_evaluate,
)
from post_defense.hedge import get_opposite_token
```

#### 2.3.2 Import de OrderType.FOK (~linha 36)

```python
from py_clob_client.clob_types import OrderArgs, OrderType, ApiCreds, ...
```
(FOK ja esta disponivel no OrderType, nao precisa mudar import)

#### 2.3.3 Nova funcao: place_hedge_order (~apos place_order, ~linha 525)

```python
def place_hedge_order(token_id: str, price: float, size: float) -> Optional[str]:
    """Envia ordem de hedge FOK (taker, fill imediato)."""
    try:
        client = get_client()
        order_args = OrderArgs(token_id=token_id, price=price, size=size, side=BUY)
        signed_order = client.create_order(order_args)
        resp = client.post_order(signed_order, OrderType.FOK, post_only=False)
        if resp.get("success"):
            return resp.get("orderID")
        else:
            print(f"[ERRO] place_hedge_order: {resp}")
            return None
    except Exception as e:
        print(f"[ERRO] place_hedge_order: {e}")
        return None
```

#### 2.3.4 MarketContext — novos campos (~linha 82)

```python
@dataclass
class MarketContext:
    ...campos existentes...
    # Post-defense
    defense_tracker: Optional[DefenseStateTracker] = None
```

#### 2.3.5 Inicializacao dos engines (~linha 626, junto com guardrails)

```python
contexts = {asset: MarketContext(asset=asset) for asset in ASSETS}
guardrails = {asset: GuardrailsPro(asset=asset) for asset in ASSETS}
pd_config = PostDefenseConfig()
pd_engines = {asset: PostDefenseEngine(asset, pd_config) for asset in ASSETS}
```

#### 2.3.6 Feed de preco ao engine (SEMPRE, nao so HOLDING) (~linha 649)

```python
# Ja existe:
guardrails[asset].update(float(now), yes_price, no_price)

# ADICIONAR logo abaixo:
# Feed midpoint ao engine (acumula historico para quando entrar HOLDING)
mid_price = yes_price  # midpoint do lado YES (bot sempre olha YES/NO)
pd_engines[asset].update(float(now), mid_price, time_to_expiry)
```

NOTA: aqui NEM busca book (custo zero). Book so e buscado durante HOLDING.

#### 2.3.7 No FILL (~linha 774, apos `ctx.state = MarketState.HOLDING`)

```python
# APOS log_event("FILLED"):
# Iniciar tracking de defesa
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
log_event("DEFENSE_STARTED", asset, ctx,
    vol_entry_short=round(vol_s, 6),
    vol_entry_long=round(vol_l, 6))
```

#### 2.3.8 HOLDING — o ponto CRITICO (~linha 708-710)

ANTES:
```python
# 6. Ja em posicao (FILLED) ou ciclo encerrado?
if ctx.state in (MarketState.HOLDING, MarketState.DONE, MarketState.SKIPPED):
    continue
```

DEPOIS:
```python
# 6. Ciclo encerrado?
if ctx.state in (MarketState.DONE, MarketState.SKIPPED):
    continue

# 6a. HOLDING — defesa pos-entrada
if ctx.state == MarketState.HOLDING:
    if pd_config.enabled and ctx.defense_tracker is not None:
        # Buscar book (1 HTTP call extra por tick HOLDING)
        entered_token = ctx.yes_token_id if ctx.entered_side == "YES" else ctx.no_token_id
        book_json = fetch_book(entered_token) if entered_token else None

        # Update engine com book
        snap = pd_engines[asset].update(
            float(now), yes_price, time_to_expiry, book_json
        )

        # Avaliar defesa
        decision = pd_evaluate(
            ctx.defense_tracker, snap, ctx, pd_config, float(now),
        )

        # Log a cada tick (no JSONL do post_defense, ja feito pelo engine)
        # Log transicao de fase no bot log
        if decision.phase_changed:
            log_event("DEFENSE_PHASE", asset, ctx,
                prev=decision.prev_phase.value,
                new=decision.phase.value,
                reason=decision.reason,
                severity=round(decision.severity, 4),
                rpi=round(decision.rpi, 4),
                adverse_move=decision.adverse_move)

        # Executar hedge se necessario
        if decision.should_hedge and decision.hedge_shares > 0:
            log_event("HEDGE_PLACING", asset, ctx,
                phase=decision.phase.value,
                shares=decision.hedge_shares,
                price=decision.hedge_price,
                token=decision.hedge_token_id,
                severity=round(decision.severity, 4))

            hedge_id = place_hedge_order(
                decision.hedge_token_id,
                decision.hedge_price,
                decision.hedge_shares,
            )

            if hedge_id:
                # FOK: fill e imediato, verificar resultado
                filled = check_order_filled(hedge_id)
                if filled:
                    ctx.defense_tracker.total_hedge_shares += decision.hedge_shares
                    ctx.defense_tracker.last_hedge_ts = float(now)
                    ctx.defense_tracker.hedge_order_id = hedge_id
                    log_event("HEDGE_FILLED", asset, ctx,
                        phase=decision.phase.value,
                        shares=decision.hedge_shares,
                        price=decision.hedge_price,
                        total_hedged=ctx.defense_tracker.total_hedge_shares)
                else:
                    log_event("HEDGE_NOT_FILLED", asset, ctx,
                        phase=decision.phase.value,
                        shares=decision.hedge_shares,
                        reason="FOK_not_matched")
            else:
                log_event("HEDGE_FAILED", asset, ctx,
                    phase=decision.phase.value,
                    reason="order_rejected")

    continue  # HOLDING nao entra na logica de entrada
```

#### 2.3.9 No reset de ciclo (~linha 674, reset_context)

```python
# No reset_context(), adicionar:
ctx.defense_tracker = None

# No NEW_CYCLE (~linha 675), adicionar:
pd_engines[asset].clear_position()
```

#### 2.3.10 No POSITION_RESULT (~linha 661-673)

Adicionar info de hedge ao log de resultado:

```python
if ctx.defense_tracker:
    hedge_total = ctx.defense_tracker.total_hedge_shares
    final_phase = ctx.defense_tracker.phase.value
else:
    hedge_total = 0
    final_phase = "NONE"

log_event("POSITION_RESULT", asset, ctx,
    ...campos existentes...,
    hedge_shares=hedge_total,
    defense_phase=final_phase)
```

---

## 3. Fluxo de Dados Completo

```
Loop 1Hz
  |
  v
fetch_market_status() -> yes_price, no_price, time_to_expiry
  |
  v
guardrails.update(yes_price, no_price)  [sempre]
pd_engines.update(mid_price, time_left)  [sempre, SEM book]
  |
  +-- Se IDLE/ORDER_PLACED: logica de entrada normal
  |
  +-- Se HOLDING:
        |
        v
      fetch_book(token_id)  [1 HTTP call extra]
        |
        v
      pd_engines.update(mid, time, book_json) -> TickSnapshot
        |  (grava JSONL com 28 indicadores)
        |
        v
      evaluate_defense(tracker, snap, ctx, config)
        |
        +-- evaluate_transition() -> nova fase
        |     |
        |     +-- NORMAL->ALERT: severity > 0
        |     +-- ALERT->DEFENSE: 3 ticks confirmados
        |     +-- DEFENSE->PANIC: severity >= 0.7 + adverse >= 0.02
        |
        +-- calc_hedge_shares(severity, shares, hedged, phase)
        |     |
        |     +-- DEFENSE: lerp(20%, 80%, severity) - ja_hedgeado
        |     +-- PANIC: 80% - ja_hedgeado
        |
        +-- DefenseDecision(should_hedge, shares, price, token)
              |
              v
            place_hedge_order(token, price, size)  [FOK taker]
              |
              v
            check_order_filled(hedge_id)
              |
              v
            log_event("HEDGE_FILLED" / "HEDGE_NOT_FILLED")
```

---

## 4. Custos de HTTP por Tick

| Estado    | Calls antes | Calls depois | Delta |
|-----------|------------|--------------|-------|
| IDLE      | 2          | 2            | 0     |
| ENTRY     | 3-4        | 3-4          | 0     |
| HOLDING   | 2          | 3            | +1 (fetch_book) |
| HEDGE     | 2          | 4-5          | +2-3 (book + order + check) |

O custo extra e minimo: +1 call/s durante HOLDING (fetch_book),
+2-3 calls apenas quando hedge e executado (raro).

---

## 5. Tabela de Parametros .env

| Variavel                  | Default | Descricao                                |
|---------------------------|---------|------------------------------------------|
| PD_ENABLED                | 1       | Liga/desliga todo o sistema              |
| PD_ALERT_CONFIRM_TICKS    | 3       | Ticks com severity>0 para ALERT->DEFENSE |
| PD_ALERT_COOLDOWN_S       | 5.0     | Tempo severity=0 para ALERT->NORMAL     |
| PD_PANIC_THRESHOLD        | 0.70    | Severity minima para PANIC               |
| PD_PANIC_ADVERSE_MIN      | 0.02    | Adverse move minimo para PANIC           |
| PD_DEFENSE_EXIT_S         | 10.0    | Tempo severity=0 para DEFENSE->NORMAL   |
| PD_HEDGE_COOLDOWN_S       | 10.0    | Cooldown entre hedges                    |
| PD_HEDGE_PANIC_MARKUP     | 0.01    | Markup no preco em PANIC                 |
| PD_MIN_HEDGE              | 0.20    | Hedge minimo (20% da posicao)            |
| PD_MAX_HEDGE              | 0.80    | Hedge maximo (80% da posicao)            |
| PD_MIN_SHARES             | 5       | Minimo de shares por hedge               |
| PD_RPI_K                  | 1.5     | Multiplicador sigma no threshold         |

---

## 6. Logs Gerados

### No JSONL do bot (logs/bot_15min_YYYY-MM-DD.jsonl):

| Evento              | Quando                     | Campos extra                          |
|---------------------|----------------------------|---------------------------------------|
| DEFENSE_STARTED     | Fill confirmado             | vol_entry_short, vol_entry_long       |
| DEFENSE_PHASE       | Transicao de fase           | prev, new, reason, severity, rpi      |
| HEDGE_PLACING       | Antes de enviar hedge       | phase, shares, price, token, severity |
| HEDGE_FILLED        | Hedge preenchido            | phase, shares, price, total_hedged    |
| HEDGE_NOT_FILLED    | FOK nao matched             | phase, shares, reason                 |
| HEDGE_FAILED        | Ordem rejeitada             | phase, reason                         |
| POSITION_RESULT     | Fim do ciclo (ja existia)   | +hedge_shares, +defense_phase         |

### No JSONL do post_defense (logs/post_defense_YYYY-MM-DD.jsonl):

Ja existente: 1 linha por tick com 28 campos (TickSnapshot).
Nenhuma alteracao necessaria.

---

## 7. Diagrama de Estados

```
                    severity > 0
          +-------- (detectado) --------+
          |                             |
          v                             |
       NORMAL -----> ALERT -----> DEFENSE -----> PANIC
          ^            |              |              |
          |            |              |              |
          +--- sev=0 --+    sev=0     |    !allow    |
          |   (5s cool)    (10s cool)  |   ou <60s   |
          +<-----------+              |              v
          +<-----------+--------------+           EXIT
                                                    |
                                                    | (fim ciclo)
                                                    v
                                                  NORMAL
```

---

## 8. Ordem de Implementacao

1. `config.py` — adicionar 7 novos parametros
2. `state_machine.py` — criar DefensePhase, DefenseStateTracker, evaluate_transition
3. `hedge.py` — criar calc_hedge_shares, calc_hedge_price, get_opposite_token
4. `decision.py` — criar DefenseDecision, evaluate_defense
5. `__init__.py` — atualizar exports
6. `bot_15min.py` — integrar (imports, init, FILL, HOLDING, reset)
7. Teste de integracao com dados sinteticos

---

## 9. Riscos e Mitigacoes

| Risco | Mitigacao |
|-------|-----------|
| Falso positivo (hedge desnecessario) | 3 ticks de confirmacao (ALERT->DEFENSE), cooldown 10s |
| FOK nao fill (book vazio) | Log HEDGE_NOT_FILLED, nao retry automatico, proximo tick tenta de novo |
| Hedge maior que saldo | Verificar get_usdc_balance() antes de hedgear |
| Double-hedge (multiplos hedges rapidos) | total_hedge_shares tracker + cooldown + max_hedge cap |
| Engine crash | try/except em todo o bloco HOLDING, fallback para `continue` |
| Latencia do fetch_book | Timeout httpx ja em 30s, se falhar book_json=None (indicadores de book ficam 0) |
| PD_ENABLED=0 | Skip total, zero custo extra |

---

## 10. Exemplo de Cenario Real

```
t=0:   FILL YES @0.95 x6 shares -> DEFENSE_STARTED
t=1-5: HOLDING, severity=0.0 -> NORMAL
t=6:   Preco cai para 0.94, severity=0.3 -> DEFENSE_PHASE NORMAL->ALERT
t=7:   severity=0.5 -> ALERT (tick 2/3)
t=8:   severity=0.6 -> ALERT (tick 3/3) -> DEFENSE_PHASE ALERT->DEFENSE
       calc_hedge: lerp(0.20, 0.80, 0.6) = 0.56 * 6 = 3.36 -> 0 (< min 5)
       Nao hedgeia (< PD_MIN_SHARES)
t=9:   severity=0.8 -> DEFENSE_PHASE DEFENSE->PANIC
       calc_hedge: 0.80 * 6 = 4.8 -> 5 shares (arredonda para min)
       HEDGE_PLACING: NO @best_ask+0.01, 5 shares, FOK
       HEDGE_FILLED: 5 shares, total_hedged=5
t=15:  severity=0.0 -> DEFENSE->NORMAL (apos 10s cooldown)
t=60:  Ciclo expira. POSITION_RESULT: side=YES, entry=0.95,
       hedge_shares=5, defense_phase=NORMAL
       Se YES perdeu: loss = -0.95*6 = -5.70, hedge_gain = +(1-ask)*5
       Se YES ganhou: gain = +0.05*6 = 0.30, hedge_loss = -ask*5
```
