import json
import paramiko
import sys
import logging
import os
import threading
import time
import schedule
import telebot
from datetime import datetime
from email.message import EmailMessage
import smtplib
from google import genai
from telebot import types # Importante para criar os botões na tela
from logging.handlers import TimedRotatingFileHandler

# ==============================================================================
# CAPÍTULO 1: CONFIGURAÇÕES E LOGS (A memória e os preparativos do Bot)
# ==============================================================================

# Configura o sistema para salvar um arquivo de texto com tudo o que acontece.
rotacionador_log = TimedRotatingFileHandler(
    filename="monitor_infra.log", 
    when="midnight", 
    interval=1, 
    backupCount=1, 
    encoding='utf-8'
)

# Define o formato visual de como a mensagem de erro/aviso vai aparecer no arquivo de log
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        rotacionador_log,
        logging.StreamHandler(sys.stdout) # Faz o log aparecer na tela preta (CMD) também
    ]
)

# Descobre onde este script Python está salvo e procura o 'servidores.json' na mesma pasta
DIRETORIO_ATUAL = os.path.dirname(os.path.abspath(__file__))
ARQUIVO_CONFIG = os.path.join(DIRETORIO_ATUAL, 'servidores.json')

def carregar_configuracao():
    """Lê o arquivo JSON. Se não achar ou tiver erro de vírgula no JSON, ele avisa e desliga o script."""
    try:
        with open(ARQUIVO_CONFIG, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Falha ao carregar config: {e}")
        sys.exit(1)

config_global = carregar_configuracao()

# Extrai as chaves secretas do JSON carregado
TOKEN = config_global.get('config_telegram', {}).get('token')
GEMINI_KEY = config_global.get('config_ia', {}).get('gemini_api_key')

# ==============================================================================
# NOVIDADE: Trata múltiplos IDs de forma segura
# ==============================================================================
_raw_chat_id = config_global.get('config_telegram', {}).get('chat_id', [])
# Se por acaso no JSON não for uma lista, transforma em lista. Converte tudo para texto.
if isinstance(_raw_chat_id, list):
    CHAT_IDS = [str(x) for x in _raw_chat_id]
else:
    CHAT_IDS = [str(_raw_chat_id)]


# Liga os motores do Telegram e da Inteligência Artificial
bot = telebot.TeleBot(TOKEN) if TOKEN else None
cliente_ia = genai.Client(api_key=GEMINI_KEY) if GEMINI_KEY else None


# ==============================================================================
# CAPÍTULO 2: FUNÇÕES DE APOIO E CONEXÃO (As ferramentas pesadas)
# ==============================================================================

def enviar_alerta_geral(mensagem, parse_mode=None):
    """Envia uma mensagem automática para TODOS os administradores cadastrados."""
    if not bot: return
    for chat_id in CHAT_IDS:
        try:
            bot.send_message(chat_id, mensagem, parse_mode=parse_mode)
        except Exception as e:
            logging.error(f"Erro ao enviar alerta para {chat_id}: {e}")

def gerar_teclado_servidores():
    """Lê a lista de servidores do JSON e transforma cada 'apelido' em um botão clicável no Telegram."""
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    servidores = config_global.get('servidores', [])
    for s in servidores:
        markup.add(types.KeyboardButton(s['apelido']))
    return markup

def enviar_email(assunto, corpo):
    """Monta um e-mail estruturado e envia usando as configurações SMTP do JSON."""
    conf = config_global.get('config_email')
    if not conf: return # Se não tiver config de e-mail no JSON, ignora e segue a vida
    
    try:
        msg = EmailMessage()
        msg['Subject'] = assunto
        msg['From'] = conf['remetente']
        msg['To'] = ", ".join(conf['destinatarios'])
        msg.set_content(corpo)
        
        with smtplib.SMTP_SSL(conf['servidor_smtp'], conf['porta_smtp'], timeout=20) as s:
            s.login(conf['usuario'], conf['senha'])
            s.send_message(msg)
        logging.info(f"E-mail enviado: {assunto}")
    except Exception as e:
        logging.error(f"Erro ao enviar e-mail: {e}")

def verificar_disco(servidor):
    """Conecta no servidor via SSH, roda o 'df -h' e vê se a % de disco passou do limite estipulado."""
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            hostname=servidor['host'], 
            username=servidor['usuario'], 
            password=servidor['senha'], 
            port=servidor.get('porta', 22), 
            timeout=10
        )
        # Pega só a última linha do resultado do disco
        cmd = f"df -h {servidor['particao']} | tail -n 1"
        _, stdout, stderr = ssh.exec_command(cmd)
        saida = stdout.read().decode().strip()
        ssh.close()

        # Fatiamento de texto para encontrar o número da porcentagem
        partes = saida.split()
        if len(partes) >= 5:
            uso = int(partes[-2].replace('%', ''))
            if uso >= servidor['limite_percentual']:
                return f"🚨 *{servidor['apelido']}*: {uso}% de uso (Limite {servidor['limite_percentual']}%)"
        return None # Retorna None se estiver tudo OK com o espaço
        
    except Exception as e:
        return f"❌ *{servidor['apelido']}*: Falha de conexão (`{e}`)"


