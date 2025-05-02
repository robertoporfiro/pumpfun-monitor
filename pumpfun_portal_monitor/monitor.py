# pumpportal_monitor/monitor.py
import asyncio
import logging
import signal
import time
import sys
import os
import json
import atexit

import aiohttp

# Usa imports relativos corretos
from . import config
from .state_manager import StateManager # Importa a classe com __init__ de 3 args
from .websocket_client import run_websocket_client

# --- Garante que DATA_DIR exista ---
try: os.makedirs(config.DATA_DIR, exist_ok=True)
except OSError as e: print(f"FATAL: Criar {config.DATA_DIR} falhou: {e}", file=sys.stderr); sys.exit(1)

# --- Configuração de Logging ---
log_level_to_set = getattr(logging, config.LOG_LEVEL_NAME, logging.INFO)
for handler in logging.root.handlers[:]: logging.root.removeHandler(handler) # Limpa handlers
logging.basicConfig(
    level=log_level_to_set, format='%(asctime)s [%(levelname)s] [%(name)s] %(message)s',
    handlers=[ logging.FileHandler(config.LOG_FILE), logging.StreamHandler(sys.stdout) ]
    # Sem converter gmtime
)
logger = logging.getLogger("pumpportal_monitor")
logging.getLogger("websockets").setLevel(logging.WARNING); logging.getLogger("aiohttp").setLevel(logging.WARNING)

_state_manager_instance: StateManager = None
_shutdown_requested = False
_pid_file_path = None # Definido em config.PID_FILE

def remove_pid_file():
    """Tenta remover o ficheiro PID."""
    global _pid_file_path
    # Garante que _pid_file_path foi definido antes de usar
    if _pid_file_path and os.path.exists(_pid_file_path):
        try:
            logger.info(f"Removendo ficheiro PID: {_pid_file_path}")
            os.remove(_pid_file_path)
            _pid_file_path = None # Reset para evitar tentativas repetidas
        except OSError as e:
            logger.error(f"Erro ao remover ficheiro PID {_pid_file_path}: {e}")

async def main():
    global _state_manager_instance, _shutdown_requested, _pid_file_path
    _pid_file_path = config.PID_FILE # Define o path do PID file

    # --- Escrever PID File ---
    try:
        pid = os.getpid(); os.makedirs(os.path.dirname(_pid_file_path), exist_ok=True)
        with open(_pid_file_path, "w") as f: f.write(str(pid))
        logger.info(f"PID File: {_pid_file_path} (PID: {pid})")
        atexit.register(remove_pid_file) # Registra para remoção na saída
    except Exception as e: logger.error(f"Não criou PID file {_pid_file_path}: {e}")

    try: config.log_initial_config()
    except Exception as e: logger.error(f"Erro log config: {e}")
    logger.info(f"Iniciando monitor (Log: {config.LOG_LEVEL_NAME}, TZ: {os.getenv('TZ', 'Default')})...")

    # --- CORRIGIDO: Instanciar StateManager com 3 argumentos ---
    _state_manager_instance = StateManager(
        config.PROCESSED_TOKENS_FILE,
        config.PENDING_TOKENS_FILE,
        config.CREATOR_BLACKLIST_FILE # Apenas 3 argumentos
    )
    # --- FIM DA CORREÇÃO ---
    _state_manager_instance.load_state() # Carrega estado

    timeout = aiohttp.ClientTimeout(total=config.API_TIMEOUT)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            save_task = asyncio.create_task(_state_manager_instance.run_periodic_save(config.SAVE_INTERVAL_SECONDS), name="SaveTask")
            websocket_task = asyncio.create_task(run_websocket_client(session, _state_manager_instance), name="WebSocketTask")
            done, pending = await asyncio.wait({websocket_task, save_task}, return_when=asyncio.FIRST_COMPLETED)
            if not _shutdown_requested: logger.error("Loop principal terminou inesperadamente.")
            # ... (processamento de tasks done/pending como antes) ...
            for task in done:
                 try: exc=task.exception(); logger.error(f"Task '{task.get_name()}' erro:", exc_info=exc) if exc else logger.info(f"Task '{task.get_name()}' OK.")
                 except asyncio.CancelledError: logger.info(f"Task '{task.get_name()}' cancelada wait.")
                 except Exception as e: logger.error(f"Erro check task '{task.get_name()}': {e}", exc_info=True)
            if pending:
                 logger.info(f"Cancelando {len(pending)} tarefas..."); [t.cancel() for t in pending if not t.done()]
                 try: await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=2.0)
                 except asyncio.TimeoutError: logger.warning("Timeout cancelamento pendentes.")

    except aiohttp.ClientError as e: logger.critical(f"Erro ClientSession: {e}")
    except Exception as e: logger.critical(f"Erro não tratado main: {e}", exc_info=True)

# --- shutdown_handler (sem alterações) ---
def shutdown_handler(signum, frame):
    # (Código como na resposta anterior, incluindo remove_pid_file())
    global _shutdown_requested, _state_manager_instance
    if _shutdown_requested: logger.debug("Shutdown já progresso."); return
    _shutdown_requested = True; logger.warning(f"Sinal {signal.Signals(signum).name}. Encerrando...")
    if _state_manager_instance and not _state_manager_instance.is_final_save_done():
        logger.info("Salvando estado final (handler)...")
        try:
            processed=list(_state_manager_instance._processed_tokens); pending=list(_state_manager_instance._pending_tokens)
            with open(config.PROCESSED_TOKENS_FILE,"w") as f: json.dump(processed,f,indent=2)
            with open(config.PENDING_TOKENS_FILE,"w") as f: json.dump(pending,f,indent=2)
            logger.info(f"Salvamento final OK. Proc:{len(processed)}, Pend:{len(pending)}")
            _state_manager_instance.mark_final_save_done()
        except Exception as e: logger.error(f"Erro salvamento final: {e}", exc_info=True)
    remove_pid_file() # Tenta remover PID
    try: # Tenta parar o loop
        loop = asyncio.get_running_loop()
        async def stop_tasks():
            tasks = [t for t in asyncio.all_tasks(loop=loop) if t is not asyncio.current_task(loop=loop)]; logger.info(f"Cancelando {len(tasks)} tarefas...")
            [t.cancel() for t in tasks if not t.done()]; await asyncio.gather(*tasks, return_exceptions=True); logger.debug("Gather cancel OK.")
            if loop.is_running(): logger.info("Enviando loop.stop()."); loop.stop()
        asyncio.ensure_future(stop_tasks(), loop=loop)
    except RuntimeError: logger.error("Nenhum loop ativo no shutdown handler.")

if __name__ == "__main__":
    signal.signal(signal.SIGINT, shutdown_handler); signal.signal(signal.SIGTERM, shutdown_handler)
    try: asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError): logger.info("Programa interrompido/cancelado.")
    except SystemExit as e: logger.error(f"Encerrando: {e}") # Captura exit do config
    except Exception as e: logger.critical(f"Erro fatal não capturado: {e}", exc_info=True)
    finally: remove_pid_file(); logger.info("Processo monitoramento formalmente encerrado.")