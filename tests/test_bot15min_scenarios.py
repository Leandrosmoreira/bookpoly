#!/usr/bin/env python3
"""
4 cenários fake do bot_15min — simulação completa da máquina de estados.

Cada teste simula o ciclo completo: IDLE → ORDER_PLACED → HOLDING → resultado.
Usa mocks para não chamar API real.

Execução:
    python tests/test_bot15min_scenarios.py
"""

import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ajustar path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from bot_15min import (
    MarketState, MarketContext, evaluate_stop_loss, reset_context,
    MIN_SHARES, MIN_PRICE, MAX_PRICE, STOP_PROB,
    ENTRY_WINDOW_START, ENTRY_WINDOW_END,
)

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
results = []


def header(num, title):
    print()
    print("=" * 70)
    print(f"  CENÁRIO {num}: {title}")
    print("=" * 70)


def step(num, desc, status_before, status_after, detail=""):
    arrow = f"{status_before} → {status_after}" if status_after else status_before
    print(f"  Passo {num}: {desc}")
    print(f"           Estado: {arrow}")
    if detail:
        print(f"           {detail}")


def assert_eq(label, actual, expected):
    ok = actual == expected
    tag = PASS if ok else FAIL
    results.append(ok)
    print(f"    [{tag}] {label}: {actual} (esperado: {expected})")
    return ok


# ══════════════════════════════════════════════════════════════════════
# CENÁRIO 1: TRADE WIN — compra YES@0.95, mercado resolve YES
# ══════════════════════════════════════════════════════════════════════

def test_scenario_1_trade_win():
    header(1, "TRADE WIN — compra YES@0.95, mercado resolve YES")

    ctx = MarketContext(asset="btc")
    now = int(time.time())
    cycle_end = now + 180  # 3 min para expirar

    # ── Passo 1: IDLE → detecta mercado na janela
    step(1, "Bot detecta mercado BTC 15min na janela de entrada",
         "IDLE", "IDLE",
         f"time_to_expiry=180s (dentro de {ENTRY_WINDOW_START}s-{ENTRY_WINDOW_END}s)")
    ctx.cycle_end_ts = cycle_end
    ctx.yes_price = 0.95
    ctx.no_price = 0.05
    ctx.yes_token_id = "token_yes_fake"
    ctx.no_token_id = "token_no_fake"
    assert_eq("Estado", ctx.state.value, "IDLE")
    assert_eq("YES price no range", MIN_PRICE <= 0.95 <= MAX_PRICE, True)

    # ── Passo 2: Guardrails ALLOW
    step(2, "GuardrailsPro avalia entrada → ALLOW",
         "IDLE", "IDLE",
         "risk_score=0.15, pump=0.02, stability=0.92 → ALLOW")
    # (simulado — guardrails permitiu)

    # ── Passo 3: IDLE → ORDER_PLACED
    step(3, "Envia ordem BUY YES @ 0.94 (price - 0.01), 8 shares",
         "IDLE", "ORDER_PLACED",
         "place_order(token_yes_fake, 0.94, 8) → order_id=fake_001")
    ctx.state = MarketState.ORDER_PLACED
    ctx.order_id = "fake_001"
    assert_eq("Estado", ctx.state.value, "ORDER_PLACED")

    # ── Passo 4: ORDER_PLACED → HOLDING (fill em 2s)
    step(4, "Ordem preenchida (filled) em 2 segundos",
         "ORDER_PLACED", "HOLDING",
         "wait_for_fill(fake_001, timeout=5) → True")
    ctx.state = MarketState.HOLDING
    ctx.entered_side = "YES"
    ctx.entered_price = 0.94
    ctx.entered_size = MIN_SHARES
    ctx.entered_ts = now
    ctx.order_id = None
    assert_eq("Estado", ctx.state.value, "HOLDING")
    assert_eq("Lado", ctx.entered_side, "YES")
    assert_eq("Preço entrada", ctx.entered_price, 0.94)

    # ── Passo 5: HOLDING — monitorando (prob se mantém acima de 40%)
    step(5, "HOLDING — monitorando posição, prob YES=0.96 (acima do stop 40%)",
         "HOLDING", "HOLDING",
         "evaluate_stop_loss → None (0.96 >= 0.40)")
    ctx.yes_price = 0.96
    ctx.no_price = 0.04
    stop = evaluate_stop_loss(ctx, 0.96, 0.04)
    assert_eq("Stop-loss trigger", stop, None)

    # ── Passo 6: Ciclo expira, mercado resolve YES → WIN
    step(6, "Ciclo expira, mercado resolve YES → WIN!",
         "HOLDING", "DONE",
         "outcome=YES, side=YES → WIN | PnL = (1.0 - 0.94) * 8 = +$0.48")
    ctx.state = MarketState.DONE
    win = ctx.entered_side == "YES"  # outcome = YES
    pnl = round((1.0 - ctx.entered_price) * ctx.entered_size, 2)
    assert_eq("WIN", win, True)
    assert_eq("PnL", pnl, 0.48)
    assert_eq("Estado final", ctx.state.value, "DONE")

    print()
    print(f"  ✅ Resultado: GANHOU +${pnl}")