# ==============================================================================
# CAPÍTULO 3: COMANDOS DO TELEGRAM (A interface que você conversa)
# ==============================================================================
# A segurança agora verifica se o ID de quem chamou está dentro da lista CHAT_IDS

@bot.message_handler(commands=['start', 'id'])
def cmd_start(m):
    # Essa função é a "recepcionista". Ela atende quem clica em Começar.
    print(f"🚨 NOVO USUÁRIO! Nome: {m.from_user.first_name} | ID CAPTURADO: {m.chat.id}")
    
    # Se o ID já estiver na lista, dá as boas vindas normais
    if str(m.chat.id) in CHAT_IDS:
        bot.reply_to(m, "Olá, Administrador! O sistema está operante. Use /status para checar a rede.")
    else:
        # Se for um desconhecido (como seu amigo agora), avisa o ID dele na tela do celular
        bot.reply_to(m, f"Olá, {m.from_user.first_name}! Você não tem acesso ao painel.\nSeu ID de autorização é: {m.chat.id}")


@bot.message_handler(commands=['json'])
def cmd_json(m):
    if str(m.chat.id) not in CHAT_IDS: return
    bot.reply_to(m, "🔎 Lendo arquivos JSON em todos os servidores...")
    threading.Thread(target=rotina_diaria_bancos).start()

@bot.message_handler(commands=['status'])
def cmd_status(m):
    print(f"ID CAPTURADO: {m.chat.id}")
    if str(m.chat.id) not in CHAT_IDS: return
    
    bot.reply_to(m, "🔎 Verificando servidores...")
    resumos = [verificar_disco(s) or f"✅ {s['apelido']}: OK" for s in config_global.get('servidores', [])]
    
    # Responde apenas para quem pediu o comando
    bot.send_message(m.chat.id, "\n".join(resumos), parse_mode="Markdown")

@bot.message_handler(commands=['analisar'])
def cmd_analisar(m):
    if str(m.chat.id) not in CHAT_IDS: return
    msg = bot.reply_to(m, "Selecione o servidor para analisar arquivos grandes:", reply_markup=gerar_teclado_servidores())
    bot.register_next_step_handler(msg, processar_analise_ia)

def processar_analise_ia(message):
    """Recebe o clique do botão do /analisar, roda o comando 'du -ah' otimizado e manda pra IA ler."""
    apelido = message.text
    servidor = next((s for s in config_global.get('servidores', []) if s['apelido'] == apelido), None)
    
    if not servidor:
        bot.send_message(message.chat.id, "❌ Servidor não encontrado.")
        return

    bot.send_message(message.chat.id, f"🧠 Analisando `{apelido}`... aguarde.", parse_mode="Markdown", reply_markup=types.ReplyKeyboardRemove())
    
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(hostname=servidor['host'], username=servidor['usuario'], password=servidor['senha'], port=servidor.get('porta', 22), timeout=20)
        
        # PEGA AS PASTAS PARA IGNORAR DO JSON
        pastas_ignorar = servidor.get('ignorar_pastas', [])
        
        # Adiciona a pasta /bkp à lista de ignorados do Linux
        pastas_ignorar.append("*/bkp*")
        
        # Cria a string de exclusão. 
        # Mantemos o bloqueio aos '.fdb' para proteger o banco quente, mas LIBERAMOS os '.fbk'!
        str_exclude = " ".join([f"--exclude={p}" for p in pastas_ignorar])
        str_exclude += " --exclude='*.fdb'" 
        
        # REDUÇÃO DE INPUT: head -n 10
        cmd = f"du -ah {str_exclude} {servidor['particao']} 2>/dev/null | sort -rh | head -n 10"
        _, stdout, _ = ssh.exec_command(cmd)
        saida_du = stdout.read().decode('utf-8').strip()
        ssh.close()

        # Se a saída estiver vazia, nem gasta token chamando a IA
        if not saida_du:
            bot.send_message(message.chat.id, "Nenhum arquivo grande encontrado ou falta de permissão.", parse_mode="Markdown")
            return

        # 3. PROMPT ESTRITO: Força a IA a gastar poucos tokens na resposta
        prompt = f"""
        Analise esta lista de arquivos pesados de um servidor Linux:
        {saida_du}
        
        Regras de resposta (Seja extremamente curto):
        1. Diga qual é o maior ofensor de espaço em 1 frase curta.
        2. Dê apenas 1 comando `rm -f` no formato de bloco de código para limpar arquivos de log ou temporários seguros.
        3. ZERO texto de introdução ou conclusão. Vá direto ao ponto.
        """
        
        resposta = cliente_ia.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        
        bot.send_message(message.chat.id, f"🤖 *Análise Gemini:*\n\n{resposta.text}", parse_mode="Markdown")
        
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Erro: `{e}`")

