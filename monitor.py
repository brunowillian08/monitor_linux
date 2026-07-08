import json
import paramiko
import sys
import logging
import os
import threading
import time
import schedule
import telebot
from datetime import datetime, timedelta
from email.message import EmailMessage
import smtplib
from google import genai
from google.api_core import exceptions
from telebot import types
from logging.handlers import TimedRotatingFileHandler
from dotenv import load_dotenv

# ==============================================================================
# CAPÍTULO 1: CONFIGURAÇÕES E LOGS
# ==============================================================================
load_dotenv()
rotacionador_log = TimedRotatingFileHandler(
    filename="monitor_infra.log", 
    when="midnight", 
    interval=1, 
    backupCount=1, 
    encoding='utf-8'
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        rotacionador_log,
        logging.StreamHandler(sys.stdout)
    ]
)

DIRETORIO_ATUAL = os.path.dirname(os.path.abspath(__file__))
ARQUIVO_CONFIG = os.path.join(DIRETORIO_ATUAL, 'servidores.json')

def carregar_configuracao():
    """Lê o arquivo JSON de configuração."""
    try:
        with open(ARQUIVO_CONFIG, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Falha ao carregar config: {e}")
        sys.exit(1)

config_global = carregar_configuracao()

# SEGURANÇA: Gestão de Segredos via Variáveis de Ambiente
TOKEN = os.getenv('TELEGRAM_TOKEN') or config_global.get('config_telegram', {}).get('token')
GEMINI_KEY = os.getenv('GEMINI_API_KEY') or config_global.get('config_ia', {}).get('gemini_api_key')

_raw_chat_id = config_global.get('config_telegram', {}).get('chat_id', [])
CHAT_IDS = [str(x) for x in _raw_chat_id] if isinstance(_raw_chat_id, list) else [str(_raw_chat_id)]

bot = telebot.TeleBot(TOKEN) if TOKEN else None
cliente_ia = genai.Client(api_key=GEMINI_KEY) if GEMINI_KEY else None

# ==============================================================================
# CAPÍTULO 2: FUNÇÕES DE APOIO E CONEXÃO
# ==============================================================================

def gerar_conteudo_ia_com_retry(prompt, max_tentativas=3):
    """Executa a chamada à IA com lógica de retentativa para erros 503 e 429."""
    for tentativa in range(max_tentativas):
        try:
            return cliente_ia.models.generate_content(
                model='gemini-3.5-flash', 
                contents=prompt
            )
        except (exceptions.ServiceUnavailable, exceptions.InternalServerError):
            wait_time = (tentativa + 1) * 5
            logging.warning(f"IA instável (503/500). Tentativa {tentativa+1}. Aguardando {wait_time}s...")
            time.sleep(wait_time)
        except exceptions.ResourceExhausted:
            logging.warning("Cota atingida (429). Aguardando 30s...")
            time.sleep(30)
        except Exception as e:
            logging.error(f"Erro inesperado na IA: {e}")
            break
    return None

def conectar_ssh(servidor, timeout=20):
    """Centraliza a conexão SSH com tratamento de erros profissional."""
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            hostname=servidor['host'], 
            username=servidor['usuario'], 
            password=servidor['senha'], 
            port=servidor.get('porta', 22), 
            timeout=timeout
        )
        return ssh
    except paramiko.AuthenticationException:
        logging.error(f"Erro de autenticação em {servidor['apelido']}.")
    except paramiko.SSHException as e:
        logging.error(f"Erro de protocolo SSH em {servidor['apelido']}: {e}")
    except Exception as e:
        logging.error(f"Falha de conexão em {servidor['apelido']}: {e}")
    return None

def enviar_alerta_geral(mensagem, parse_mode=None):
    if not bot: return
    for chat_id in CHAT_IDS:
        try:
            bot.send_message(chat_id, mensagem, parse_mode=parse_mode)
        except Exception as e:
            logging.error(f"Erro ao enviar alerta para {chat_id}: {e}")

