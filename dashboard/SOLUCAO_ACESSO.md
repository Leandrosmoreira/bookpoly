# 🔧 Solução para Acesso ao Dashboard

## ❌ Problema

O erro `ERR_CONNECTION_TIMED_OUT` significa que a porta 5001 está bloqueada pelo firewall do provedor/VPS.

## ✅ Soluções

### Opção 1: Túnel SSH (Mais Confiável - SEMPRE FUNCIONA)

**No seu PC (PowerShell ou Git Bash):**

```bash
# Criar túnel SSH na porta 5001
ssh -L 5001:localhost:5001 root@31.97.165.64
```

**Deixe esse terminal aberto**, depois acesse no navegador:
```
http://localhost:5001
```

---

### Opção 2: Mudar para Porta 80 ou 443 (Mais Comum)

Edite `dashboard/app.py` e mude a porta:

```python
app.run(host='0.0.0.0', port=80, debug=False)  # Porta 80 (HTTP padrão)
```

Ou porta 443 (HTTPS):
```python
app.run(host='0.0.0.0', port=443, debug=False)
```

**Atenção:** Portas abaixo de 1024 requerem sudo.

---

### Opção 3: Usar Porta 8080 ou 8888

```python
app.run(host='0.0.0.0', port=8080, debug=False)
```

Depois acesse: `http://31.97.165.64:8080`

---

### Opção 4: Verificar e Abrir Firewall

```bash
# Verificar firewall
sudo ufw status

# Se necessário, abrir porta (CUIDADO)
sudo ufw allow 5001/tcp
```

---

## 🎯 Recomendação

**Use Túnel SSH (Opção 1)** - é a mais confiável e sempre funciona, mesmo com firewall bloqueado.

---

## 🔍 Testar Conexão

**No seu PC (PowerShell):**

```powershell
# Testar se consegue conectar
Test-NetConnection -ComputerName 31.97.165.64 -Port 5001
```

Se falhar, a porta está bloqueada e você deve usar túnel SSH.

