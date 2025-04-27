# pumpfun_portal_monitor/config.py

import os
from dotenv import load_dotenv
# Remova 'import logging' daqui se não for usado em outro lugar no config.py

# --- Carregar .env ---
dotenv_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(dotenv_path=dotenv_path, verbose=True)
if not os.getenv("WS_URL"):
    load_dotenv(verbose=True)

# Diretório de Dados Dentro do Container
DATA_DIR = "/data"
# Não precisa mais criar aqui se o Dockerfile/Compose define VOLUME
# os.makedirs(DATA_DIR, exist_ok=True)

# --- Configurações Carregadas ---
WS_URL = os.getenv("WS_URL", "wss://pumpportal.fun/api/data")
RUGCHECK_API_ENDPOINT = os.getenv("RUGCHECK_API_ENDPOINT")
LOG_FILE = os.path.join(DATA_DIR, os.getenv("LOG_FILE", "pumpfun_portal_monitor_async.log"))
PROCESSED_TOKENS_FILE = os.path.join(DATA_DIR, os.getenv("PROCESSED_TOKENS_FILE", "processed_tokens_async.json"))
PENDING_TOKENS_FILE = os.path.join(DATA_DIR, os.getenv("PENDING_TOKENS_FILE", "pending_tokens_async.json"))
CHECK_RETRY_DELAY_SECONDS = int(os.getenv("CHECK_RETRY_DELAY_SECONDS", "15"))
CHECK_MAX_DURATION_SECONDS = int(os.getenv("CHECK_MAX_DURATION_SECONDS", "180"))
SAVE_INTERVAL_SECONDS = int(os.getenv("SAVE_INTERVAL_SECONDS", "300"))

# --- CORREÇÃO: Armazenar apenas o NOME do nível de log ---
LOG_LEVEL_NAME = os.getenv("LOG_LEVEL", "INFO").upper() # Guarda "INFO", "DEBUG", etc.

# --- Constantes Internas ---
RECONNECT_INTERVAL_MIN = 5
RECONNECT_INTERVAL_MAX = 60

# Remova a linha antiga:
# LOG_LEVEL = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)