def gerar_teclado_servidores():
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    servidores = config_global.get('servidores', [])
    for s in servidores:
        markup.add(types.KeyboardButton(s['apelido']))
    return markup

def enviar_email(assunto, corpo):
    conf = config_global.get('config_email')
    if not conf: return
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
    ssh = conectar_ssh(servidor, timeout=10)
    if not ssh: return f"❌ *{servidor['apelido']}*: Falha de conexão"
    try:
        cmd = f"df -h {servidor['particao']} | tail -n 1"
        _, stdout, _ = ssh.exec_command(cmd)
        saida = stdout.read().decode().strip()
        partes = saida.split()
        if len(partes) >= 5:
            uso = int(partes[-2].replace('%', ''))
            if uso > servidor['limite_percentual']:
                return f"🚨 *{servidor['apelido']}*: {uso}% de uso (Limite {servidor['limite_percentual']}%)"
        return None
    finally:
        ssh.close()

# ==============================================================================
# CAPÍTULO 3: COMANDOS DO TELEGRAM
# ==============================================================================

@bot.message_handler(commands=['start', 'id'])
def cmd_start(m):
    if str(m.chat.id) in CHAT_IDS:
        bot.reply_to(m, "Olá, Administrador! O sistema está operante. Use /status para checar a rede.")
    else:
        bot.reply_to(m, f"Olá, {m.from_user.first_name}! Seu ID de autorização é: {m.chat.id}")

@bot.message_handler(commands=['json'])
def cmd_json(m):
    if str(m.chat.id) not in CHAT_IDS: return
    bot.reply_to(m, "🔎 Lendo arquivos JSON em todos os servidores...")
    threading.Thread(target=rotina_diaria_bancos).start()

@bot.message_handler(commands=['status'])
def cmd_status(m):
    if str(m.chat.id) not in CHAT_IDS: return
    bot.reply_to(m, "🔎 Verificando servidores...")
    resumos = [verificar_disco(s) or f"✅ {s['apelido']}: OK" for s in config_global.get('servidores', [])]
    bot.send_message(m.chat.id, "\n".join(resumos), parse_mode="Markdown")

@bot.message_handler(commands=['analisar'])
def cmd_analisar(m):
    if str(m.chat.id) not in CHAT_IDS: return
    msg = bot.reply_to(m, "Selecione o servidor para análise segura:", reply_markup=gerar_teclado_servidores())
    bot.register_next_step_handler(msg, processar_analise_ia)

def processar_analise_ia(message):
    apelido = message.text
    servidor = next((s for s in config_global.get('servidores', []) if s['apelido'] == apelido), None)
    if not servidor: return

    bot.send_message(message.chat.id, f"🧠 Analisando `{apelido}` com blindagem de segurança...", parse_mode="Markdown", reply_markup=types.ReplyKeyboardRemove())
    
    ssh = conectar_ssh(servidor)
    if not ssh:
        bot.send_message(message.chat.id, "❌ Falha ao conectar ao servidor.")
        return

    try:
        # SEGURANÇA: Blindagem de Bancos na Origem
        pastas_ignorar = servidor.get('ignorar_pastas', [])
        pastas_ignorar.append("*/bkp*")
        
        excluir_bancos = [
            'SCPI*.fdb', 'SIP.fdb', 'SIS.fdb', 'SAS.fdb', 'SIE.fdb', 
            'SSA.fdb', 'FILESERVER.fdb', 'SIA*.fdb', 'SYSTEM.DAT'
        ]
        
        str_exclude = " ".join([f"--exclude={p}" for p in pastas_ignorar])
        str_exclude += " " + " ".join([f"--exclude='{b}'" for b in excluir_bancos])
        
        cmd = f"du -ah {str_exclude} {servidor['particao']} 2>/dev/null | sort -rh | head -n 10"
        _, stdout, _ = ssh.exec_command(cmd)
        saida_du = stdout.read().decode('utf-8').strip()

        if not saida_du:
            bot.send_message(message.chat.id, "✅ Nada crítico para limpar encontrado.")
            return

        # SEGURANÇA: Instruções de Segurança no Prompt
        prompt = f"""
        [REGRA CRÍTICA: SEGURANÇA OPERACIONAL]
        Analise os arquivos pesados abaixo. 
        NUNCA sugira apagar bancos de dados (.fdb) ou arquivos de sistema (.DAT).
        FOQUE APENAS em logs ou arquivos temporários.

        ARQUIVOS:
        {saida_du}
        
        Responda:
        1. O maior ofensor de espaço (1 frase curta).
        2. Um único comando `rm -f` para limpeza SEGURA.
        """
        
        resposta = gerar_conteudo_ia_com_retry(prompt)
        msg_ia = f"🤖 *Análise Gemini:*\n\n{resposta.text}" if resposta else "❌ IA indisponível."
        bot.send_message(message.chat.id, msg_ia, parse_mode="Markdown")
        
    finally:
        ssh.close()

