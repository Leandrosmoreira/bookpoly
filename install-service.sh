#!/bin/bash
# Script para instalar o serviço systemd do BookPoly Recorder

set -e

SERVICE_FILE="bookpoly-recorder.service"
SYSTEMD_PATH="/etc/systemd/system"

echo "Instalando serviço systemd para BookPoly Recorder..."

# Copiar arquivo de serviço
sudo cp "$SERVICE_FILE" "$SYSTEMD_PATH/"

# Recarregar systemd
sudo systemctl daemon-reload

# Habilitar serviço para iniciar no boot
sudo systemctl enable bookpoly-recorder.service

echo ""
echo "✅ Serviço instalado!"
echo ""
echo "Comandos úteis:"
echo "  sudo systemctl start bookpoly-recorder    # Iniciar serviço"
echo "  sudo systemctl stop bookpoly-recorder     # Parar serviço"
echo "  sudo systemctl status bookpoly-recorder   # Ver status"
echo "  sudo systemctl restart bookpoly-recorder   # Reiniciar serviço"
echo "  sudo journalctl -u bookpoly-recorder -f  # Ver logs em tempo real"
echo ""

