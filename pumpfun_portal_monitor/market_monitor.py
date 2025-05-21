# pumpportal_monitor/market_monitor.py
import asyncio
import logging
import time
import json
from typing import Dict, Any, Optional, Set, Tuple
import aiohttp
import random
from datetime import datetime, timezone
import os
import aiofiles # Importar aiofiles

# Imports relativos dentro do pacote
from . import config
from .state_manager import StateManager

logger = logging.getLogger(__name__)

# --- Set global para rastrear tokens monitorados ---
tokens_being_monitored: Set[str] = set()
# --- Lock global para o arquivo monitored_tokens.json ---
_monitored_tokens_file_lock = asyncio.Lock()


# --- Função para escrever arquivo de tokens monitorados ---
async def _write_monitored_tokens_file():
    """Escreve o conteúdo atual do set tokens_being_monitored no arquivo JSON."""
    global tokens_being_monitored, _monitored_tokens_file_lock
    file_path = config.MONITORED_TOKENS_FILE
    async with _monitored_tokens_file_lock:
        try:
            monitored_list = sorted(list(tokens_being_monitored))
            async with aiofiles.open(file_path, "w") as f:
                await f.write(json.dumps(monitored_list, indent=2))
            logger.debug(f"Atualizado {os.path.basename(file_path)} com {len(monitored_list)} tokens.")
        except Exception as e:
            logger.error(f"Erro ao escrever em {file_path}: {e}", exc_info=True)

# --- Funções Auxiliares Refatoradas ---

def _find_target_pair(pairs: list, mint_address: str) -> Optional[Dict[str, Any]]:
    """Encontra o par SOL ou o par com maior liquidez na lista de pares da DexScreener."""
    # --- CORPO DA FUNÇÃO INDENTADO ---
    sol_pair = None; highest_liquidity_pair = None; max_liquidity = -1.0
    for i, pair in enumerate(pairs):
        if not isinstance(pair, dict): continue
        quote_token = pair.get("quoteToken", {}) or {}; liquidity_data = pair.get("liquidity", {}) or {}
        quote_address = quote_token.get("address"); is_sol_pair = quote_address == "So11111111111111111111111111111111111111112"
        liquidity_usd = 0.0
        try:
            liquidity_usd_raw = liquidity_data.get("usd"); liquidity_usd = float(liquidity_usd_raw) if liquidity_usd_raw is not None and liquidity_usd_raw != '' else 0.0
        except (ValueError,TypeError): logger.warning(f"[{mint_address}] Liq inválida par {pair.get('pairAddress', '?')}: '{liquidity_data.get('usd')}'"); continue
        logger.debug(f"[{mint_address}] Aval par {i}: {pair.get('pairAddress','?')} ({ (pair.get('baseToken', {}) or {}).get('symbol', '?')}/{quote_token.get('symbol', '?')}), Liq: ${liquidity_usd:.2f}")
        if is_sol_pair: sol_pair = pair; logger.debug(f"[{mint_address}] Par SOL: {pair.get('pairAddress','?')}"); break
        if liquidity_usd > max_liquidity: max_liquidity = liquidity_usd; highest_liquidity_pair = pair
    target_pair = sol_pair if sol_pair else highest_liquidity_pair
    if not target_pair: logger.warning(f"[{mint_address}] Ñ achou par alvo Dex."); return None
    logger.debug(f"[{mint_address}] Usando par alvo: {target_pair.get('pairAddress','?')}")
    return target_pair
    # --- FIM DA INDENTAÇÃO CORRETA ---