@bot.message_handler(commands=['bancos'])
def cmd_bancos(m):
    if str(m.chat.id) not in CHAT_IDS: return
    msg = bot.reply_to(m, "Selecione o servidor para buscar backups esquecidos:", reply_markup=gerar_teclado_servidores())
    bot.register_next_step_handler(msg, processar_bancos_ia)

def processar_bancos_ia(message):
    apelido = message.text
    servidor = next((s for s in config_global.get('servidores', []) if s['apelido'] == apelido), None)
    if not servidor: return

    bot.send_message(message.chat.id, f"🔎 Buscando .fbk e .fdb em `{apelido}`...", parse_mode="Markdown", reply_markup=types.ReplyKeyboardRemove())
    
    ssh = conectar_ssh(servidor)
    if not ssh: return

    try:
        cmd = f'find {servidor["particao"]} -type f \( -iname "*.fbk" -o -iname "*copia*.fdb" \) -exec du -h {{}} + 2>/dev/null | sort -rh | head -n 15'
        _, stdout, _ = ssh.exec_command(cmd)
        saida = stdout.read().decode('utf-8').strip()

        if not saida:
            bot.send_message(message.chat.id, "✅ Nenhum backup perdido encontrado.")
            return

        prompt = f"Gere comandos rm -f para estes arquivos de banco encontrados em {apelido}: {saida}"
        resposta = gerar_conteudo_ia_com_retry(prompt)
        msg_ia = f"🤖 *Sugestão de Limpeza:*\n\n{resposta.text}" if resposta else "❌ IA indisponível."
        bot.send_message(message.chat.id, msg_ia, parse_mode="Markdown")
    finally:
        ssh.close()

@bot.message_handler(commands=['logs'])
def cmd_logs(m):
    if str(m.chat.id) not in CHAT_IDS: return
    msg = bot.reply_to(m, "Selecione o servidor para ler os logs:", reply_markup=gerar_teclado_servidores())
    bot.register_next_step_handler(msg, processar_logs_ia)

def processar_logs_ia(message):
    """Versão Otimizada com Batching para evitar limites de API."""
    apelido = message.text
    servidor = next((s for s in config_global.get('servidores', []) if s['apelido'] == apelido), None)
    if not servidor: return

    bot.send_message(message.chat.id, f"🔎 Analisando logs em lote para `{apelido}`...", parse_mode="Markdown")
    
    ssh = conectar_ssh(servidor)
    if not ssh: return

    try:
        cmd_find = "find /data -type f -name 'manutencao_banco.txt' -mtime -15 2>/dev/null"
        _, stdout, _ = ssh.exec_command(cmd_find)
        arquivos_log = stdout.read().decode('utf-8').strip().split('\n')
        
        logs_consolidados = ""
        for arquivo in filter(None, arquivos_log):
            _, stdout_tail, _ = ssh.exec_command(f"tail -n 20 {arquivo}")
            conteudo = stdout_tail.read().decode('utf-8').strip()
            logs_consolidados += f"\n--- ARQUIVO: {arquivo} ---\n{conteudo}\n"

        if not logs_consolidados:
            bot.send_message(message.chat.id, "✅ Nenhum log recente encontrado.")
            return

        prompt = f"Analise estes logs de banco: {logs_consolidados}\nResuma em uma linha por arquivo se houve Sucesso ou Erro."
        resposta = gerar_conteudo_ia_com_retry(prompt)
        msg_ia = f"🤖 *Relatório Consolidado:*\n\n{resposta.text}" if resposta else "❌ IA indisponível."
        bot.send_message(message.chat.id, msg_ia, parse_mode="Markdown")
    finally:
        ssh.close()

