#!/bin/bash
# Script para iniciar servidor HTTP simples para download dos arquivos

set -e

COMPRESSED_DIR="/root/bookpoly/data/compressed"

# Encontrar porta livre
find_free_port() {
    for port in 8888 8889 8890 8891 8892; do
        if ! lsof -i :$port >/dev/null 2>&1 && ! netstat -tuln 2>/dev/null | grep -q ":$port "; then
            echo $port
            return
        fi
    done
    echo "9000"  # fallback
}

PORT=$(find_free_port)

echo "🌐 Iniciando servidor HTTP para download..."
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
echo "🌐 Servidor rodando em: 0.0.0.0:$PORT"
echo ""
python3 -m http.server $PORT --bind 0.0.0.0