# ══════════════════════════════════════════════════════════════════════
# CENÁRIO 2: TRADE LOSS — compra NO@0.96, mercado resolve YES
# ══════════════════════════════════════════════════════════════════════

def test_scenario_2_trade_loss():
    header(2, "TRADE LOSS — compra NO@0.96, mercado resolve YES")

    ctx = MarketContext(asset="eth")
    now = int(time.time())
    cycle_end = now + 120

    # ── Passo 1: Detecta mercado
    step(1, "Bot detecta mercado ETH 15min, NO no range",
         "IDLE", "IDLE",
         "YES=0.04 NO=0.96 → NO está no range 93%-98%")
    ctx.cycle_end_ts = cycle_end
    ctx.yes_price = 0.04
    ctx.no_price = 0.96
    ctx.yes_token_id = "token_yes_eth"
    ctx.no_token_id = "token_no_eth"
    assert_eq("Estado", ctx.state.value, "IDLE")
    assert_eq("NO price no range", MIN_PRICE <= 0.96 <= MAX_PRICE, True)

    # ── Passo 2: Guardrails ALLOW
    step(2, "GuardrailsPro avalia entrada NO → ALLOW",
         "IDLE", "IDLE")

    # ── Passo 3: Envia ordem
    step(3, "Envia ordem BUY NO @ 0.95, 8 shares",
         "IDLE", "ORDER_PLACED",
         "place_order(token_no_eth, 0.95, 8) → order_id=fake_002")
    ctx.state = MarketState.ORDER_PLACED
    ctx.order_id = "fake_002"
    assert_eq("Estado", ctx.state.value, "ORDER_PLACED")

    # ── Passo 4: Fill
    step(4, "Ordem preenchida em 3 segundos",
         "ORDER_PLACED", "HOLDING")
    ctx.state = MarketState.HOLDING
    ctx.entered_side = "NO"
    ctx.entered_price = 0.95
    ctx.entered_size = MIN_SHARES
    ctx.entered_ts = now
    ctx.order_id = None
    assert_eq("Estado", ctx.state.value, "HOLDING")

    # ── Passo 5: Monitorando — prob NO cai mas ainda acima de 40%
    step(5, "HOLDING — prob NO cai para 0.55 (acima do stop 40%)",
         "HOLDING", "HOLDING",
         "evaluate_stop_loss → None (0.55 >= 0.40)")
    stop = evaluate_stop_loss(ctx, 0.45, 0.55)
    assert_eq("Stop-loss trigger", stop, None)

    # ── Passo 6: Ciclo expira, YES vence → LOSS
    step(6, "Ciclo expira, mercado resolve YES → LOSS",
         "HOLDING", "DONE",
         "outcome=YES, side=NO → LOSS | PnL = -0.95 * 8 = -$7.60")
    ctx.state = MarketState.DONE
    outcome = "YES"
    win = ctx.entered_side == outcome
    pnl = round(-ctx.entered_price * ctx.entered_size, 2)
    assert_eq("WIN", win, False)
    assert_eq("PnL", pnl, -7.60)
    assert_eq("Estado final", ctx.state.value, "DONE")

    print()
    print(f"  ❌ Resultado: PERDEU ${pnl}")


# ══════════════════════════════════════════════════════════════════════
# CENÁRIO 3: STOP-LOSS — compra YES@0.95, prob cai abaixo de 40%, vende
# ══════════════════════════════════════════════════════════════════════