@bot.message_handler(commands=['bancos'])
def cmd_bancos(m):
    if str(m.chat.id) not in CHAT_IDS: return
    msg = bot.reply_to(m, "Selecione o servidor para buscar backups esquecidos:", reply_markup=gerar_teclado_servidores())
    bot.register_next_step_handler(msg, processar_bancos_ia)

def processar_bancos_ia(message):
    """Recebe o clique do botão do /bancos e procura arquivos .fbk ou copia.fdb com a IA."""
    apelido = message.text
    servidor = next((s for s in config_global.get('servidores', []) if s['apelido'] == apelido), None)
    
    if not servidor: return

    bot.send_message(message.chat.id, f"🔎 Buscando .fbk e .fdb em `{apelido}`...", parse_mode="Markdown", reply_markup=types.ReplyKeyboardRemove())
    
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(hostname=servidor['host'], username=servidor['usuario'], password=servidor['senha'], port=servidor.get('porta', 22), timeout=30)
        
        cmd = f'find {servidor["particao"]} -type f \\( -iname "*.fbk" -o -iname "*copia*.fdb" \\) -exec du -h {{}} + 2>/dev/null | sort -rh | head -n 15'
        _, stdout, _ = ssh.exec_command(cmd)
        saida = stdout.read().decode('utf-8').strip()
        ssh.close()

        if not saida:
            bot.send_message(message.chat.id, "✅ Nenhum backup perdido encontrado.")
            return

        prompt = f"Gere comandos rm -f para estes arquivos de banco encontrados em {apelido}: {saida}"
        resposta = cliente_ia.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        bot.send_message(message.chat.id, f"🤖 *Sugestão de Limpeza:*\n\n{resposta.text}", parse_mode="Markdown")
        
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Erro: `{e}`")

@bot.message_handler(commands=['logs'])
def cmd_logs(m):
    if str(m.chat.id) not in CHAT_IDS: return
    msg = bot.reply_to(m, "Selecione o servidor para ler os logs de manutenção:", reply_markup=gerar_teclado_servidores())
    bot.register_next_step_handler(msg, processar_logs_ia)

def processar_logs_ia(message):
    """Lê os últimos 15 dias de logs de manutenção de um servidor e envia para a IA resumir."""
    apelido = message.text
    servidor = next((s for s in config_global.get('servidores', []) if s['apelido'] == apelido), None)
    
    if not servidor: return

    bot.send_message(message.chat.id, f"🔎 Buscando arquivos `manutencao_banco.txt` em `{apelido}` (últimos 15 dias)...", parse_mode="Markdown", reply_markup=types.ReplyKeyboardRemove())
    
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(hostname=servidor['host'], username=servidor['usuario'], password=servidor['senha'], port=servidor.get('porta', 22), timeout=25)
        
        cmd_find = "find /data -type f -name 'manutencao_banco.txt' -mtime -15 2>/dev/null"
        _, stdout, _ = ssh.exec_command(cmd_find)
        arquivos_log = stdout.read().decode('utf-8').strip().split('\n')
        arquivos_log = [arq for arq in arquivos_log if arq]

        if not arquivos_log:
            bot.send_message(message.chat.id, "✅ Nenhum log recente encontrado.")
            ssh.close()
            return

        for arquivo in arquivos_log:
            cmd_tail = f"tail -n 30 {arquivo}"
            _, stdout_tail, _ = ssh.exec_command(cmd_tail)
            conteudo = stdout_tail.read().decode('utf-8').strip()

            if conteudo:
                prompt = f"""
                Você é um DBA. Analise as últimas linhas deste log de banco de dados:
                {conteudo}
                Responda de forma curta em apenas uma linha se o processo terminou com sucesso ou se teve erro. 
                Comece com ✅ (Sucesso) ou 🚨 (Erro).
                """
                resposta = cliente_ia.models.generate_content(model='gemini-2.5-flash', contents=prompt)
                bot.send_message(message.chat.id, f"📄 `{arquivo}`:\n{resposta.text}", parse_mode="Markdown")
                time.sleep(15) # Freio para não estourar a cota gratuita do Google (Evita Erro 429)
                
        ssh.close()
        bot.send_message(message.chat.id, f"🏁 Leitura de logs de `{apelido}` finalizada!", parse_mode="Markdown")
        
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Erro ao ler logs:\n`{e}`", parse_mode="Markdown")

