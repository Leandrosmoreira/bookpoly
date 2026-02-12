# 🎯 Guia Completo: Claim Sweeper em Modo LIVE

O **Claim Sweeper** é um módulo que automaticamente reivindica (claims) os prêmios de trades ganhos no Polymarket. Ele verifica periodicamente se há posições vencedoras e executa os claims automaticamente.

---

## ⚠️ IMPORTANTE: SEGURANÇA

- **SEMPRE teste em DRY RUN primeiro!**
- O modo LIVE executa transações reais na blockchain
- Verifique as credenciais antes de ativar
- Monitore os logs após ativar

---

## 📋 Passo a Passo Completo

### 1️⃣ Baixar o código atualizado

```bash
cd /root/bookpoly
git pull origin main
```

---

### 2️⃣ Adicionar credenciais ao .env

**Edite o arquivo `.env` e adicione as credenciais do Polymarket:**

```bash
# Abrir o arquivo .env
nano /root/bookpoly/.env
```

**Adicione estas linhas (substitua pelos seus valores reais):**

```bash
# Polymarket API Credentials
POLYMARKET_API_KEY=sua_api_key_aqui
POLYMARKET_API_SECRET=seu_secret_aqui
POLYMARKET_FUNDER=seu_wallet_address_aqui

# Claim Sweeper Configuration
CLAIM_ENABLED=true
CLAIM_DRY_RUN=true  # Mude para false quando estiver pronto para LIVE
CLAIM_POLL_SECONDS=120
CLAIM_JITTER_SECONDS=10
CLAIM_MAX_PER_CYCLE=5
CLAIM_SELL_PRICE=0.99
```

**OU use o comando direto (substitua os valores):**

```bash
cd /root/bookpoly
echo "" >> .env
echo "# Polymarket API Credentials" >> .env
echo "POLYMARKET_API_KEY=sua_api_key" >> .env
echo "POLYMARKET_API_SECRET=seu_secret" >> .env
echo "POLYMARKET_FUNDER=seu_wallet" >> .env
echo "" >> .env
echo "# Claim Sweeper" >> .env
echo "CLAIM_ENABLED=true" >> .env
echo "CLAIM_DRY_RUN=true" >> .env
```

---

### 3️⃣ Testar em DRY RUN (OBRIGATÓRIO!)

**O script já vem configurado para DRY RUN por padrão:**

```bash
cd /root/bookpoly
chmod +x start_claim_sweeper.sh
./start_claim_sweeper.sh
```

**O que acontece no DRY RUN:**
- ✅ Escaneia posições vencedoras
- ✅ Simula a execução dos claims
- ✅ Mostra logs detalhados
- ❌ **NÃO executa transações reais**

**Verifique os logs:**
- Deve mostrar: `Mode: DRY RUN`
- Deve mostrar: `DRY RUN mode: No real claims will be executed`
- Deve escanear posições sem executar

**Para parar:** `Ctrl+C`

---

### 4️⃣ Rodar em LIVE (quando estiver pronto)

**⚠️ ATENÇÃO: Isso executa transações reais!**

**Opção A: Rodar manualmente (recomendado para primeiro teste)**

```bash
cd /root/bookpoly
CLAIM_DRY_RUN=false ./start_claim_sweeper.sh
```

**Opção B: Rodar em background**

```bash
cd /root/bookpoly
nohup CLAIM_DRY_RUN=false ./start_claim_sweeper.sh > logs/claim_sweeper_$(date +%Y%m%d_%H%M%S).log 2>&1 &
```

**Opção C: Editar .env e usar o script**

```bash
# Editar .env
nano /root/bookpoly/.env

# Mudar esta linha:
CLAIM_DRY_RUN=false

# Depois rodar:
./start_claim_sweeper.sh
```

---

### 5️⃣ Instalar como serviço systemd (opcional, mas recomendado)

**Isso mantém o claim sweeper rodando automaticamente mesmo após reinicializações:**

```bash
# 1. Copiar o arquivo de serviço
sudo cp /root/bookpoly/claim-sweeper.service /etc/systemd/system/

# 2. Recarregar systemd
sudo systemctl daemon-reload

# 3. Habilitar para iniciar automaticamente
sudo systemctl enable claim-sweeper.service

# 4. Iniciar o serviço
sudo systemctl start claim-sweeper.service

# 5. Verificar status
sudo systemctl status claim-sweeper.service

# 6. Ver logs em tempo real
sudo journalctl -u claim-sweeper -f
```

