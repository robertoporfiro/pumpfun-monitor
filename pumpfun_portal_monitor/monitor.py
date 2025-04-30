# pumpportal_monitor/monitor.py
import asyncio
import logging
import signal
import time
import sys
import os
import json

import aiohttp

from . import config # Importa o config.py atualizado
from .state_manager import StateManager
from .websocket_client import run_websocket_client

# --- Garante que DATA_DIR exista ANTES de configurar logging ---
os.makedirs(config.DATA_DIR, exist_ok=True)

# --- Configuração de Logging ---
log_level_to_set = getattr(logging, config.LOG_LEVEL_NAME, logging.INFO)
logging.basicConfig(
    level=log_level_to_set,
    format='%(asctime)s [%(levelname)s] [%(name)s] %(message)s',
    handlers=[
        logging.FileHandler(config.LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logging.Formatter.converter = time.gmtime
logger = logging.getLogger("pumpportal_monitor") # Logger principal

# Definir níveis de log para bibliotecas externas
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)

_state_manager_instance: StateManager = None
_shutdown_requested = False

async def main():
    """Função principal assíncrona."""
    global _state_manager_instance, _shutdown_requested

    # Chama a função para logar a configuração inicial
    config.log_initial_config() # Loga as configs APÓS basicConfig

    logger.info(f"Iniciando monitor (Nível de Log Efetivo: {logging.getLevelName(logger.getEffectiveLevel())})...")

    _state_manager_instance = StateManager(config.PROCESSED_TOKENS_FILE, config.PENDING_TOKENS_FILE)
    _state_manager_instance.load_state()

    timeout = aiohttp.ClientTimeout(total=config.API_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        save_task = asyncio.create_task(
            _state_manager_instance.run_periodic_save(config.SAVE_INTERVAL_SECONDS),
            name="PeriodicSaveTask"
        )
        websocket_task = asyncio.create_task(
            run_websocket_client(session, _state_manager_instance),
            name="WebSocketClientTask"
        )

        all_tasks = {websocket_task, save_task}
        done, pending = await asyncio.wait(all_tasks, return_when=asyncio.FIRST_COMPLETED)

        if not _shutdown_requested:
             logger.error("O loop principal terminou inesperadamente. Verificando exceções...")

        for task in done:
            task_name = task.get_name()
            try:
                exc = task.exception()
                if exc: logger.error(f"Tarefa '{task_name}' finalizada com erro:", exc_info=exc)
                else: logger.info(f"Tarefa '{task_name}' finalizada sem erros.")
            except asyncio.CancelledError: logger.info(f"Tarefa '{task_name}' foi cancelada durante o wait.")
            except Exception as e: logger.error(f"Erro ao verificar status da tarefa concluída '{task_name}': {e}", exc_info=True)

        if pending:
            logger.info("Cancelando tarefas pendentes...")
            for task in pending:
                task_name = task.get_name()
                if not task.done():
                    task.cancel()
                    try: await asyncio.wait_for(task, timeout=2.0)
                    except asyncio.CancelledError: logger.debug(f"Tarefa pendente '{task_name}' cancelada.")
                    except asyncio.TimeoutError: logger.warning(f"Timeout ao esperar cancelamento da tarefa '{task_name}'.")
                    except Exception as e: logger.error(f"Erro durante cancelamento da tarefa '{task_name}': {e}", exc_info=True)

# --- shutdown_handler (sem alterações da última versão) ---
def shutdown_handler(signum, frame):
    """Handler de sinal para encerramento gracioso."""
    global _shutdown_requested, _state_manager_instance
    if _shutdown_requested:
        logger.debug("Shutdown já em progresso.")
        return
    _shutdown_requested = True
    logger.warning(f"Sinal {signal.Signals(signum).name} recebido. Iniciando encerramento gracioso...")

    if _state_manager_instance and not _state_manager_instance.is_final_save_done():
        logger.info("Salvando estado final (handler)...")
        try:
            processed_list = _state_manager_instance.get_processed_tokens_copy()
            pending_list = _state_manager_instance.get_pending_tokens_copy()
            processed_file = config.PROCESSED_TOKENS_FILE
            pending_file = config.PENDING_TOKENS_FILE
            with open(processed_file, "w") as f: json.dump(processed_list, f, indent=2)
            with open(pending_file, "w") as f: json.dump(pending_list, f, indent=2)
            logger.info(f"Salvamento final (handler) OK. Processados: {len(processed_list)}, Pendentes: {len(pending_list)}")
            _state_manager_instance.mark_final_save_done()
        except Exception as e: logger.error(f"Erro no salvamento final (handler): {e}", exc_info=True)
    elif _state_manager_instance and _state_manager_instance.is_final_save_done(): logger.debug("Salvamento final (handler) já realizado.")
    else: logger.warning("StateManager indisponível no handler, não foi possível salvar estado.")

    try:
        loop = asyncio.get_running_loop()
        async def stop_loop_and_tasks():
            tasks = [t for t in asyncio.all_tasks(loop=loop) if t is not asyncio.current_task(loop=loop)]
            if tasks:
                logger.info(f"Cancelando {len(tasks)} tarefas pendentes...")
                for task in tasks:
                    if not task.done(): task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                logger.debug("Gather de cancelamento concluído.")
            else: logger.info("Nenhuma tarefa pendente para cancelar.")
            if loop.is_running():
                logger.info("Enviando comando loop.stop().")
                loop.stop()
            else: logger.info("Loop não estava rodando ao tentar parar.")
        loop.call_soon_threadsafe(asyncio.ensure_future, stop_loop_and_tasks())
    except RuntimeError: logger.error("Nenhum loop de eventos ativo no shutdown handler.")


if __name__ == "__main__":
    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError): logger.info("Programa interrompido/cancelado.")
    except SystemExit as e: logger.error(f"Encerrando devido a erro: {e}")
    except Exception as e: logger.critical(f"Erro fatal não capturado no loop principal: {e}", exc_info=True)
    finally: logger.info("Processo de monitoramento formalmente encerrado.")