def _parse_market_data_from_pair(pair_data: Dict[str, Any], mint_address: str) -> Optional[Dict[str, Any]]:
    """Extrai e valida os dados de mercado necessários do dicionário do par alvo."""
    # --- CORPO DA FUNÇÃO INDENTADO ---
    extracted = {}; required = ["price_usd", "volume_m5", "buys_m5", "price_change_h1", "fdv"]
    try:
        extracted["price_usd"]=float(pair_data.get("priceUsd")) if pair_data.get("priceUsd") is not None else None
        extracted["volume_m5"]=float((pair_data.get("volume",{})or{}).get("m5")) if (pair_data.get("volume",{})or{}).get("m5") is not None else None
        txns_m5=pair_data.get("txns",{}).get("m5",{})or{}; extracted["buys_m5"]=int(txns_m5.get("buys")) if txns_m5.get("buys") is not None else None
        extracted["sells_m5"]=int(txns_m5.get("sells")) if txns_m5.get("sells") is not None else 0
        extracted["price_change_h1"]=float((pair_data.get("priceChange",{})or{}).get("h1")) if (pair_data.get("priceChange",{})or{}).get("h1") is not None else None
        extracted["fdv"]=float(pair_data.get("fdv")) if pair_data.get("fdv") is not None else None
        missing=[k for k in required if extracted.get(k) is None]
        if missing: logger.warning(f"[{mint_address}] Dados essenciais Dex ausentes: {missing}. Par: {pair_data.get('pairAddress')}"); return None
        logger.debug(f"[{mint_address}] Dados Dex OK: P={extracted['price_usd']:.8f}, V5m={extracted['volume_m5']:.0f}, B5m={extracted['buys_m5']}, S5m={extracted['sells_m5']}, H1%={extracted['price_change_h1']:.1f}, FDV={extracted['fdv']:,.0f}"); return extracted
    except(ValueError,TypeError,KeyError) as e: logger.error(f"[{mint_address}] Erro conversão/extração Dex: {e}. Par: {pair_data.get('pairAddress')}", exc_info=False); return None
    except Exception as e: logger.error(f"[{mint_address}] Erro inesperado _parse_market: {e}. Par: {pair_data.get('pairAddress')}", exc_info=True); return None
    # --- FIM DA INDENTAÇÃO CORRETA ---

async def fetch_market_data(mint: str, ses: aiohttp.ClientSession) -> Optional[Dict[str, Any]]:
    """Busca e processa dados de mercado recentes da API DexScreener."""
    url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
    logger.debug(f"[{mint}] Fetching Dex: {url}")
    
    try:
        timeout = aiohttp.ClientTimeout(total=config.API_TIMEOUT)
        async with ses.get(url, timeout=timeout) as resp:
            status = resp.status
            text = await resp.text()
            logger.debug(f"[{mint}] Dex status: {status}")
            
            if status == 200:
                try:
                    data = json.loads(text)
                except json.JSONDecodeError as e:
                    logger.error(f"[{mint}] Erro JSON Dex: {e}. Resp:{text[:500]}")
                    return None
                
                pairs = data.get("pairs")
                if not pairs or not isinstance(pairs, list):
                    logger.warning(f"[{mint}] Campo 'pairs' inválido Dex: {data}")
                    return None
                
                if not pairs:
                    logger.warning(f"[{mint}] Nenhum par encontrado na DexScreener")
                    return None
                
                target = _find_target_pair(pairs, mint)
                if not target:
                    logger.warning(f"[{mint}] Nenhum par alvo encontrado na DexScreener")
                    return None
                
                market_data = _parse_market_data_from_pair(target, mint)
                if not market_data:
                    logger.warning(f"[{mint}] Falha ao extrair dados de mercado do par alvo")
                    return None
                
                return market_data
                
            elif status == 404:
                logger.warning(f"[{mint}] Token não encontrado Dex (404)")
                return None
            elif status == 429:
                logger.warning(f"[{mint}] Rate limit Dex (429)")
                return None
            else:
                logger.error(f"[{mint}] Erro Dex: Status {status}. Resp: {text[:500]}")
                return None
                
    except asyncio.TimeoutError:
        logger.error(f"[{mint}] Timeout ao buscar dados Dex")
        return None
    except aiohttp.ClientError as e:
        logger.error(f"[{mint}] Erro de conexão Dex: {type(e).__name__} - {e}")
        return None
    except Exception as e:
        logger.error(f"[{mint}] Erro inesperado fetch_market: {type(e).__name__} - {e}", 
                    exc_info=(config.LOG_LEVEL_NAME=="DEBUG"))
        return None

