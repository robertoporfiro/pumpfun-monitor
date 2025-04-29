import json
import logging
import asyncio
from typing import Set, List, Dict, Any
from . import config

logger = logging.getLogger(__name__)

class StateManager:
    def __init__(self, processed_file: str, pending_file: str):
        self._processed_file = processed_file
        self._pending_file = pending_file
        self._processed_tokens: Set[str] = set()
        self._pending_tokens: List[Dict[str, Any]] = []
        self._save_lock = asyncio.Lock()
        self._final_save_done = False
        # Adicionar aqui conjuntos para bought ou monitored se quiser persistir
        # self._bought_tokens: Set[str] = set()

    def load_state(self):
        """Carrega o estado inicial dos arquivos."""
        try:
            with open(self._processed_file, "r") as f:
                self._processed_tokens = set(json.load(f))
            logger.info(f"Carregados {len(self._processed_tokens)} tokens processados de {self._processed_file}")
        except FileNotFoundError:
            logger.warning(f"Arquivo {self._processed_file} não encontrado. Iniciando lista vazia.")
            self._processed_tokens = set()
        except json.JSONDecodeError:
            logger.error(f"Erro ao decodificar JSON de {self._processed_file}. Iniciando lista vazia.")
            self._processed_tokens = set()

        try:
            with open(self._pending_file, "r") as f:
                self._pending_tokens = json.load(f)
            logger.info(f"Carregados {len(self._pending_tokens)} tokens pendentes de {self._pending_file}")
        except FileNotFoundError:
            logger.warning(f"Arquivo {self._pending_file} não encontrado. Iniciando lista vazia.")
            self._pending_tokens = []
        except json.JSONDecodeError:
            logger.error(f"Erro ao decodificar JSON de {self._pending_file}. Iniciando lista vazia.")
            self._pending_tokens = []

    async def save_state(self, final_save=False):
        """Salva o estado atual em arquivos JSON, com lock."""
        if final_save:
            if self._final_save_done:
                 logger.debug("Salvamento final já realizado.")
                 return
            logger.info("Realizando salvamento final do estado...")
            self._final_save_done = True
        else:
             if self._final_save_done:
                 return
             logger.debug("Tentando salvamento periódico do estado...")

        async with self._save_lock:
            # Salvar Processados
            try:
                with open(self._processed_file, "w") as f:
                    # Usar uma cópia para evitar problemas de concorrência se o set for modificado
                    json.dump(list(self._processed_tokens), f, indent=2)
                logger.info(f"Salvos {len(self._processed_tokens)} tokens processados em {self._processed_file}")
            except Exception as e:
                logger.error(f"Erro ao salvar tokens processados: {e}")

            # Salvar Pendentes
            try:
                with open(self._pending_file, "w") as f:
                    # Usar uma cópia
                    json.dump(list(self._pending_tokens), f, indent=2)
                logger.info(f"Salvos {len(self._pending_tokens)} tokens pendentes em {self._pending_file}")
            except Exception as e:
                logger.error(f"Erro ao salvar tokens pendentes: {e}")

    async def run_periodic_save(self, interval: int):
        """Tarefa assíncrona para salvar o estado periodicamente."""
        while True:
            await asyncio.sleep(interval)
            await self.save_state()

    def is_token_processed(self, mint_address: str) -> bool:
        """Verifica se um token já foi processado (inclui análise inicial)."""
        return mint_address in self._processed_tokens

    def add_processed_token(self, mint_address: str):
        """Adiciona um token à lista de processados (após análise inicial)."""
        self._processed_tokens.add(mint_address)

    def add_pending_token(self, token_data: Dict[str, Any]):
        """Adiciona um token à lista de pendentes para revisão."""
        mint = token_data.get("mint")
        if not mint: return # Não adiciona se não tiver mint
        # Evitar duplicados
        if not any(p.get("mint") == mint for p in self._pending_tokens):
            self._pending_tokens.append(token_data)
            logger.debug(f"Token {mint} adicionado aos pendentes.")
        else:
            logger.debug(f"Token {mint} já estava na lista de pendentes.")

    # --- Métodos síncronos para acesso seguro pelo signal handler ---
    def get_processed_tokens_copy(self) -> List[str]:
        return list(self._processed_tokens)

    def get_pending_tokens_copy(self) -> List[Dict[str, Any]]:
        return list(self._pending_tokens) # Retorna cópia

    def mark_final_save_done(self):
        self._final_save_done = True

    def is_final_save_done(self) -> bool:
        return self._final_save_done