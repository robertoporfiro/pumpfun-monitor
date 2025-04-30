# pumpportal_monitor/market_monitor.py
import asyncio
import logging
import time
import json
from typing import Dict, Any, Optional, Set
import aiohttp

from . import config
from .state_manager import StateManager

logger = logging.getLogger(__name__)

tokens_being_monitored: Set[str] = set()

# --- fetch_market_data ATUALIZADO para buscar mais campos ---
async def fetch_market_data(mint_address: str, session: aiohttp.ClientSession) -> Optional[Dict[str, Any]]:
    """Busca dados de mercado recentes (incluindo sells, h1 change, fdv) da API DexScreener."""
    url = f"https://api.dexscreener.com/latest/dex/tokens/{mint_address}"
    try:
        logger.debug(f"[{mint_address}] Fetching DexScreener data from: {url}")
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=config.API_TIMEOUT)) as response:
            response_status = response.status
            response_text = await response.text()
            logger.debug(f"[{mint_address}] DexScreener response status: {response_status}")

            if response_status == 200:
                try: data = json.loads(response_text)
                except json.JSONDecodeError as e:
                     logger.error(f"[{mint_address}] Erro ao decodificar JSON da DexScreener: {e}. Resposta: {response_text[:500]}")
                     return None

                pairs = data.get("pairs")
                if not pairs or not isinstance(pairs, list):
                    logger.warning(f"[{mint_address}] Nenhum par válido encontrado na DexScreener.")
                    return None

                sol_pair = None
                highest_liquidity_pair = None
                max_liquidity = -1

                for i, pair in enumerate(pairs):
                    if not isinstance(pair, dict): continue
                    quote_token = pair.get("quoteToken", {}) or {}
                    liquidity = pair.get("liquidity", {}) or {}
                    quote_address = quote_token.get("address")
                    is_sol_pair = quote_address == "So11111111111111111111111111111111111111112"
                    liquidity_usd = float(liquidity.get("usd", 0.0))
                    logger.debug(f"[{mint_address}] Avaliando par {i}: {pair.get('pairAddress', 'N/A')} ({ (pair.get('baseToken', {}) or {}).get('symbol', 'N/A')}/{quote_token.get('symbol', 'N/A')}), Liq: ${liquidity_usd:.2f}")
                    if is_sol_pair:
                        sol_pair = pair
                        break
                    if liquidity_usd > max_liquidity:
                        max_liquidity = liquidity_usd
                        highest_liquidity_pair = pair

                target_pair = sol_pair if sol_pair else highest_liquidity_pair

                if not target_pair:
                    logger.warning(f"[{mint_address}] Não foi possível determinar o par alvo na DexScreener.")
                    return None

                logger.debug(f"[{mint_address}] Usando par alvo: {target_pair.get('pairAddress', 'N/A')}")

                # Extrair dados (com tratamento para None)
                price_usd_str = target_pair.get("priceUsd")
                volume_m5_val = (target_pair.get("volume", {}) or {}).get("m5")
                txns_m5 = target_pair.get("txns", {}).get("m5", {}) or {} # Pega o dict m5
                buys_m5_val = txns_m5.get("buys")
                sells_m5_val = txns_m5.get("sells") # <<< NOVO: Pega sells
                price_change = target_pair.get("priceChange", {}) or {}
                price_change_h1_val = price_change.get("h1") # <<< NOVO: Pega h1 change
                fdv_val = target_pair.get("fdv") # <<< NOVO: Pega fdv

                # Conversão e validação
                price_usd = float(price_usd_str) if price_usd_str is not None else None
                volume_m5 = float(volume_m5_val) if volume_m5_val is not None else None
                buys_m5 = int(buys_m5_val) if buys_m5_val is not None else None
                sells_m5 = int(sells_m5_val) if sells_m5_val is not None else 0 # <<< Default 0 para sells
                price_change_h1 = float(price_change_h1_val) if price_change_h1_val is not None else None
                fdv = float(fdv_val) if fdv_val is not None else None

                # Requer os dados essenciais para a decisão de compra
                if price_usd is None or volume_m5 is None or buys_m5 is None or sells_m5 is None or price_change_h1 is None or fdv is None:
                     logger.warning(f"[{mint_address}] Dados incompletos do par alvo DexScreener (Price:{price_usd}, Vol5m:{volume_m5}, Buys5m:{buys_m5}, Sells5m:{sells_m5}, H1%:{price_change_h1}, FDV:{fdv}).")
                     return None

                logger.debug(f"[{mint_address}] Dados DexScreener extraídos: Price={price_usd:.8f}, Vol5m={volume_m5:.2f}, Buys5m={buys_m5}, Sells5m={sells_m5}, H1%={price_change_h1:.2f}, FDV={fdv:,.0f}")
                return {
                    "price_usd": price_usd,
                    "volume_m5": volume_m5,
                    "buys_m5": buys_m5,
                    "sells_m5": sells_m5,         # Retorna sells
                    "price_change_h1": price_change_h1, # Retorna h1 change
                    "fdv": fdv                  # Retorna fdv
                }
            # ... (tratamento de erro 404 e outros status) ...
            elif response_status == 404:
                logger.warning(f"[{mint_address}] Token não encontrado na DexScreener (404).")
                return None
            else:
                logger.error(f"[{mint_address}] Erro ao buscar dados da DexScreener: Status {response_status}. Resposta: {response_text[:500]}")
                return None
    # ... (tratamento de exceptions: TimeoutError, ClientError, etc.) ...
    except asyncio.TimeoutError:
        logger.warning(f"[{mint_address}] Timeout ({config.API_TIMEOUT}s) ao buscar dados da DexScreener.")
        return None
    except aiohttp.ClientError as e:
        logger.error(f"[{mint_address}] Erro de cliente ao buscar dados da DexScreener: {e}")
        return None
    except Exception as e:
        logger.error(f"[{mint_address}] Erro inesperado em fetch_market_data: {e}", exc_info=True)
        return None

