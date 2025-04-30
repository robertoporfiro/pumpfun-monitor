# pumpportal_monitor/config.py
import os
import logging
from dotenv import load_dotenv
import sys

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

# --- Configurações do Monitor de Mercado (Usado apenas se AutoBuy=False) ---
MARKET_MONITOR_DURATION = int(os.getenv("MARKET_MONITOR_DURATION", "300"))
MARKET_POLL_INTERVAL = int(os.getenv("MARKET_POLL_INTERVAL", "10"))
MARKET_MIN_VOLUME_M5 = float(os.getenv("MARKET_MIN_VOLUME_M5", "1000"))
MARKET_MIN_BUYS_M5 = int(os.getenv("MARKET_MIN_BUYS_M5", "10"))
MARKET_PRICE_DROP_TOLERANCE = float(os.getenv("MARKET_PRICE_DROP_TOLERANCE", "0.15"))
# --- NOVOS FILTROS DE MERCADO ---
MARKET_MIN_BUY_SELL_RATIO = float(os.getenv("MARKET_MIN_BUY_SELL_RATIO", "0.60"))
MARKET_MAX_FDV = float(os.getenv("MARKET_MAX_FDV", "200000"))
MARKET_MIN_H1_PRICE_CHANGE = float(os.getenv("MARKET_MIN_H1_PRICE_CHANGE", "-15.0"))
# --- FIM NOVOS FILTROS DE MERCADO ---

# --- Configurações do Sniperoo ---
SNIPEROO_API_KEY = os.getenv("SNIPEROO_API_KEY")
SNIPEROO_BUY_ENDPOINT = os.getenv("SNIPEROO_BUY_ENDPOINT", "https://api.sniperoo.app/trading/buy-token?toastFrontendId=0")
SNIPEROO_BUY_AMOUNT_SOL = float(os.getenv("SNIPEROO_BUY_AMOUNT_SOL", "0.05"))
SNIPEROO_WALLET_ADDRESS = os.getenv("SNIPEROO_WALLET_ADDRESS")
SNIPEROO_AUTOSELL_ENABLED = os.getenv("SNIPEROO_AUTOSELL_ENABLED", "True").lower() == 'true'
SNIPEROO_AUTOSELL_PROFIT_PCT = float(os.getenv("SNIPEROO_AUTOSELL_PROFIT_PCT", "20.0"))
SNIPEROO_AUTOSELL_STOPLOSS_PCT = float(os.getenv("SNIPEROO_AUTOSELL_STOPLOSS_PCT", "10.0"))
SNIPEROO_USE_AUTOBUY_MODE = os.getenv("SNIPEROO_USE_AUTOBUY_MODE", "False").lower() == 'true'
# Parâmetros da Estratégia AutoBuy (usados APENAS se SNIPEROO_USE_AUTOBUY_MODE for True)
SNIPEROO_AUTOBUY_PRICE_METRIC_TYPE = os.getenv("SNIPEROO_AUTOBUY_PRICE_METRIC_TYPE", "price_change")
SNIPEROO_AUTOBUY_PRICE_METRIC_PLUSMINUS = os.getenv("SNIPEROO_AUTOBUY_PRICE_METRIC_PLUSMINUS", "minus")
SNIPEROO_AUTOBUY_PRICE_METRIC_ENABLED = os.getenv("SNIPEROO_AUTOBUY_PRICE_METRIC_ENABLED", "True").lower() == 'true'
SNIPEROO_AUTOBUY_PRICE_METRIC_MIN = float(os.getenv("SNIPEROO_AUTOBUY_PRICE_METRIC_MIN", "0"))
SNIPEROO_AUTOBUY_PRICE_METRIC_MAX = float(os.getenv("SNIPEROO_AUTOBUY_PRICE_METRIC_MAX", "0"))
SNIPEROO_AUTOBUY_EXPIRES_VALUE = int(os.getenv("SNIPEROO_AUTOBUY_EXPIRES_VALUE", "10"))
SNIPEROO_AUTOBUY_EXPIRES_UNIT = os.getenv("SNIPEROO_AUTOBUY_EXPIRES_UNIT", "minutes")
# Parâmetros adicionais da transação
SNIPEROO_PRIORITY_FEE = int(os.getenv("SNIPEROO_PRIORITY_FEE", "100000"))
SNIPEROO_SLIPPAGE_BPS = int(os.getenv("SNIPEROO_SLIPPAGE_BPS", "1500"))
SNIPEROO_MAX_RETRIES = int(os.getenv("SNIPEROO_MAX_RETRIES", "2"))

# --- Filtros de Segurança Pré-Monitoramento ---
MIN_RUGCHECK_SCORE = float(os.getenv("MIN_RUGCHECK_SCORE", "0.9"))
MIN_INITIAL_LIQUIDITY = float(os.getenv("MIN_INITIAL_LIQUIDITY", "3000"))
FILTER_MAX_INSIDERS_DETECTED = int(os.getenv("FILTER_MAX_INSIDERS_DETECTED", "0"))
FILTER_MAX_SINGLE_HOLDER_PCT = float(os.getenv("FILTER_MAX_SINGLE_HOLDER_PCT", "15.0"))
FILTER_MAX_CREATOR_HOLDING_PCT = float(os.getenv("FILTER_MAX_CREATOR_HOLDING_PCT", "1.0"))

