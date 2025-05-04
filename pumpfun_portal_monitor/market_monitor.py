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
    # --- CORPO DA FUNÇÃO INDENTADO ---
    url=f"https://api.dexscreener.com/latest/dex/tokens/{mint}"; logger.debug(f"[{mint}] Fetching Dex: {url}")
    try:
        async with ses.get(url, timeout=aiohttp.ClientTimeout(total=config.API_TIMEOUT)) as resp:
            st=resp.status; txt=await resp.text(); logger.debug(f"[{mint}] Dex status: {st}")
            if st==200:
                try: data=json.loads(txt)
                except json.JSONDecodeError as e: logger.error(f"[{mint}] Erro JSON Dex: {e}. Resp:{txt[:500]}"); return None
                pairs=data.get("pairs");
                if not pairs or not isinstance(pairs, list): logger.warning(f"[{mint}] Campo 'pairs' inválido Dex: {data}"); return None
                target=_find_target_pair(pairs, mint); return _parse_market_data_from_pair(target, mint) if target else None
            elif st==404: logger.warning(f"[{mint}] Token não encontrado Dex (404)."); return None
            elif st==429: logger.warning(f"[{mint}] Rate limit Dex (429)."); return None
            else: logger.error(f"[{mint}] Erro Dex: Status {st}. Resp: {txt[:500]}"); return None
    except Exception as e: logger.error(f"[{mint}] Exc fetch_market: {type(e).__name__} - {e}", exc_info=(config.LOG_LEVEL_NAME=="DEBUG")); return None
    # --- FIM DA INDENTAÇÃO CORRETA ---

def _check_market_criteria(m: Dict[str, Any], init_p: float, mint: str, i: int) -> Tuple[bool, str]:
    """Avalia se os dados de mercado atuais atendem aos critérios de compra definidos."""
    # --- CORPO DA FUNÇÃO INDENTADO ---
    p_ok=m["price_usd"]>=init_p*(1.0-config.MARKET_PRICE_DROP_TOLERANCE); v_ok=m["volume_m5"]>=config.MARKET_MIN_VOLUME_M5
    b_ok=m["buys_m5"]>=config.MARKET_MIN_BUYS_M5; total_tx=m["buys_m5"]+m["sells_m5"]
    br_ok=(m["buys_m5"]/total_tx>=config.MARKET_MIN_BUY_SELL_RATIO) if total_tx>5 else True
    fdv_ok=m["fdv"]<config.MARKET_MAX_FDV; h1_ok=m["price_change_h1"]>=config.MARKET_MIN_H1_PRICE_CHANGE
    log=(f"  P:{m['price_usd']:.6f}(>{init_p*(1.0-config.MARKET_PRICE_DROP_TOLERANCE):.6f})->{'OK' if p_ok else 'F'}|"
         f"V5m:{m['volume_m5']/1000:.1f}k(>{config.MARKET_MIN_VOLUME_M5/1000:.0f}k)->{'OK' if v_ok else 'F'}|"
         f"B5m:{m['buys_m5']}(>{config.MARKET_MIN_BUYS_M5})->{'OK' if b_ok else 'F'}|"
         f"B/S:{(m['buys_m5']/total_tx*100) if total_tx>0 else 0:.0f}%(>{config.MARKET_MIN_BUY_SELL_RATIO*100:.0f}%)->{'OK' if br_ok else 'F'}|"
         f"FDV:{m['fdv']/1000:.1f}k(<{config.MARKET_MAX_FDV/1000:.0f}k)->{'OK' if fdv_ok else 'F'}|"
         f"H1%:{m['price_change_h1']:.1f}%(>{config.MARKET_MIN_H1_PRICE_CHANGE:.1f}%)->{'OK' if h1_ok else 'F'}")
    logger.debug(f"[{mint}] Check (Iter {i}):{log}")
    return (p_ok and v_ok and b_ok and br_ok and fdv_ok and h1_ok), log
    # --- FIM DA INDENTAÇÃO CORRETA ---

