# pumpportal_monitor/market_monitor.py
import asyncio
import logging
import time
import json
from typing import Dict, Any, Optional, Set
import aiohttp

# Imports relativos dentro do pacote
from . import config
from .state_manager import StateManager

logger = logging.getLogger(__name__)

# Conjunto global para rastrear tokens monitorados
tokens_being_monitored: Set[str] = set()

# --- fetch_market_data (permanece igual à versão anterior) ---
async def fetch_market_data(mint_address: str, session: aiohttp.ClientSession) -> Optional[Dict[str, Any]]:
    """Busca dados de mercado recentes para um token da API DexScreener."""
    url = f"https://api.dexscreener.com/latest/dex/tokens/{mint_address}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=config.API_TIMEOUT)) as response:
            if response.status == 200:
                data = await response.json()
                pairs = data.get("pairs")
                if not pairs or not isinstance(pairs, list):
                    logger.warning(f"[{mint_address}] Nenhum par válido encontrado na DexScreener.")
                    return None

                sol_pair = None
                highest_liquidity_pair = None
                max_liquidity = -1

                for pair in pairs:
                    if not isinstance(pair, dict): continue

                    quote_token = pair.get("quoteToken", {}) or {}
                    liquidity = pair.get("liquidity", {}) or {}

                    quote_address = quote_token.get("address")
                    is_sol_pair = quote_address == "So11111111111111111111111111111111111111112"
                    liquidity_usd = float(liquidity.get("usd", 0.0))

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

                price_usd_str = target_pair.get("priceUsd")
                volume_m5_val = (target_pair.get("volume", {}) or {}).get("m5")
                buys_m5_val = (target_pair.get("txns", {}).get("m5", {}) or {}).get("buys")

                price_usd = float(price_usd_str) if price_usd_str is not None else None
                volume_m5 = float(volume_m5_val) if volume_m5_val is not None else None
                buys_m5 = int(buys_m5_val) if buys_m5_val is not None else None

                if price_usd is None or volume_m5 is None or buys_m5 is None:
                     logger.warning(f"[{mint_address}] Dados incompletos do par alvo na DexScreener.")
                     return None

                logger.debug(f"[{mint_address}] DexScreener Data: Price={price_usd:.8f}, Vol5m={volume_m5:.2f}, Buys5m={buys_m5}")
                return {
                    "price_usd": price_usd,
                    "volume_m5": volume_m5,
                    "buys_m5": buys_m5,
                }
            elif response.status == 404:
                logger.warning(f"[{mint_address}] Token não encontrado na DexScreener (404).")
                return None
            else:
                logger.error(f"[{mint_address}] Erro ao buscar dados da DexScreener: Status {response.status}")
                return None
    except asyncio.TimeoutError:
        logger.warning(f"[{mint_address}] Timeout ao buscar dados da DexScreener.")
        return None
    except aiohttp.ClientError as e:
        logger.error(f"[{mint_address}] Erro de cliente ao buscar dados da DexScreener: {e}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"[{mint_address}] Erro ao decodificar JSON da DexScreener: {e}")
        return None
    except Exception as e:
        logger.error(f"[{mint_address}] Erro inesperado em fetch_market_data: {e}", exc_info=True)
        return None

# --- monitor_market_activity (permanece igual à versão anterior) ---
async def monitor_market_activity(mint_address: str, initial_price: float, session: aiohttp.ClientSession, state: StateManager):
    """Monitora a atividade de mercado de um token e dispara a compra se os critérios forem atendidos."""
    start_time = time.monotonic()
    deadline = start_time + config.MARKET_MONITOR_DURATION
    logger.info(f"[{mint_address}] Iniciando monitoramento de mercado (duração: {config.MARKET_MONITOR_DURATION}s)")

    try:
        consecutive_fetch_errors = 0
        max_fetch_errors = 3

        while time.monotonic() < deadline:
            await asyncio.sleep(config.MARKET_POLL_INTERVAL)

            market_data = await fetch_market_data(mint_address, session)

            if market_data:
                consecutive_fetch_errors = 0
                current_price = market_data.get("price_usd", 0.0)
                volume_m5 = market_data.get("volume_m5", 0.0)
                buys_m5 = market_data.get("buys_m5", 0)

                price_ok = current_price >= initial_price * (1.0 - config.MARKET_PRICE_DROP_TOLERANCE)
                volume_ok = volume_m5 >= config.MARKET_MIN_VOLUME_M5
                buys_ok = buys_m5 >= config.MARKET_MIN_BUYS_M5

                logger.debug(f"[{mint_address}] Market Check: PriceOK={price_ok} ({current_price:.6f} vs init {initial_price:.6f} * {1.0 - config.MARKET_PRICE_DROP_TOLERANCE:.2f}), VolOK={volume_ok} ({volume_m5:.2f} vs {config.MARKET_MIN_VOLUME_M5}), BuysOK={buys_ok} ({buys_m5} vs {config.MARKET_MIN_BUYS_M5})")

                if price_ok and volume_ok and buys_ok:
                    logger.info(f"[{mint_address}] CRITÉRIOS DE MERCADO ATINGIDOS! Tentando enviar ordem para Sniperoo...")
                    # Chama a função para enviar a ordem (que agora usa configs globais)
                    buy_success = await place_sniperoo_buy_order(session=session, mint_address=mint_address)
                    if buy_success:
                        logger.info(f"[{mint_address}] Ordem enviada/registrada via Sniperoo (monitor de mercado).")
                        # state.add_bought_token(mint_address) # Exemplo
                    else:
                        logger.warning(f"[{mint_address}] Falha ao enviar/registrar ordem via Sniperoo após critérios de mercado.")
                    break # Sai do loop após tentar enviar a ordem

            else:
                consecutive_fetch_errors += 1
                logger.warning(f"[{mint_address}] Não foi possível obter dados de mercado (Erro {consecutive_fetch_errors}/{max_fetch_errors}).")
                if consecutive_fetch_errors >= max_fetch_errors:
                    logger.error(f"[{mint_address}] Desistindo do monitoramento após {max_fetch_errors} falhas da API DexScreener.")
                    break

        # Verifica se saiu por timeout sem atingir critérios
        # Adiciona verificação 'market_data is not None' para evitar erro se falhou na ultima iteração
        market_criteria_met = market_data is not None and price_ok and volume_ok and buys_ok
        if time.monotonic() >= deadline and not market_criteria_met:
            logger.info(f"[{mint_address}] Tempo limite de monitoramento de mercado atingido sem atender aos critérios.")

    except asyncio.CancelledError:
         logger.info(f"[{mint_address}] Tarefa de monitoramento de mercado cancelada.")
    except Exception as e:
         logger.error(f"[{mint_address}] Erro na tarefa de monitoramento de mercado: {e}", exc_info=True)
    finally:
        tokens_being_monitored.discard(mint_address)
        logger.debug(f"[{mint_address}] Removido do monitoramento de mercado ativo.")

# --- Função de Compra Sniperoo ATUALIZADA ---
async def place_sniperoo_buy_order(session: aiohttp.ClientSession, mint_address: str) -> bool:
    """
    Envia uma ordem de compra imediata via Sniperoo API usando configurações globais.
    FORÇA autoBuy.enabled para False.
    """
    # Busca as configurações necessárias
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

    # Validações essenciais
    if not api_key or not endpoint or not wallet_address:
        logger.error(f"[{mint_address}] Configuração Sniperoo incompleta (KEY, ENDPOINT ou WALLET faltando).")
        return False

    # Monta o Payload base
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
            "enabled": False, # <<< FORÇADO PARA FALSE
            "strategy": { # Valores padrão/irrelevantes
                "priceMetric": {"metricType": "price_change", "minusOrPlus": "minus", "enabled": False, "minValue": 0, "maxValue": 0},
                "expiresAt": {"value": 1, "unit": "minutes"}
            }
        }
    }

    # Adiciona campos opcionais se configurados
    if priority_fee > 0:
        payload["computeUnitPriceMicroLamports"] = priority_fee
    if slippage_bps > 0:
        payload["slippageBps"] = slippage_bps
    if max_retries > 0:
        payload["maxRetries"] = max_retries

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    try:
        logger.info(f"[{mint_address}] Enviando ordem de compra IMEDIATA para Sniperoo: {sol_amount:.4f} SOL (Fee: {priority_fee}, Slippage: {slippage_bps} BPS)")
        logger.debug(f"Payload Sniperoo: {json.dumps(payload)}")

        async with session.post(endpoint, json=payload, headers=headers, timeout=config.API_TIMEOUT + 10) as response:
            response_text = await response.text()
            try:
                response_data = json.loads(response_text) if response_text else {}
            except json.JSONDecodeError:
                logger.error(f"[{mint_address}] Resposta inválida (não JSON) da API Sniperoo. Status: {response.status}, Resposta: {response_text[:500]}")
                return False

            if 200 <= response.status < 300:
                order_info = response_data.get("order", response_data)
                order_id = order_info.get("orderId", order_info.get("id", "N/A")) if isinstance(order_info, dict) else "N/A"
                logger.info(f"[{mint_address}] Ordem de compra enviada com sucesso via Sniperoo! ID: {order_id}. Resposta: {response_data}")
                return True
            else:
                error_detail = response_data.get("error", {}).get("message") if isinstance(response_data.get("error"), dict) else None
                error_msg = response_data.get("message", error_detail if error_detail else response_text)
                logger.error(f"[{mint_address}] Erro ao enviar ordem para Sniperoo. Status: {response.status}, Erro: {error_msg}")
                return False
    except asyncio.TimeoutError:
         logger.error(f"[{mint_address}] Timeout ao enviar ordem para Sniperoo.")
         return False
    except aiohttp.ClientError as e:
        logger.error(f"[{mint_address}] Erro de conexão/cliente ao enviar ordem para Sniperoo: {e}")
        return False
    except Exception as e:
        logger.error(f"[{mint_address}] Erro inesperado ao enviar ordem para Sniperoo: {e}", exc_info=True)
        return False