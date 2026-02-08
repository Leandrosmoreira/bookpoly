# 📥 Download dos Arquivos Raw via FTP

## 📊 Estatísticas dos Dados

- **Total**: ~4.6 GB
- **Arquivos**: 30 arquivos
- **Estrutura**:
  - `books/`: 3.8 GB (dados do order book)
  - `signals/`: 411 MB (sinais gerados)
  - `volatility/`: 439 MB (dados de volatilidade)

## 🚀 Opção 1: Servidor FTP (Recomendado)

### Passo 1: Iniciar o servidor FTP

```bash
cd /root/bookpoly
./serve_raw_ftp.sh [porta]
```

**Porta padrão**: 2121

**Credenciais**:
- **Usuário**: `bookpoly`
- **Senha**: `bookpoly123`

### Passo 2: Conectar do seu PC

#### Usando FileZilla (GUI):
1. Abra o FileZilla
2. Host: `[IP_DO_SERVIDOR]`
3. Porta: `2121`
4. Usuário: `bookpoly`
5. Senha: `bookpoly123`
6. Clique em "Conectar"

#### Usando linha de comando:
```bash
ftp ftp://bookpoly:bookpoly123@[IP_DO_SERVIDOR]:2121
```

#### Usando navegador:
```
ftp://bookpoly:bookpoly123@[IP_DO_SERVIDOR]:2121
```

### Passo 3: Baixar os arquivos

Navegue até a pasta `raw/` e baixe os arquivos desejados.

---

## 📦 Opção 2: Compactar e Transferir

### Compactar os arquivos:

```bash
cd /root/bookpoly
tar -czf raw_backup.tar.gz -C data raw
```

Ou por pasta:

```bash
tar -czf raw_books.tar.gz -C data/raw books
tar -czf raw_signals.tar.gz -C data/raw signals
tar -czf raw_volatility.tar.gz -C data/raw volatility
```

### Transferir via SCP:

```bash
# Do seu PC
scp usuario@servidor:/root/bookpoly/raw_backup.tar.gz ./
```

---

## 🔧 Solução de Problemas

### Servidor FTP não inicia:

```bash
# Instalar dependências
pip install pyftpdlib
```

### Firewall bloqueando:

```bash
# Abrir porta no firewall
sudo ufw allow 2121/tcp
```

### Verificar se o servidor está rodando:

```bash
netstat -tlnp | grep 2121
```

---

## 📋 Estrutura dos Arquivos

```
data/raw/
├── books/
│   ├── BTC15m_2026-02-08.jsonl
│   ├── ETH15m_2026-02-08.jsonl
│   ├── SOL15m_2026-02-08.jsonl
│   └── XRP15m_2026-02-08.jsonl
├── signals/
│   ├── signals_2026-02-08.jsonl
│   └── signals_2026-02-07.jsonl
└── volatility/
    ├── BTCUSDT_volatility_2026-02-08.jsonl
    ├── ETHUSDT_volatility_2026-02-08.jsonl
    ├── SOLUSDT_volatility_2026-02-08.jsonl
    └── XRPUSDT_volatility_2026-02-08.jsonl
```

---

## ⚠️ Segurança

**IMPORTANTE**: As credenciais padrão são para teste. Para produção:

1. Altere a senha no script `serve_raw_ftp.sh`
2. Use SFTP ao invés de FTP
3. Configure firewall adequadamente

---

## 📞 Comandos Úteis

```bash
# Ver tamanho dos arquivos
du -sh data/raw/*

# Contar arquivos
find data/raw -type f | wc -l

# Listar arquivos
find data/raw -type f -name "*.jsonl"

# Servir FTP em background
nohup ./serve_raw_ftp.sh 2121 > /tmp/ftp_server.log 2>&1 &

# Parar servidor FTP
pkill -f "serve_raw_ftp"
```

