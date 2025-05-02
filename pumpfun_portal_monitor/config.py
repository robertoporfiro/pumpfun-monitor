# pumpportal_monitor/config.py
import os
import logging
from dotenv import load_dotenv
import sys

# --- Carregar .env ---
# Ajustado para procurar no diretório pai primeiro, depois no atual
dotenv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
loaded = load_dotenv(dotenv_path=dotenv_path, verbose=False)
if not loaded:
    load_dotenv(verbose=False) # Tenta carregar do diretório atual se não achou no pai

DATA_DIR = os.getenv("BOT_DATA_DIR", "/data") # Usar BOT_DATA_DIR se definido pela Web UI
# --- Fim ---

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
MARKET_MIN_BUY_SELL_RATIO = float(os.getenv("MARKET_MIN_BUY_SELL_RATIO", "0.50")) # Atualizado default
MARKET_MAX_FDV = float(os.getenv("MARKET_MAX_FDV", "250000")) # Atualizado default
MARKET_MIN_H1_PRICE_CHANGE = float(os.getenv("MARKET_MIN_H1_PRICE_CHANGE", "-20.0")) # Atualizado default

# --- Configurações do Sniperoo ---
SNIPEROO_API_KEY = os.getenv("SNIPEROO_API_KEY")
SNIPEROO_BUY_ENDPOINT = os.getenv("SNIPEROO_BUY_ENDPOINT", "https://api.sniperoo.app/trading/buy-token?toastFrontendId=0")
SNIPEROO_BUY_AMOUNT_SOL = float(os.getenv("SNIPEROO_BUY_AMOUNT_SOL", "0.05"))
SNIPEROO_WALLET_ADDRESS = os.getenv("SNIPEROO_WALLET_ADDRESS")
SNIPEROO_AUTOSELL_ENABLED = os.getenv("SNIPEROO_AUTOSELL_ENABLED", "True").lower() == 'true'
SNIPEROO_AUTOSELL_PROFIT_PCT = float(os.getenv("SNIPEROO_AUTOSELL_PROFIT_PCT", "30.0")) # Atualizado default
SNIPEROO_AUTOSELL_STOPLOSS_PCT = float(os.getenv("SNIPEROO_AUTOSELL_STOPLOSS_PCT", "30.0"))# Atualizado default
SNIPEROO_USE_AUTOBUY_MODE = os.getenv("SNIPEROO_USE_AUTOBUY_MODE", "False").lower() == 'true'
SNIPEROO_AUTOBUY_PRICE_METRIC_TYPE = os.getenv("SNIPEROO_AUTOBUY_PRICE_METRIC_TYPE", "price_change")
SNIPEROO_AUTOBUY_PRICE_METRIC_PLUSMINUS = os.getenv("SNIPEROO_AUTOBUY_PRICE_METRIC_PLUSMINUS", "plus")
SNIPEROO_AUTOBUY_PRICE_METRIC_ENABLED = os.getenv("SNIPEROO_AUTOBUY_PRICE_METRIC_ENABLED", "True").lower() == 'true'
SNIPEROO_AUTOBUY_PRICE_METRIC_MIN = float(os.getenv("SNIPEROO_AUTOBUY_PRICE_METRIC_MIN", "10"))
SNIPEROO_AUTOBUY_PRICE_METRIC_MAX = float(os.getenv("SNIPEROO_AUTOBUY_PRICE_METRIC_MAX", "500"))
SNIPEROO_AUTOBUY_EXPIRES_VALUE = int(os.getenv("SNIPEROO_AUTOBUY_EXPIRES_VALUE", "10"))
SNIPEROO_AUTOBUY_EXPIRES_UNIT = os.getenv("SNIPEROO_AUTOBUY_EXPIRES_UNIT", "minutes")
SNIPEROO_PRIORITY_FEE = int(os.getenv("SNIPEROO_PRIORITY_FEE", "150000")) # Atualizado default
SNIPEROO_SLIPPAGE_BPS = int(os.getenv("SNIPEROO_SLIPPAGE_BPS", "1500"))
SNIPEROO_MAX_RETRIES = int(os.getenv("SNIPEROO_MAX_RETRIES", "3")) # Aumentado default

