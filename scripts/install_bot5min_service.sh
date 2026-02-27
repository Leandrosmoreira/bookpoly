#!/bin/bash
# Instala e inicia o serviço 24/7 do bot_5min na VPS
# Uso: sudo bash scripts/install_bot5min_service.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVICE_NAME="bot5min"

echo "=== Instalando serviço $SERVICE_NAME ==="
echo "  Projeto: $PROJECT_DIR"

sudo cp "$SCRIPT_DIR/bot5min.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl start "$SERVICE_NAME"

echo ""
echo "Serviço instalado e iniciado."
echo "  Status:  sudo systemctl status $SERVICE_NAME"
echo "  Logs:   sudo journalctl -u $SERVICE_NAME -f"
echo "  Parar:  sudo systemctl stop $SERVICE_NAME"
echo "  Reiniciar: sudo systemctl restart $SERVICE_NAME"
echo ""
sudo systemctl status "$SERVICE_NAME" --no-pager || true