def _check_market_criteria(m: Dict[str, Any], init_p: float, mint: str, i: int) -> Tuple[bool, str]:
    """Avalia se os dados de mercado atuais atendem aos critérios de compra definidos."""
    criteria = {
        "price": {
            "value": m["price_usd"],
            "min": init_p * (1.0 - config.MARKET_PRICE_DROP_TOLERANCE),
            "ok": m["price_usd"] >= init_p * (1.0 - config.MARKET_PRICE_DROP_TOLERANCE)
        },
        "volume": {
            "value": m["volume_m5"],
            "min": config.MARKET_MIN_VOLUME_M5,
            "ok": m["volume_m5"] >= config.MARKET_MIN_VOLUME_M5
        },
        "buys": {
            "value": m["buys_m5"],
            "min": config.MARKET_MIN_BUYS_M5,
            "ok": m["buys_m5"] >= config.MARKET_MIN_BUYS_M5
        },
        "buy_sell_ratio": {
            "value": m["buys_m5"] / (m["buys_m5"] + m["sells_m5"]) if (m["buys_m5"] + m["sells_m5"]) > 5 else 1.0,
            "min": config.MARKET_MIN_BUY_SELL_RATIO,
            "ok": (m["buys_m5"] / (m["buys_m5"] + m["sells_m5"]) >= config.MARKET_MIN_BUY_SELL_RATIO) if (m["buys_m5"] + m["sells_m5"]) > 5 else True
        },
        "fdv": {
            "value": m["fdv"],
            "max": config.MARKET_MAX_FDV,
            "ok": m["fdv"] < config.MARKET_MAX_FDV
        },
        "price_change": {
            "value": m["price_change_h1"],
            "min": config.MARKET_MIN_H1_PRICE_CHANGE,
            "ok": m["price_change_h1"] >= config.MARKET_MIN_H1_PRICE_CHANGE
        }
    }
    
    # Build log message
    log_parts = []
    for name, data in criteria.items():
        if name == "buy_sell_ratio":
            value_str = f"{data['value']*100:.0f}%"
            min_str = f"{data['min']*100:.0f}%"
        elif name in ["volume", "fdv"]:
            value_str = f"${data['value']/1000:.1f}k"
            min_str = f"${data['min']/1000:.0f}k" if name == "volume" else f"${data['max']/1000:.0f}k"
        else:
            value_str = f"{data['value']:.6f}" if name == "price" else f"{data['value']:.1f}"
            min_str = f"{data['min']:.6f}" if name == "price" else f"{data['min']:.1f}"
        
        log_parts.append(f"{name}:{value_str}(>{min_str})->{'OK' if data['ok'] else 'F'}")
    
    log = " | ".join(log_parts)
    logger.debug(f"[{mint}] Check (Iter {i}): {log}")
    
    # All criteria must be met
    return all(data["ok"] for data in criteria.values()), log

# --- monitor_market_activity (Usa set global e escreve arquivo) ---
async def monitor_market_activity(mint_address: str, initial_price: float, session: aiohttp.ClientSession, state: StateManager):
    """Monitora a atividade de mercado e dispara compra se os critérios forem atendidos."""
    start_time = time.monotonic()
    deadline = start_time + config.MARKET_MONITOR_DURATION
    
    logger.info(f"[{mint_address}] Iniciando monitor (Dur: {config.MARKET_MONITOR_DURATION}s, Int: {config.MARKET_POLL_INTERVAL}s, InitP: {initial_price:.8f})")
    
    # Initial random delay to avoid thundering herd
    await asyncio.sleep(random.uniform(3, 7))
    
    try:
        errors = 0
        max_errors = 5
        iteration = 0
        buy_attempted = False
        last_log = ""
        criteria_met = False
        
        while time.monotonic() < deadline and not buy_attempted:
            iteration += 1
            current_task = asyncio.current_task()
            
            # Check if monitoring should be stopped
            if mint_address not in tokens_being_monitored or getattr(current_task, '_must_cancel', False):
                logger.info(f"[{mint_address}] Monitoramento interrompido.")
                break
            
            # Sleep between iterations (except first)
            if iteration > 1:
                try:
                    await asyncio.sleep(config.MARKET_POLL_INTERVAL)
                except asyncio.CancelledError:
                    logger.info(f"[{mint_address}] Sleep cancelado.")
                    break
            
            logger.debug(f"[{mint_address}] Iteração {iteration} monitor...")
            
            # Fetch market data
            mkt_data = await fetch_market_data(mint_address, session)
            if mkt_data:
                errors = 0  # Reset error counter on successful fetch
                
                try:
                    criteria_met, last_log = _check_market_criteria(mkt_data, initial_price, mint_address, iteration)
                except Exception as e:
                    logger.error(f"[{mint_address}] Erro check critérios (Iter {iteration}): {e}", exc_info=True)
                    criteria_met = False
                
                if criteria_met:
                    logger.info(f"[{mint_address}] CRITÉRIOS ATINGIDOS (Iter {iteration})! Enviando ordem Sniperoo...")
                    buy_attempted = True
                    
                    # Attempt to place buy order
                    buy_ok = await place_sniperoo_buy_order(session=session, mint_address=mint_address)
                    
                    if buy_ok:
                        logger.info(f"[{mint_address}] Ordem IMEDIATA enviada. Encerrando monitor.")
                    else:
                        logger.warning(f"[{mint_address}] Falha envio ordem IMEDIATA. Encerrando monitor.")
                        try:
                            state.add_pending_token({
                                "mint": mint_address,
                                "ts": datetime.now(timezone.utc).isoformat(),
                                "reason": "Critérios OK, falha envio Sniperoo",
                                "market_data": mkt_data,
                                "criteria_log": last_log
                            })
                        except Exception as se:
                            logger.error(f"[{mint_address}] Erro add pending: {se}")
                    break
                else:
                    logger.debug(f"[{mint_address}] Critérios não atingidos (Iter {iteration}).")
            else:
                # Handle fetch failure
                errors += 1
                logger.warning(f"[{mint_address}] Falha fetch Dex ({errors}/{max_errors}).")
                
                if errors >= max_errors:
                    logger.error(f"[{mint_address}] Desistindo monitor - {max_errors} erros Dex.")
                    try:
                        state.add_pending_token({
                            "mint": mint_address,
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "reason": f"{max_errors} erros Dex"
                        })
                    except Exception as se:
                        logger.error(f"[{mint_address}] Erro add pending: {se}")
                    break
        
        if time.monotonic() >= deadline and not buy_attempted:
            logger.info(f"[{mint_address}] Timeout monitor ({config.MARKET_MONITOR_DURATION}s) sem critérios.")
            
    except asyncio.CancelledError:
        logger.info(f"[{mint_address}] Tarefa monitoramento cancelada.")
    except Exception as e:
        logger.error(f"[{mint_address}] Erro fatal tarefa monitor: {e}", exc_info=True)
        try:
            state.add_pending_token({
                "mint": mint_address,
                "ts": datetime.now(timezone.utc).isoformat(),
                "reason": f"Erro monitor: {e}"
            })
        except Exception as se:
            logger.error(f"[{mint_address}] Erro add pending: {se}")
    finally:
        # Cleanup
        was_present = mint_address in tokens_being_monitored
        tokens_being_monitored.discard(mint_address)
        if was_present:
            await _write_monitored_tokens_file()
        logger.debug(f"[{mint_address}] Removido monitor ativo (finally).")

