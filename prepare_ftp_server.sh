#!/bin/bash
#
# Script para preparar servidor FTP simples usando Python
# Útil para transferir arquivos da pasta raw
#

set -e

PORT="${1:-2121}"
RAW_DIR="./data/raw"

echo "🚀 Preparando servidor FTP simples na porta $PORT"
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
echo ""

# Verificar se pyftpdlib está instalado
if ! python3 -c "import pyftpdlib" 2>/dev/null; then
    echo "📦 Instalando pyftpdlib..."
    pip install pyftpdlib
fi

# Criar script Python para servidor FTP
cat > /tmp/ftp_server.py <<EOF
#!/usr/bin/env python3
"""
Servidor FTP simples para servir arquivos da pasta raw
"""
import os
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
    
    print(f"🚀 Servidor FTP iniciado em ftp://0.0.0.0:{FTP_PORT}")
    print(f"📁 Diretório: {FTP_DIR}")
    print(f"👤 Usuário: {FTP_USER}")
    print(f"🔑 Senha: {FTP_PASS}")
    print("")
    print("Para conectar:")
    print(f"  ftp://{FTP_USER}:{FTP_PASS}@$(hostname -I | awk '{print \$1}'):{FTP_PORT}")
    print("")
    print("Pressione Ctrl+C para parar")
    
    server.serve_forever()

if __name__ == "__main__":
    main()
EOF

chmod +x /tmp/ftp_server.py

echo "✅ Servidor FTP pronto!"
echo ""
echo "Para iniciar:"
echo "  python3 /tmp/ftp_server.py"
echo ""
echo "Ou em background:"
echo "  nohup python3 /tmp/ftp_server.py > /tmp/ftp_server.log 2>&1 &"
echo ""

