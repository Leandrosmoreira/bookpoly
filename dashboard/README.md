# 📈 BookPoly Dashboard

Dashboard web em tempo real para monitorar o bot de paper trading.

## 🚀 Instalação

```bash
cd /root/bookpoly

# Instalar dependências
source venv/bin/activate
pip install flask>=3.0.0

# Instalar como serviço systemd
sudo ./install_dashboard.sh
```

## 🌐 Acesso

Após instalar, acesse:

```
http://SEU_IP:5001
```

Para descobrir o IP do servidor:
```bash
hostname -I | awk '{print $1}'
```

## 📊 Funcionalidades

### Dashboard Principal
- **Balance**: Saldo atual, inicial e ROI
- **Trades**: Total, abertos e fechados
- **Performance**: Vitórias, derrotas e win rate
- **P&L**: Lucro/Prejuízo total e diário

### Trades Recentes
Tabela com os últimos trades mostrando:
- Hora
- Mercado
- Tipo (ENTER/CLOSED/BLOCKED)
- Preço de entrada
- Resultado
- P&L

### Log ao Vivo
Log em tempo real do bot com:
- Entradas de trades
- Fechamentos
- Bloqueios
- Resumos periódicos
- Todas as mensagens do bot

## 🔄 Atualização

O dashboard atualiza automaticamente a cada 2 segundos.

## 🛠️ Comandos Úteis

```bash
# Ver status
sudo systemctl status dashboard.service

# Reiniciar
sudo systemctl restart dashboard.service

# Parar
sudo systemctl stop dashboard.service

# Ver logs
sudo journalctl -u dashboard.service -f
```

## 📝 Estrutura

```
dashboard/
├── app.py              # Servidor Flask
├── templates/
│   └── index.html      # HTML do dashboard
├── static/
│   ├── style.css       # Estilos
│   └── script.js       # JavaScript (atualização em tempo real)
└── README.md           # Esta documentação
```

## 🔧 Configuração

A porta padrão é **5001**. Para alterar, edite `dashboard/app.py`:

```python
app.run(host='0.0.0.0', port=5001, debug=False)
```

## 🐛 Troubleshooting

**Dashboard não carrega:**
- Verifique se o serviço está rodando: `sudo systemctl status dashboard.service`
- Verifique os logs: `sudo journalctl -u dashboard.service -n 50`
- Verifique se a porta está aberta: `netstat -tuln | grep 5001`

**Dados não aparecem:**
- Verifique se o bot está rodando: `sudo systemctl status paper-trading.service`
- Verifique se há logs: `ls -lh /root/bookpoly/logs/paper_trading_*.log`