@bot.message_handler(commands=['varredura'])
def cmd_varredura(m):
    if str(m.chat.id) not in CHAT_IDS: return
    bot.reply_to(m, "🚀 Disparando varredura simultânea...")
    
    def orquestrador():
        threads, resultados = [], []
        for s in config_global.get('servidores', []):
            t = threading.Thread(target=buscar_bancos_perdidos_background, args=(s, resultados))
            threads.append(t); t.start()
        for t in threads: t.join()
        
        if resultados:
            bot.send_message(m.chat.id, "📊 *Resultado:*\n\n" + "\n\n".join(resultados), parse_mode="Markdown")
        else:
            bot.send_message(m.chat.id, "✅ Nada encontrado.", parse_mode="Markdown")

    threading.Thread(target=orquestrador).start()

@bot.message_handler(commands=['auditoria'])
def cmd_auditoria(m):
    if str(m.chat.id) not in CHAT_IDS: return
    
    # Armazenamos o ID exato de quem enviou o comando
    chat_solicitante = m.chat.id
    
    bot.reply_to(
        m, 
        "🕵️‍♂️ <b>Iniciando varredura contra possiveis vazamento de dados...</b>\n"
        "Conectando simultaneamente nas bases. Os relatórios individuais chegarão assim que cada servidor responder.",
        parse_mode='HTML'
    )
    
    def orquestrador_seguranca():
        threads = []
        resultados_suspeitos = []
        checklist_bancos = []
        
        for s in config_global.get('servidores', []):
            if s.get('ignorar_auditoria') is True: continue
            t = threading.Thread(
                target=auditoria_vazamento_background, 
                # PASSAMOS APENAS O CHAT DE QUEM PEDIU AQUI
                args=(s, resultados_suspeitos, checklist_bancos, chat_solicitante)
            )
            threads.append(t)
            t.start()
            
        for t in threads:
            t.join()
            
        resumo_cobertura = "\n".join(checklist_bancos)
        
        msg_final = "🏁 <b>Varredura Concluída!</b>\n\n"
        if resultados_suspeitos:
            msg_final += f"⚠️ Alertas críticos detalhados individualmente acima.\n\n📊 <b>Status da Infraestrutura:</b>\n{resumo_cobertura}"
        else:
            msg_final += f"✅ Nenhum acesso fora do horário comercial encontrado em nenhuma das bases.\n\n📊 <b>Status da Infraestrutura:</b>\n{resumo_cobertura}"
            
        # Responde apenas para quem pediu
        bot.send_message(chat_solicitante, msg_final, parse_mode='HTML')

    threading.Thread(target=orquestrador_seguranca).start()
# ==============================================================================
# CAPÍTULO 4: TAREFAS DE SEGUNDO PLANO
# ==============================================================================


def formatar_linha_auditoria(usuario, data_str, hora_str):
    """Auxiliar Senior: Limpa e padroniza as strings para caberem na tabela."""
    user_clean = usuario.replace("@", "")[:12]
    hora_clean = hora_str.split(".")[0] # Remove os milissegundos (.0000)
    
    # Tenta encurtar a data de 2026-06-11 para 11/06 para economizar espaço horizontal
    try:
        if "-" in data_str:
            dt = datetime.strptime(data_str, "%Y-%m-%d")
            data_clean = dt.strftime("%d/%m")
        else:
            data_clean = data_str[:5]
    except:
        data_clean = data_str[:5]

    return user_clean, data_clean, hora_clean

