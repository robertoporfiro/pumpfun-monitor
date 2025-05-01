# pumpportal_monitor/market_monitor.py
import asyncio
import logging
import time
import json
from typing import Dict, Any, Optional, Set, Tuple
import aiohttp
import random
from datetime import datetime, timezone # <<< IMPORTAÇÃO ADICIONADA

# Imports relativos dentro do pacote
from . import config
from .state_manager import StateManager

logger = logging.getLogger(__name__)

tokens_being_monitored: Set[str] = set()

# --- Funções Auxiliares Refatoradas ---

def _find_target_pair(pairs: list, mint_address: str) -> Optional[Dict[str, Any]]:
    """Encontra o par SOL ou o par com maior liquidez na lista de pares da DexScreener."""
    sol_pair = None
    highest_liquidity_pair = None
    max_liquidity = -1.0

    for i, pair in enumerate(pairs):
        if not isinstance(pair, dict): continue
        quote_token = pair.get("quoteToken", {}) or {}
        liquidity_data = pair.get("liquidity", {}) or {}
        quote_address = quote_token.get("address")
        is_sol_pair = quote_address == "So11111111111111111111111111111111111111112"
        liquidity_usd = 0.0
        try:
            liquidity_usd_raw = liquidity_data.get("usd")
            if liquidity_usd_raw is not None and liquidity_usd_raw != '':
                liquidity_usd = float(liquidity_usd_raw)
            else:
                 liquidity_usd = 0.0
        except (ValueError, TypeError):
            logger.warning(f"[{mint_address}] Valor de liquidez inválido para par {pair.get('pairAddress', 'N/A')}: '{liquidity_data.get('usd')}'")
            continue

        logger.debug(f"[{mint_address}] Avaliando par {i}: {pair.get('pairAddress', 'N/A')} ({ (pair.get('baseToken', {}) or {}).get('symbol', 'N/A')}/{quote_token.get('symbol', 'N/A')}), Liq: ${liquidity_usd:.2f}")
        if is_sol_pair:
            sol_pair = pair
            logger.debug(f"[{mint_address}] Encontrado par SOL: {pair.get('pairAddress', 'N/A')}")
            break
        if liquidity_usd > max_liquidity:
            max_liquidity = liquidity_usd
            highest_liquidity_pair = pair

    target_pair = sol_pair if sol_pair else highest_liquidity_pair
    if not target_pair:
        logger.warning(f"[{mint_address}] Não foi possível determinar par alvo (SOL ou >liq) na DexScreener.")
        return None
    logger.debug(f"[{mint_address}] Usando par alvo: {target_pair.get('pairAddress', 'N/A')}")
    return target_pair