# --- Filtros de Segurança ---
MIN_RUGCHECK_SCORE = float(os.getenv("MIN_RUGCHECK_SCORE", "1.0")) # Atualizado default
MIN_INITIAL_LIQUIDITY = float(os.getenv("MIN_INITIAL_LIQUIDITY", "10000")) # Atualizado default
FILTER_MAX_CREATOR_HOLDING_PCT = float(os.getenv("FILTER_MAX_CREATOR_HOLDING_PCT", "3.0")) # Atualizado default
FILTER_MAX_SINGLE_HOLDER_PCT = float(os.getenv("FILTER_MAX_SINGLE_HOLDER_PCT", "10.0"))# Atualizado default
FILTER_MAX_INSIDERS_DETECTED = int(os.getenv("FILTER_MAX_INSIDERS_DETECTED", "0"))
# --- Caminho Blacklist ---
CREATOR_BLACKLIST_FILE = os.path.join(os.path.dirname(__file__), '..', os.getenv("CREATOR_BLACKLIST_FILE", "creator_blacklist.json")) # Assumindo que blacklist está na raiz

# --- Arquivos Web UI ---
PID_FILE = os.path.join(DATA_DIR, os.getenv("PID_FILE", "bot.pid"))
MONITORED_TOKENS_FILE = os.path.join(DATA_DIR, os.getenv("MONITORED_TOKENS_FILE", "monitored_tokens.json"))

# --- ADICIONADO: Restrição de Horário ---
TRADING_ENABLED = os.getenv("TRADING_ENABLED", "True").lower() == 'true'
TRADING_START_HOUR = int(os.getenv("TRADING_START_HOUR", "0"))
TRADING_END_HOUR = int(os.getenv("TRADING_END_HOUR", "24"))
# --- FIM ADIÇÃO ---

# --- Validações e Log Inicial ---
config_errors = []
if not SNIPEROO_API_KEY: config_errors.append("FATAL: SNIPEROO_API_KEY")
if not SNIPEROO_BUY_ENDPOINT: config_errors.append("FATAL: SNIPEROO_BUY_ENDPOINT")
if not SNIPEROO_WALLET_ADDRESS: config_errors.append("FATAL: SNIPEROO_WALLET_ADDRESS")
if not RUGCHECK_API_ENDPOINT: config_errors.append("[WARNING] RUGCHECK_API_ENDPOINT")

if config_errors:
    # (Código de log de erro/fatal como antes)
    # ...
    if is_fatal: sys.exit("Erro fatal config.")

def log_initial_config():
    logger = logging.getLogger("config")
    logger.info("--- Configurações Carregadas ---")
    # ... (Logs existentes para Wallet, Buy, AutoSell, Sniperoo Mode, Bot Monitor) ...
    logger.info(f"Wallet: {SNIPEROO_WALLET_ADDRESS[:4]}...{SNIPEROO_WALLET_ADDRESS[-4:]}" if SNIPEROO_WALLET_ADDRESS else "N/D")
    logger.info(f"Buy: {SNIPEROO_BUY_AMOUNT_SOL} SOL | Slip: {SNIPEROO_SLIPPAGE_BPS} BPS | Fee: {SNIPEROO_PRIORITY_FEE} | Retry: {SNIPEROO_MAX_RETRIES}")
    logger.info(f"AutoSell: {SNIPEROO_AUTOSELL_ENABLED} (TP: {SNIPEROO_AUTOSELL_PROFIT_PCT}%, SL: {SNIPEROO_AUTOSELL_STOPLOSS_PCT}%)")
    logger.info(f"Sniperoo AutoBuy Mode: {SNIPEROO_USE_AUTOBUY_MODE}")
    if not SNIPEROO_USE_AUTOBUY_MODE:
        logger.info(f"Bot Monitor: ON (Dur:{MARKET_MONITOR_DURATION}s, Int:{MARKET_POLL_INTERVAL}s, Vol5m:${MARKET_MIN_VOLUME_M5:,.0f}, B5m:{MARKET_MIN_BUYS_M5}, B/S>{MARKET_MIN_BUY_SELL_RATIO*100:.0f}%, MaxFDV:${MARKET_MAX_FDV/1000:,.0f}k, H1%>{MARKET_MIN_H1_PRICE_CHANGE:.0f}%)")
    logger.info(f"Filtros Seg: RC Score>={MIN_RUGCHECK_SCORE}, RC Liq>={MIN_INITIAL_LIQUIDITY:,.0f}, CrHold<={FILTER_MAX_CREATOR_HOLDING_PCT}%, SingleH<={FILTER_MAX_SINGLE_HOLDER_PCT}%, RC Ins<={FILTER_MAX_INSIDERS_DETECTED}")
    logger.info(f"Blacklist File: {CREATOR_BLACKLIST_FILE}")
    # --- ADICIONADO: Log do Horário ---
    logger.info(f"Trading Enabled: {TRADING_ENABLED}")
    if TRADING_ENABLED: logger.info(f"Trading Window: {TRADING_START_HOUR:02d}:00 - {TRADING_END_HOUR:02d}:00 (TZ: {os.getenv('TZ', 'System Default')})")
    # --- FIM ADIÇÃO ---
    logger.info("---------------------------------")