def auditoria_vazamento_background(servidor, resultados_suspeitos, checklist_bancos, chat_id):
    """
    Versão Forense Cirúrgica Corrigida: Sem agrupamentos, com segundos 100% reais e disparo imediato por servidor.
    """
    apelido = servidor['apelido']
    
    import unicodedata
    apelido_sem_acentos = "".join(c for c in unicodedata.normalize('NFD', apelido) if unicodedata.category(c) != 'Mn')
    id_limpo = "".join([c for c in apelido_sem_acentos if c.isalnum()]).lower()
    data_corte = (datetime.now() - timedelta(days=2)).strftime('%d.%m.%Y')
    
    try:
        logging.info(f"[AUDITORIA] Conectando para varredura: {apelido}")
        
        ssh = conectar_ssh(servidor)
        if not ssh:
            checklist_bancos.append(f"❌ {apelido}: Falha de Conexão SSH")
            return
            
        caminho_banco = servidor.get('caminho_banco_fdb') 
        if not caminho_banco:
            checklist_bancos.append(f"⚠️ {apelido}: Ignorado (Caminho FDB não configurado)")
            ssh.close()
            return

        NOME_CONTAINER_PADRAO = "manutencao_fb5"

        cmd_exec = (
            f"DIR_BANCO=$(dirname {caminho_banco}); "
            f"CP_BANCO=\"$DIR_BANCO/audito_{id_limpo}.fdb\"; "
            f"cp {caminho_banco} \"$CP_BANCO\" && "
            f"chmod 777 \"$CP_BANCO\" && "
            f"if docker exec {NOME_CONTAINER_PADRAO} [ -x /usr/local/firebird/bin/isql ]; then ISQL_INTERNAL='/usr/local/firebird/bin/isql'; "
            f"elif docker exec {NOME_CONTAINER_PADRAO} [ -x /opt/firebird/bin/isql ]; then ISQL_INTERNAL='/opt/firebird/bin/isql'; "
            f"else ISQL_INTERNAL=$(docker exec {NOME_CONTAINER_PADRAO} which isql 2>/dev/null || echo 'isql'); fi; "
            f'echo "SET HEADING OFF; '
            f"SELECT 'USUARIO:' || CAST(s.LOGIN AS VARCHAR(50)) || ' | DATA:' || CAST(ld.DATA AS VARCHAR(30)) || ' | HORA:' || CAST(ld.HORA AS VARCHAR(30)) "
            f"FROM LOG_DB ld "
            f"INNER JOIN SENHA s ON s.CPF = ld.CPF AND s.LOGIN LIKE '@%' "
            f"WHERE ld.acao = 'ENTROU' "
            f"  AND ld.DATA >= '{data_corte}' "
            f"  AND (ld.HORA >= '17:45:00' OR ld.HORA <= '08:00:00');\" | "
            f"docker exec -i {NOME_CONTAINER_PADRAO} $ISQL_INTERNAL -user FSCSCPI8 -password scpi \"$CP_BANCO\" 2>&1; "
            f"rm -f \"$CP_BANCO\""
        )
                   
        _, stdout, _ = ssh.exec_command(cmd_exec)
        saida = stdout.read().decode('utf-8', errors='ignore').strip()
        ssh.close()
        
        logging.info(f"[AUDITORIA RAW OUTPUT {apelido}]:\n{saida}")
        
        linhas_validas = [ln.strip() for ln in saida.split('\n') if 'USUARIO:' in ln]
        checklist_bancos.append(f"🔹 {apelido}: Auditado com Sucesso")
        
        # CORRIGIDO: Variável renomeada corretamente para 'linhas_validas' evitando NameError
        if linhas_validas:
            # Constrói o grid monoespaçado exclusivo para este servidor
            tabela = f"🚨 <b>CONTA para analisar em {apelido.upper()}</b>\n"
            tabela += f"<pre>{'USUÁRIO':<15} | {'DATA':<10} | {'HORA':<8}\n"
            tabela += f"{'-'*15}-+-{'-'*10}-+-{'-'*8}\n"
            
            for linha in linhas_validas:
                try:
                    partes = linha.split(" | ")
                    user = partes[0].split(":")[1].strip().replace("@", "")[:15]
                    data_log = partes[1].split(":")[1].strip()
                    
                    # MELHORIA SÊNIOR: Remove a palavra 'HORA:' e corta os milissegundos (.0000)
                    # Mantendo o formato exato com os segundos intactos: 07:47:39
                    hora_bruta = partes[2].replace("HORA:", "").strip()
                    hora_log = hora_bruta.split(".")[0]
                    
                    tabela += f"{user:<15} | {data_log:<10} | {hora_log:<8}\n"
                except Exception as ex:
                    # Fallback de segurança forense para não perder a linha crua caso falhe
                    tabela += f"{linha[:38]}\n"
                    
            tabela += "</pre>"
            
            # Dispara imediatamente a mensagem isolada deste servidor para o Telegram!
            bot.send_message(chat_id, tabela, parse_mode='HTML')
            resultados_suspeitos.append(tabela)
            
    except Exception as e:
        logging.error(f"Erro crítico na auditoria de {apelido}: {e}")
        checklist_bancos.append(f"❌ {apelido}: Erro de Execução ({str(e)[:30]})")

