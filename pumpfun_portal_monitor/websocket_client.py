# pumpportal_monitor/websocket_client.py
import asyncio
import json
import logging
import random
from datetime import datetime, timezone
import time # Importa time para localtime
import os # Importa os para getenv
from typing import Dict, Any, Tuple, Optional
import aiohttp
import websockets

# Imports relativos
from . import config
from .state_manager import StateManager
from .token_checker import check_token_reliability
# Importa do market_monitor
from .market_monitor import monitor_market_activity, place_sniperoo_buy_order, fetch_market_data, tokens_being_monitored
# from .utils_reverted import format_analysis_output_reverted as format_analysis_output
# Use o utils.py normal (sem GMGN)
from .utils import format_analysis_output


logger = logging.getLogger(__name__)

# --- Funções Auxiliares Refatoradas (Versão sem GMGN) ---

def _validate_incoming_token_data(data: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Valida os dados iniciais do token recebido via WebSocket."""
    mint_address = data.get("mint"); signature = data.get("signature", "Desconhecido")
    if not mint_address or not isinstance(mint_address, str) or len(mint_address) not in (43, 44):
        actual_len = len(mint_address) if isinstance(mint_address, str) else 'N/A'
        logger.warning(f"Endereço inválido ({actual_len} chars) ou ausente: {mint_address}. Sig: {signature}"); return None, signature
    return mint_address, signature

def _calculate_safety_metrics(raw_data_rc: Dict[str, Any], mint_address: str) -> Dict[str, Any]:
    """Calcula métricas de segurança a partir dos dados RugCheck."""
    # (Código como na resposta anterior)
    metrics = {"mint_address": mint_address, "score_norm": raw_data_rc.get("score_normalised"), "is_rugged": raw_data_rc.get("rugged"),
               "mint_auth_none": raw_data_rc.get("mintAuthority") is None, "freeze_auth_none": raw_data_rc.get("freezeAuthority") is None,
               "risks_detected": raw_data_rc.get("risks", []) or [], "initial_price_rugcheck": raw_data_rc.get("price"),
               "creator": raw_data_rc.get("creator"), "top_holders": raw_data_rc.get("topHolders", []) or [],
               "known_accounts": raw_data_rc.get("knownAccounts", {}) or {}, "lp_locked_pct": 0.0, "initial_liq_value": 0.0,
               "creator_holds_too_much": False, "creator_holding_pct_calculated": 0.0, "single_holder_exceeds_limit": False,
               "max_single_holder_pct_found": 0.0, "insiders_detected": raw_data_rc.get("graphInsidersDetected", 0),
               "insiders_ok": True, "initial_liq_ok": True}
    markets = raw_data_rc.get("markets", []) or []; lp_pool_address = None; owner_of_lp_pool_address = None
    if markets and isinstance(markets, list) and len(markets)>0 and markets[0].get("marketType")=="pump_fun":
        lp_info = markets[0].get("lp", {}) or {}; metrics["lp_locked_pct"] = float(lp_info.get("lpLockedPct", 0.0))
        lp_pool_address = markets[0].get("liquidityA"); owner_of_lp_pool_address = (markets[0].get("liquidityAAccount", {}) or {}).get("owner")
    initial_liq_raw = raw_data_rc.get("totalMarketLiquidity")
    if isinstance(initial_liq_raw, (int, float)): metrics["initial_liq_value"] = float(initial_liq_raw)
    else: logger.warning(f"[{mint_address}] Valor liq inicial inválido: {initial_liq_raw}"); metrics["initial_liq_value"] = 0.0
    if config.MIN_INITIAL_LIQUIDITY > 0: metrics["initial_liq_ok"] = metrics["initial_liq_value"] >= config.MIN_INITIAL_LIQUIDITY
    creator_balance_raw = raw_data_rc.get("creatorBalance", 0); token_supply_raw = (raw_data_rc.get("token", {}) or {}).get("supply")
    if isinstance(creator_balance_raw, (int, float)) and creator_balance_raw > 0:
        if isinstance(token_supply_raw, (int, float)) and token_supply_raw > 0:
            metrics["creator_holding_pct_calculated"] = (creator_balance_raw / token_supply_raw) * 100
            if metrics["creator_holding_pct_calculated"] > config.FILTER_MAX_CREATOR_HOLDING_PCT:
                metrics["creator_holds_too_much"] = True; logger.warning(f"[{mint_address}] Saldo criador ({metrics['creator_holding_pct_calculated']:.2f}%) > limite ({config.FILTER_MAX_CREATOR_HOLDING_PCT}%).")
        else: logger.warning(f"[{mint_address}] Saldo criador > 0, mas supply inválido ({token_supply_raw}). Rejeitando."); metrics["creator_holds_too_much"] = True
    creator_address_from_known = None; amm_addresses = set()
    for addr, info in metrics["known_accounts"].items():
        if info.get("type") == "CREATOR": creator_address_from_known = addr
        elif info.get("type") == "AMM": amm_addresses.add(addr)
    if lp_pool_address: amm_addresses.add(lp_pool_address)
    if owner_of_lp_pool_address: amm_addresses.add(owner_of_lp_pool_address)
    if isinstance(metrics["top_holders"], list):
        for holder in metrics["top_holders"]:
            addr = holder.get("address")
            if addr in amm_addresses or addr == metrics["creator"] or addr == creator_address_from_known: continue
            try:
                pct = float(holder.get("pct", 0.0)); metrics["max_single_holder_pct_found"] = max(metrics["max_single_holder_pct_found"], pct)
                if pct > config.FILTER_MAX_SINGLE_HOLDER_PCT:
                    metrics["single_holder_exceeds_limit"] = True; logger.warning(f"[{mint_address}] Holder {addr} excede limite ({pct:.2f}% > {config.FILTER_MAX_SINGLE_HOLDER_PCT}%)."); break
            except (ValueError, TypeError): logger.warning(f"[{mint_address}] Pct inválido holder {addr}: {holder.get('pct')}")
    metrics["insiders_ok"] = metrics["insiders_detected"] <= config.FILTER_MAX_INSIDERS_DETECTED
    if not metrics["insiders_ok"]: logger.warning(f"[{mint_address}] Insiders ({metrics['insiders_detected']}) > limite ({config.FILTER_MAX_INSIDERS_DETECTED}).")
    return metrics

def _evaluate_safety_checks(metrics: Dict[str, Any]) -> Tuple[bool, str]:
    """Avalia filtros de segurança baseados APENAS nas métricas RugCheck."""
    # (Código como na resposta anterior)
    score_ok = (metrics["score_norm"] is not None and isinstance(metrics["score_norm"],(int,float)) and metrics["score_norm"]>=config.MIN_RUGCHECK_SCORE)
    passes = ( metrics["is_rugged"] is False and metrics["mint_auth_none"] is True and metrics["freeze_auth_none"] is True and
               metrics["lp_locked_pct"] == 100 and not metrics["risks_detected"] and score_ok and metrics["initial_liq_ok"] and
               not metrics["creator_holds_too_much"] and not metrics["single_holder_exceeds_limit"] and metrics["insiders_ok"] )
    reason = ( f"(RCScore:{metrics['score_norm'] if metrics['score_norm'] is not None else '?'}{'[OK]' if score_ok else '[F]'},"
               f"RCRug:{metrics['is_rugged']}{'[OK]' if not metrics['is_rugged'] else '[F]'},"
               f"MintN:{metrics['mint_auth_none']}{'[OK]' if metrics['mint_auth_none'] else '[F]'},"
               f"FrzN:{metrics['freeze_auth_none']}{'[OK]' if metrics['freeze_auth_none'] else '[F]'},"
               f"LP:{metrics['lp_locked_pct']:.0f}%{'[OK]' if metrics['lp_locked_pct']==100 else '[F]'},"
               f"RCRisks:{not metrics['risks_detected']}{'[OK]' if not metrics['risks_detected'] else '[F]'},"
               f"CrBal:{not metrics['creator_holds_too_much']}{'[OK]' if not metrics['creator_holds_too_much'] else '[F]'},"
               f"InitLiq:{metrics['initial_liq_ok']}{'[OK]' if metrics['initial_liq_ok'] else '[F]'},"
               f"SingleH:{not metrics['single_holder_exceeds_limit']}{'[OK]' if not metrics['single_holder_exceeds_limit'] else '[F]'},"
               f"RCIns:{metrics['insiders_ok']}{'[OK]' if metrics['insiders_ok'] else '[F]'})" )
    return passes, reason

# --- _handle_safe_token (Chamada Corrigida) ---
async def _handle_safe_token(mint: str, sig: str, ts_utc: datetime, init_p: Optional[float], ses: aiohttp.ClientSession, st: StateManager):
    """Decide ação para token seguro: AutoBuy ou Monitor."""
    if config.SNIPEROO_USE_AUTOBUY_MODE:
        logger.info(f"[{mint}] Seg OK. Modo AutoBuy ATIVO."); cur_mkt = await fetch_market_data(mint, ses)
        cur_p = f"{cur_mkt['price_usd']:.8f}" if cur_mkt and cur_mkt.get('price_usd') else "N/A"
        logger.info(f"[{mint}] Preço DexScreener pré-AutoBuy: {cur_p} USD.")
        logger.info(f"[{mint}] Enviando ordem AutoBuy p/ Sniperoo (Preço Dex: {cur_p})...")
        # --- CORREÇÃO AQUI: Passa 'mint' como 'mint_address' ---
        buy_ok = await place_sniperoo_buy_order(session=ses, mint_address=mint)
        # --- FIM CORREÇÃO ---
        if buy_ok: logger.info(f"[{mint}] Ordem AutoBuy registrada via Sniperoo.")
        else: logger.warning(f"[{mint}] Falha registro AutoBuy Sniperoo."); st.add_pending_token({"mint": mint, "sig": sig, "ts": ts_utc.isoformat(), "reason": "Seguro, falha registro AutoBuy"})
    else: # Modo Monitor Bot
        if init_p is not None and isinstance(init_p, (int, float)) and init_p > 1e-18:
            if mint not in tokens_being_monitored: # Usa set global
                tokens_being_monitored.add(mint) # Adiciona ao set global
                logger.info(f"[{mint}] Seg OK. Modo Monitor ATIVO. Iniciando monitor (InitP RC: {init_p:.8f})...")
                # Passa state para a task poder adicionar a pendentes se necessário
                asyncio.create_task(monitor_market_activity(mint, float(init_p), ses, st), name=f"MarketMonitor_{mint[:6]}")
            else: logger.debug(f"[{mint}] Já sendo monitorado (set global).")
        else: logger.warning(f"[{mint}] Seg OK (Modo Monitor), mas InitP RC inválido ({init_p}). Ñ monitorando."); st.add_pending_token({"mint": mint, "sig": sig, "ts": ts_utc.isoformat(), "reason": "Seguro(Monitor), InitP inválido"})

# --- Função Principal de Processamento (Orquestradora) ---
async def _process_graduated_token(data: Dict[str, Any], session: aiohttp.ClientSession, state: StateManager):
    """Orquestra o processamento: valida, verifica RugCheck, avalia e decide ação."""
    # --- Verificação de Horário de Trading ---
    if not config.TRADING_ENABLED: return
    try:
        now_struct = time.localtime(); current_hour = now_struct.tm_hour
        start_h, end_h = config.TRADING_START_HOUR, config.TRADING_END_HOUR
        if not (start_h <= current_hour < end_h):
            logger.debug(f"Ignorado: Hora ({current_hour:02d}h {time.strftime('%Z')}) fora janela ({start_h:02d}h-{end_h:02d}h).")
            return
    except Exception as time_err: logger.error(f"Erro verificar horário: {time_err}. Continuando...")

    mint, sig = _validate_incoming_token_data(data)
    if not mint: return
    ts_utc = datetime.now(timezone.utc)
    # --- Usa set global para verificar monitoramento ---
    if state.is_token_processed(mint) or mint in tokens_being_monitored:
         logger.debug(f"Token {mint} já visto/monitorado."); return
    # --------------------------------------------------

    state.add_processed_token(mint)
    logger.info(f"\n=== Novo token graduado! ===\n  Endereço: {mint}\n  Assinatura: {sig}\n  Horário UTC: {ts_utc.strftime('%Y-%m-%d %H:%M:%S')}Z\n  Solscan: https://solscan.io/token/{mint}")

    rc_result = await check_token_reliability(mint, session)

    if rc_result["status"] == "success":
        analysis_summary = format_analysis_output(mint, rc_result)
        logger.info(analysis_summary)
        rc_raw = rc_result.get("raw_data")
        if not rc_raw or not isinstance(rc_raw, dict):
             logger.error(f"[{mint}] Dados RC ausentes."); state.add_pending_token({"mint": mint, "sig": sig, "ts": ts_utc.isoformat(), "reason": "RC OK mas raw_data vazio"}); return

        creator_address = rc_raw.get("creator")
        if state.is_creator_blacklisted(creator_address):
            logger.warning(f"[{mint}] REJEITADO: Criador {creator_address} na blacklist.")
            state.add_pending_token({"mint": mint, "sig": sig, "ts": ts_utc.isoformat(), "reason": f"Criador Blacklist: {creator_address}"})
            return

        try:
            metrics = _calculate_safety_metrics(rc_raw, mint)
            passes, reason = _evaluate_safety_checks(metrics)
        except Exception as e:
            logger.error(f"[{mint}] Erro métricas: {e}", exc_info=True); state.add_pending_token({"mint": mint, "sig": sig, "ts": ts_utc.isoformat(), "reason": f"Erro métricas: {e}"}); return

        if passes:
            await _handle_safe_token(mint, sig, ts_utc, metrics.get("initial_price_rugcheck"), session, state)
        else:
            logger.info(f"[{mint}] REJEITADO filtros segurança {reason}.")
            state.add_pending_token({"mint": mint, "sig": sig, "ts": ts_utc.isoformat(), "reason": f"Falhou filtros {reason}"})

    elif rc_result["status"] in ["skipped", "timeout", "error"]:
        reason = rc_result.get("reason","?"); status = rc_result.get("status","?")
        logger.warning(f"[{mint}] Falha/Skip RugCheck ({status}): {reason}"); state.add_pending_token({"mint": mint, "sig": sig, "ts": ts_utc.isoformat(), "reason": f"RC API {status}: {reason}"})
    else:
         logger.error(f"[{mint}] Status RC inesperado: {rc_result.get('status')}"); state.add_pending_token({"mint": mint, "sig": sig, "ts": ts_utc.isoformat(), "reason": f"Status RC Inesp: {rc_result.get('status')}"})


# --- _handle_message e run_websocket_client (sem alterações) ---
async def _handle_message(message: str, session: aiohttp.ClientSession, state: StateManager):
    # (Código como antes)
    try:
        data = json.loads(message)
        if data.get("txType") == "migrate":
            logger.debug(f"Msg 'migrate' recebida: {json.dumps(data)}")
            await _process_graduated_token(data, session, state)
        else:
            log_msg = f"Msg ignorada: type={data.get('txType', '?')}"
            if 'method' in data: log_msg += f", method={data.get('method')}"
            logger.debug(log_msg)
    except json.JSONDecodeError: logger.error(f"Erro JSON: {message[:500]}...")
    except Exception as e: logger.error(f"Erro processar msg: {e}", exc_info=True)

async def run_websocket_client(session: aiohttp.ClientSession, state: StateManager):
    # (Código como antes)
    reconnect_interval = config.RECONNECT_INTERVAL_MIN; attempt = 0; task = asyncio.current_task()
    while not getattr(task, '_must_cancel', False):
        try:
            logger.info(f"Conectando WS: {config.WS_URL} (Tentativa {attempt + 1})")
            async with websockets.connect(config.WS_URL, ping_interval=30, ping_timeout=25, close_timeout=15) as ws:
                logger.info("Conexão WS OK!"); attempt = 0; reconnect_interval = config.RECONNECT_INTERVAL_MIN
                await ws.send(json.dumps({"method": "subscribeMigration", "keys": []})); logger.info("Inscrito 'subscribeMigration'")
                async for msg in ws:
                    if getattr(task, '_must_cancel', False): logger.info("WS cancelado, parando msgs."); break
                    await _handle_message(str(msg), session, state)
                if getattr(task, '_must_cancel', False): break
                logger.warning("Loop WS 'async for' terminou (conexão fechada?).")
        except asyncio.CancelledError: logger.info("Tarefa WS cancelada."); break
        except (websockets.exceptions.ConnectionClosedOK) as e: logger.info(f"WS fechado OK: {e.code} '{e.reason}'")
        except (websockets.exceptions.ConnectionClosedError) as e: logger.warning(f"WS fechado c/ Erro: {e.code} '{e.reason}'")
        except websockets.exceptions.InvalidURI: logger.critical(f"URI WS inválida: {config.WS_URL}"); break
        except Exception as e: logger.error(f"Erro loop WS: {type(e).__name__} - {e}", exc_info=(config.LOG_LEVEL_NAME=="DEBUG"))
        if getattr(task, '_must_cancel', False): logger.info("WS cancelado antes reconexão."); break
        attempt += 1; wait = min(reconnect_interval * (2 ** min(attempt, 5)), config.RECONNECT_INTERVAL_MAX); wait += random.uniform(0, wait * 0.1)
        logger.info(f"Tentando reconectar WS em {wait:.2f}s... (Tentativa {attempt + 1})")
        try: await asyncio.sleep(wait)
        except asyncio.CancelledError: logger.info("Reconexão WS cancelada durante sleep."); break
    logger.info("Tarefa run_websocket_client finalizada.")