# --- monitor_market_activity ATUALIZADO com novos critérios ---
async def monitor_market_activity(mint_address: str, initial_price: float, session: aiohttp.ClientSession, state: StateManager):
    """Monitora a atividade de mercado e dispara compra se TODOS os critérios forem atendidos."""
    start_time = time.monotonic()
    deadline = start_time + config.MARKET_MONITOR_DURATION
    logger.info(f"[{mint_address}] Iniciando monitoramento de mercado (Duração: {config.MARKET_MONITOR_DURATION}s, Intervalo: {config.MARKET_POLL_INTERVAL}s)")
    await asyncio.sleep(5)

    try:
        consecutive_fetch_errors = 0
        max_fetch_errors = 3
        iteration = 0
        market_criteria_met = False

        while time.monotonic() < deadline:
            iteration += 1
            if mint_address not in tokens_being_monitored: # Checa cancelamento externo
                logger.info(f"[{mint_address}] Monitoramento interrompido (não está mais em tokens_being_monitored).")
                break
            await asyncio.sleep(config.MARKET_POLL_INTERVAL)

            logger.debug(f"[{mint_address}] Iteração {iteration} do monitoramento de mercado...")
            market_data = await fetch_market_data(mint_address, session)

            if market_data:
                consecutive_fetch_errors = 0
                current_price = market_data.get("price_usd", 0.0)
                volume_m5 = market_data.get("volume_m5", 0.0)
                buys_m5 = market_data.get("buys_m5", 0)
                # --- Obtem novos dados ---
                sells_m5 = market_data.get("sells_m5", 0)
                price_change_h1 = market_data.get("price_change_h1", 0.0)
                fdv = market_data.get("fdv", float('inf')) # Infinito se não encontrar
                # --- Fim novos dados ---

                # --- Avalia TODOS os critérios ---
                price_threshold = initial_price * (1.0 - config.MARKET_PRICE_DROP_TOLERANCE)
                price_ok = current_price >= price_threshold
                volume_ok = volume_m5 >= config.MARKET_MIN_VOLUME_M5
                buys_ok = buys_m5 >= config.MARKET_MIN_BUYS_M5
                # --- NOVOS CRITÉRIOS ---
                total_txns_m5 = buys_m5 + sells_m5
                buy_ratio_ok = (buys_m5 / total_txns_m5 >= config.MARKET_MIN_BUY_SELL_RATIO) if total_txns_m5 > 5 else True # Tolera se poucas txns
                fdv_ok = fdv < config.MARKET_MAX_FDV
                price_h1_ok = price_change_h1 >= config.MARKET_MIN_H1_PRICE_CHANGE
                # --- FIM NOVOS CRITÉRIOS ---

                # --- LOGS DETALHADOS ---
                logger.debug(f"[{mint_address}] Checando Critérios (Iteração {iteration}):")
                logger.debug(f"  - Preço Atual: {current_price:.8f} (Limite Inf: {price_threshold:.8f}) -> {'OK' if price_ok else 'FALHOU'}")
                logger.debug(f"  - Volume 5m: {volume_m5:.2f} (Min: {config.MARKET_MIN_VOLUME_M5:.2f}) -> {'OK' if volume_ok else 'FALHOU'}")
                logger.debug(f"  - Compras 5m: {buys_m5} (Min: {config.MARKET_MIN_BUYS_M5}) -> {'OK' if buys_ok else 'FALHOU'}")
                logger.debug(f"  - Razão Buy 5m: {(buys_m5 / total_txns_m5 * 100) if total_txns_m5 > 0 else 0:.1f}% (Min: {config.MARKET_MIN_BUY_SELL_RATIO*100:.0f}%) -> {'OK' if buy_ratio_ok else 'FALHOU'}")
                logger.debug(f"  - FDV: ${fdv:,.0f} (Max: ${config.MARKET_MAX_FDV:,.0f}) -> {'OK' if fdv_ok else 'FALHOU'}")
                logger.debug(f"  - Preço H1: {price_change_h1:.2f}% (Min: {config.MARKET_MIN_H1_PRICE_CHANGE:.1f}%) -> {'OK' if price_h1_ok else 'FALHOU'}")
                # --- FIM LOGS ---

                # Atualiza condição final
                market_criteria_met = (
                    price_ok and volume_ok and buys_ok and
                    buy_ratio_ok and fdv_ok and price_h1_ok
                )

                if market_criteria_met:
                    logger.info(f"[{mint_address}] CRITÉRIOS DE MERCADO ATINGIDOS (Iteração {iteration})! Tentando enviar ordem para Sniperoo...")
                    buy_success = await place_sniperoo_buy_order(session=session, mint_address=mint_address)
                    if buy_success: logger.info(f"[{mint_address}] Ordem de compra IMEDIATA enviada via Sniperoo. Encerrando monitoramento.")
                    else: logger.warning(f"[{mint_address}] Falha ao enviar ordem de compra IMEDIATA via Sniperoo. Encerrando monitoramento.")
                    break # Sai do loop while
                else:
                    logger.debug(f"[{mint_address}] Critérios de mercado ainda não atingidos (Iteração {iteration}).")

            else: # Falha ao obter dados de mercado
                consecutive_fetch_errors += 1
                logger.warning(f"[{mint_address}] Não foi possível obter dados de mercado da DexScreener (Erro {consecutive_fetch_errors}/{max_fetch_errors}).")
                if consecutive_fetch_errors >= max_fetch_errors:
                    logger.error(f"[{mint_address}] Desistindo do monitoramento após {max_fetch_errors} falhas consecutivas da API DexScreener.")
                    break

        # Verifica se saiu por timeout sem nunca ter atingido os critérios
        if time.monotonic() >= deadline and not market_criteria_met:
            logger.info(f"[{mint_address}] Tempo limite ({config.MARKET_MONITOR_DURATION}s) de monitoramento atingido sem atender critérios.")

    except asyncio.CancelledError:
         logger.info(f"[{mint_address}] Tarefa de monitoramento de mercado cancelada.")
    except Exception as e:
         logger.error(f"[{mint_address}] Erro na tarefa de monitoramento de mercado: {e}", exc_info=True)
    finally:
        tokens_being_monitored.discard(mint_address)
        logger.debug(f"[{mint_address}] Removido do monitoramento de mercado ativo (bloco finally).")

