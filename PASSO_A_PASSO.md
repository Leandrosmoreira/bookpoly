# Passo a passo — bookpoly (bot 15min + claim)

Guia rápido dos comandos mais usados no servidor.

---

## 1. Aplicar alterações do Git (depois que alguém atualizou o repositório)

```bash
cd /root/bookpoly
git pull origin main
```

Depois **reinicie o bot** para ele usar o código novo:

```bash
sudo systemctl restart bot15min
```

Verificar se o serviço está rodando:

```bash
sudo systemctl status bot15min
```

---

## 2. Ver o dashboard no terminal

```bash
cd /root/bookpoly
/root/bookpoly/.venv/bin/python scripts/dashboard_bot15min.py
```

Ou, se `python3` estiver no PATH:

```bash
python3 scripts/dashboard_bot15min.py
```

O dashboard atualiza a cada 10 segundos. Para sair: **Ctrl+C**.

---

## 3. Reiniciar o bot (serviço)

```bash
sudo systemctl restart bot15min
```

Comando correto: **systemctl** (não `temctl`).

Ver logs do serviço:

```bash
sudo journalctl -u bot15min -f
```

---

## 4. Rodar o claim (resgatar ganhos)

Do diretório do projeto, usando o mesmo Python do bot.

Uma vez (só este run):

```bash
cd /root/bookpoly
/root/bookpoly/.venv/bin/python -m claim.main
```

Em loop (scan a cada 5 min):

```bash
/root/bookpoly/.venv/bin/python -m claim.main --loop
```

---

## 5. Ver status dos mercados 15min (script de status)

```bash
cd /root/bookpoly
/root/bookpoly/.venv/bin/python scripts/status_bot15min_markets.py
```

---

## 6. Resumo rápido

| O quê              | Comando |
|--------------------|--------|
| Atualizar código   | `cd /root/bookpoly && git pull origin main` |
| Reiniciar bot      | `sudo systemctl restart bot15min` |
| Ver dashboard      | `/root/bookpoly/.venv/bin/python scripts/dashboard_bot15min.py` |
| Rodar claim        | `/root/bookpoly/.venv/bin/python -m claim.main` |
| Status do serviço  | `sudo systemctl status bot15min` |
| Log do serviço     | `sudo journalctl -u bot15min -f` |

---

**Nota:** No servidor o comando é `python3` ou o Python do venv (`.venv/bin/python`), não `python`.
