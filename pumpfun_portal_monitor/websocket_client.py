# pumpportal_monitor/websocket_client.py
import asyncio
import json
import logging
import random
from datetime import datetime, timezone
from typing import Dict, Any
import aiohttp
import websockets

from . import config
from .state_manager import StateManager
from .token_checker import check_token_reliability
from .market_monitor import monitor_market_activity, place_sniperoo_buy_order, fetch_market_data, tokens_being_monitored
from .utils import format_analysis_output

logger = logging.getLogger(__name__)

async def _process_graduated_token(data: Dict[str, Any], session: aiohttp.ClientSession, state: StateManager):
    """Processa token graduado, verifica segurança e decide entre monitoramento de mercado ou ordem AutoBuy."""
    mint_address = data.get("mint")
    signature = data.get("signature", "Desconhecido")
    timestamp_utc = datetime.now(timezone.utc)

    if not mint_address or not isinstance(mint_address, str) or len(mint_address) not in (43, 44):
        actual_len = len(mint_address) if isinstance(mint_address, str) else 'N/A'
        logger.warning(f"Endereço de token inválido ({actual_len} chars) ou ausente: {mint_address}. Sig: {signature}")
        return

    if state.is_token_processed(mint_address) or mint_address in tokens_being_monitored:
        logger.debug(f"Token {mint_address} já processado ou monitorado. Ignorando.")
        return

    state.add_processed_token(mint_address)
    logger.info(f"\n=== Novo token graduado detectado! ===")
    logger.info(f"Endereço: {mint_address}")
    logger.info(f"Assinatura: {signature}")
    logger.info(f"Horário UTC: {timestamp_utc.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"Link Solscan: https://solscan.io/token/{mint_address}")

    # 1. Análise de Segurança Inicial (RugCheck)
    check_result = await check_token_reliability(mint_address, session)

    if check_result["status"] == "success":
        analysis_summary = format_analysis_output(mint_address, check_result)
        logger.info(analysis_summary) # Loga o resumo completo

        # 2. Extrair Dados e Realizar Filtros de Segurança
        raw_data = check_result.get("raw_data", {})
        score_norm = raw_data.get("score_normalised")
        is_rugged = raw_data.get("rugged")
        mint_auth_none = raw_data.get("mintAuthority") is None
        freeze_auth_none = raw_data.get("freezeAuthority") is None
        risks_detected = raw_data.get("risks", [])
        initial_price_rugcheck = raw_data.get("price")
        creator = raw_data.get("creator") # Endereço do criador
        top_holders = raw_data.get("topHolders", []) or []
        known_accounts = raw_data.get("knownAccounts", {}) or {}

        # Calcular LP bloqueada
        lp_locked_pct = 0
        markets = raw_data.get("markets", []) or []
        if markets and isinstance(markets, list) and len(markets) > 0 and markets[0].get("marketType") == "pump_fun":
             lp_info = markets[0].get("lp", {}) or {}
             lp_locked_pct = lp_info.get("lpLockedPct", 0)

        # --- VERIFICAÇÕES ADICIONAIS ---
        # Saldo do Criador
        creator_balance_raw = raw_data.get("creatorBalance", 0)
        creator_holds_too_much = False
        creator_holding_pct_calculated = 0.0
        token_supply_raw = (raw_data.get("token", {}) or {}).get("supply")
        if isinstance(creator_balance_raw, (int, float)) and creator_balance_raw > 0:
            if isinstance(token_supply_raw, (int, float)) and token_supply_raw > 0:
                creator_holding_pct_calculated = (creator_balance_raw / token_supply_raw) * 100
                if creator_holding_pct_calculated > config.FILTER_MAX_CREATOR_HOLDING_PCT:
                    creator_holds_too_much = True
                    logger.warning(f"[{mint_address}] Saldo do criador ({creator_holding_pct_calculated:.2f}%) excede limite ({config.FILTER_MAX_CREATOR_HOLDING_PCT}%).")
            else:
                logger.warning(f"[{mint_address}] Saldo do criador > 0, mas supply indisponível. Rejeitando.")
                creator_holds_too_much = True

        # Máximo Holder Único
        single_holder_exceeds_limit = False
        max_single_holder_pct_found = 0.0
        # Identifica AMM/Criador para exclusão
        creator_address_from_known = None
        amm_addresses_from_known = set()
        lp_pool_address = (markets[0].get("liquidityA") if markets else None)
        owner_of_lp_pool_address = ((markets[0].get("liquidityAAccount", {}) or {}).get("owner") if markets else None)

        for addr, info in known_accounts.items():
            if info.get("type") == "CREATOR": creator_address_from_known = addr
            elif info.get("type") == "AMM": amm_addresses_from_known.add(addr)
        if lp_pool_address: amm_addresses_from_known.add(lp_pool_address)
        if owner_of_lp_pool_address: amm_addresses_from_known.add(owner_of_lp_pool_address)

        if isinstance(top_holders, list):
            for holder in top_holders:
                holder_addr = holder.get("address")
                if holder_addr in amm_addresses_from_known or holder_addr == creator or holder_addr == creator_address_from_known:
                    continue
                try:
                    current_pct = float(holder.get("pct", 0.0))
                    max_single_holder_pct_found = max(max_single_holder_pct_found, current_pct)
                    if current_pct > config.FILTER_MAX_SINGLE_HOLDER_PCT:
                        single_holder_exceeds_limit = True
                        logger.warning(f"[{mint_address}] Holder único {holder_addr} excede limite ({current_pct:.2f}% > {config.FILTER_MAX_SINGLE_HOLDER_PCT}%).")
                        break
                except (ValueError, TypeError): pass

        # Insiders Detectados pela API
        insiders_detected = raw_data.get("graphInsidersDetected", 0)
        insiders_ok = insiders_detected <= config.FILTER_MAX_INSIDERS_DETECTED
        if not insiders_ok:
             logger.warning(f"[{mint_address}] Número de insiders detectados ({insiders_detected}) excede limite ({config.FILTER_MAX_INSIDERS_DETECTED}).")

        # Liquidez Inicial
        initial_liq_ok = True # Default True se filtro <= 0
        if config.MIN_INITIAL_LIQUIDITY > 0:
             initial_liq = raw_data.get("totalMarketLiquidity", 0)
             initial_liq_ok = initial_liq >= config.MIN_INITIAL_LIQUIDITY

        # --- FIM VERIFICAÇÕES ADICIONAIS ---

        # Avaliação Final dos Filtros
        passes_safety_checks = (
            is_rugged is False and mint_auth_none and freeze_auth_none and
            lp_locked_pct == 100 and not risks_detected and
            (score_norm is not None and isinstance(score_norm, (int, float)) and score_norm >= config.MIN_RUGCHECK_SCORE) and
            initial_liq_ok and # Usa a flag calculada
            not creator_holds_too_much and
            not single_holder_exceeds_limit and # Adiciona filtro de holder único
            insiders_ok # Adiciona filtro de insiders detectados
        )

        if passes_safety_checks:
            # --- DECISÃO DE FLUXO ---
            if config.SNIPEROO_USE_AUTOBUY_MODE:
                logger.info(f"[{mint_address}] Passou filtros de segurança. Modo AutoBuy Sniperoo ATIVO.")
                logger.debug(f"[{mint_address}] Buscando preço atual na DexScreener antes de registrar ordem AutoBuy...")
                current_market_data = await fetch_market_data(mint_address, session)
                current_price_str = "N/A"
                if current_market_data and current_market_data.get("price_usd") is not None:
                    current_price_str = f"{current_market_data['price_usd']:.8f}"
                    logger.info(f"[{mint_address}] Preço atual DexScreener: {current_price_str} USD.")
                else:
                    logger.warning(f"[{mint_address}] Não foi possível obter preço atual da DexScreener antes de registrar ordem.")

                logger.info(f"[{mint_address}] Enviando ordem AutoBuy para Sniperoo (Preço Dex: {current_price_str})...")
                buy_success = await place_sniperoo_buy_order(session=session, mint_address=mint_address)
                if buy_success:
                     logger.info(f"[{mint_address}] Ordem AutoBuy registrada com sucesso via Sniperoo.")
                else:
                     logger.warning(f"[{mint_address}] Falha ao registrar ordem AutoBuy via Sniperoo.")
                     state.add_pending_token({"mint": mint_address, "signature": signature, "timestamp_utc": timestamp_utc.isoformat(), "reason": "Seguro, mas falha ao registrar ordem AutoBuy"})

            else: # Modo Monitoramento Bot
                if initial_price_rugcheck is not None and isinstance(initial_price_rugcheck, (int, float)) and initial_price_rugcheck > 1e-18:
                    if mint_address not in tokens_being_monitored:
                         tokens_being_monitored.add(mint_address)
                         logger.info(f"[{mint_address}] Passou filtros de segurança. Modo Monitoramento Bot ATIVO. Iniciando monitoramento (preço inicial RugCheck: {initial_price_rugcheck:.8f})...")
                         asyncio.create_task(monitor_market_activity(mint_address, float(initial_price_rugcheck), session, state), name=f"MarketMonitor_{mint_address[:6]}")
                    else: logger.debug(f"[{mint_address}] Já está sendo monitorado (verificação dupla).")
                else:
                     logger.warning(f"[{mint_address}] Passou filtros (Modo Monitor), mas preço inicial RugCheck inválido/zero ({initial_price_rugcheck}). Não monitorando.")
                     state.add_pending_token({"mint": mint_address, "signature": signature, "timestamp_utc": timestamp_utc.isoformat(), "reason": "Seguro (Modo Monitor), mas preço inicial RugCheck inválido/zero"})
        else:
            # Falhou nos filtros de segurança - Log Aprimorado
            reason_log = (
                f"(ScoreN: {score_norm if score_norm is not None else 'N/A'}, "
                f"Rugged: {is_rugged}, MintN: {mint_auth_none}, FreezeN: {freeze_auth_none}, "
                f"LP: {lp_locked_pct:.0f}%, Risks: {bool(risks_detected)}, "
                f"CreatorBalOK: {not creator_holds_too_much} ({creator_holding_pct_calculated:.2f}%), "
                f"InitLiqOK: {initial_liq_ok}, "
                f"SingleHolderOK: {not single_holder_exceeds_limit} (Max: {max_single_holder_pct_found:.2f}%), "
                f"InsidersOK: {insiders_ok} ({insiders_detected}))"
            )
            logger.info(f"[{mint_address}] NÃO passou nos filtros de segurança pré-monitoramento {reason_log}.")
            state.add_pending_token({"mint": mint_address, "signature": signature, "timestamp_utc": timestamp_utc.isoformat(), "reason": f"Falhou filtros segurança {reason_log}"})

    elif check_result["status"] in ["skipped", "timeout", "error"]:
         reason = check_result.get("reason", "Unknown failure")
         status = check_result.get("status", "error")
         logger.warning(f"[{mint_address}] Falha/Skip na verificação RugCheck ({status}): {reason}")
         logger.info(f"[{mint_address}] Verifique manualmente: https://rugcheck.xyz/tokens/{mint_address}")
         state.add_pending_token({"mint": mint_address, "signature": signature, "timestamp_utc": timestamp_utc.isoformat(), "reason": f"RugCheck API check failed/skipped ({status}): {reason}"})


# --- _handle_message e run_websocket_client (sem alterações da última versão) ---
async def _handle_message(message: str, session: aiohttp.ClientSession, state: StateManager):
    # (Código igual ao anterior)
    try:
        data = json.loads(message)
        logger.debug(f"Mensagem recebida: {json.dumps(data)}")

        if data.get("txType") == "migrate":
            await _process_graduated_token(data, session, state)
        else:
             log_msg = f"Mensagem ignorada: txType={data.get('txType', 'N/A')}"
             if 'method' in data: log_msg += f", method={data.get('method')}"
             logger.debug(log_msg)

    except json.JSONDecodeError:
        logger.error(f"Erro ao decodificar mensagem JSON: {message[:500]}...")
    except Exception as e:
        logger.error(f"Erro inesperado ao processar mensagem: {e}", exc_info=True)

async def run_websocket_client(session: aiohttp.ClientSession, state: StateManager):
    # (Código igual ao anterior)
    reconnect_interval = config.RECONNECT_INTERVAL_MIN
    attempt = 0
    current_task = asyncio.current_task()

    while not getattr(current_task, '_must_cancel', False):
        try:
            logger.info(f"Tentando conectar ao WebSocket: {config.WS_URL} (Tentativa {attempt + 1})")
            async with websockets.connect(config.WS_URL, ping_interval=30, ping_timeout=20, close_timeout=10) as ws:
                logger.info("Conexão WebSocket estabelecida com sucesso!")
                attempt = 0
                reconnect_interval = config.RECONNECT_INTERVAL_MIN

                payload = {"method": "subscribeMigration", "keys": []}
                await ws.send(json.dumps(payload))
                logger.info("Inscrito em eventos de migração (subscribeMigration)")

                async for message in ws:
                    if getattr(current_task, '_must_cancel', False):
                        logger.info("Cancelamento solicitado, parando de processar mensagens WebSocket.")
                        break
                    await _handle_message(str(message), session, state)

                if getattr(current_task, '_must_cancel', False): break

        except asyncio.CancelledError:
            logger.info("Tarefa WebSocket cancelada durante conexão/recebimento.")
            break
        except (websockets.exceptions.ConnectionClosedError, websockets.exceptions.ConnectionClosedOK) as e:
            logger.warning(f"Conexão WebSocket fechada: Código={e.code}, Razão='{e.reason}'")
        except ConnectionRefusedError:
             logger.error("Erro de conexão: Conexão recusada pelo servidor.")
        except websockets.exceptions.InvalidURI:
             logger.error(f"Erro fatal: URI do WebSocket inválida: {config.WS_URL}")
             break
        except websockets.exceptions.PayloadTooBig:
             logger.error("Erro: Payload da mensagem WebSocket muito grande.")
        except OSError as e:
             logger.error(f"Erro de Rede/OS na conexão WebSocket: {e}")
        except Exception as e:
            logger.error(f"Erro inesperado no loop do WebSocket: {e}", exc_info=True)

        if getattr(current_task, '_must_cancel', False):
             logger.info("Cancelamento solicitado antes da reconexão.")
             break

        attempt += 1
        wait_time = min(reconnect_interval * (2 ** min(attempt, 5)), config.RECONNECT_INTERVAL_MAX)
        wait_time += random.uniform(0, 1)
        logger.info(f"Tentando reconectar em {wait_time:.2f} segundos...")
        try:
            await asyncio.sleep(wait_time)
        except asyncio.CancelledError:
             logger.info("Reconexão cancelada durante o sleep.")
             break

    logger.info("Tarefa run_websocket_client finalizada.")