def buscar_bancos_perdidos_background(servidor, resultados):
    ssh = conectar_ssh(servidor)
    if not ssh: return
    try:
        cmd = f'find {servidor["particao"]} -path "*/bkp" -prune -o -type f \( -iname "*.fbk" -o -iname "*copia*.fdb" \) -exec du -h {{}} + 2>/dev/null | sort -rh | head -n 15'
        _, stdout, _ = ssh.exec_command(cmd)
        saida = stdout.read().decode('utf-8').strip()
        if saida: resultados.append(f"⚠️ *{servidor['apelido']}*:\n```\n{saida}\n```")
    finally:
        ssh.close()

def verificar_json_bancos(servidor):
    if servidor.get('so', 'linux').lower() == 'windows': return []
    ssh = conectar_ssh(servidor)
    if not ssh: return []
    try:
        cmd = "find /data -type f -name 'status_manutencao.json' -mtime -2 -exec cat {} \; -exec echo '---FIM_JSON---' \; 2>/dev/null"
        _, stdout, _ = ssh.exec_command(cmd)
        saida = stdout.read().decode('utf-8', errors='ignore').strip()
        if not saida: return []
        
        alertas = []
        for bloco in saida.split('---FIM_JSON---'):
            if not bloco.strip(): continue
            try:
                dados = json.loads(bloco.strip())
                if dados.get('status', '').upper() in ['ERRO', 'ERRO_CRITICO']:
                    data_att = dados.get('data_atualizacao', '')
                    if data_att:
                        data_json = datetime.strptime(data_att, "%Y-%m-%d %H:%M:%S")
                        if datetime.now() - data_json > timedelta(hours=24): continue
                    alertas.append(f"🚨 *Status:* `{dados.get('status')}`\n🖥️ *Servidor:* `{servidor['apelido']}`\n🗄️ *Banco:* `{dados.get('banco')}`\n💬 *Erro:* `{dados.get('mensagem')}`")
            except: continue
        return alertas
    finally:
        ssh.close()

def rotina_diaria_bancos():
    alertas = []
    for s in config_global.get('servidores', []):
        alertas.extend(verificar_json_bancos(s))
    msg = "⚠️ *RELATÓRIO DE ERROS*\n\n" + "\n\n".join(alertas) if alertas else "✅ *Sem erros de reindexações.*"
    enviar_alerta_geral(msg, parse_mode="Markdown")

# ==============================================================================
# CAPÍTULO 5: AGENDADOR
# ==============================================================================