def _parse_market_data_from_pair(pair_data: Dict[str, Any], mint_address: str) -> Optional[Dict[str, Any]]:
    """Extrai e valida os dados de mercado necessários do dicionário do par alvo."""
    extracted_data = {}
    is_complete = True
    try:
        price_usd_str = pair_data.get("priceUsd")
        extracted_data["price_usd"] = float(price_usd_str) if price_usd_str is not None else None
        volume_m5_val = (pair_data.get("volume", {}) or {}).get("m5")
        extracted_data["volume_m5"] = float(volume_m5_val) if volume_m5_val is not None else None
        txns_m5 = pair_data.get("txns", {}).get("m5", {}) or {}
        buys_m5_val = txns_m5.get("buys")
        sells_m5_val = txns_m5.get("sells")
        extracted_data["buys_m5"] = int(buys_m5_val) if buys_m5_val is not None else None
        extracted_data["sells_m5"] = int(sells_m5_val) if sells_m5_val is not None else 0
        price_change_h1_val = (pair_data.get("priceChange", {}) or {}).get("h1")
        extracted_data["price_change_h1"] = float(price_change_h1_val) if price_change_h1_val is not None else None
        fdv_val = pair_data.get("fdv")
        extracted_data["fdv"] = float(fdv_val) if fdv_val is not None else None

        required_for_decision = ["price_usd", "volume_m5", "buys_m5", "price_change_h1", "fdv"]
        missing_keys = [k for k in required_for_decision if extracted_data.get(k) is None]
        if missing_keys:
            logger.warning(f"[{mint_address}] Dados essenciais incompletos do par DexScreener. Ausentes: {missing_keys}. Par: {pair_data.get('pairAddress', 'N/A')}")
            return None

        logger.debug(f"[{mint_address}] Dados DexScreener OK: Price={extracted_data['price_usd']:.8f}, Vol5m={extracted_data['volume_m5']:.2f}, Buys5m={extracted_data['buys_m5']}, Sells5m={extracted_data['sells_m5']}, H1%={extracted_data['price_change_h1']:.2f}, FDV={extracted_data['fdv']:,.0f}")
        return extracted_data
    except (ValueError, TypeError) as e:
        logger.error(f"[{mint_address}] Erro conversão dados par DexScreener: {e}. Par: {pair_data.get('pairAddress')}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"[{mint_address}] Erro inesperado processar par DexScreener: {e}. Par: {pair_data.get('pairAddress')}", exc_info=True)
        return None

async def fetch_market_data(mint_address: str, session: aiohttp.ClientSession) -> Optional[Dict[str, Any]]:
    """Busca e processa dados de mercado recentes da API DexScreener."""
    url = f"https://api.dexscreener.com/latest/dex/tokens/{mint_address}"
    try:
        logger.debug(f"[{mint_address}] Buscando dados DexScreener de: {url}")
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=config.API_TIMEOUT)) as response:
            response_status = response.status; response_text = await response.text()
            logger.debug(f"[{mint_address}] Status resposta DexScreener: {response_status}")
            if response_status == 200:
                try: data = json.loads(response_text)
                except json.JSONDecodeError as e:
                    logger.error(f"[{mint_address}] Erro decodificar JSON DexScreener: {e}. Resp: {response_text[:500]}"); return None
                pairs = data.get("pairs")
                if not pairs or not isinstance(pairs, list):
                    logger.warning(f"[{mint_address}] Campo 'pairs' ausente/inválido DexScreener: {data}"); return None
                target_pair = _find_target_pair(pairs, mint_address)
                if not target_pair: return None
                return _parse_market_data_from_pair(target_pair, mint_address)
            elif response_status == 404: logger.warning(f"[{mint_address}] Token não encontrado DexScreener (404). URL: {url}"); return None
            elif response_status == 429: logger.warning(f"[{mint_address}] Rate limit DexScreener (429). URL: {url}"); return None
            else: logger.error(f"[{mint_address}] Erro buscar dados DexScreener: Status {response_status}. URL: {url}. Resp: {response_text[:500]}"); return None
    except asyncio.TimeoutError: logger.warning(f"[{mint_address}] Timeout ({config.API_TIMEOUT}s) buscar dados DexScreener. URL: {url}"); return None
    except aiohttp.ClientConnectionError as e: logger.error(f"[{mint_address}] Erro conexão DexScreener: {e}. URL: {url}"); return None
    except aiohttp.ClientError as e: logger.error(f"[{mint_address}] Erro cliente aiohttp DexScreener: {e}. URL: {url}"); return None
    except Exception as e: logger.error(f"[{mint_address}] Erro inesperado fetch_market_data: {e}. URL: {url}", exc_info=True); return None


