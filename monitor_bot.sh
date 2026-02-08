#!/bin/bash
# Script para monitorar o bot em tempo real

cd /root/bookpoly

# Encontrar o log mais recente
LATEST_LOG=$(ls -t logs/paper_trading_*.log 2>/dev/null | head -1)

if [ -z "$LATEST_LOG" ]; then
    echo "❌ Nenhum log encontrado. O bot está rodando?"
    exit 1
fi

echo "📋 Monitorando log: $LATEST_LOG"
echo ""
echo "🔍 Log ao vivo (Ctrl+C para parar):"
echo ""

tail -f "$LATEST_LOG" 2>/dev/null