**Comandos úteis do systemd:**

```bash
# Parar o serviço
sudo systemctl stop claim-sweeper

# Reiniciar o serviço
sudo systemctl restart claim-sweeper

# Ver logs
sudo journalctl -u claim-sweeper -n 50

# Ver logs em tempo real
sudo journalctl -u claim-sweeper -f

# Desabilitar auto-start
sudo systemctl disable claim-sweeper
```

---

## 🔍 Verificação e Monitoramento

### Verificar se está rodando:

```bash
# Ver processos
ps aux | grep "claims.loop"

# Ver logs do script
tail -f logs/claim_sweeper_*.log

# Ver logs do systemd (se instalado)
sudo journalctl -u claim-sweeper -f
```

### Verificar configuração:

```bash
# Ver variáveis de ambiente
cd /root/bookpoly
source venv/bin/activate
python -c "from claims.config import ClaimConfig; c = ClaimConfig(); print(f'Dry Run: {c.dry_run}'); print(f'Enabled: {c.enabled}'); print(f'Configured: {c.is_configured()}')"
```

---

## 📊 Como Funciona

1. **Scanner**: A cada 2 minutos (com jitter aleatório), escaneia todas as posições
2. **Filtro**: Identifica posições vencedoras que ainda não foram reivindicadas
3. **Executor**: Executa o claim vendendo as shares a $0.99 (workaround da API)
4. **Ledger**: Registra todos os claims em um banco de dados SQLite
5. **Logs**: Gera logs detalhados de cada operação

---

## ⚙️ Configurações Avançadas

**Edite o `.env` para personalizar:**

```bash
# Timing
CLAIM_POLL_SECONDS=120        # Intervalo entre scans (segundos)
CLAIM_JITTER_SECONDS=10       # Jitter aleatório (0-10s)
CLAIM_MAX_PER_CYCLE=5         # Máximo de claims por ciclo

# Preço de venda (workaround)
CLAIM_SELL_PRICE=0.99         # Preço máximo aceito pela API (perde $0.01/share)

# Mercados específicos (opcional)
CLAIM_MARKET_SLUGS=btc-15m,eth-15m  # Só claim destes mercados (vazio = todos)
```

---

## 🐛 Troubleshooting

### Erro: "POLYMARKET_API_KEY not set"
- Verifique se as credenciais estão no `.env`
- Certifique-se de que o arquivo `.env` está no diretório `/root/bookpoly`

### Erro: "API authentication failed"
- Verifique se a API key e secret estão corretos
- Verifique se o wallet está correto

### Claim não está executando
- Verifique se `CLAIM_DRY_RUN=false` no `.env`
- Verifique os logs para erros
- Certifique-se de que há posições vencedoras para reivindicar

### Serviço systemd não inicia
- Verifique permissões: `sudo chmod 644 /etc/systemd/system/claim-sweeper.service`
- Verifique logs: `sudo journalctl -u claim-sweeper -n 50`
- Verifique se o `.env` está acessível

---

## 📝 Resumo Rápido

```bash
# 1. Atualizar código
git pull origin main

# 2. Adicionar credenciais ao .env
nano .env  # Adicione POLYMARKET_API_KEY, POLYMARKET_API_SECRET, POLYMARKET_FUNDER

# 3. Testar em DRY RUN
./start_claim_sweeper.sh

# 4. Rodar em LIVE
CLAIM_DRY_RUN=false ./start_claim_sweeper.sh

# 5. Instalar serviço (opcional)
sudo cp claim-sweeper.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable claim-sweeper
sudo systemctl start claim-sweeper
```

---

## ✅ Checklist Antes de Ativar LIVE

- [ ] Código atualizado (`git pull`)
- [ ] Credenciais adicionadas ao `.env`
- [ ] Testado em DRY RUN com sucesso
- [ ] Logs verificados e sem erros
- [ ] Wallet tem saldo suficiente para gas fees
- [ ] Entendeu que perde $0.01 por share (workaround da API)
- [ ] Monitoramento configurado (logs/systemd)

---

**🎯 Pronto! O Claim Sweeper está configurado e pronto para uso!**

