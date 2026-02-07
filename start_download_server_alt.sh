#!/bin/bash
# Script alternativo - tenta várias portas comuns

set -e

COMPRESSED_DIR="/root/bookpoly/data/compressed"

# Tentar portas comuns que geralmente não são bloqueadas
PORTS=(8000 9000 3000 5000)

for PORT in "${PORTS[@]}"; do
    if ! lsof -i :$PORT >/dev/null 2>&1 && ! netstat -tuln 2>/dev/null | grep -q ":$PORT "; then
        echo "🌐 Iniciando servidor HTTP na porta $PORT..."
        echo ""
        echo "📁 Diretório: $COMPRESSED_DIR"
        echo "🔌 Porta: $PORT"
        echo ""
        echo "✅ Servidor iniciado!"
        echo ""
        echo "📥 Para baixar os arquivos, acesse no seu navegador:"
        echo ""
        echo "   http://31.97.165.64:$PORT/"
        echo ""
        echo "   Ou use os links diretos:"
        echo "   http://31.97.165.64:$PORT/books.tar.gz"
        echo "   http://31.97.165.64:$PORT/signals.tar.gz"
        echo "   http://31.97.165.64:$PORT/volatility.tar.gz"
        echo ""
        echo "⚠️  Pressione Ctrl+C para parar o servidor"
        echo ""
        
        cd "$COMPRESSED_DIR"
        python3 -m http.server $PORT --bind 0.0.0.0
        exit 0
    fi
done

echo "❌ Nenhuma porta disponível. Tentando porta 8888..."
cd "$COMPRESSED_DIR"
python3 -m http.server 8888 --bind 0.0.0.0