# --- _build_sniperoo_payload (Definição Corrigida) ---
def _build_sniperoo_payload(mint_address: str) -> Optional[Dict[str, Any]]:
    """Constrói o payload para a API Sniperoo com base na configuração."""
    # --- CORPO DA FUNÇÃO INDENTADO ---
    api_key = config.SNIPEROO_API_KEY
    wallet_address = config.SNIPEROO_WALLET_ADDRESS
    use_sniperoo_autobuy = config.SNIPEROO_USE_AUTOBUY_MODE

    if not api_key or not config.SNIPEROO_BUY_ENDPOINT or not wallet_address:
        logger.error(f"[{mint_address}] Config Sniperoo essencial ausente."); return None

    payload = {
        "walletAddresses": [wallet_address],
        "tokenAddress": mint_address, # Usa o argumento
        "inputAmount": config.SNIPEROO_BUY_AMOUNT_SOL,
        "autoSell": {
            "enabled": config.SNIPEROO_AUTOSELL_ENABLED,
            "strategy": { "strategyName":"simple", "profitPercentage": config.SNIPEROO_AUTOSELL_PROFIT_PCT, "stopLossPercentage": config.SNIPEROO_AUTOSELL_STOPLOSS_PCT }
        },
        "autoBuy": {
            "enabled": use_sniperoo_autobuy,
            "strategy": {
                "priceMetric": { "metricType":config.SNIPEROO_AUTOBUY_PRICE_METRIC_TYPE, "minusOrPlus":config.SNIPEROO_AUTOBUY_PRICE_METRIC_PLUSMINUS, "enabled":use_sniperoo_autobuy and config.SNIPEROO_AUTOBUY_PRICE_METRIC_ENABLED, "minValue":config.SNIPEROO_AUTOBUY_PRICE_METRIC_MIN, "maxValue":config.SNIPEROO_AUTOBUY_PRICE_METRIC_MAX },
                "expiresAt": { "value":config.SNIPEROO_AUTOBUY_EXPIRES_VALUE, "unit":config.SNIPEROO_AUTOBUY_EXPIRES_UNIT }
            }
        }
    }
    if config.SNIPEROO_PRIORITY_FEE > 0: payload["computeUnitPriceMicroLamports"] = config.SNIPEROO_PRIORITY_FEE
    if config.SNIPEROO_SLIPPAGE_BPS > 0: payload["slippageBps"] = config.SNIPEROO_SLIPPAGE_BPS
    if config.SNIPEROO_MAX_RETRIES > 0: payload["maxRetries"] = config.SNIPEROO_MAX_RETRIES
    return payload
    # --- FIM DA INDENTAÇÃO CORRETA ---

