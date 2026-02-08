#!/bin/bash
#
# Servidor FTP simples para servir arquivos da pasta raw
# Inicia um servidor FTP na porta especificada
#

PORT="${1:-2121}"
RAW_DIR="./data/raw"

echo "🚀 Iniciando servidor FTP para pasta raw"
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
fi

# Ativar venv se existir
if [ -d "venv" ]; then
    source venv/bin/activate
    PYTHON_CMD="python"
    PIP_CMD="pip"
else
    PYTHON_CMD="python3"
    PIP_CMD="pip3"
fi

# Verificar se pyftpdlib está instalado
if ! $PYTHON_CMD -c "import pyftpdlib" 2>/dev/null; then
    echo "📦 Instalando pyftpdlib..."
    if [ -d "venv" ]; then
        $PIP_CMD install pyftpdlib
    else
        $PIP_CMD install --user pyftpdlib || $PIP_CMD install --break-system-packages pyftpdlib
    fi
fi

# Obter IP do servidor
SERVER_IP=$(hostname -I | awk '{print $1}')

# Criar script Python para servidor FTP
$PYTHON_CMD <<EOF
import os
import sys
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

# Configurações
FTP_DIR = os.path.abspath("$RAW_DIR")
FTP_PORT = $PORT
FTP_USER = "bookpoly"
FTP_PASS = "bookpoly123"

def main():
    # Criar autorizador
    authorizer = DummyAuthorizer()
    authorizer.add_user(FTP_USER, FTP_PASS, FTP_DIR, perm="elradfmw")
    
    # Criar handler
    handler = FTPHandler
    handler.authorizer = authorizer
    handler.banner = "BookPoly FTP Server - Raw Data"
    
    # Criar servidor
    address = ("0.0.0.0", FTP_PORT)
    server = FTPServer(address, handler)
    
    print(f"🚀 Servidor FTP iniciado!")
    print(f"📁 Diretório: {FTP_DIR}")
    print(f"🌐 Endereço: ftp://{FTP_USER}:{FTP_PASS}@$SERVER_IP:$FTP_PORT")
    print(f"👤 Usuário: {FTP_USER}")
    print(f"🔑 Senha: {FTP_PASS}")
    print("")
    print("Para conectar do seu computador:")
    print(f"  ftp://$SERVER_IP:$FTP_PORT")
    print("  ou use um cliente FTP como FileZilla")
    print("")
    print("Pressione Ctrl+C para parar")
    print("=" * 60)
    
    server.serve_forever()

if __name__ == "__main__":
    main()
EOF