def _check_market_criteria(market_data: Dict[str, Any], initial_price: float, mint_address: str, iteration: int) -> Tuple[bool, str]:
    """Avalia se os dados de mercado atuais atendem aos critérios de compra definidos."""
    current_price = market_data["price_usd"]; volume_m5 = market_data["volume_m5"]; buys_m5 = market_data["buys_m5"]
    sells_m5 = market_data["sells_m5"]; price_change_h1 = market_data["price_change_h1"]; fdv = market_data["fdv"]

    price_threshold = initial_price * (1.0 - config.MARKET_PRICE_DROP_TOLERANCE)
    price_ok = current_price >= price_threshold
    volume_ok = volume_m5 >= config.MARKET_MIN_VOLUME_M5
    buys_ok = buys_m5 >= config.MARKET_MIN_BUYS_M5
    total_txns_m5 = buys_m5 + sells_m5
    buy_ratio_ok = (buys_m5 / total_txns_m5 >= config.MARKET_MIN_BUY_SELL_RATIO) if total_txns_m5 > 5 else True
    fdv_ok = fdv < config.MARKET_MAX_FDV
    price_h1_ok = price_change_h1 >= config.MARKET_MIN_H1_PRICE_CHANGE

    log_details = (
        f"  - Preço Atual: {current_price:.8f} (Limite Inf: {price_threshold:.8f}) -> {'OK' if price_ok else 'FAIL'}\n"
        f"  - Volume 5m: {volume_m5:.2f} (Min: {config.MARKET_MIN_VOLUME_M5:.2f}) -> {'OK' if volume_ok else 'FAIL'}\n"
        f"  - Compras 5m: {buys_m5} (Min: {config.MARKET_MIN_BUYS_M5}) -> {'OK' if buys_ok else 'FAIL'}\n"
        f"  - Razão Buy 5m: {(buys_m5 / total_txns_m5 * 100) if total_txns_m5 > 0 else 0:.1f}% (Min: {config.MARKET_MIN_BUY_SELL_RATIO*100:.0f}%) -> {'OK' if buy_ratio_ok else 'FAIL'}\n"
        f"  - FDV: ${fdv:,.0f} (Max: ${config.MARKET_MAX_FDV:,.0f}) -> {'OK' if fdv_ok else 'FAIL'}\n"
        f"  - Preço H1: {price_change_h1:.2f}% (Min: {config.MARKET_MIN_H1_PRICE_CHANGE:.1f}%) -> {'OK' if price_h1_ok else 'FAIL'}"
    )
    logger.debug(f"[{mint_address}] Checando Critérios (Iteração {iteration}):\n{log_details}")
    all_criteria_met = (price_ok and volume_ok and buys_ok and buy_ratio_ok and fdv_ok and price_h1_ok)
    return all_criteria_met, log_details


# --- monitor_market_activity (com import random e datetime corrigido) ---
async def monitor_market_activity(mint_address: str, initial_price: float, session: aiohttp.ClientSession, state: StateManager):
    """Monitora a atividade de mercado e dispara compra se TODOS os critérios forem atendidos."""
    start_time = time.monotonic()
    deadline = start_time + config.MARKET_MONITOR_DURATION
    logger.info(f"[{mint_address}] Iniciando monitoramento de mercado (Duração: {config.MARKET_MONITOR_DURATION}s, Intervalo: {config.MARKET_POLL_INTERVAL}s, Preço Inicial: {initial_price:.8f})")
    await asyncio.sleep(random.uniform(3, 7)) # Delay inicial

    try:
        consecutive_fetch_errors = 0
        max_fetch_errors = 5
        iteration = 0
        buy_attempted = False
        market_criteria_met_on_exit = False

        while time.monotonic() < deadline and not buy_attempted:
            iteration += 1
            if mint_address not in tokens_being_monitored:
                logger.info(f"[{mint_address}] Monitoramento interrompido externamente.")
                break
            if iteration > 1: # Sleep antes de checar, exceto na primeira iteração
                 try: await asyncio.sleep(config.MARKET_POLL_INTERVAL)
                 except asyncio.CancelledError: logger.info(f"[{mint_address}] Monitoramento cancelado durante sleep."); break

            logger.debug(f"[{mint_address}] Iteração {iteration} do monitoramento de mercado...")
            market_data = await fetch_market_data(mint_address, session)

            if market_data:
                consecutive_fetch_errors = 0
                try:
                    market_criteria_met, log_details = _check_market_criteria(market_data, initial_price, mint_address, iteration)
                    market_criteria_met_on_exit = market_criteria_met
                except Exception as e:
                    logger.error(f"[{mint_address}] Erro ao verificar critérios (Iter {iteration}): {e}", exc_info=True)
                    market_criteria_met = False; market_criteria_met_on_exit = False

                if market_criteria_met:
                    logger.info(f"[{mint_address}] CRITÉRIOS DE MERCADO ATINGIDOS (Iteração {iteration})! Tentando enviar ordem Sniperoo...")
                    buy_attempted = True
                    buy_success = await place_sniperoo_buy_order(session=session, mint_address=mint_address)
                    if buy_success: logger.info(f"[{mint_address}] Ordem de compra IMEDIATA enviada. Encerrando monitor.")
                    else:
                        logger.warning(f"[{mint_address}] Falha envio ordem IMEDIATA Sniperoo. Encerrando monitor.")
                        # --- CORREÇÃO: Adicionar token pendente AQUI ---
                        try:
                            state.add_pending_token({
                                "mint": mint_address,
                                "timestamp_utc": datetime.now(timezone.utc).isoformat(), # Usa datetime importado
                                "reason": f"Critérios mercado OK, falha envio Sniperoo (Monitor)",
                                "market_data_at_buy_attempt": market_data,
                                "criteria_log": log_details
                            })
                        except Exception as state_err:
                             logger.error(f"[{mint_address}] Erro adicional ao salvar token pendente após falha no envio: {state_err}")
                    break # Sai do loop
                else: logger.debug(f"[{mint_address}] Critérios mercado não atingidos (Iter {iteration}).")
            else: # Falha fetch
                consecutive_fetch_errors += 1
                logger.warning(f"[{mint_address}] Falha fetch DexScreener (Erro {consecutive_fetch_errors}/{max_fetch_errors}).")
                market_criteria_met_on_exit = False
                if consecutive_fetch_errors >= max_fetch_errors:
                    logger.error(f"[{mint_address}] Desistindo monitoramento - {max_fetch_errors} erros DexScreener.")
                    # --- CORREÇÃO: Adicionar token pendente AQUI ---
                    try:
                        state.add_pending_token({
                            "mint": mint_address,
                            "timestamp_utc": datetime.now(timezone.utc).isoformat(), # Usa datetime importado
                            "reason": f"Monitoramento abortado - {max_fetch_errors} erros DexScreener"
                        })
                    except Exception as state_err:
                         logger.error(f"[{mint_address}] Erro adicional ao salvar token pendente após erros DexScreener: {state_err}")
                    break

        # Verifica timeout sem tentativa de compra
        if time.monotonic() >= deadline and not buy_attempted:
            logger.info(f"[{mint_address}] Timeout monitoramento ({config.MARKET_MONITOR_DURATION}s) sem atingir critérios.")

    except asyncio.CancelledError: logger.info(f"[{mint_address}] Tarefa monitoramento cancelada.")
    except Exception as e:
        logger.error(f"[{mint_address}] Erro fatal tarefa monitoramento: {e}", exc_info=True)
        # --- CORREÇÃO: Adicionar token pendente AQUI ---
        try:
            state.add_pending_token({
                "mint": mint_address,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(), # Usa datetime importado
                "reason": f"Erro inesperado monitor: {e}"
            })
        except Exception as state_err:
             logger.error(f"[{mint_address}] Erro adicional salvar pending após erro monitor: {state_err}")
    finally:
        tokens_being_monitored.discard(mint_address)
        logger.debug(f"[{mint_address}] Removido do monitoramento ativo (finally).")