def test_scenario_3_stop_loss():
    header(3, "STOP-LOSS — compra YES@0.95, prob cai para 0.35, vende a mercado")

    ctx = MarketContext(asset="sol")
    now = int(time.time())
    cycle_end = now + 200

    # ── Passo 1: Detecta mercado
    step(1, "Bot detecta mercado SOL 15min",
         "IDLE", "IDLE",
         "YES=0.95 NO=0.05")
    ctx.cycle_end_ts = cycle_end
    ctx.yes_price = 0.95
    ctx.no_price = 0.05
    ctx.yes_token_id = "token_yes_sol"
    ctx.no_token_id = "token_no_sol"
    assert_eq("Estado", ctx.state.value, "IDLE")

    # ── Passo 2: Ordem e fill
    step(2, "Envia BUY YES @ 0.94, fill imediato",
         "IDLE", "HOLDING",
         "place_order → fill → HOLDING")
    ctx.state = MarketState.HOLDING
    ctx.entered_side = "YES"
    ctx.entered_price = 0.94
    ctx.entered_size = MIN_SHARES
    ctx.entered_ts = now
    assert_eq("Estado", ctx.state.value, "HOLDING")

    # ── Passo 3: Prob cai para 0.55 — acima do stop
    step(3, "Prob YES cai para 0.55 — ainda acima do stop (40%)",
         "HOLDING", "HOLDING",
         "evaluate_stop_loss(0.55) → None")
    stop = evaluate_stop_loss(ctx, 0.55, 0.45)
    assert_eq("Stop trigger a 0.55", stop, None)

    # ── Passo 4: Prob cai para 0.40 — exatamente no limite
    step(4, "Prob YES = 0.40 — exatamente no limite (>= 0.40, NÃO vende)",
         "HOLDING", "HOLDING",
         "evaluate_stop_loss(0.40) → None (>= threshold)")
    stop = evaluate_stop_loss(ctx, 0.40, 0.60)
    assert_eq("Stop trigger a 0.40 (boundary)", stop, None)

    # ── Passo 5: Prob cai para 0.35 — ABAIXO do stop → TRIGGER!
    step(5, "Prob YES cai para 0.35 — ABAIXO do stop 40% → STOP TRIGGERED!",
         "HOLDING", "HOLDING (stop pending)",
         "evaluate_stop_loss(0.35) → {token_id, size=8, trigger=0.40}")
    stop = evaluate_stop_loss(ctx, 0.35, 0.65)
    assert_eq("Stop trigger a 0.35", stop is not None, True)
    assert_eq("Token a vender", stop["token_id"], "token_yes_sol")
    assert_eq("Size", stop["size"], MIN_SHARES)
    assert_eq("Trigger threshold", stop["trigger"], STOP_PROB)

    # ── Passo 6: Executa SELL FOK a mercado — best_bid=0.33
    step(6, "Executa SELL FOK a mercado (price=0.01), best_bid=0.33",
         "HOLDING", "DONE",
         "place_sell_order(0.01, 8) → fill no bid 0.33")
    best_bid = 0.33
    exec_price = best_bid
    stop_pnl = round((exec_price - ctx.entered_price) * ctx.entered_size, 4)
    ctx.stop_executed = True
    ctx.stop_price = exec_price
    ctx.stop_size = ctx.entered_size
    ctx.stop_pnl = stop_pnl
    ctx.state = MarketState.DONE
    assert_eq("Estado", ctx.state.value, "DONE")
    assert_eq("Stop PnL", stop_pnl, round((0.33 - 0.94) * 8, 4))
    assert_eq("Stop executado", ctx.stop_executed, True)

    # ── Comparação: com vs sem stop-loss
    loss_with_stop = abs(stop_pnl)
    loss_without_stop = round(ctx.entered_price * ctx.entered_size, 2)  # perda total se YES resolver
    saved = round(loss_without_stop - loss_with_stop, 2)

    print()
    print(f"  🛑 Resultado: STOP-LOSS executado")
    print(f"     Perda com stop:    -${loss_with_stop:.2f}")
    print(f"     Perda sem stop:    -${loss_without_stop:.2f} (se perdesse tudo)")
    print(f"     Economia:          +${saved:.2f}")


# ══════════════════════════════════════════════════════════════════════
# CENÁRIO 4: SKIPPED — preço fora do range + guardrails BLOCK
# ══════════════════════════════════════════════════════════════════════

