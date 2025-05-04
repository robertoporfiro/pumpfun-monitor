# bot_manager_web/src/routes/api.py
import os
import json
import logging
from flask import Blueprint, jsonify, current_app # Import current_app para acessar o logger global

# Usa a variável de ambiente definida no docker-compose para saber onde procurar os arquivos
DATA_DIR = os.getenv("BOT_DATA_DIR", "/data") # Default para /data
PID_FILE = os.path.join(DATA_DIR, "bot.pid")
MONITORED_TOKENS_FILE = os.path.join(DATA_DIR, "monitored_tokens.json")

api_bp = Blueprint("api", __name__, url_prefix="/api")

# Obtém o logger configurado na app principal (main.py)
logger = logging.getLogger(__name__) # Ou use current_app.logger dentro das rotas

def is_process_running(pid: int) -> bool:
    """Verifica se um processo com o PID fornecido está a correr (método Unix/Linux)."""
    if pid <= 0:
        return False
    try:
        # Enviar sinal 0 não afeta o processo, apenas verifica se existe
        os.kill(pid, 0)
    except OSError as err:
        # ESRCH = No such process
        # logger.debug(f"Erro ao verificar PID {pid} (Provavelmente não existe): {err}")
        return False
    except Exception as e:
        # Outros erros (ex: permissão)
        logger.error(f"Erro inesperado ao verificar PID {pid}: {e}")
        return False
    else:
        # Processo existe
        # logger.debug(f"Processo com PID {pid} encontrado.")
        return True

@api_bp.route("/status", methods=["GET"])
def get_status():
    """Endpoint para obter o estado do bot e os tokens monitorados."""
    # Adiciona log de entrada para depuração
    logger.info(">>> Recebida requisição para /api/status")

    bot_pid = None
    is_running = False
    monitored_tokens = []
    error_message = None
    pid_file_exists = False
    monitored_file_exists = False

    # 1. Verificar PID e estado do processo
    try:
        pid_file_exists = os.path.exists(PID_FILE)
        if pid_file_exists:
            logger.debug(f"Encontrado PID file: {PID_FILE}")
            with open(PID_FILE, "r") as f:
                pid_str = f.read().strip()
                logger.debug(f"Conteúdo do PID file: '{pid_str}'")
                if pid_str.isdigit():
                    bot_pid = int(pid_str)
                    is_running = is_process_running(bot_pid)
                    if not is_running:
                         logger.warning(f"PID {bot_pid} (de {PID_FILE}) não corresponde a processo ativo.")
                    else:
                         logger.debug(f"Processo com PID {bot_pid} está ativo.")
                else:
                    logger.error(f"Conteúdo inválido no PID file {PID_FILE}: '{pid_str}'")
                    error_message = f"PID file ({os.path.basename(PID_FILE)}) inválido."
        else:
            logger.debug(f"PID file {PID_FILE} não encontrado.")
    except Exception as e:
        logger.error(f"Erro ao ler/verificar PID file {PID_FILE}: {e}", exc_info=True)
        error_message = f"Erro acesso PID: {e}"
        is_running = False # Assume parado em caso de erro

    # 2. Ler tokens monitorados
    try:
        monitored_file_exists = os.path.exists(MONITORED_TOKENS_FILE)
        if monitored_file_exists:
            logger.debug(f"Encontrado arquivo de tokens monitorados: {MONITORED_TOKENS_FILE}")
            with open(MONITORED_TOKENS_FILE, "r") as f:
                try:
                    content = f.read().strip()
                    if content:
                         loaded_data = json.loads(content)
                         if isinstance(loaded_data, list):
                             monitored_tokens = loaded_data
                             logger.debug(f"Carregados {len(monitored_tokens)} tokens monitorados.")
                         else:
                              logger.error(f"{MONITORED_TOKENS_FILE} não contém lista JSON válida.")
                              e_msg = f"Formato inválido monitored_tokens.json"
                              error_message = f"{error_message} {e_msg}" if error_message else e_msg
                              monitored_tokens = []
                    else:
                        logger.debug(f"{MONITORED_TOKENS_FILE} está vazio.")
                        monitored_tokens = [] # Ficheiro vazio
                except json.JSONDecodeError as json_err:
                    logger.error(f"Erro JSON {MONITORED_TOKENS_FILE}: {json_err}")
                    e_msg = f"Erro leitura monitored_tokens.json"
                    error_message = f"{error_message} {e_msg}" if error_message else e_msg
                    monitored_tokens = []
        else:
             logger.debug(f"{MONITORED_TOKENS_FILE} não encontrado.")
    except Exception as e:
        logger.error(f"Erro ler {MONITORED_TOKENS_FILE}: {e}", exc_info=True)
        e_msg = f"Erro acesso monitored_tokens.json"
        error_message = f"{error_message} {e_msg}" if error_message else e_msg
        monitored_tokens = []

    response_data = {
        "is_running": is_running,
        "monitored_tokens": sorted(monitored_tokens), # Ordena para consistência na UI
        "pid": bot_pid if is_running else None,
        "error": error_message,
        # Adicionando debug info pode ajudar a diagnosticar problemas de montagem de volume
        "debug_info": {
             "pid_file_path": PID_FILE,
             "monitored_file_path": MONITORED_TOKENS_FILE,
             "pid_file_exists": pid_file_exists,
             "monitored_file_exists": monitored_file_exists
        }
    }

    # Adiciona log de saída (nível DEBUG)
    logger.debug(f"<<< Respondendo a /api/status com: {response_data}")
    return jsonify(response_data)