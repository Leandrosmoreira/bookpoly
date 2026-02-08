#!/bin/bash
#
# Inicia o bot com sistema de defesa ativado
#

cd /root/bookpoly || exit 1
source venv/bin/activate

# Ativar defesa
export DEFENSE_ENABLED=true

# Configurar moedas (padrão: btc,eth,sol,xrp)
export SIGNAL_COINS="${SIGNAL_COINS:-btc,eth,sol,xrp}"

# Modo paper trading
export BOT_PAPER_TRADING=true
export BOT_DRY_RUN=false

echo "🚀 Iniciando bot com sistema de defesa..."
echo "   Moedas: $SIGNAL_COINS"
echo "   Modo: PAPER TRADING"
echo "   Defesa: ENABLED"
echo ""

# Executar bot
exec python -m bot.main --paper

