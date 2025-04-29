# pumpportal_monitor/websocket_client.py
import asyncio
import json
import logging
import random
from datetime import datetime, timezone
from typing import Dict, Any
import aiohttp
import websockets

# Imports relativos
from . import config
from .state_manager import StateManager
from .token_checker import check_token_reliability
# Importa a nova função e o set de monitoramento
from .market_monitor import monitor_market_activity, tokens_being_monitored
from .utils import format_analysis_output

logger = logging.getLogger(__name__)

async def _process_graduated_token(data: Dict[str, Any], session: aiohttp.ClientSession, state: StateManager):
    """Processa um evento de token graduado, realiza análise de segurança e inicia monitoramento de mercado."""
    mint_address = data.get("mint")
    signature = data.get("signature", "Desconhecido")
    tx_type = data.get("txType", "migrate")
    timestamp_utc = datetime.now(timezone.utc)

    if not mint_address or not isinstance(mint_address, str) or len(mint_address) not in (43, 44):
        actual_len = len(mint_address) if isinstance(mint_address, str) else 'N/A'
        logger.warning(f"Endereço de token inválido ({actual_len} chars) ou ausente: {mint_address}. Sig: {signature}")
        return

    # Verifica se já foi processado OU se já está sendo monitorado ativamente
    if state.is_token_processed(mint_address) or mint_address in tokens_being_monitored:
        logger.debug(f"Token {mint_address} já processado ou monitorado. Ignorando.")
        return

    # Marca como processado *antes* da análise para evitar que uma segunda mensagem inicie outra análise
    state.add_processed_token(mint_address)
    logger.info(f"\n=== Novo token graduado detectado! ===")
    logger.info(f"Endereço: {mint_address}")
    logger.info(f"Assinatura: {signature}")
    logger.info(f"Horário UTC: {timestamp_utc.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"Link Solscan: https://solscan.io/token/{mint_address}")

    # 1. Análise de Segurança Inicial (RugCheck)
    check_result = await check_token_reliability(mint_address, session)

    if check_result["status"] == "success":
        # Loga o resumo ANTES de decidir se monitora
        analysis_summary = format_analysis_output(mint_address, check_result)
        logger.info(analysis_summary)

        # 2. Filtro de Segurança Pré-Monitoramento
        raw_data = check_result.get("raw_data", {})
        score_norm = raw_data.get("score_normalised") # Pega o score normalizado (0.0 a 1.0)
        is_rugged = raw_data.get("rugged")
        mint_auth_none = raw_data.get("mintAuthority") is None
        freeze_auth_none = raw_data.get("freezeAuthority") is None
        risks_detected = raw_data.get("risks", [])
        initial_price = raw_data.get("price") # Preço obtido do RugCheck
        creator_balance_raw = raw_data.get("creatorBalance", 0) # Pega o saldo raw do criador

        # Calcula % de LP bloqueada a partir dos dados do RugCheck
        lp_locked_pct = 0
        markets = raw_data.get("markets", []) or []
        if markets and isinstance(markets, list) and len(markets) > 0 and markets[0].get("marketType") == "pump_fun":
             lp_info = markets[0].get("lp", {}) or {}
             lp_locked_pct = lp_info.get("lpLockedPct", 0)

        # --- NOVO: Verifica Saldo do Criador ---
        MAX_CREATOR_HOLDING_PCT = 1.0 # Limite de 1% (pode mover para config.py)
        creator_holds_too_much = False
        creator_holding_pct_calculated = 0.0 # Para log
        token_supply_raw = (raw_data.get("token", {}) or {}).get("supply")

        if isinstance(creator_balance_raw, (int, float)) and creator_balance_raw > 0:
            if isinstance(token_supply_raw, (int, float)) and token_supply_raw > 0:
                creator_holding_pct_calculated = (creator_balance_raw / token_supply_raw) * 100
                if creator_holding_pct_calculated > MAX_CREATOR_HOLDING_PCT:
                    creator_holds_too_much = True
                    logger.warning(f"[{mint_address}] Saldo do criador ({creator_holding_pct_calculated:.2f}%) excede o limite de {MAX_CREATOR_HOLDING_PCT}%.")
            else:
                # Se o saldo do criador > 0 mas não conseguimos o supply total, consideramos risco
                logger.warning(f"[{mint_address}] Saldo do criador > 0 ({creator_balance_raw}), mas supply total indisponível para calcular %. Rejeitando por segurança.")
                creator_holds_too_much = True
        # --- FIM DA VERIFICAÇÃO DO CRIADOR ---


        # Aplica todos os filtros de segurança
        passes_safety_checks = (
            is_rugged is False and
            mint_auth_none and
            freeze_auth_none and
            lp_locked_pct == 100 and
            not risks_detected and
            (score_norm is not None and isinstance(score_norm, (int, float)) and score_norm >= config.MIN_RUGCHECK_SCORE) and
            (raw_data.get("totalMarketLiquidity", 0) >= config.MIN_INITIAL_LIQUIDITY if config.MIN_INITIAL_LIQUIDITY > 0 else True) and
            not creator_holds_too_much # <-- Nova condição adicionada
        )

        if passes_safety_checks:
            # Verifica se o preço inicial é válido para usar como referência
            if initial_price is not None and isinstance(initial_price, (int, float)) and initial_price > 1e-18: # Evita zero ou negativo
                # 3. Iniciar Monitoramento de Mercado
                if mint_address not in tokens_being_monitored:
                     tokens_being_monitored.add(mint_address)
                     logger.info(f"[{mint_address}] Passou nos filtros de segurança. Iniciando monitoramento de mercado (preço inicial RugCheck: {initial_price:.8f})...")
                     # Cria a tarefa de monitoramento em background
                     asyncio.create_task(
                         monitor_market_activity(mint_address, float(initial_price), session, state),
                         name=f"MarketMonitor_{mint_address[:6]}" # Nomeia a task
                     )
                else:
                     logger.debug(f"[{mint_address}] Já está sendo monitorado (verificação dupla).") # Segurança
            else:
                 # Passou na segurança, mas não tem preço inicial válido para monitorar
                 logger.warning(f"[{mint_address}] Passou nos filtros de segurança, mas preço inicial da API RugCheck inválido/zero ({initial_price}). Não iniciando monitoramento de mercado.")
                 state.add_pending_token({
                     "mint": mint_address, "signature": signature, "timestamp_utc": timestamp_utc.isoformat(),
                     "reason": "Seguro (RugCheck OK), mas preço inicial inválido/zero"
                 })
        else:
            # Falhou nos filtros de segurança
            # Log de falha aprimorado
            reason_log = (
                f"(Score Norm: {score_norm if score_norm is not None else 'N/A'}, "
                f"Rugged: {is_rugged}, MintNone: {mint_auth_none}, FreezeNone: {freeze_auth_none}, "
                f"LP: {lp_locked_pct:.0f}%, Risks: {bool(risks_detected)}, "
                f"CreatorBalanceOK: {not creator_holds_too_much} ({creator_holding_pct_calculated:.2f}%), "
                f"InitialLiqOK: {raw_data.get('totalMarketLiquidity', 0) >= config.MIN_INITIAL_LIQUIDITY if config.MIN_INITIAL_LIQUIDITY > 0 else 'N/A'})"
            )
            logger.info(f"[{mint_address}] NÃO passou nos filtros de segurança pré-monitoramento {reason_log}.")
            state.add_pending_token({
                "mint": mint_address, "signature": signature, "timestamp_utc": timestamp_utc.isoformat(),
                "reason": f"Falhou filtros segurança {reason_log}" # Adiciona detalhes ao pending
            })

    elif check_result["status"] in ["skipped", "timeout", "error"]:
         # Falha ao obter análise do RugCheck
         reason = check_result.get("reason", "Unknown failure")
         status = check_result.get("status", "error")
         logger.warning(f"[{mint_address}] Falha/Skip na verificação RugCheck ({status}): {reason}")
         logger.info(f"[{mint_address}] Verifique manualmente: https://rugcheck.xyz/tokens/{mint_address}")
         state.add_pending_token({
            "mint": mint_address, "signature": signature, "timestamp_utc": timestamp_utc.isoformat(),
            "reason": f"RugCheck API check failed/skipped ({status}): {reason}"
         })


async def _handle_message(message: str, session: aiohttp.ClientSession, state: StateManager):
    """Decodifica e roteia a mensagem WebSocket."""
    try:
        data = json.loads(message)
        # Log menos verboso por default, descomente se precisar MUITO detalhe
        # logger.debug(f"Mensagem recebida: {json.dumps(data)}")

        if data.get("txType") == "migrate":
            # Chama a função de processamento atualizada
            await _process_graduated_token(data, session, state)
        else:
             # Log ignora apenas em DEBUG
             log_msg = f"Mensagem ignorada: txType={data.get('txType', 'N/A')}"
             if 'method' in data:
                 log_msg += f", method={data.get('method')}"
             logger.debug(log_msg)

    except json.JSONDecodeError:
        logger.error(f"Erro ao decodificar mensagem JSON: {message[:500]}...")
    except Exception as e:
        logger.error(f"Erro inesperado ao processar mensagem: {e}", exc_info=True)


async def run_websocket_client(session: aiohttp.ClientSession, state: StateManager):
    """Gerencia a conexão WebSocket, mensagens e reconexão."""
    reconnect_interval = config.RECONNECT_INTERVAL_MIN
    attempt = 0
    # Obtém a task atual para verificar cancelamento posteriormente
    current_task = asyncio.current_task()

    while not getattr(current_task, '_must_cancel', False): # Verifica flag de cancelamento
        try:
            logger.info(f"Tentando conectar ao WebSocket: {config.WS_URL} (Tentativa {attempt + 1})")
            async with websockets.connect(config.WS_URL, ping_interval=30, ping_timeout=20, close_timeout=10) as ws:
                logger.info("Conexão WebSocket estabelecida com sucesso!")
                attempt = 0
                reconnect_interval = config.RECONNECT_INTERVAL_MIN

                payload = {"method": "subscribeMigration", "keys": []}
                await ws.send(json.dumps(payload))
                logger.info("Inscrito em eventos de migração (subscribeMigration)")

                # Loop para receber mensagens enquanto a conexão estiver ativa
                async for message in ws:
                     # Verifica cancelamento antes de processar cada mensagem
                    if getattr(current_task, '_must_cancel', False):
                        logger.info("Cancelamento solicitado, parando de processar mensagens WebSocket.")
                        break
                    await _handle_message(str(message), session, state)

                # Se saiu do loop async for, verifica se foi por cancelamento
                if getattr(current_task, '_must_cancel', False):
                     break # Sai do while externo também

        except asyncio.CancelledError:
            logger.info("Tarefa WebSocket cancelada durante conexão/recebimento.")
            break # Sai do loop while
        except (websockets.exceptions.ConnectionClosedError, websockets.exceptions.ConnectionClosedOK) as e:
            logger.warning(f"Conexão WebSocket fechada: Código={e.code}, Razão='{e.reason}'")
        except ConnectionRefusedError:
             logger.error("Erro de conexão: Conexão recusada pelo servidor.")
        except websockets.exceptions.InvalidURI:
             logger.error(f"Erro fatal: URI do WebSocket inválida: {config.WS_URL}")
             break # Erro fatal, não tenta reconectar
        except websockets.exceptions.PayloadTooBig:
             logger.error("Erro: Payload da mensagem WebSocket muito grande.")
        except OSError as e:
             logger.error(f"Erro de Rede/OS na conexão WebSocket: {e}")
        except Exception as e:
            # Captura qualquer outra exceção durante a conexão ou recebimento
            logger.error(f"Erro inesperado no loop do WebSocket: {e}", exc_info=True)

        # Se chegamos aqui e não fomos cancelados, tentamos reconectar
        if getattr(current_task, '_must_cancel', False):
             logger.info("Cancelamento solicitado antes da reconexão.")
             break

        # Lógica de reconexão com backoff exponencial
        attempt += 1
        wait_time = min(reconnect_interval * (2 ** min(attempt, 5)), config.RECONNECT_INTERVAL_MAX)
        wait_time += random.uniform(0, 1) # Jitter
        logger.info(f"Tentando reconectar em {wait_time:.2f} segundos...")
        try:
            await asyncio.sleep(wait_time)
        except asyncio.CancelledError:
             logger.info("Reconexão cancelada durante o sleep.")
             break # Sai do loop while

    logger.info("Tarefa run_websocket_client finalizada.")