# --- monitor_market_activity (Usa set global e escreve arquivo) ---
async def monitor_market_activity(mint_address: str, initial_price: float, session: aiohttp.ClientSession, state: StateManager):
    """Monitora a atividade de mercado e dispara compra se os critérios forem atendidos."""
    # --- CORPO DA FUNÇÃO INDENTADO ---
    start_time = time.monotonic(); deadline = start_time + config.MARKET_MONITOR_DURATION
    logger.info(f"[{mint_address}] Iniciando monitor (Dur: {config.MARKET_MONITOR_DURATION}s, Int: {config.MARKET_POLL_INTERVAL}s, InitP: {initial_price:.8f})")
    await asyncio.sleep(random.uniform(3, 7))

    try:
        errors = 0; max_errors = 5; i = 0; buy_attempted = False; last_log = ""; criteria_met = False
        while time.monotonic() < deadline and not buy_attempted:
            i += 1; current_task = asyncio.current_task()
            if mint_address not in tokens_being_monitored or getattr(current_task, '_must_cancel', False):
                 logger.info(f"[{mint_address}] Monitoramento interrompido."); break
            if i > 1:
                 try: await asyncio.sleep(config.MARKET_POLL_INTERVAL)
                 except asyncio.CancelledError: logger.info(f"[{mint_address}] Sleep cancelado."); break

            logger.debug(f"[{mint_address}] Iteração {i} monitor...")
            mkt_data = await fetch_market_data(mint_address, session)
            if mkt_data:
                errors = 0
                try: criteria_met, last_log = _check_market_criteria(mkt_data, initial_price, mint_address, i)
                except Exception as e: logger.error(f"[{mint_address}] Erro check critérios (Iter {i}): {e}", exc_info=True); criteria_met = False
                if criteria_met:
                    logger.info(f"[{mint_address}] CRITÉRIOS ATINGIDOS (Iter {i})! Enviando ordem Sniperoo...")
                    buy_attempted = True
                    buy_ok = await place_sniperoo_buy_order(session=session, mint_address=mint_address) # Chamada correta
                    if buy_ok: logger.info(f"[{mint_address}] Ordem IMEDIATA enviada. Encerrando monitor.")
                    else:
                        logger.warning(f"[{mint_address}] Falha envio ordem IMEDIATA. Encerrando monitor.")
                        try: state.add_pending_token({"mint": mint_address, "ts": datetime.now(timezone.utc).isoformat(),"reason": f"Critérios OK, falha envio Sniperoo", "market_data": mkt_data, "criteria_log": last_log})
                        except Exception as se: logger.error(f"[{mint_address}] Erro add pending: {se}")
                    break # Sai do loop
                else: logger.debug(f"[{mint_address}] Critérios não atingidos (Iter {i}).")
            else: # Falha fetch
                errors += 1; logger.warning(f"[{mint_address}] Falha fetch Dex ({errors}/{max_errors}).")
                if errors >= max_errors:
                    logger.error(f"[{mint_address}] Desistindo monitor - {max_errors} erros Dex.");
                    try: state.add_pending_token({"mint": mint_address, "ts": datetime.now(timezone.utc).isoformat(),"reason": f"{max_errors} erros Dex"})
                    except Exception as se: logger.error(f"[{mint_address}] Erro add pending: {se}")
                    break
        if time.monotonic() >= deadline and not buy_attempted: logger.info(f"[{mint_address}] Timeout monitor ({config.MARKET_MONITOR_DURATION}s) sem critérios.")
    except asyncio.CancelledError: logger.info(f"[{mint_address}] Tarefa monitoramento cancelada.")
    except Exception as e:
        logger.error(f"[{mint_address}] Erro fatal tarefa monitor: {e}", exc_info=True)
        try: state.add_pending_token({"mint": mint_address, "ts": datetime.now(timezone.utc).isoformat(),"reason": f"Erro monitor: {e}"})
        except Exception as se: logger.error(f"[{mint_address}] Erro add pending: {se}")
    finally:
        was_present = mint_address in tokens_being_monitored
        tokens_being_monitored.discard(mint_address)
        if was_present: await _write_monitored_tokens_file()
        logger.debug(f"[{mint_address}] Removido monitor ativo (finally).")
    # --- FIM DA INDENTAÇÃO CORRETA ---

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
    # --- CORPO DA FUNÇÃO INDENTADO ---
    payload = _build_sniperoo_payload(mint_address);
    if not payload: return False
    end=config.SNIPEROO_BUY_ENDPOINT; key=config.SNIPEROO_API_KEY; auto_buy=config.SNIPEROO_USE_AUTOBUY_MODE
    h={"Authorization": f"Bearer {key}","Content-Type":"application/json","Accept":"application/json"}
    try:
        log_act = "Registrando AutoBuy" if auto_buy else "Enviando Compra IMEDIATA"
        logger.info(f"[{mint_address}] {log_act} p/ Sniperoo: {config.SNIPEROO_BUY_AMOUNT_SOL:.4f} SOL (Fee:{config.SNIPEROO_PRIORITY_FEE}, Slip:{config.SNIPEROO_SLIPPAGE_BPS} BPS)")
        logger.debug(f"Payload Sniperoo: {json.dumps(payload)}")
        tout = aiohttp.ClientTimeout(total=config.API_TIMEOUT + 15)
        async with session.post(end, json=payload, headers=h, timeout=tout) as resp:
            stat=resp.status; txt=await resp.text()
            try: data = json.loads(txt) if txt else {}
            except json.JSONDecodeError: logger.error(f"[{mint_address}] Resp inválida Sniperoo(ñ JSON). Stat:{stat}, URL:{end}, Resp:{txt[:500]}"); return False
            if 200 <= stat < 300:
                order_info=data.get("order", data); oid="N/A"
                if isinstance(order_info, dict): oid=order_info.get("orderId", order_info.get("id"))
                elif isinstance(data, dict): oid=data.get("orderId", data.get("id"))
                log_ok = "Ordem AutoBuy registrada" if auto_buy else "Ordem compra enviada"
                logger.info(f"[{mint_address}] {log_ok} c/ sucesso! ID:{oid}. Resp:{json.dumps(data)}"); return True
            else:
                err_msg="Erro"; err_det=data.get("error"); msg=data.get("message")
                if isinstance(err_det, dict): err_msg = err_det.get("message", json.dumps(err_det))
                elif isinstance(err_det, str): err_msg = err_det
                elif msg: err_msg = msg
                elif txt: err_msg = txt[:500]
                else: err_msg = json.dumps(data)
                logger.error(f"[{mint_address}] Erro envio/registro Sniperoo. Stat:{stat}, URL:{end}, Erro:{err_msg}"); return False
    except Exception as e: logger.error(f"[{mint_address}] Exc place_sniperoo_buy_order: {type(e).__name__}. URL:{end}", exc_info=(config.LOG_LEVEL_NAME=="DEBUG")); return False
    # --- FIM DA INDENTAÇÃO CORRETA ---