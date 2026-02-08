#!/bin/bash
# Script para iniciar o dashboard tentando múltiplas portas

set -e

cd /root/bookpoly

# Parar qualquer instância anterior
pkill -f "dashboard/app.py" 2>/dev/null || true
sleep 1

# Portas para tentar
PORTS=(5001 8080 8888 3000 5000)

for PORT in "${PORTS[@]}"; do
    if ! lsof -i :$PORT >/dev/null 2>&1 && ! netstat -tuln 2>/dev/null | grep -q ":$PORT "; then
        echo "🌐 Iniciando dashboard na porta $PORT..."
        echo ""
        
        source venv/bin/activate
        nohup python dashboard/app.py $PORT > /tmp/dashboard_$PORT.log 2>&1 &
        DASHBOARD_PID=$!
        
        sleep 2
        
        # Verificar se iniciou
        if ps -p $DASHBOARD_PID > /dev/null 2>&1; then
            IP=$(hostname -I | awk '{print $1}')
            echo "✅ Dashboard iniciado na porta $PORT"
            echo ""
            echo "📊 Acesse:"
            echo "   http://$IP:$PORT"
            echo ""
            echo "🔍 Ou use túnel SSH (recomendado):"
            echo "   ssh -L $PORT:localhost:$PORT root@$IP"
            echo "   Depois: http://localhost:$PORT"
            echo ""
            echo "📋 Logs: /tmp/dashboard_$PORT.log"
            exit 0
        fi
    fi
done

echo "❌ Nenhuma porta disponível. Tentando porta 5001..."
source venv/bin/activate
python dashboard/app.py 5001

