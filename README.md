# 🖥️ Monitor de Infraestrutura Inteligente

Sistema profissional de monitoramento para servidores Linux com integração ao Telegram, E-mail e IA Gemini.

## 🚀 Funcionalidades
- **Monitoramento de Disco**: Checagem automática via SSH.
- **Alertas em Tempo Real**: Notificações automáticas a cada hora em caso de falha.
- **Relatórios**: Resumo matinal consolidado todos os dias às 07:15.
- **Interativo**: Comando `/status` para checagem imediata via Telegram.

## 🛠️ Instalação
1. Copie os arquivos para o servidor (ex: `/opt/monitor_infra`).
2. Instale as dependências:
   `pip install paramiko pyTelegramBotAPI google-genai schedule`
3. Certifique-se de que o arquivo `servidores.json` está na mesma pasta.

## ⚙️ Executando como Serviço (Systemd)
Para que o script não pare nunca, crie o arquivo `/etc/systemd/system/monitor-linux.service`:
```ini
[Unit]
Description=Monitor de Infraestrutura do Bruno
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/monitor_infra
ExecStart=/usr/bin/python3 monitor_v2.py
Restart=always

[Install]
WantedBy=multi-user.target