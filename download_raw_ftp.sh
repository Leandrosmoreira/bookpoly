#!/bin/bash
#
# Script para fazer download dos arquivos da pasta raw via FTP
# 
# Uso:
#   ./download_raw_ftp.sh [servidor_ftp] [usuario] [senha] [diretorio_remoto]
#

set -e

# Cores
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configurações padrão
FTP_SERVER="${1:-localhost}"
FTP_USER="${2:-anonymous}"
FTP_PASS="${3:-}"
REMOTE_DIR="${4:-/raw}"

# Diretório local de destino
LOCAL_DIR="./raw_download"
RAW_DIR="./data/raw"

echo -e "${GREEN}📥 Download de arquivos da pasta raw via FTP${NC}"
echo ""
echo "Configurações:"
echo "  Servidor FTP: $FTP_SERVER"
echo "  Usuário: $FTP_USER"
echo "  Diretório remoto: $REMOTE_DIR"
echo "  Diretório local: $LOCAL_DIR"
echo ""

# Criar diretório de destino
mkdir -p "$LOCAL_DIR"

# Verificar se há arquivos na pasta raw
if [ ! -d "$RAW_DIR" ]; then
    echo -e "${YELLOW}⚠️  Pasta $RAW_DIR não encontrada${NC}"
    exit 1
fi

FILE_COUNT=$(find "$RAW_DIR" -type f | wc -l)
TOTAL_SIZE=$(du -sh "$RAW_DIR" | cut -f1)

echo "Arquivos encontrados: $FILE_COUNT"
echo "Tamanho total: $TOTAL_SIZE"
echo ""

# Criar lista de arquivos para upload
TEMP_FILE_LIST=$(mktemp)
find "$RAW_DIR" -type f > "$TEMP_FILE_LIST"

echo "📋 Lista de arquivos:"
head -10 "$TEMP_FILE_LIST"
if [ "$FILE_COUNT" -gt 10 ]; then
    echo "... e mais $((FILE_COUNT - 10)) arquivos"
fi
echo ""

# Opção 1: Usar lftp (recomendado - mais robusto)
if command -v lftp &> /dev/null; then
    echo -e "${GREEN}✅ Usando lftp${NC}"
    
    # Criar script lftp
    LFTP_SCRIPT=$(mktemp)
    cat > "$LFTP_SCRIPT" <<EOF
set ftp:list-options -a
set ftp:passive-mode true
set ftp:ssl-allow no
open -u $FTP_USER,$FTP_PASS $FTP_SERVER
cd $REMOTE_DIR
lcd $LOCAL_DIR
mirror --parallel=4 --verbose
quit
EOF
    
    echo "Executando lftp..."
    lftp -f "$LFTP_SCRIPT"
    rm "$LFTP_SCRIPT"
    
# Opção 2: Usar ftp (básico)
elif command -v ftp &> /dev/null; then
    echo -e "${YELLOW}⚠️  Usando ftp básico (lftp recomendado)${NC}"
    
    # Criar script ftp
    FTP_SCRIPT=$(mktemp)
    cat > "$FTP_SCRIPT" <<EOF
open $FTP_SERVER
user $FTP_USER $FTP_PASS
binary
cd $REMOTE_DIR
lcd $LOCAL_DIR
prompt off
mget *
quit
EOF
    
    echo "Executando ftp..."
    ftp -n < "$FTP_SCRIPT"
    rm "$FTP_SCRIPT"
    
# Opção 3: Usar curl
elif command -v curl &> /dev/null; then
    echo -e "${YELLOW}⚠️  Usando curl${NC}"
    
    while IFS= read -r file; do
        filename=$(basename "$file")
        echo "Baixando: $filename"
        curl -u "$FTP_USER:$FTP_PASS" "ftp://$FTP_SERVER$REMOTE_DIR/$filename" -o "$LOCAL_DIR/$filename"
    done < "$TEMP_FILE_LIST"
    
else
    echo -e "${YELLOW}❌ Nenhum cliente FTP encontrado${NC}"
    echo "Instale um dos seguintes:"
    echo "  - lftp (recomendado): apt-get install lftp"
    echo "  - ftp: apt-get install ftp"
    echo "  - curl: apt-get install curl"
    exit 1
fi

rm "$TEMP_FILE_LIST"

echo ""
echo -e "${GREEN}✅ Download concluído!${NC}"
echo "Arquivos salvos em: $LOCAL_DIR"
echo ""
echo "Para compactar:"
echo "  tar -czf raw_backup.tar.gz -C $LOCAL_DIR ."
echo "  ou"
echo "  zip -r raw_backup.zip $LOCAL_DIR"