# --- place_sniperoo_buy_order (sem alterações da última versão) ---

def _build_sniperoo_payload(mint_address: str) -> Optional[Dict[str, Any]]:
    """Constrói o payload para a API Sniperoo com base na configuração."""
    api_key = config.SNIPEROO_API_KEY
    wallet_address = config.SNIPEROO_WALLET_ADDRESS
    use_sniperoo_autobuy = config.SNIPEROO_USE_AUTOBUY_MODE

    if not api_key or not config.SNIPEROO_BUY_ENDPOINT or not wallet_address:
        logger.error(f"[{mint_address}] Configuração Sniperoo essencial ausente (KEY, ENDPOINT, WALLET).")
        return None

    payload = {
        "walletAddresses": [wallet_address],
        "tokenAddress": mint_address,
        "inputAmount": config.SNIPEROO_BUY_AMOUNT_SOL,
        "autoSell": {
            "enabled": config.SNIPEROO_AUTOSELL_ENABLED,
            "strategy": { "strategyName": "simple", "profitPercentage": config.SNIPEROO_AUTOSELL_PROFIT_PCT, "stopLossPercentage": config.SNIPEROO_AUTOSELL_STOPLOSS_PCT }
        },
        "autoBuy": {
            "enabled": use_sniperoo_autobuy,
            "strategy": {
                "priceMetric": { "metricType": config.SNIPEROO_AUTOBUY_PRICE_METRIC_TYPE, "minusOrPlus": config.SNIPEROO_AUTOBUY_PRICE_METRIC_PLUSMINUS, "enabled": use_sniperoo_autobuy and config.SNIPEROO_AUTOBUY_PRICE_METRIC_ENABLED, "minValue": config.SNIPEROO_AUTOBUY_PRICE_METRIC_MIN, "maxValue": config.SNIPEROO_AUTOBUY_PRICE_METRIC_MAX },
                "expiresAt": { "value": config.SNIPEROO_AUTOBUY_EXPIRES_VALUE, "unit": config.SNIPEROO_AUTOBUY_EXPIRES_UNIT }
            }
        }
    }
    if config.SNIPEROO_PRIORITY_FEE > 0: payload["computeUnitPriceMicroLamports"] = config.SNIPEROO_PRIORITY_FEE
    if config.SNIPEROO_SLIPPAGE_BPS > 0: payload["slippageBps"] = config.SNIPEROO_SLIPPAGE_BPS
    if config.SNIPEROO_MAX_RETRIES > 0: payload["maxRetries"] = config.SNIPEROO_MAX_RETRIES
    return payload