# --- place_sniperoo_buy_order (sem alterações da última versão) ---
async def place_sniperoo_buy_order(session: aiohttp.ClientSession, mint_address: str) -> bool:
    """
    Envia uma ordem de compra para a Sniperoo API, configurando o payload
    (incluindo autoBuy) com base nas variáveis de configuração globais.
    """
    api_key = config.SNIPEROO_API_KEY
    endpoint = config.SNIPEROO_BUY_ENDPOINT
    wallet_address = config.SNIPEROO_WALLET_ADDRESS
    sol_amount = config.SNIPEROO_BUY_AMOUNT_SOL
    auto_sell_enabled = config.SNIPEROO_AUTOSELL_ENABLED
    auto_sell_profit = config.SNIPEROO_AUTOSELL_PROFIT_PCT
    auto_sell_stoploss = config.SNIPEROO_AUTOSELL_STOPLOSS_PCT
    priority_fee = config.SNIPEROO_PRIORITY_FEE
    slippage_bps = config.SNIPEROO_SLIPPAGE_BPS
    max_retries = config.SNIPEROO_MAX_RETRIES
    use_sniperoo_autobuy = config.SNIPEROO_USE_AUTOBUY_MODE

    if not api_key or not endpoint or not wallet_address:
        logger.error(f"[{mint_address}] Configuração Sniperoo incompleta.")
        return False

    payload = {
        "walletAddresses": [wallet_address],
        "tokenAddress": mint_address,
        "inputAmount": sol_amount,
        "autoSell": {
            "enabled": auto_sell_enabled,
            "strategy": {
                "strategyName": "simple",
                "profitPercentage": auto_sell_profit,
                "stopLossPercentage": auto_sell_stoploss
            }
        },
        "autoBuy": {
            "enabled": use_sniperoo_autobuy,
            "strategy": {
                "priceMetric": {
                    "metricType": config.SNIPEROO_AUTOBUY_PRICE_METRIC_TYPE,
                    "minusOrPlus": config.SNIPEROO_AUTOBUY_PRICE_METRIC_PLUSMINUS,
                    "enabled": use_sniperoo_autobuy and config.SNIPEROO_AUTOBUY_PRICE_METRIC_ENABLED,
                    "minValue": config.SNIPEROO_AUTOBUY_PRICE_METRIC_MIN,
                    "maxValue": config.SNIPEROO_AUTOBUY_PRICE_METRIC_MAX
                },
                "expiresAt": {
                    "value": config.SNIPEROO_AUTOBUY_EXPIRES_VALUE,
                    "unit": config.SNIPEROO_AUTOBUY_EXPIRES_UNIT
                }
            }
        }
    }

    if priority_fee > 0: payload["computeUnitPriceMicroLamports"] = priority_fee
    if slippage_bps > 0: payload["slippageBps"] = slippage_bps
    if max_retries > 0: payload["maxRetries"] = max_retries

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    try:
        log_action = "Registrando ordem AutoBuy" if use_sniperoo_autobuy else "Enviando ordem de compra IMEDIATA"
        logger.info(f"[{mint_address}] {log_action} para Sniperoo: {sol_amount:.4f} SOL (Fee: {priority_fee}, Slippage: {slippage_bps} BPS)")
        logger.debug(f"Payload Sniperoo: {json.dumps(payload)}")

        async with session.post(endpoint, json=payload, headers=headers, timeout=config.API_TIMEOUT + 10) as response:
            response_text = await response.text()
            try: response_data = json.loads(response_text) if response_text else {}
            except json.JSONDecodeError:
                logger.error(f"[{mint_address}] Resposta inválida (não JSON) da API Sniperoo. Status: {response.status}, Resposta: {response_text[:500]}")
                return False

            if 200 <= response.status < 300:
                order_info = response_data.get("order", response_data)
                order_id = order_info.get("orderId", order_info.get("id", "N/A")) if isinstance(order_info, dict) else "N/A"
                log_success_action = "Ordem AutoBuy registrada" if use_sniperoo_autobuy else "Ordem de compra imediata enviada"
                logger.info(f"[{mint_address}] {log_success_action} com sucesso via Sniperoo! ID: {order_id}. Resposta: {response_data}")
                return True
            else:
                error_detail = response_data.get("error", {}).get("message") if isinstance(response_data.get("error"), dict) else None
                error_msg = response_data.get("message", error_detail if error_detail else response_text)
                logger.error(f"[{mint_address}] Erro ao enviar/registrar ordem para Sniperoo. Status: {response.status}, Erro: {error_msg}")
                return False
    except asyncio.TimeoutError:
         logger.error(f"[{mint_address}] Timeout ao enviar/registrar ordem para Sniperoo.")
         return False
    except aiohttp.ClientError as e:
        logger.error(f"[{mint_address}] Erro de conexão/cliente ao enviar/registrar ordem para Sniperoo: {e}")
        return False
    except Exception as e:
        logger.error(f"[{mint_address}] Erro inesperado ao enviar/registrar ordem para Sniperoo: {e}", exc_info=True)
        return False