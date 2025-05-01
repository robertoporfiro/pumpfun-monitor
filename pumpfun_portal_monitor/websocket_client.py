# pumpportal_monitor/websocket_client.py
import asyncio
import json
import logging
import random
from datetime import datetime, timezone
from typing import Dict, Any, Tuple, Optional
import aiohttp
import websockets

# Imports relativos
from . import config
from .state_manager import StateManager
from .token_checker import check_token_reliability
# Importa do market_monitor (agora refatorado)
from .market_monitor import monitor_market_activity, place_sniperoo_buy_order, fetch_market_data, tokens_being_monitored
from .utils import format_analysis_output

logger = logging.getLogger(__name__)

# --- Funções Auxiliares para _process_graduated_token ---

def _validate_incoming_token_data(data: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Valida os dados iniciais do token recebido via WebSocket."""
    mint_address = data.get("mint")
    signature = data.get("signature", "Desconhecido")

    if not mint_address or not isinstance(mint_address, str) or len(mint_address) not in (43, 44):
        actual_len = len(mint_address) if isinstance(mint_address, str) else 'N/A'
        logger.warning(f"Endereço de token inválido ({actual_len} chars) ou ausente: {mint_address}. Sig: {signature}")
        return None, signature # Retorna None para mint se inválido
    return mint_address, signature

def _calculate_safety_metrics(raw_data: Dict[str, Any], mint_address: str) -> Dict[str, Any]:
    """Calcula métricas de segurança e contexto a partir dos dados brutos da API RugCheck."""
    metrics = {
        "score_norm": raw_data.get("score_normalised"),
        "is_rugged": raw_data.get("rugged"),
        "mint_auth_none": raw_data.get("mintAuthority") is None,
        "freeze_auth_none": raw_data.get("freezeAuthority") is None,
        "risks_detected": raw_data.get("risks", []) or [], # Garante lista
        "initial_price_rugcheck": raw_data.get("price"),
        "creator": raw_data.get("creator"),
        "top_holders": raw_data.get("topHolders", []) or [],
        "known_accounts": raw_data.get("knownAccounts", {}) or {},
        "lp_locked_pct": 0.0, # Default float
        "initial_liq_value": 0.0, # Default float
        "creator_holds_too_much": False,
        "creator_holding_pct_calculated": 0.0,
        "single_holder_exceeds_limit": False,
        "max_single_holder_pct_found": 0.0,
        "insiders_detected": raw_data.get("graphInsidersDetected", 0),
        "insiders_ok": True, # Default OK
        "initial_liq_ok": True, # Default OK
    }

    # Liquidez e detalhes do pool
    markets = raw_data.get("markets", []) or []
    lp_pool_address = None
    owner_of_lp_pool_address = None
    if markets and isinstance(markets, list) and len(markets) > 0 and markets[0].get("marketType") == "pump_fun":
        lp_info = markets[0].get("lp", {}) or {}
        metrics["lp_locked_pct"] = float(lp_info.get("lpLockedPct", 0.0)) # Garante float
        lp_pool_address = markets[0].get("liquidityA") # Conta de token
        owner_of_lp_pool_address = (markets[0].get("liquidityAAccount", {}) or {}).get("owner") # Carteira dona

    # Liquidez Inicial
    initial_liq_raw = raw_data.get("totalMarketLiquidity")
    if isinstance(initial_liq_raw, (int, float)):
        metrics["initial_liq_value"] = float(initial_liq_raw)
        if config.MIN_INITIAL_LIQUIDITY > 0:
            metrics["initial_liq_ok"] = metrics["initial_liq_value"] >= config.MIN_INITIAL_LIQUIDITY
    else:
        metrics["initial_liq_ok"] = False # Falha se não for numérico e filtro ativo
        logger.warning(f"[{mint_address}] Valor de liquidez inicial inválido: {initial_liq_raw}")


    # Saldo do Criador
    creator_balance_raw = raw_data.get("creatorBalance", 0)
    token_supply_raw = (raw_data.get("token", {}) or {}).get("supply")
    if isinstance(creator_balance_raw, (int, float)) and creator_balance_raw > 0:
        if isinstance(token_supply_raw, (int, float)) and token_supply_raw > 0:
            metrics["creator_holding_pct_calculated"] = (creator_balance_raw / token_supply_raw) * 100
            if metrics["creator_holding_pct_calculated"] > config.FILTER_MAX_CREATOR_HOLDING_PCT:
                metrics["creator_holds_too_much"] = True
                # Aviso é logado aqui para contexto imediato
                logger.warning(f"[{mint_address}] Saldo do criador ({metrics['creator_holding_pct_calculated']:.2f}%) excede limite ({config.FILTER_MAX_CREATOR_HOLDING_PCT}%).")
        else:
            logger.warning(f"[{mint_address}] Saldo do criador > 0, mas supply indisponível/inválido ({token_supply_raw}). Rejeitando verificação de saldo.")
            metrics["creator_holds_too_much"] = True # Rejeita por segurança

    # Identifica endereços AMM/Criador para exclusão da análise de holder único
    creator_address_from_known = None
    amm_addresses_from_known = set()
    for addr, info in metrics["known_accounts"].items():
        if info.get("type") == "CREATOR": creator_address_from_known = addr
        elif info.get("type") == "AMM": amm_addresses_from_known.add(addr)
    if lp_pool_address: amm_addresses_from_known.add(lp_pool_address)
    if owner_of_lp_pool_address: amm_addresses_from_known.add(owner_of_lp_pool_address)

    # Máximo Holder Único
    if isinstance(metrics["top_holders"], list):
        for holder in metrics["top_holders"]:
            holder_addr = holder.get("address")
            # Pula endereços conhecidos (AMM, Criador)
            if holder_addr in amm_addresses_from_known or holder_addr == metrics["creator"] or holder_addr == creator_address_from_known:
                continue
            try:
                current_pct = float(holder.get("pct", 0.0))
                metrics["max_single_holder_pct_found"] = max(metrics["max_single_holder_pct_found"], current_pct)
                if current_pct > config.FILTER_MAX_SINGLE_HOLDER_PCT:
                    metrics["single_holder_exceeds_limit"] = True
                    logger.warning(f"[{mint_address}] Holder único {holder_addr} excede limite ({current_pct:.2f}% > {config.FILTER_MAX_SINGLE_HOLDER_PCT}%).")
                    break # Falhou, não precisa checar mais
            except (ValueError, TypeError):
                 logger.warning(f"[{mint_address}] Valor de pct inválido para holder {holder_addr}: {holder.get('pct')}")
                 pass # Ignora holder com pct inválido

    # Insiders OK
    metrics["insiders_ok"] = metrics["insiders_detected"] <= config.FILTER_MAX_INSIDERS_DETECTED
    if not metrics["insiders_ok"]:
        logger.warning(f"[{mint_address}] Número de insiders detectados ({metrics['insiders_detected']}) excede limite ({config.FILTER_MAX_INSIDERS_DETECTED}).")

    return metrics

def _evaluate_safety_checks(metrics: Dict[str, Any]) -> Tuple[bool, str]:
    """Avalia os filtros de segurança com base nas métricas calculadas."""
    # Validação de score_norm antes de usar
    score_ok = (metrics["score_norm"] is not None and
                isinstance(metrics["score_norm"], (int, float)) and
                metrics["score_norm"] >= config.MIN_RUGCHECK_SCORE)

    passes_safety_checks = (
        metrics["is_rugged"] is False and
        metrics["mint_auth_none"] is True and # Ser explícito
        metrics["freeze_auth_none"] is True and # Ser explícito
        metrics["lp_locked_pct"] == 100 and
        not metrics["risks_detected"] and # Lista vazia
        score_ok and
        metrics["initial_liq_ok"] and
        not metrics["creator_holds_too_much"] and
        not metrics["single_holder_exceeds_limit"] and
        metrics["insiders_ok"]
    )

    # Formata a string de log com os resultados das verificações
    reason_log = (
        f"(ScoreN: {metrics['score_norm'] if metrics['score_norm'] is not None else 'N/A'}{'[OK]' if score_ok else '[FAIL]'}, "
        f"Rugged: {metrics['is_rugged']}{'[OK]' if metrics['is_rugged'] is False else '[FAIL]'}, "
        f"MintN: {metrics['mint_auth_none']}{'[OK]' if metrics['mint_auth_none'] else '[FAIL]'}, "
        f"FreezeN: {metrics['freeze_auth_none']}{'[OK]' if metrics['freeze_auth_none'] else '[FAIL]'}, "
        f"LP: {metrics['lp_locked_pct']:.0f}%{'[OK]' if metrics['lp_locked_pct'] == 100 else '[FAIL]'}, "
        f"Risks: {not metrics['risks_detected']}{'[OK]' if not metrics['risks_detected'] else '[FAIL]'}, "
        f"CreatorBalOK: {not metrics['creator_holds_too_much']}{'[OK]' if not metrics['creator_holds_too_much'] else '[FAIL]'} ({metrics['creator_holding_pct_calculated']:.2f}%), "
        f"InitLiqOK: {metrics['initial_liq_ok']}{'[OK]' if metrics['initial_liq_ok'] else '[FAIL]'} ({metrics['initial_liq_value']:.2f}), "
        f"SingleHolderOK: {not metrics['single_holder_exceeds_limit']}{'[OK]' if not metrics['single_holder_exceeds_limit'] else '[FAIL]'} (Max: {metrics['max_single_holder_pct_found']:.2f}%), "
        f"InsidersOK: {metrics['insiders_ok']}{'[OK]' if metrics['insiders_ok'] else '[FAIL]'} ({metrics['insiders_detected']}))"
    )

    return passes_safety_checks, reason_log

async def _handle_safe_token(mint_address: str, signature: str, timestamp_utc: datetime, initial_price_rugcheck: Optional[float], session: aiohttp.ClientSession, state: StateManager):
    """Decide a ação para um token que passou nos filtros de segurança."""
    if config.SNIPEROO_USE_AUTOBUY_MODE:
        # Modo AutoBuy Sniperoo: Envia a ordem imediatamente
        logger.info(f"[{mint_address}] Passou filtros segurança. Modo AutoBuy ATIVO.")
        logger.debug(f"[{mint_address}] Buscando preço DexScreener pré-registro AutoBuy...")
        current_market_data = await fetch_market_data(mint_address, session) # Usa a função refatorada
        current_price_str = "N/A"
        if current_market_data and current_market_data.get("price_usd") is not None:
            try: current_price_str = f"{current_market_data['price_usd']:.8f}"
            except Exception: current_price_str = "Erro Format"
            logger.info(f"[{mint_address}] Preço atual DexScreener: {current_price_str} USD.")
        else: logger.warning(f"[{mint_address}] Não obteve preço DexScreener pré-registro.")

        logger.info(f"[{mint_address}] Enviando ordem AutoBuy p/ Sniperoo (Preço Dex: {current_price_str})...")
        buy_success = await place_sniperoo_buy_order(session=session, mint_address=mint_address)
        if buy_success: logger.info(f"[{mint_address}] Ordem AutoBuy registrada via Sniperoo.")
        else:
            logger.warning(f"[{mint_address}] Falha ao registrar ordem AutoBuy via Sniperoo.")
            state.add_pending_token({"mint": mint_address, "signature": signature, "timestamp_utc": timestamp_utc.isoformat(), "reason": "Seguro, mas falha registro AutoBuy"})

    else: # Modo Monitoramento Bot
        if initial_price_rugcheck is not None and isinstance(initial_price_rugcheck, (int, float)) and initial_price_rugcheck > 1e-18:
            if mint_address not in tokens_being_monitored:
                tokens_being_monitored.add(mint_address)
                logger.info(f"[{mint_address}] Passou filtros segurança. Modo Monitor Bot ATIVO. Iniciando monitoramento (preço inicial: {initial_price_rugcheck:.8f})...")
                asyncio.create_task(monitor_market_activity(mint_address, float(initial_price_rugcheck), session, state), name=f"MarketMonitor_{mint_address[:6]}")
            else: logger.debug(f"[{mint_address}] Já sendo monitorado (verif. dupla).")
        else:
            logger.warning(f"[{mint_address}] Passou filtros (Modo Monitor), mas preço inicial inválido/zero ({initial_price_rugcheck}). Não monitorando.")
            state.add_pending_token({"mint": mint_address, "signature": signature, "timestamp_utc": timestamp_utc.isoformat(), "reason": "Seguro (Modo Monitor), preço inicial inválido/zero"})

# --- Função Principal de Processamento (Orquestradora) ---
async def _process_graduated_token(data: Dict[str, Any], session: aiohttp.ClientSession, state: StateManager):
    """Orquestra o processamento de um token graduado."""
    mint_address, signature = _validate_incoming_token_data(data)
    if not mint_address: return

    timestamp_utc = datetime.now(timezone.utc)

    if state.is_token_processed(mint_address) or mint_address in tokens_being_monitored:
        logger.debug(f"Token {mint_address} já processado ou monitorado. Ignorando.")
        return

    state.add_processed_token(mint_address) # Marca como processado (iniciado)
    logger.info(f"\n=== Novo token graduado detectado! ===")
    logger.info(f"Endereço: {mint_address}")
    logger.info(f"Assinatura: {signature}")
    logger.info(f"Horário UTC: {timestamp_utc.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"Link Solscan: https://solscan.io/token/{mint_address}")

    # 1. Análise RugCheck
    check_result = await check_token_reliability(mint_address, session)

    if check_result["status"] == "success":
        analysis_summary = format_analysis_output(mint_address, check_result)
        logger.info(analysis_summary)

        raw_data = check_result.get("raw_data")
        if not raw_data or not isinstance(raw_data, dict):
             logger.error(f"[{mint_address}] Dados brutos RugCheck ausentes/inválidos. Status: {check_result.get('status')}")
             state.add_pending_token({"mint": mint_address, "signature": signature, "timestamp_utc": timestamp_utc.isoformat(), "reason": "RugCheck success mas raw_data vazio/inválido"})
             return

        # 2. Calcular Métricas e Avaliar Segurança
        try:
            safety_metrics = _calculate_safety_metrics(raw_data, mint_address)
            passes_safety_checks, reason_log = _evaluate_safety_checks(safety_metrics)
        except Exception as e:
            logger.error(f"[{mint_address}] Erro inesperado ao calcular/avaliar métricas de segurança: {e}", exc_info=True)
            state.add_pending_token({"mint": mint_address, "signature": signature, "timestamp_utc": timestamp_utc.isoformat(), "reason": f"Erro cálculo/avaliação métricas: {e}"})
            return

        if passes_safety_checks:
            # 3. Lidar com Token Seguro
            await _handle_safe_token(mint_address, signature, timestamp_utc, safety_metrics.get("initial_price_rugcheck"), session, state)
        else:
            logger.info(f"[{mint_address}] NÃO passou nos filtros de segurança {reason_log}.")
            state.add_pending_token({"mint": mint_address, "signature": signature, "timestamp_utc": timestamp_utc.isoformat(), "reason": f"Falhou filtros segurança {reason_log}"})

    elif check_result["status"] in ["skipped", "timeout", "error"]:
        reason = check_result.get("reason", "Unknown failure")
        status = check_result.get("status", "error")
        logger.warning(f"[{mint_address}] Falha/Skip na verificação RugCheck ({status}): {reason}")
        logger.info(f"[{mint_address}] Verifique manualmente: https://rugcheck.xyz/tokens/{mint_address}")
        state.add_pending_token({"mint": mint_address, "signature": signature, "timestamp_utc": timestamp_utc.isoformat(), "reason": f"RugCheck API check failed/skipped ({status}): {reason}"})
    else:
         # Caso inesperado
         logger.error(f"[{mint_address}] Status inesperado da verificação RugCheck: {check_result.get('status')}")
         state.add_pending_token({"mint": mint_address, "signature": signature, "timestamp_utc": timestamp_utc.isoformat(), "reason": f"Status RugCheck inesperado: {check_result.get('status')}"})


# --- _handle_message (sem alterações da última versão) ---
async def _handle_message(message: str, session: aiohttp.ClientSession, state: StateManager):
    try:
        data = json.loads(message)
        # Log menos verboso por default
        if data.get("txType") == "migrate":
            logger.debug(f"Mensagem 'migrate' recebida: {json.dumps(data)}")
            await _process_graduated_token(data, session, state)
        else:
            log_msg = f"Mensagem ignorada: txType={data.get('txType', 'N/A')}"
            if 'method' in data: log_msg += f", method={data.get('method')}"
            logger.debug(log_msg)
    except json.JSONDecodeError:
        logger.error(f"Erro ao decodificar mensagem JSON: {message[:500]}...")
    except Exception as e:
        logger.error(f"Erro inesperado ao processar mensagem: {e}", exc_info=True)


# --- run_websocket_client (sem alterações da última versão) ---
async def run_websocket_client(session: aiohttp.ClientSession, state: StateManager):
    reconnect_interval = config.RECONNECT_INTERVAL_MIN
    attempt = 0
    current_task = asyncio.current_task()

    while not getattr(current_task, '_must_cancel', False):
        try:
            logger.info(f"Tentando conectar ao WebSocket: {config.WS_URL} (Tentativa {attempt + 1})")
            async with websockets.connect(config.WS_URL, ping_interval=30, ping_timeout=25, close_timeout=15) as ws:
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
                logger.warning("Loop 'async for' terminou, conexão WebSocket provavelmente fechada pelo servidor.")
        except asyncio.CancelledError:
            logger.info("Tarefa WebSocket cancelada durante conexão/recebimento.")
            break
        except websockets.exceptions.ConnectionClosedOK as e:
             logger.info(f"Conexão WebSocket fechada normalmente: Código={e.code}, Razão='{e.reason}'")
        except websockets.exceptions.ConnectionClosedError as e:
            logger.warning(f"Conexão WebSocket fechada com erro: Código={e.code}, Razão='{e.reason}'")
        except websockets.exceptions.InvalidURI:
            logger.critical(f"Erro fatal: URI do WebSocket inválida: {config.WS_URL}. Encerrando tarefa.")
            break
        except websockets.exceptions.PayloadTooBig:
            logger.error("Erro: Payload da mensagem WebSocket muito grande.")
        except ConnectionRefusedError:
            logger.error("Erro de conexão: Conexão recusada pelo servidor.")
        except OSError as e:
            logger.error(f"Erro de Rede/OS na conexão WebSocket: {e}")
        except asyncio.TimeoutError:
             logger.warning("Timeout na conexão/manutenção do WebSocket.")
        except Exception as e:
            logger.error(f"Erro inesperado no loop do WebSocket: {e}", exc_info=True)

        if getattr(current_task, '_must_cancel', False):
            logger.info("Cancelamento solicitado antes da reconexão.")
            break
        attempt += 1
        wait_time = min(reconnect_interval * (2 ** min(attempt, 5)), config.RECONNECT_INTERVAL_MAX)
        wait_time += random.uniform(0, wait_time * 0.1)
        logger.info(f"Tentando reconectar em {wait_time:.2f} segundos... (Tentativa {attempt + 1})")
        try:
            await asyncio.sleep(wait_time)
        except asyncio.CancelledError:
            logger.info("Reconexão cancelada durante o sleep.")
            break
    logger.info("Tarefa run_websocket_client finalizada.")