@bot.message_handler(commands=['varredura'])
def cmd_varredura(m):
    """Comando ninja: Dispara a busca por arquivos perdidos em TODOS os servidores ao mesmo tempo."""
    if str(m.chat.id) not in CHAT_IDS: return
    
    bot.reply_to(m, "🚀 Disparando conexões simultâneas... Aguarde o relatório final.")
    
    def orquestrador():
        threads = []
        resultados_globais = [] 
        
        for s in config_global.get('servidores', []):
            t = threading.Thread(target=buscar_bancos_perdidos_background, args=(s, resultados_globais))
            threads.append(t)
            t.start() 
            
        for t in threads:
            t.join()
            
        # Envia a resposta final apenas para quem rodou o comando
        if resultados_globais:
            msg_final = "📊 *Resultado da Varredura:*\n\n" + "\n\n".join(resultados_globais)
            bot.send_message(m.chat.id, msg_final, parse_mode="Markdown")
        else:
            bot.send_message(m.chat.id, "✅ *Varredura Concluída!* Nenhum arquivo `.fbk` ou `.fdb` perdido foi encontrado nos servidores.", parse_mode="Markdown")

    threading.Thread(target=orquestrador).start()


# ==============================================================================
# CAPÍTULO 4: TAREFAS DE SEGUNDO PLANO (A inteligência das rotinas)
# ==============================================================================

def buscar_bancos_perdidos_background(servidor, resultados_globais):
    """Função usada pelo comando /varredura para buscar arquivos rapidamente e sem IA."""
    apelido = servidor['apelido']
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(hostname=servidor['host'], username=servidor['usuario'], password=servidor['senha'], port=servidor.get('porta', 22), timeout=30)
        
        cmd = f'find {servidor["particao"]} -path "*/bkp" -prune -o -type f \\( -iname "*.fbk" -o -iname "*copia*.fdb" -o -iname "*ant*.fdb" -o -iname "*old*.fdb" \\) -exec du -h {{}} + 2>/dev/null | sort -rh | head -n 15'
        _, stdout, _ = ssh.exec_command(cmd)
        saida = stdout.read().decode('utf-8').strip()
        ssh.close()

        if saida:
            resultados_globais.append(f"⚠️ *{apelido}:*\n```text\n{saida}\n```")
            
    except Exception as e:
        logging.error(f"Erro ao varrer bancos em {apelido}: {e}")
        resultados_globais.append(f"❌ *{apelido}:* Erro de conexão (`{e}`)")

