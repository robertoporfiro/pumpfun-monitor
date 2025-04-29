# pumpportal_monitor/config.py
import os
import logging
from dotenv import load_dotenv

# --- Carregar .env ---
dotenv_path = os.path.join(os.path.dirname(__file__), '..', '.env')
loaded = load_dotenv(dotenv_path=dotenv_path, verbose=False)
if not loaded or not os.getenv("WS_URL"):
    load_dotenv(verbose=False)
DATA_DIR = "/data"
# --- Fim do código inicial ---

# --- Configurações Carregadas ---
WS_URL = os.getenv("WS_URL", "wss://pumpportal.fun/api/data")
RUGCHECK_API_ENDPOINT = os.getenv("RUGCHECK_API_ENDPOINT")
LOG_FILE = os.path.join(DATA_DIR, os.getenv("LOG_FILE", "pumpportal_monitor_async.log"))
PROCESSED_TOKENS_FILE = os.path.join(DATA_DIR, os.getenv("PROCESSED_TOKENS_FILE", "processed_tokens_async.json"))
PENDING_TOKENS_FILE = os.path.join(DATA_DIR, os.getenv("PENDING_TOKENS_FILE", "pending_tokens_async.json"))
API_TIMEOUT = int(os.getenv("API_TIMEOUT", "10"))

# Check / Reliability Config
CHECK_RETRY_DELAY_SECONDS = int(os.getenv("CHECK_RETRY_DELAY_SECONDS", "15"))
CHECK_MAX_DURATION_SECONDS = int(os.getenv("CHECK_MAX_DURATION_SECONDS", "180"))

# State & Connection Config
SAVE_INTERVAL_SECONDS = int(os.getenv("SAVE_INTERVAL_SECONDS", "300"))
RECONNECT_INTERVAL_MIN = 5
RECONNECT_INTERVAL_MAX = 60
LOG_LEVEL_NAME = os.getenv("LOG_LEVEL", "INFO").upper()

# --- Configurações do Monitor de Mercado ---
MARKET_MONITOR_DURATION = int(os.getenv("MARKET_MONITOR_DURATION", "300"))
MARKET_POLL_INTERVAL = int(os.getenv("MARKET_POLL_INTERVAL", "10"))
MARKET_MIN_VOLUME_M5 = float(os.getenv("MARKET_MIN_VOLUME_M5", "1000"))
MARKET_MIN_BUYS_M5 = int(os.getenv("MARKET_MIN_BUYS_M5", "10"))
MARKET_PRICE_DROP_TOLERANCE = float(os.getenv("MARKET_PRICE_DROP_TOLERANCE", "0.15"))

# --- Configurações do Sniperoo ---
SNIPEROO_API_KEY = os.getenv("SNIPEROO_API_KEY")
SNIPEROO_BUY_ENDPOINT = os.getenv("SNIPEROO_BUY_ENDPOINT", "https://api.sniperoo.app/trading/buy-token?toastFrontendId=0")
SNIPEROO_BUY_AMOUNT_SOL = float(os.getenv("SNIPEROO_BUY_AMOUNT_SOL", "0.05"))
SNIPEROO_WALLET_ADDRESS = os.getenv("SNIPEROO_WALLET_ADDRESS")
# AutoSell
SNIPEROO_AUTOSELL_ENABLED = os.getenv("SNIPEROO_AUTOSELL_ENABLED", "True").lower() == 'true'
SNIPEROO_AUTOSELL_PROFIT_PCT = float(os.getenv("SNIPEROO_AUTOSELL_PROFIT_PCT", "20.0"))
SNIPEROO_AUTOSELL_STOPLOSS_PCT = float(os.getenv("SNIPEROO_AUTOSELL_STOPLOSS_PCT", "10.0"))
# AutoBuy (Lido do .env, mas a lógica de envio pode sobrescrever para False)
SNIPEROO_AUTOBUY_ENABLED_CONFIG = os.getenv("SNIPEROO_AUTOBUY_ENABLED", "False").lower() == 'true'
# Parâmetros da Estratégia AutoBuy (usados se SNIPEROO_AUTOBUY_ENABLED_CONFIG for True)
SNIPEROO_AUTOBUY_PRICE_METRIC_TYPE = os.getenv("SNIPEROO_AUTOBUY_PRICE_METRIC_TYPE", "price_change")
SNIPEROO_AUTOBUY_PRICE_METRIC_PLUSMINUS = os.getenv("SNIPEROO_AUTOBUY_PRICE_METRIC_PLUSMINUS", "minus")
SNIPEROO_AUTOBUY_PRICE_METRIC_ENABLED = os.getenv("SNIPEROO_AUTOBUY_PRICE_METRIC_ENABLED", "True").lower() == 'true'
SNIPEROO_AUTOBUY_PRICE_METRIC_MIN = float(os.getenv("SNIPEROO_AUTOBUY_PRICE_METRIC_MIN", "0"))
SNIPEROO_AUTOBUY_PRICE_METRIC_MAX = float(os.getenv("SNIPEROO_AUTOBUY_PRICE_METRIC_MAX", "0"))
SNIPEROO_AUTOBUY_EXPIRES_VALUE = int(os.getenv("SNIPEROO_AUTOBUY_EXPIRES_VALUE", "10"))
SNIPEROO_AUTOBUY_EXPIRES_UNIT = os.getenv("SNIPEROO_AUTOBUY_EXPIRES_UNIT", "minutes")

