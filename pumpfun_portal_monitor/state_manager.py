# pumpportal_monitor/state_manager.py
import json
import logging
import asyncio
import os
from typing import Set, List, Dict, Any, Optional
from . import config # Importa config para usar o nome do blacklist file

logger = logging.getLogger(__name__)

class StateManager:
    # --- __init__ com 3 argumentos ---
    def __init__(self, processed_file: str, pending_file: str, blacklist_file: str):
        self._processed_file = processed_file
        self._pending_file = pending_file
        self._blacklist_file = blacklist_file # Caminho para o arquivo da blacklist
        self._processed_tokens: Set[str] = set()
        self._pending_tokens: List[Dict[str, Any]] = []
        self._creator_blacklist: Set[str] = set() # Conjunto para blacklist em memória
        self._save_lock = asyncio.Lock()
        self._final_save_done = False
        # REMOVIDO: Não gerencia mais monitored_file ou _currently_monitored

    def _load_creator_blacklist(self):
        """Carrega a blacklist de criadores do arquivo JSON."""
        file_path = self._blacklist_file
        try:
            if not os.path.exists(file_path):
                 logger.warning(f"Arquivo blacklist '{file_path}' não encontrado. Blacklist vazia.")
                 self._creator_blacklist = set(); return
            with open(file_path, "r") as f:
                try:
                    bl = json.load(f)
                    if isinstance(bl, list): self._creator_blacklist = {str(a) for a in bl if isinstance(a, str)}; logger.info(f"Carregados {len(self._creator_blacklist)} blacklist de '{file_path}'")
                    else: logger.error(f"Erro blacklist: '{file_path}' não contém lista JSON."); self._creator_blacklist = set()
                except json.JSONDecodeError: logger.error(f"Erro JSON blacklist '{file_path}'. Blacklist vazia."); self._creator_blacklist = set()
        except Exception as e: logger.error(f"Erro carregar blacklist '{file_path}': {e}", exc_info=True); self._creator_blacklist = set()

    def load_state(self):
        """Carrega todo o estado inicial dos arquivos (processados, pendentes, blacklist)."""
        # Carrega processados
        try:
            with open(self._processed_file, "r") as f: self._processed_tokens = set(json.load(f))
            logger.info(f"Carregados {len(self._processed_tokens)} processados.")
        except FileNotFoundError: logger.warning(f"{self._processed_file} não encontrado."); self._processed_tokens = set()
        except Exception as e: logger.error(f"Erro carregar {self._processed_file}: {e}"); self._processed_tokens = set()
        # Carrega pendentes
        try:
            with open(self._pending_file, "r") as f: self._pending_tokens = json.load(f)
            logger.info(f"Carregados {len(self._pending_tokens)} pendentes.")
        except FileNotFoundError: logger.warning(f"{self._pending_file} não encontrado."); self._pending_tokens = []
        except Exception as e: logger.error(f"Erro carregar {self._pending_file}: {e}"); self._pending_tokens = []
        # Carrega blacklist
        self._load_creator_blacklist()
        # REMOVIDO: Chamada _load_monitored_tokens()

    # --- REMOVIDOS: Métodos relacionados a monitored_tokens.json ---

    async def save_state(self, final_save=False):
        """Salva o estado atual (processados e pendentes) em arquivos JSON."""
        if final_save:
            if self._final_save_done: logger.debug("Salvamento final já feito."); return
            logger.info("Salvamento final estado..."); self._final_save_done = True
        else:
            if self._final_save_done: return
            logger.debug("Salvamento periódico estado...")
        async with self._save_lock:
            pc = list(self._processed_tokens); pd = list(self._pending_tokens) # Cópias
            try:
                with open(self._processed_file, "w") as f: json.dump(pc, f, indent=2)
                logger.info(f"Salvos {len(pc)} processados em {self._processed_file}")
            except Exception as e: logger.error(f"Erro salvar processados: {e}", exc_info=True)
            try:
                with open(self._pending_file, "w") as f: json.dump(pd, f, indent=2)
                logger.info(f"Salvos {len(pd)} pendentes em {self._pending_file}")
            except Exception as e: logger.error(f"Erro salvar pendentes: {e}", exc_info=True)

    async def run_periodic_save(self, interval: int):
        while True: await asyncio.sleep(interval); await self.save_state()

    def is_token_processed(self, mint: str) -> bool: return mint in self._processed_tokens
    def add_processed_token(self, mint: str): self._processed_tokens.add(mint)
    def add_pending_token(self, data: Dict[str, Any]):
        mint = data.get("mint"); reason = data.get("reason", "?")
        if not mint: logger.warning("Tentativa add pending sem mint."); return
        if not any(p.get("mint") == mint for p in self._pending_tokens):
            self._pending_tokens.append(data); logger.info(f"Token {mint} add pending. Razão: {reason}")
        else: logger.debug(f"Token {mint} já estava pending.")
    def is_creator_blacklisted(self, c_addr: Optional[str]) -> bool: return bool(c_addr and isinstance(c_addr, str) and c_addr in self._creator_blacklist)
    def get_processed_tokens_copy(self) -> List[str]: return list(self._processed_tokens)
    def get_pending_tokens_copy(self) -> List[Dict[str, Any]]: return list(self._pending_tokens)
    def mark_final_save_done(self): self._final_save_done = True
    def is_final_save_done(self) -> bool: return self._final_save_done