async def place_sniperoo_buy_order(session: aiohttp.ClientSession, mint_address: str) -> bool:
    """Envia uma ordem de compra para a Sniperoo API."""
    payload = _build_sniperoo_payload(mint_address)
    if not payload: return False

    endpoint = config.SNIPEROO_BUY_ENDPOINT
    api_key = config.SNIPEROO_API_KEY
    use_sniperoo_autobuy = config.SNIPEROO_USE_AUTOBUY_MODE

    headers = { "Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "application/json" }

    try:
        log_action = "Registrando ordem AutoBuy" if use_sniperoo_autobuy else "Enviando ordem IMEDIATA"
        logger.info(f"[{mint_address}] {log_action} p/ Sniperoo: {config.SNIPEROO_BUY_AMOUNT_SOL:.4f} SOL (Fee: {config.SNIPEROO_PRIORITY_FEE}, Slippage: {config.SNIPEROO_SLIPPAGE_BPS} BPS)")
        logger.debug(f"Payload Sniperoo: {json.dumps(payload)}")
        timeout = aiohttp.ClientTimeout(total=config.API_TIMEOUT + 15)

        async with session.post(endpoint, json=payload, headers=headers, timeout=timeout) as response:
            response_status = response.status; response_text = await response.text()
            try: response_data = json.loads(response_text) if response_text else {}
            except json.JSONDecodeError:
                logger.error(f"[{mint_address}] Resp inválida Sniperoo (ñ JSON). Status: {response_status}, URL: {endpoint}, Resp: {response_text[:500]}"); return False

            if 200 <= response_status < 300:
                order_info = response_data.get("order", response_data); order_id = "N/A"
                if isinstance(order_info, dict): order_id = order_info.get("orderId", order_info.get("id"))
                elif isinstance(response_data, dict): order_id = response_data.get("orderId", response_data.get("id"))
                log_success = "Ordem AutoBuy registrada" if use_sniperoo_autobuy else "Ordem compra enviada"
                logger.info(f"[{mint_address}] {log_success} c/ sucesso via Sniperoo! ID: {order_id}. Resp: {json.dumps(response_data)}"); return True
            else:
                error_message = "Erro desconhecido"; error_details = response_data.get("error"); msg = response_data.get("message")
                if isinstance(error_details, dict): error_message = error_details.get("message", json.dumps(error_details))
                elif isinstance(error_details, str): error_message = error_details
                elif msg: error_message = msg
                elif response_text: error_message = response_text[:500]
                else: error_message = json.dumps(response_data)
                logger.error(f"[{mint_address}] Erro envio/registro ordem Sniperoo. Status: {response_status}, URL: {endpoint}, Erro: {error_message}"); return False
    except asyncio.TimeoutError: logger.error(f"[{mint_address}] Timeout ordem Sniperoo. URL: {endpoint}"); return False
    except aiohttp.ClientConnectionError as e: logger.error(f"[{mint_address}] Erro conexão ordem Sniperoo: {e}. URL: {endpoint}"); return False
    except aiohttp.ClientError as e: logger.error(f"[{mint_address}] Erro cliente aiohttp ordem Sniperoo: {e}. URL: {endpoint}"); return False
    except Exception as e: logger.error(f"[{mint_address}] Erro inesperado ordem Sniperoo: {e}. URL: {endpoint}", exc_info=True); return False