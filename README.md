# 🖥️ Monitor de Infraestrutura Inteligente (com IA)

Sistema profissional de monitoramento para servidores Linux/Windows. Totalmente integrado com Telegram, E-mail e com a Inteligência Artificial do Google (Gemini) para análise e resolução de problemas em tempo real.

## 🚀 Super Funcionalidades

- **Monitoramento Silencioso e Inteligente**: Checagem automática via SSH. Possui "Janela de Manutenção" configurada (silêncio total das 20h às 05h para evitar alarmes falsos de backup).
- **Rotina de Bancos de Dados**: Todos os dias às 07:00, o robô lê os arquivos `status_manutencao.json` e alerta se houve falha na reindexação/backup da madrugada.
- **Resumo Matinal**: Relatório consolidado da infraestrutura entregue no Telegram e no E-mail pontualmente às 07:15.
- **Motor Multi-Thread**: Consegue realizar varreduras em dezenas de servidores ao mesmo tempo, reduzindo o tempo de espera para poucos segundos.
- **Log Rotation Embutido**: O sistema se auto-limpa, guardando o histórico de logs (`monitor_infra.log`) rotacionados a cada meia-noite sem lotar o HD.

## 🤖 Comandos Interativos (Telegram)
O bot possui uma interface de botões para você não precisar digitar nomes de servidores.

- `/status` - 📊 Verifica o espaço em disco atual de todos os servidores.
- `/varredura` - 🚀 Dispara conexões simultâneas para achar arquivos `.fbk` e `.fdb` perdidos em todos os servidores de uma vez.
- `/analisar` - 🧠 Acessa um servidor com disco cheio, varre os arquivos pesados e pede para o Gemini gerar os comandos de limpeza.
- `/bancos` - 🔎 Busca backups esquecidos em um servidor específico e a IA sugere os comandos `rm -f`.
- `/logs` - 📄 Lê os últimos 15 dias de logs de manutenção (`manutencao_banco.txt`) e a IA resume se os processos terminaram com Sucesso ✅ ou Erro 🚨.

## 🛠️ Instalação e Configuração

1. Clone o repositório ou copie os arquivos para o servidor (ex: `/opt/monitor_infra`).
2. Instale as dependências necessárias do Python:
```bash
pip install paramiko pyTelegramBotAPI google-genai schedule
```

3. Crie o arquivo `servidores.json` na raiz da pasta usando o modelo `servidores.exemplo.json` (adicione seus tokens, senhas e chaves de API).

## ⚙️ Executando como Serviço (Systemd)

Para garantir que o script rode 24/7 de forma imortal no Linux, crie o arquivo de serviço:

```bash
nano /etc/systemd/system/monitor-linux.service
```
Cole a configuração abaixo:
```bash
[Unit]
Description=Monitor de Infraestrutura do Bruno
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/monitor_infra
ExecStart=/usr/bin/python3 monitor2.py
Restart=always

[Install]
WantedBy=multi-user.target
```
Ative e inicie o serviço com os comandos:
```bash
systemctl daemon-reload
systemctl enable monitor-linux.service
systemctl start monitor-linux.service
systemctl status monitor-linux.service
```