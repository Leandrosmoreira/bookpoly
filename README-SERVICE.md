# Configuração 24/7 com Systemd

## Status Atual
✅ O recorder está **RODANDO** (processo ativo)
✅ Está **GRAVANDO** dados a cada segundo
❌ **NÃO tem restart automático** - se cair, precisa reiniciar manualmente

## Instalar Restart Automático

Para garantir que o recorder reinicie automaticamente se cair:

```bash
cd /root/bookpoly
./install-service.sh
sudo systemctl start bookpoly-recorder
```

## Comandos Úteis

```bash
# Ver status
sudo systemctl status bookpoly-recorder

# Ver logs em tempo real
sudo journalctl -u bookpoly-recorder -f

# Reiniciar manualmente
sudo systemctl restart bookpoly-recorder

# Parar
sudo systemctl stop bookpoly-recorder

# Verificar se está gravando
tail -f /root/bookpoly/data/raw/books/BTC15m_*.jsonl | jq -r '.ts_iso'
```

## Configuração do Serviço

O serviço está configurado para:
- ✅ Reiniciar automaticamente se cair (`Restart=always`)
- ✅ Aguardar 10 segundos antes de reiniciar (`RestartSec=10`)
- ✅ Iniciar automaticamente no boot (`WantedBy=multi-user.target`)
- ✅ Usar o ambiente virtual correto

## Monitoramento

Para verificar se está funcionando:
1. Ver processos: `ps aux | grep main.py`
2. Ver último registro: `tail -1 data/raw/books/BTC15m_*.jsonl | jq -r '.ts_iso'`
3. Ver logs: `sudo journalctl -u bookpoly-recorder -n 50`

