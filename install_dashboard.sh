#!/bin/bash
# Script para instalar o dashboard como serviço systemd

set -e

echo "📊 Instalando BookPoly Dashboard..."

# Copiar serviço
sudo cp /root/bookpoly/dashboard.service /etc/systemd/system/

# Recarregar systemd
sudo systemctl daemon-reload

# Habilitar serviço
sudo systemctl enable dashboard.service

# Iniciar serviço
sudo systemctl start dashboard.service

# Verificar status
sleep 2
sudo systemctl status dashboard.service --no-pager | head -15

echo ""
echo "✅ Dashboard instalado!"
echo ""
echo "🌐 Acesse em: http://$(hostname -I | awk '{print $1}'):5001"
echo ""
echo "📋 Comandos úteis:"
echo "  sudo systemctl status dashboard.service"
echo "  sudo systemctl restart dashboard.service"
echo "  sudo systemctl stop dashboard.service"

