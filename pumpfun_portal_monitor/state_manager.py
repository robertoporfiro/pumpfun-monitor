import json
import logging
import asyncio
from typing import Set, List, Dict, Any
from . import config # Usar import relativo

logger = logging.getLogger(__name__)

class StateManager:
    def __init__(self, processed_file: str, pending_file: str):
        self._processed_file = processed_file
        self._pending_file = pending_file
        self._processed_tokens: Set[str] = set()
        self._pending_tokens: List[Dict[str, Any]] = []
        self._save_lock = asyncio.Lock()
        self._final_save_done = False

    def load_state(self):
        """Carrega o estado inicial dos arquivos."""
        try:
            with open(self._processed_file, "r") as f:
                self._processed_tokens = set(json.load(f))
            logger.info(f"Carregados {len(self._processed_tokens)} tokens processados de {self._processed_file}")
        except FileNotFoundError:
            logger.warning(f"Arquivo {self._processed_file} não encontrado. Iniciando com lista vazia.")
            self._processed_tokens = set()
        except json.JSONDecodeError:
            logger.error(f"Erro ao decodificar JSON de {self._processed_file}. Iniciando com lista vazia.")
            self._processed_tokens = set()

        try:
            with open(self._pending_file, "r") as f:
                self._pending_tokens = json.load(f)
            logger.info(f"Carregados {len(self._pending_tokens)} tokens pendentes de {self._pending_file}")
        except FileNotFoundError:
            logger.warning(f"Arquivo {self._pending_file} não encontrado. Iniciando com lista vazia.")
            self._pending_tokens = []
        except json.JSONDecodeError:
            logger.error(f"Erro ao decodificar JSON de {self._pending_file}. Iniciando com lista vazia.")
            self._pending_tokens = []

    async def save_state(self, final_save=False):
        """Salva o estado atual em arquivos JSON, com lock para evitar concorrência."""
        if final_save:
            if self._final_save_done:
                 logger.debug("Salvamento final já realizado.")
                 return
            logger.info("Realizando salvamento final do estado...")
            self._final_save_done = True
        else:
             if self._final_save_done: # Não salva periodicamente após shutdown iniciar
                 return
             logger.debug("Tentando salvamento periódico do estado...")

        async with self._save_lock:
            try:
                with open(self._processed_file, "w") as f:
                    json.dump(list(self._processed_tokens), f, indent=2)
                logger.info(f"Salvos {len(self._processed_tokens)} tokens processados em {self._processed_file}")
            except Exception as e:
                logger.error(f"Erro ao salvar tokens processados: {e}")

            try:
                with open(self._pending_file, "w") as f:
                    json.dump(self._pending_tokens, f, indent=2)
                logger.info(f"Salvos {len(self._pending_tokens)} tokens pendentes em {self._pending_file}")
            except Exception as e:
                logger.error(f"Erro ao salvar tokens pendentes: {e}")

    async def run_periodic_save(self, interval: int):
        """Tarefa assíncrona para salvar o estado periodicamente."""
        while True:
            await asyncio.sleep(interval)
            await self.save_state() # Chama a versão assíncrona

    def is_token_processed(self, mint_address: str) -> bool:
        """Verifica se um token já foi processado."""
        return mint_address in self._processed_tokens

    def add_processed_token(self, mint_address: str):
        """Adiciona um token à lista de processados."""
        self._processed_tokens.add(mint_address)

    def add_pending_token(self, token_data: Dict[str, Any]):
        """Adiciona um token à lista de pendentes."""
        # Evitar duplicados na lista de pendentes
        if not any(p["mint"] == token_data.get("mint") for p in self._pending_tokens):
            self._pending_tokens.append(token_data)
            logger.debug(f"Token {token_data.get('mint')} adicionado aos pendentes.")
        else:
            logger.debug(f"Token {token_data.get('mint')} já estava na lista de pendentes.")