def job_resumo_matinal():
    alertas, oks = [], []
    for s in config_global.get('servidores', []):
        res = verificar_disco(s)
        alertas.append(res) if res else oks.append(f"✅ {s['apelido']}")
    msg = f"🌅 *Resumo - {datetime.now().strftime('%d/%m')}*\n\n"
    if alertas: msg += "*Atenção:* " + "\n".join(alertas) + "\n\n"
    msg += "*Servidores OK:* " + " | ".join(oks)
    enviar_alerta_geral(msg, parse_mode="Markdown")
    enviar_email("Resumo ", msg.replace('*', ''))

def job_checagem_hourly():
    if 20 <= datetime.now().hour < 5: return
    alertas = [verificar_disco(s) for s in config_global.get('servidores', []) if verificar_disco(s)]
    if alertas: enviar_alerta_geral("⚠️ *Alerta:* \n" + "\n".join(alertas), parse_mode="Markdown")

def rotina_automatizada_auditoria():
    """
    Rotina Automatizada: Executa a varredura e envia os resultados para TODOS os administradores.
    """
    logging.info("[AGENDADOR] Iniciando varredura automatizada de auditoria para toda a equipe...")
    
    # Envia o aviso de início para todos os administradores cadastrados
    for id_adm in CHAT_IDS:
        try:
            bot.send_message(
                int(id_adm), 
                "⏰ <b>[AGENDADOR] Iniciando varredura ...</b>\nBases sendo auditadas em background.",
                parse_mode='HTML'
            )
        except Exception as e:
            logging.error(f"Falha ao avisar administrador {id_adm}: {e}")

    def orquestrador_seguranca_automatizado():
        threads = []
        resultados_suspeitos = []
        checklist_bancos = []
        
        # Como o envio em tempo real por servidor dentro da thread enviaria apenas para um ID fixo,
        # para o agendador vamos fazer com que as threads guardem os resultados na lista global
        # e no final nós disparamos o consolidado para todos os administradores de uma vez só, 
        # evitando fazer spam de mensagens individuais de cada servidor para todo mundo.
        
        for s in config_global.get('servidores', []):
            if s.get('ignorar_auditoria') is True: continue
            
            # Criamos uma função lambda rápida para envelopar o envio para todos
            t = threading.Thread(
                target=auditoria_vazamento_background_agendador, 
                args=(s, resultados_suspeitos, checklist_bancos)
            )
            threads.append(t)
            t.start()
            
        for t in threads:
            t.join()
            
        resumo_cobertura = "\n".join(checklist_bancos)
        
        # Monta o blocão final do agendador para enviar para a equipe inteira
        if resultados_suspeitos:
            msg_final = "🚨 <b>[AGENDADOR] Alerta Máximo de Acessos Suspeitos!</b>\n\n"
            msg_final += "\n\n".join(resultados_suspeitos)
            msg_final += f"\n\n📊 <b>Status da Infraestrutura:</b>\n{resumo_cobertura}"
        else:
            msg_final = "✅ <b>[AGENDADOR] Relatório de Rotina Concluído</b>\nNenhum acesso suspeito detectado.\n\n"
            msg_final += f"📊 <b>Status da Infraestrutura:</b>\n{resumo_cobertura}"
            
        # Dispara para todos os administradores cadastrados no sistema
        for id_adm in CHAT_IDS:
            try:
                bot.send_message(int(id_adm), msg_final, parse_mode='HTML')
            except Exception as e:
                logging.error(f"Falha ao enviar relatório final para {id_adm}: {e}")

    threading.Thread(target=orquestrador_seguranca_automatizado).start()

def run_scheduler():
    schedule.every().day.at("07:00").do(lambda: threading.Thread(target=rotina_diaria_bancos).start())
    schedule.every().day.at("07:15").do(lambda: threading.Thread(target=job_resumo_matinal).start())
    schedule.every(3).hours.do(lambda: threading.Thread(target=job_checagem_hourly).start())
    schedule.every().day.at("08:30").do(rotina_automatizada_auditoria)
    while True:
        schedule.run_pending()
        time.sleep(10)

if __name__ == "__main__":
    logging.info("=== Monitor Protegido Iniciado ===")
    threading.Thread(target=run_scheduler, daemon=True).start()
    bot.infinity_polling()