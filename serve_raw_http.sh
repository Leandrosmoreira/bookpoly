#!/bin/bash
#
# Servidor HTTP simples para servir arquivos da pasta raw
# Alternativa mais simples que FTP
#

PORT="${1:-8080}"
RAW_DIR="./data/raw"

echo "🚀 Iniciando servidor HTTP para pasta raw"
echo ""

# Verificar se a pasta raw existe
if [ ! -d "$RAW_DIR" ]; then
    echo "❌ Pasta $RAW_DIR não encontrada"
    exit 1
fi

FILE_COUNT=$(find "$RAW_DIR" -type f | wc -l)
TOTAL_SIZE=$(du -sh "$RAW_DIR" | cut -f1)

echo "📊 Estatísticas:"
echo "  Arquivos: $FILE_COUNT"
echo "  Tamanho: $TOTAL_SIZE"
echo "  Porta: $PORT"
echo ""

# Ativar venv se existir
if [ -d "venv" ]; then
    source venv/bin/activate
    PYTHON_CMD="python"
else
    PYTHON_CMD="python3"
fi

# Obter IP do servidor
SERVER_IP=$(hostname -I | awk '{print $1}')

echo "🚀 Servidor HTTP iniciado!"
echo "📁 Diretório: $(pwd)/$RAW_DIR"
echo "🌐 Endereço: http://$SERVER_IP:$PORT"
echo ""
echo "Para acessar do seu computador:"
echo "  http://$SERVER_IP:$PORT/books/"
echo "  http://$SERVER_IP:$PORT/signals/"
echo "  http://$SERVER_IP:$PORT/volatility/"
echo ""
echo "Ou use um cliente HTTP como wget ou curl:"
echo "  wget -r http://$SERVER_IP:$PORT/"
echo ""
echo "Pressione Ctrl+C para parar"
echo "=" * 60

# Iniciar servidor HTTP simples
cd "$RAW_DIR" || exit 1
$PYTHON_CMD -m http.server "$PORT"