def verificar_json_bancos(servidor):
    """Lê o arquivo status_manutencao.json no Linux e filtra erros graves."""
    apelido = servidor['apelido']
    host = servidor['host']
    so = servidor.get('so', 'linux').lower()
    
    alertas = []
    if so == 'windows': return alertas 
        
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(hostname=host, username=servidor['usuario'], password=servidor['senha'], port=servidor.get('porta', 22), timeout=15)
        
        cmd = "find /data -type f -name 'status_manutencao.json' -mtime -2 -exec cat {} \\; -exec echo '---FIM_JSON---' \\; 2>/dev/null"
            
        _, stdout, _ = ssh.exec_command(cmd)
        saida = stdout.read().decode('utf-8', errors='ignore').strip()
        ssh.close()
        
        if not saida: return alertas
            
        blocos = saida.split('---FIM_JSON---')
        
        for bloco in blocos:
            if not bloco.strip(): continue
            try:
                dados = json.loads(bloco.strip())
                status = dados.get('status', '').upper()
                
                if status in ['ERRO', 'ERRO_CRITICO']:
                    nome_srv = dados.get('cliente_servidor', dados.get('servidor', apelido))
                    banco = dados.get('banco', 'Desconhecido')
                    msg = dados.get('mensagem', 'Sem mensagem')
                    data_att = dados.get('data_atualizacao', 'Sem data')
                    
                    alerta = (f"🚨 *Status:* `{status}`\n"
                              f"🖥️ *Servidor:* `{nome_srv}`\n"
                              f"🗄️ *Banco:* `{banco}`\n"
                              f"📅 *Data:* `{data_att}`\n"
                              f"💬 *Erro:* `{msg}`")
                    alertas.append(alerta)
            except json.JSONDecodeError:
                continue 
                
    except Exception as e:
        logging.error(f"Erro ao ler JSONs em {apelido}: {e}")
        
    return alertas

def rotina_diaria_bancos():
    """Roda em todos os servidores e manda o consolidadão de Erros de JSON pro Telegram."""
    logging.info("Iniciando varredura diária de JSONs de banco...")
    alertas_gerais = []
    
    for s in config_global.get('servidores', []):
        erros_encontrados = verificar_json_bancos(s)
        if erros_encontrados:
            alertas_gerais.extend(erros_encontrados)
            
    if bot:
        if alertas_gerais:
            msg_final = "⚠️ *RELATÓRIO DE ERROS NAS REINDEXAÇÕES*\n\n" + "\n\n".join(alertas_gerais)
        else:
            msg_final = "✅ *Manutenção dos Bancos:* Varredura concluída! Tudo OK, nenhum erro encontrado."
            
        enviar_alerta_geral(msg_final, parse_mode="Markdown")
        logging.info("Relatório de bancos enviado pro Telegram.")


# ==============================================================================
# CAPÍTULO 5: AGENDADOR E MOTOR PRINCIPAL (O coração que mantém tudo batendo)
# ==============================================================================

def job_resumo_matinal():
    """Gera o relatório visual de bom dia, separando quem tá OK de quem tá com problema."""
    logging.info("Iniciando resumo matinal...")
    alertas = []
    oks = []
    for s in config_global.get('servidores', []):
        res = verificar_disco(s)
        if res and ("🚨" in str(res) or "❌" in str(res)):
            alertas.append(res)
        else:
            oks.append(f"✅ {s['apelido']}")
    
    msg = f"🌅 *Resumo Infra - {datetime.now().strftime('%d/%m')}*\n\n"
    if alertas: msg += "*Atenção Necessária:*\n" + "\n".join(alertas) + "\n\n"
    if oks: msg += "*Servidores OK:* " + " | ".join(oks)

    enviar_alerta_geral(msg, parse_mode="Markdown")
    enviar_email("Resumo da Infraestrutura", msg.replace('*', '')) 

def job_checagem_hourly():
    """Checagem silenciosa. Só apita no Telegram se o bicho pegar."""
    hora_atual = datetime.now().hour
    
    if hora_atual >= 20 or hora_atual < 5:
        logging.info(f"Checagem ignorada. O relógio marca {hora_atual}h (Janela de Manutenção).")
        return 
        
    logging.info("Iniciando checagem horária silenciosa...")
    
    alertas = [verificar_disco(s) for s in config_global.get('servidores', [])]
    alertas = [a for a in alertas if a and "🚨" in a]
    
    if alertas and bot:
        msg_alerta = "⚠️ *Alerta de Rotina:* \n\n" + "\n".join(alertas)
        enviar_alerta_geral(msg_alerta, parse_mode="Markdown")


def run_scheduler():
    schedule.every().day.at("07:00").do(lambda: threading.Thread(target=rotina_diaria_bancos).start())
    schedule.every().day.at("07:15").do(lambda: threading.Thread(target=job_resumo_matinal).start())
    schedule.every(3).hours.do(lambda: threading.Thread(target=job_checagem_hourly).start())
    
    while True:
        schedule.run_pending()
        time.sleep(10)

if __name__ == "__main__":
    logging.info("=== Monitor Iniciado com Sucesso ===")
    threading.Thread(target=run_scheduler, daemon=True).start()
    bot.infinity_polling()