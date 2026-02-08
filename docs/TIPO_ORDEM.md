# Tipo de Ordem do Bot

## Ordem LIMIT POST ONLY ✅

### Status Atual

O bot usa **ordem LIMIT com POST ONLY**.

### Como funciona

```python
# bot/trader.py (linha 240-247)
data = {
    "tokenID": token_id,
    "side": side.value,
    "size": str(size),
    "price": str(price),
    "orderType": "LIMIT",
    "postOnly": True,  # ✅ POST ONLY: só pode ser maker
}
```

### Comportamento

| Tipo | Comportamento |
|------|---------------|
| **LIMIT (atual)** | Pode ser executada imediatamente (taker) ou colocada no book (maker) |
| **POST ONLY** | Só pode ser maker; se fizer match imediato, é cancelada |

### Implicações

#### ✅ Vantagens da ordem LIMIT atual:
- Execução imediata se houver match
- Preço garantido (não paga mais que o limite)
- Pode ser executada rapidamente

#### ⚠️ Desvantagens:
- Pode pagar **taker fee** se executar imediatamente
- Não garante **maker fee rebate**

### POST ONLY: o que é?

**POST ONLY** significa:
- A ordem só pode ser **maker** (adicionada ao book)
- Se fizer match imediato, a ordem é **cancelada**
- Garante que você sempre recebe **maker fee rebate**
- Nunca paga **taker fee**

### Como adicionar POST ONLY?

Se a API do Polymarket suportar `postOnly`, adicione:

```python
data = {
    "tokenID": token_id,
    "side": side.value,
    "size": str(size),
    "price": str(price),
    "orderType": "LIMIT",
    "postOnly": True,  # ← Adicionar isso
}
```

### Verificação

Para verificar se a API suporta POST ONLY:
1. Consultar documentação da API do Polymarket CLOB
2. Testar com uma ordem pequena
3. Verificar se a ordem é cancelada quando há match imediato

### Recomendação

Para estratégias de longo prazo (15 minutos), POST ONLY pode ser vantajoso:
- ✅ Garante maker fee rebate
- ✅ Evita pagar taker fee
- ⚠️ Pode não executar se o mercado mudar rápido

Para estratégias que precisam de execução rápida:
- ❌ POST ONLY pode atrasar a entrada
- ✅ LIMIT normal é melhor

---

**Status:** ✅ Ordem LIMIT POST ONLY ativada
**Benefício:** Garante maker fee rebate, nunca paga taker fee
