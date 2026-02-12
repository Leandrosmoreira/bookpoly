# 🔑 Guia: Gerar API Keys do Polymarket

Este guia explica como usar o script `generate_api_keys.py` para gerar automaticamente as credenciais da API do Polymarket a partir da sua conta Magic Email.

---

## 📋 Pré-requisitos

1. **Conta Polymarket com Magic Email** (não MetaMask)
2. **Acesso à sua private key**
3. **Acesso ao seu funder address (wallet)**

---

## 🚀 Passo a Passo

### 1️⃣ Baixar o código atualizado

```bash
cd /root/bookpoly
git pull origin main
```

---

### 2️⃣ Instalar py-clob-client

```bash
cd /root/bookpoly
source venv/bin/activate
pip install py-clob-client
```

**Verificar instalação:**
```bash
python -c "from py_clob_client.client import ClobClient; print('✅ OK')"
```

---

### 3️⃣ Obter sua Private Key

**Opção A: Via reveal.magic.link (Recomendado)**
1. Acesse: https://reveal.magic.link/polymarket
2. Faça login com sua conta Polymarket
3. Copie a **private key** exibida

**Opção B: Via Polymarket App**
1. Abra o app Polymarket
2. Vá em **Cash** → Menu (3 pontos) → **"Export Private Key"**
3. Copie a private key

**Formato esperado:**
- Com ou sem prefixo `0x`
- 64 caracteres hexadecimais
- Exemplo: `0x1234567890abcdef...` ou `1234567890abcdef...`

---

### 4️⃣ Obter seu Funder Address

1. Acesse: https://polymarket.com/settings
2. Encontre a seção **"Wallet"** ou **"Funder Address"**
3. Copie o endereço da carteira (começa com `0x`)

**Formato esperado:**
- Endereço Ethereum/Polygon (42 caracteres)
- Começa com `0x`
- Exemplo: `0x1234567890abcdef1234567890abcdef12345678`

---

### 5️⃣ Executar o script

```bash
cd /root/bookpoly
source venv/bin/activate
python scripts/generate_api_keys.py
```

**O script vai pedir:**

1. **Private key:**
   ```
   Enter your private key (with or without 0x): 
   ```
   - Cole sua private key (com ou sem `0x`)
   - Pressione Enter

2. **Funder address:**
   ```
   Enter your Polymarket wallet address (0x...): 
   ```
   - Cole seu endereço de wallet
   - Pressione Enter

---

### 6️⃣ Copiar as credenciais geradas

**O script vai exibir algo como:**

```
============================================================
 SUCCESS! Add these to your .env file:
============================================================

# Polymarket API Credentials (Magic Email)
POLYMARKET_API_KEY=abc123...
POLYMARKET_API_SECRET=xyz789...
POLYMARKET_PASSPHRASE=passphrase123
POLYMARKET_FUNDER=0x1234...
POLYMARKET_PRIVATE_KEY=abcd...
POLYMARKET_SIGNATURE_TYPE=1

# Enable Claim Sweeper
CLAIM_ENABLED=true
CLAIM_DRY_RUN=true  # Set to false for live claims
```

**Opção 1: Salvar automaticamente**
- Quando perguntado `Save to .env.polymarket? (y/n):`, digite `y`
- O script salva em `.env.polymarket`
- Depois copie para `.env`: `cat .env.polymarket >> .env`

**Opção 2: Copiar manualmente**
- Copie todas as linhas exibidas
- Cole no arquivo `.env`:
  ```bash
  nano /root/bookpoly/.env
  # Cole as linhas no final do arquivo
  ```

---

## ✅ Verificação

**Verificar se as credenciais foram adicionadas:**

```bash
cd /root/bookpoly
grep -E "POLYMARKET_API_KEY|POLYMARKET_FUNDER" .env
```

**Deve mostrar:**
```
POLYMARKET_API_KEY=abc123...
POLYMARKET_FUNDER=0x1234...
```

---

## 🔒 Segurança

⚠️ **IMPORTANTE:**
- **NUNCA** compartilhe sua private key
- **NUNCA** faça commit do `.env` no Git
- Mantenha o `.env` com permissões restritas:
  ```bash
  chmod 600 /root/bookpoly/.env
  ```

---

## 🐛 Troubleshooting

### Erro: "py-clob-client not installed"
```bash
source venv/bin/activate
pip install py-clob-client
```

### Erro: "Private key length is wrong"
- Certifique-se de copiar a **chave completa** (64 caracteres hex)
- Remova espaços ou quebras de linha
- O script aceita com ou sem prefixo `0x`

### Erro: "Invalid private key format"
- Verifique se copiou corretamente
- Certifique-se de que é uma conta **Magic Email**, não MetaMask
- Tente exportar novamente da fonte original

### Erro: "Wrong funder address"
- Verifique se copiou o endereço completo (42 caracteres)
- Certifique-se de que começa com `0x`
- Verifique em https://polymarket.com/settings

### Erro de conexão
- Verifique sua conexão com a internet
- O script precisa conectar-se a `https://clob.polymarket.com`
- Tente novamente após alguns segundos

---

## 📝 Resumo Rápido

```bash
# 1. Atualizar código
cd /root/bookpoly
git pull origin main

# 2. Instalar dependência
source venv/bin/activate
pip install py-clob-client

# 3. Executar script
python scripts/generate_api_keys.py

# 4. Seguir instruções:
#    - Cole private key (de reveal.magic.link)
#    - Cole funder address (de polymarket.com/settings)
#    - Copie output para .env

# 5. Verificar
grep POLYMARKET_API_KEY .env
```

---

## 🎯 Próximos Passos

Após gerar as API keys:

1. **Testar Claim Sweeper em DRY RUN:**
   ```bash
   ./start_claim_sweeper.sh
   ```

2. **Quando estiver pronto, ativar LIVE:**
   ```bash
   CLAIM_DRY_RUN=false ./start_claim_sweeper.sh
   ```

3. **Instalar como serviço (opcional):**
   ```bash
   sudo cp claim-sweeper.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable claim-sweeper
   sudo systemctl start claim-sweeper
   ```

---

**✅ Pronto! Suas API keys foram geradas e configuradas!**

