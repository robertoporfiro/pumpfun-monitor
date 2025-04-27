# pumpfun_portal_monitor/monitor.py

import asyncio
import logging
import signal
import time
import sys
import os
import json # Importar json que estava faltando no shutdown_handler

import aiohttp

from pumpfun_portal_monitor import config
from pumpfun_portal_monitor.state_manager import StateManager
from pumpfun_portal_monitor.websocket_client import run_websocket_client

# --- Configuração de Logging (CORRIGIDA) ---
logging.basicConfig(
    # Usa getattr para buscar o nível pelo NOME (string) armazenado em config
    level=getattr(logging, config.LOG_LEVEL_NAME, logging.INFO), # <<< CORREÇÃO AQUI
    format='%(asctime)s [%(levelname)s] [%(name)s] %(message)s',
    handlers=[
        logging.FileHandler(config.LOG_FILE),
        logging.StreamHandler()
    ]
)
logging.Formatter.converter = time.gmtime
logger = logging.getLogger(__name__)
# Configura níveis de log para libs externas aqui, se desejado
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)

# Variável global para o StateManager, acessível pelo signal handler
_state_manager_instance: StateManager = None

async def main():
    """Função principal assíncrona."""
    global _state_manager_instance

    logger.info("Iniciando monitoramento assíncrono modularizado...")

    # Cria e inicializa o StateManager
    _state_manager_instance = StateManager(config.PROCESSED_TOKENS_FILE, config.PENDING_TOKENS_FILE)
    _state_manager_instance.load_state()

    async with aiohttp.ClientSession() as session:
        # Inicia tarefas concorrentes
        save_task = asyncio.create_task(
            _state_manager_instance.run_periodic_save(config.SAVE_INTERVAL_SECONDS),
            name="PeriodicSaveTask"
        )
        websocket_task = asyncio.create_task(
            run_websocket_client(session, _state_manager_instance),
            name="WebSocketClientTask"
        )

        # Espera a primeira tarefa terminar (geralmente websocket_task se houver erro fatal)
        done, pending = await asyncio.wait(
            [websocket_task, asyncio.shield(save_task)], # Protege save_task de cancelamento inicial
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in done:
            try:
                # Verifica se a task levantou uma exceção
                if task.exception():
                    logger.error(f"Tarefa {task.get_name()} finalizada com erro: {task.exception()}", exc_info=task.exception())
                else:
                    logger.info(f"Tarefa {task.get_name()} finalizada normalmente.")
            except asyncio.CancelledError:
                 logger.warning(f"Tarefa {task.get_name()} foi cancelada.")
            except Exception as e: # Captura genérica para segurança
                 logger.error(f"Erro ao processar tarefa concluída {task.get_name()}: {e}", exc_info=True)


        logger.warning("Uma tarefa principal foi concluída ou falhou. Encerrando as outras...")
        for task in pending:
            if not task.done():
                task.cancel()
                try:
                    # Dá uma chance para a task processar o cancelamento
                    await asyncio.wait_for(task, timeout=1.0)
                except asyncio.CancelledError:
                    logger.debug(f"Tarefa pendente {task.get_name()} cancelada com sucesso.")
                except asyncio.TimeoutError:
                    logger.warning(f"Timeout ao esperar cancelamento da tarefa pendente {task.get_name()}.")
                except Exception as e:
                     logger.error(f"Erro durante cancelamento da tarefa pendente {task.get_name()}: {e}", exc_info=True)


def shutdown_handler(signum, frame):
    """Handler de sinal para encerramento gracioso."""
    logger.warning(f"Recebido sinal de interrupção ({signal.Signals(signum).name}). Encerrando...")
    global _state_manager_instance

    # Salva o estado final de forma síncrona
    if _state_manager_instance and not _state_manager_instance._final_save_done: # Verifica flag
        logger.info("Tentando salvamento final do estado (handler)...")
        try:
            # Acessa os dados diretamente para evitar async em handler
            processed_list = list(_state_manager_instance._processed_tokens)
            pending_list = list(_state_manager_instance._pending_tokens) # Copia para evitar race condition?

            with open(config.PROCESSED_TOKENS_FILE, "w") as f:
                json.dump(processed_list, f, indent=2)
            with open(config.PENDING_TOKENS_FILE, "w") as f:
                json.dump(pending_list, f, indent=2)

            logger.info(f"Salvamento final (handler) concluído. Processados: {len(processed_list)}, Pendentes: {len(pending_list)}")
            _state_manager_instance._final_save_done = True # Marca como salvo
        except Exception as e:
            logger.error(f"Erro no salvamento final (handler): {e}", exc_info=True)
    elif _state_manager_instance and _state_manager_instance._final_save_done:
        logger.debug("Salvamento final já realizado anteriormente.")
    else:
        logger.warning("StateManager não inicializado, não foi possível salvar o estado.")


    # Pede para parar o loop do asyncio
    try:
        loop = asyncio.get_running_loop()
        # Cancela todas as outras tarefas
        tasks = [t for t in asyncio.all_tasks(loop=loop) if t is not asyncio.current_task(loop=loop)]
        if tasks:
            logger.info(f"Cancelando {len(tasks)} tarefas pendentes (handler)...")
            for task in tasks:
                if not task.done():
                    task.cancel()
        # Para o loop
        # Usar call_soon_threadsafe é geralmente mais seguro de signal handlers
        loop.call_soon_threadsafe(loop.stop)
        logger.info("Comando loop.stop() enviado.")
    except RuntimeError:
        logger.error("Nenhum loop de eventos rodando no shutdown handler.")
        # Se não há loop rodando, podemos tentar sair diretamente
        # Mas geralmente o finally de asyncio.run cuidará disso
        # sys.exit(1)

if __name__ == "__main__":
    # Configura handlers de sinal
    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    # Executa o loop principal do asyncio
    try:
        # asyncio.run gerencia o loop, incluindo a limpeza
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        # Esses são esperados durante o shutdown normal
        logger.info("Execução interrompida ou cancelada.")
    finally:
        logger.info("Monitoramento encerrado.")
        # O salvamento final DEVE ter ocorrido no handler.
        # asyncio.run() tenta limpar as tarefas restantes.