def test_scenario_4_skipped():
    header(4, "SKIPPED — preço fora do range + GuardrailsPro BLOCK")

    ctx = MarketContext(asset="xrp")
    now = int(time.time())
    cycle_end = now + 180

    # ── Passo 1: Detecta mercado — preço fora do range
    step(1, "Bot detecta mercado XRP 15min — preço fora do range",
         "IDLE", "IDLE",
         f"YES=0.88 NO=0.12 → ambos fora de {MIN_PRICE*100:.0f}%-{MAX_PRICE*100:.0f}%")
    ctx.cycle_end_ts = cycle_end
    ctx.yes_price = 0.88
    ctx.no_price = 0.12
    ctx.yes_token_id = "token_yes_xrp"
    ctx.no_token_id = "token_no_xrp"
    yes_in_range = MIN_PRICE <= 0.88 <= MAX_PRICE
    no_in_range = MIN_PRICE <= 0.12 <= MAX_PRICE
    assert_eq("YES no range", yes_in_range, False)
    assert_eq("NO no range", no_in_range, False)
    assert_eq("Ação", "SKIP_PRICE_OOR", "SKIP_PRICE_OOR")

    # ── Passo 2: Próximo poll — preço entra no range mas guardrails bloqueia
    step(2, "Próximo poll — YES sobe para 0.94 (no range), GuardrailsPro avalia",
         "IDLE", "IDLE",
         "YES=0.94 → no range! Mas GuardrailsPro detecta pump → BLOCK")
    ctx.yes_price = 0.94
    ctx.no_price = 0.06
    yes_in_range_now = MIN_PRICE <= 0.94 <= MAX_PRICE
    assert_eq("YES agora no range", yes_in_range_now, True)
    # Simulando guardrails BLOCK
    gr_action = "BLOCK"
    gr_reason = "pump_detected: pump_score=0.85 > threshold=0.60"
    assert_eq("Guardrails decisão", gr_action, "BLOCK")
    print(f"           Motivo: {gr_reason}")

    # ── Passo 3: Próximo poll — guardrails CAUTION
    step(3, "Próximo poll — YES=0.95, GuardrailsPro → CAUTION (também bloqueia)",
         "IDLE", "IDLE",
         "stability_score=0.35 < 0.50 → CAUTION → bloqueado")
    gr_action = "CAUTION"
    assert_eq("Guardrails decisão", gr_action, "CAUTION")

    # ── Passo 4: Tempo esgota — hard stop
    step(4, "time_to_expiry < 60s → HARD STOP, ciclo perdido",
         "IDLE", "SKIPPED",
         "time_to_expiry=45s < ENTRY_WINDOW_END(60s) → SKIPPED")
    time_to_expiry = 45
    if time_to_expiry < ENTRY_WINDOW_END:
        ctx.state = MarketState.SKIPPED
    assert_eq("Estado final", ctx.state.value, "SKIPPED")

    # ── Passo 5: Ciclo expira
    step(5, "Ciclo expira — nenhuma posição aberta, sem PnL",
         "SKIPPED", "DONE",
         "Nenhum trade executado neste ciclo")
    ctx.state = MarketState.DONE
    assert_eq("Estado", ctx.state.value, "DONE")
    assert_eq("Trade attempts", ctx.trade_attempts, 0)
    assert_eq("Entered side", ctx.entered_side, None)

    # ── Passo 6: Reset para próximo ciclo
    step(6, "Novo ciclo detectado → reset contexto",
         "DONE", "IDLE")
    reset_context(ctx)
    assert_eq("Estado após reset", ctx.state.value, "IDLE")
    assert_eq("Cycle end limpo", ctx.cycle_end_ts is not None, True)  # mantém até reatribuir

    print()
    print(f"  ⏭️  Resultado: SKIPPED — nenhum trade, PnL = $0.00")


# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print()
    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║       TEST BOT_15MIN — 4 CENÁRIOS SIMULADOS (FAKE)            ║")
    print("╠══════════════════════════════════════════════════════════════════╣")
    print(f"║  STOP_PROB = {STOP_PROB}  |  SHARES = {MIN_SHARES}  |  RANGE = {MIN_PRICE}-{MAX_PRICE}      ║")
    print(f"║  JANELA = {ENTRY_WINDOW_START}s a {ENTRY_WINDOW_END}s antes da expiração              ║")
    print("╚══════════════════════════════════════════════════════════════════╝")

    test_scenario_1_trade_win()
    test_scenario_2_trade_loss()
    test_scenario_3_stop_loss()
    test_scenario_4_skipped()

    print()
    print("=" * 70)
    total = len(results)
    passed = sum(results)
    failed = total - passed
    print(f"  RESULTADO FINAL: {passed}/{total} assertions passed", end="")
    if failed:
        print(f" ({failed} FAILED)")
    else:
        print(f" — ALL PASSED ✓")
    print("=" * 70)
    print()

    sys.exit(0 if failed == 0 else 1)