# --- Filtros de Segurança Opcionais ---
MIN_RUGCHECK_SCORE = float(os.getenv("MIN_RUGCHECK_SCORE", "0.9"))
MIN_INITIAL_LIQUIDITY = float(os.getenv("MIN_INITIAL_LIQUIDITY", "3000"))

# --- Validações de Configuração Essencial ---
config_errors = []
if not SNIPEROO_API_KEY:
    config_errors.append("FATAL: SNIPEROO_API_KEY não definido no .env!")
if not SNIPEROO_BUY_ENDPOINT:
    config_errors.append("FATAL: SNIPEROO_BUY_ENDPOINT não definido no .env!")
if not SNIPEROO_WALLET_ADDRESS:
    config_errors.append("FATAL: SNIPEROO_WALLET_ADDRESS não definido no .env!")
if not RUGCHECK_API_ENDPOINT:
     config_errors.append("[WARNING] RUGCHECK_API_ENDPOINT não definido. Análise RugCheck será pulada.")

# Logar erros de configuração ou sair se for fatal
if config_errors:
    # Configura um logger básico temporário para garantir que os erros sejam vistos
    temp_logger = logging.getLogger("config_validation")
    temp_handler = logging.StreamHandler()
    temp_formatter = logging.Formatter('%(asctime)s [%(levelname)s] [%(name)s] %(message)s')
    temp_handler.setFormatter(temp_formatter)
    temp_logger.addHandler(temp_handler)
    temp_logger.setLevel(logging.ERROR)
    is_fatal = False
    for error in config_errors:
        temp_logger.error(f"Erro de Configuração: {error}")
        if "FATAL" in error:
            is_fatal = True
    if is_fatal:
        import sys
        sys.exit("Erro fatal na configuração. Verifique o .env e os logs.")

# Log inicial de algumas configurações importantes (será chamado após logger principal ser configurado)
def log_initial_config():
    logger = logging.getLogger(__name__) # Pega o logger configurado em monitor.py
    logger.info(f"Config - SOL Amount per Buy: {SNIPEROO_BUY_AMOUNT_SOL}")
    logger.info(f"Config - Wallet Address: {SNIPEROO_WALLET_ADDRESS[:4]}...{SNIPEROO_WALLET_ADDRESS[-4:]}" if SNIPEROO_WALLET_ADDRESS else "NÃO DEFINIDO")
    logger.info(f"Config - AutoSell Enabled: {SNIPEROO_AUTOSELL_ENABLED}")
    if SNIPEROO_AUTOSELL_ENABLED:
        logger.info(f"Config - AutoSell Profit: {SNIPEROO_AUTOSELL_PROFIT_PCT}%")
        logger.info(f"Config - AutoSell StopLoss: {SNIPEROO_AUTOSELL_STOPLOSS_PCT}%")
    # Indica se o autobuy está habilitado conforme .env, mas a lógica de envio pode forçar false
    logger.info(f"Config - Sniperoo AutoBuy Enabled (from .env): {SNIPEROO_AUTOBUY_ENABLED_CONFIG}")
    logger.info(f"Config - Min RugCheck Score: {MIN_RUGCHECK_SCORE}")
    logger.info(f"Config - Min Initial Liquidity: ${MIN_INITIAL_LIQUIDITY:,.2f}")