# --- Validações de Configuração Essencial ---
config_errors = []
if not SNIPEROO_API_KEY: config_errors.append("FATAL: SNIPEROO_API_KEY")
if not SNIPEROO_BUY_ENDPOINT: config_errors.append("FATAL: SNIPEROO_BUY_ENDPOINT")
if not SNIPEROO_WALLET_ADDRESS: config_errors.append("FATAL: SNIPEROO_WALLET_ADDRESS")
if not RUGCHECK_API_ENDPOINT: config_errors.append("[WARNING] RUGCHECK_API_ENDPOINT")

if config_errors:
    temp_logger = logging.getLogger("config_validation")
    temp_handler = logging.StreamHandler()
    temp_formatter = logging.Formatter('%(asctime)s [%(levelname)s] [%(name)s] %(message)s')
    temp_handler.setFormatter(temp_formatter)
    temp_logger.addHandler(temp_handler)
    temp_logger.setLevel(logging.INFO)
    is_fatal = False
    for error in config_errors:
        level = logging.ERROR if "FATAL" in error else logging.WARNING
        msg = f"Configuração Ausente/Inválida: {error.replace('FATAL: ', '').replace('[WARNING] ', '')}"
        temp_logger.log(level, msg)
        if "FATAL" in error: is_fatal = True
    if is_fatal:
        print("ERRO FATAL NA CONFIGURAÇÃO. Verifique o arquivo .env e os logs. Encerrando.", file=sys.stderr)
        sys.exit("Erro fatal na configuração.")

# Log inicial de configurações importantes
def log_initial_config():
    logger = logging.getLogger("config")
    logger.info("--- Configurações Carregadas ---")
    logger.info(f"Wallet Address: {SNIPEROO_WALLET_ADDRESS[:4]}...{SNIPEROO_WALLET_ADDRESS[-4:]}" if SNIPEROO_WALLET_ADDRESS else "NÃO DEFINIDO")
    logger.info(f"SOL Amount per Buy: {SNIPEROO_BUY_AMOUNT_SOL}")
    logger.info(f"Slippage BPS: {SNIPEROO_SLIPPAGE_BPS} ({SNIPEROO_SLIPPAGE_BPS/100:.2f}%)")
    logger.info(f"Priority Fee (MicroLamports): {SNIPEROO_PRIORITY_FEE}")
    logger.info(f"Max Retries: {SNIPEROO_MAX_RETRIES}")
    logger.info(f"AutoSell Enabled: {SNIPEROO_AUTOSELL_ENABLED}")
    if SNIPEROO_AUTOSELL_ENABLED:
        logger.info(f"  AutoSell Profit: {SNIPEROO_AUTOSELL_PROFIT_PCT}%")
        logger.info(f"  AutoSell StopLoss: {SNIPEROO_AUTOSELL_STOPLOSS_PCT}%")
    logger.info(f"Sniperoo AutoBuy Mode Enabled: {SNIPEROO_USE_AUTOBUY_MODE}")
    if SNIPEROO_USE_AUTOBUY_MODE:
         logger.info(f"  AutoBuy Metric: {SNIPEROO_AUTOBUY_PRICE_METRIC_TYPE} {SNIPEROO_AUTOBUY_PRICE_METRIC_PLUSMINUS} {SNIPEROO_AUTOBUY_PRICE_METRIC_MIN}-{SNIPEROO_AUTOBUY_PRICE_METRIC_MAX} (Enabled: {SNIPEROO_AUTOBUY_PRICE_METRIC_ENABLED})")
         logger.info(f"  AutoBuy Expires: {SNIPEROO_AUTOBUY_EXPIRES_VALUE} {SNIPEROO_AUTOBUY_EXPIRES_UNIT}")
    else: # Log dos filtros do Bot Market Monitor
         logger.info(f"Bot Market Monitor Enabled: True")
         logger.info(f"  Market Monitor Duration: {MARKET_MONITOR_DURATION}s")
         logger.info(f"  Market Poll Interval: {MARKET_POLL_INTERVAL}s")
         logger.info(f"  Market Min Volume (5m): ${MARKET_MIN_VOLUME_M5:,.2f}")
         logger.info(f"  Market Min Buys (5m): {MARKET_MIN_BUYS_M5}")
         logger.info(f"  Market Price Drop Tolerance: {MARKET_PRICE_DROP_TOLERANCE*100:.1f}%")
         # --- LOG NOVOS FILTROS DE MERCADO ---
         logger.info(f"  Market Min Buy/Sell Ratio (5m): {MARKET_MIN_BUY_SELL_RATIO*100:.0f}%")
         logger.info(f"  Market Max FDV: ${MARKET_MAX_FDV:,.0f}")
         logger.info(f"  Market Min H1 Price Change: {MARKET_MIN_H1_PRICE_CHANGE:.1f}%")
         # --- FIM LOG NOVOS FILTROS ---
    logger.info(f"Min RugCheck Score: {MIN_RUGCHECK_SCORE}")
    logger.info(f"Min Initial Liquidity (RugCheck): ${MIN_INITIAL_LIQUIDITY:,.2f}")
    logger.info(f"Max Creator Holding Pct: {FILTER_MAX_CREATOR_HOLDING_PCT:.2f}%")
    logger.info(f"Max Single Holder Pct: {FILTER_MAX_SINGLE_HOLDER_PCT:.2f}%")
    logger.info(f"Max Insiders Detected (RugCheck): {FILTER_MAX_INSIDERS_DETECTED}")
    logger.info("---------------------------------")