# --- place_sniperoo_buy_order (Definição Corrigida) ---
async def place_sniperoo_buy_order(session: aiohttp.ClientSession, mint_address: str) -> bool:
    """Envia uma ordem de compra para a Sniperoo API."""
    payload = _build_sniperoo_payload(mint_address)
    if not payload: return False
    
    endpoint = config.SNIPEROO_BUY_ENDPOINT
    api_key = config.SNIPEROO_API_KEY
    auto_buy = config.SNIPEROO_USE_AUTOBUY_MODE
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    # Retry configuration from config
    max_retries = config.SNIPEROO_MAX_RETRIES
    base_delay = 2  # seconds
    max_delay = 10  # seconds
    
    # Non-retryable status codes
    non_retryable_status_codes = {400, 401, 403, 404, 422}
    
    for attempt in range(max_retries):
        try:
            if attempt > 0:
                delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                logger.info(f"[{mint_address}] Tentativa {attempt + 1}/{max_retries} após {delay:.1f}s...")
                await asyncio.sleep(delay)
            
            log_action = "Registrando AutoBuy" if auto_buy else "Enviando Compra IMEDIATA"
            logger.info(f"[{mint_address}] {log_action} p/ Sniperoo: {config.SNIPEROO_BUY_AMOUNT_SOL:.4f} SOL (Fee:{config.SNIPEROO_PRIORITY_FEE}, Slip:{config.SNIPEROO_SLIPPAGE_BPS} BPS)")
            logger.debug(f"Payload Sniperoo: {json.dumps(payload)}")
            
            timeout = aiohttp.ClientTimeout(total=config.API_TIMEOUT + 15)
            async with session.post(endpoint, json=payload, headers=headers, timeout=timeout) as resp:
                status = resp.status
                text = await resp.text()
                
                try:
                    data = json.loads(text) if text else {}
                except json.JSONDecodeError:
                    logger.error(f"[{mint_address}] Resposta inválida Sniperoo (não-JSON). Status:{status}, URL:{endpoint}, Resp:{text[:500]}")
                    if attempt < max_retries - 1 and status not in non_retryable_status_codes:
                        continue
                    return False
                
                if 200 <= status < 300:
                    order_info = data.get("order", data)
                    order_id = "N/A"
                    if isinstance(order_info, dict):
                        order_id = order_info.get("orderId", order_info.get("id"))
                    elif isinstance(data, dict):
                        order_id = data.get("orderId", data.get("id"))
                    
                    log_success = "Ordem AutoBuy registrada" if auto_buy else "Ordem compra enviada"
                    logger.info(f"[{mint_address}] {log_success} com sucesso! ID:{order_id}. Resp:{json.dumps(data)}")
                    return True
                else:
                    # Extract error message
                    error_msg = "Erro"
                    error_details = data.get("error")
                    message = data.get("message")
                    
                    if isinstance(error_details, dict):
                        error_msg = error_details.get("message", json.dumps(error_details))
                        logger.error(f"[{mint_address}] Detalhes erro Sniperoo: {json.dumps(error_details, indent=2)}")
                    elif isinstance(error_details, str):
                        error_msg = error_details
                    elif message:
                        error_msg = message
                    elif text:
                        error_msg = text[:500]
                    else:
                        error_msg = json.dumps(data)
                    
                    logger.error(f"[{mint_address}] Erro envio/registro Sniperoo. Status:{status}, URL:{endpoint}, Erro:{error_msg}")
                    logger.debug(f"[{mint_address}] Headers resposta Sniperoo: {dict(resp.headers)}")
                    
                    # Don't retry on certain status codes
                    if status in non_retryable_status_codes:
                        logger.error(f"[{mint_address}] Erro {status} - não tentando novamente")
                        return False
                    
                    if attempt < max_retries - 1:
                        continue
                    return False
                    
        except asyncio.TimeoutError:
            logger.error(f"[{mint_address}] Timeout na requisição Sniperoo")
            if attempt < max_retries - 1:
                continue
            return False
        except aiohttp.ClientError as e:
            logger.error(f"[{mint_address}] Erro de conexão Sniperoo: {type(e).__name__} - {e}")
            if attempt < max_retries - 1:
                continue
            return False
        except Exception as e:
            logger.error(f"[{mint_address}] Erro inesperado place_sniperoo_buy_order: {type(e).__name__}. URL:{endpoint}", 
                        exc_info=(config.LOG_LEVEL_NAME=="DEBUG"))
            if attempt < max_retries - 1:
                continue
            return False
    
    logger.error(f"[{mint_address}] Todas as {max_retries} tentativas falharam")
    return False