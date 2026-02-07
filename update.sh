#!/bin/bash
# Script para atualizar o código do Git e reiniciar o serviço sem perder dados

set -e

echo "🔄 Atualizando código do repositório..."

# Salvar o status atual do serviço
SERVICE_STATUS=$(systemctl is-active bookpoly-recorder.service || echo "inactive")

if [ "$SERVICE_STATUS" = "active" ]; then
    echo "✅ Serviço está rodando. Vou reiniciá-lo após a atualização..."
fi

# Fazer pull do Git
cd /root/bookpoly
git pull

# Verificar se há mudanças em requirements.txt
if git diff HEAD@{1} HEAD --name-only | grep -q requirements.txt; then
    echo "📦 requirements.txt mudou. Atualizando dependências..."
    source venv/bin/activate
    pip install -r requirements.txt
fi

# Reiniciar o serviço se estava rodando
if [ "$SERVICE_STATUS" = "active" ]; then
    echo "🔄 Reiniciando serviço..."
    systemctl restart bookpoly-recorder.service
    sleep 2
    
    # Verificar se reiniciou com sucesso
    if systemctl is-active --quiet bookpoly-recorder.service; then
        echo "✅ Serviço reiniciado com sucesso!"
        echo ""
        echo "📊 Status:"
        systemctl status bookpoly-recorder.service --no-pager -l | head -10
    else
        echo "❌ Erro ao reiniciar o serviço!"
        systemctl status bookpoly-recorder.service --no-pager
        exit 1
    fi
else
    echo "ℹ️  Serviço não estava rodando. Use 'systemctl start bookpoly-recorder' para iniciar."
fi

echo ""
echo "✅ Atualização concluída!"

