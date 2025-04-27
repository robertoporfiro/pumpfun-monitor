import asyncio
import json
import logging
import random
import time
from datetime import datetime, timezone
from typing import Dict, Any
import aiohttp
import websockets

from . import config
from .state_manager import StateManager
from .token_checker import check_token_reliability
from .utils import format_analysis_output

logger = logging.getLogger(__name__)

async def _process_graduated_token(data: Dict[str, Any], session: aiohttp.ClientSession, state: StateManager):
    """Processa um evento de token graduado (migração). Chamada internamente."""
    mint_address = data.get("mint")
    signature = data.get("signature", "Desconhecido")
    tx_type = data.get("txType", "migrate")
    timestamp_utc = datetime.now(timezone.utc)

    if not mint_address or not isinstance(mint_address, str) or len(mint_address) not in (43, 44):
        actual_len = len(mint_address) if isinstance(mint_address, str) else 'N/A'
        logger.warning(f"Endereço de token com formato/comprimento inesperado ({actual_len} chars) ou ausente na mensagem: {mint_address}. Sig: {signature}")
        return

    if state.is_token_processed(mint_address):
        logger.debug(f"Token {mint_address} já processado anteriormente. Ignorando.")
        return

    state.add_processed_token(mint_address)
    logger.info(f"\n=== Novo token graduado detectado! ===")
    logger.info(f"Endereço do Token: {mint_address}")
    logger.info(f"Assinatura da Transação: {signature}")
    logger.info(f"Tipo de Transação: {tx_type}")
    logger.info(f"Horário UTC: {timestamp_utc.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"Link Solscan: https://solscan.io/token/{mint_address}")
    logger.info(f"Link GMGN: https://gmgn.ai/sol/token/{mint_address}")

    check_result = await check_token_reliability(mint_address, session)

    if check_result["status"] == "success":
        analysis_summary = format_analysis_output(mint_address, check_result)
        logger.info(analysis_summary)
    else:
        # Logar o motivo da falha/skip e adicionar aos pendentes
        reason = check_result.get("reason", "Unknown failure")
        status = check_result.get("status", "error")
        if status == "skipped":
             logger.info(f"[{mint_address}] Verificação de confiabilidade pulada ({reason}).")
        else:
             logger.warning(f"[{mint_address}] Falha ao verificar confiabilidade via API ({status}): {reason}")

        logger.info(f"[{mint_address}] Verifique manualmente: https://rugcheck.xyz/tokens/{mint_address}")
        state.add_pending_token({
            "mint": mint_address,
            "signature": signature,
            "timestamp_utc": timestamp_utc.isoformat(),
            "reason": f"API check failed/skipped ({status}): {reason}"
         })

async def _handle_message(message: str, session: aiohttp.ClientSession, state: StateManager):
    """Decodifica e roteia a mensagem WebSocket. Chamada internamente."""
    try:
        data = json.loads(message)
        logger.debug(f"Mensagem recebida: {json.dumps(data)}")

        if data.get("txType") == "migrate":
            # Processa a migração - não cria task separada aqui para garantir
            # que state.add_processed_token seja chamado sequencialmente.
            await _process_graduated_token(data, session, state)
        else:
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

    while True:
        try:
            logger.info(f"Tentando conectar ao WebSocket: {config.WS_URL} (Tentativa {attempt + 1})")
            async with websockets.connect(config.WS_URL, ping_interval=30, ping_timeout=20) as ws:
                logger.info("Conexão WebSocket estabelecida com sucesso!")
                attempt = 0
                reconnect_interval = config.RECONNECT_INTERVAL_MIN

                payload = {"method": "subscribeMigration", "keys": []}
                await ws.send(json.dumps(payload))
                logger.info("Inscrito em eventos de migração (subscribeMigration)")

                async for message in ws:
                    await _handle_message(str(message), session, state)

        except (websockets.exceptions.ConnectionClosedError, websockets.exceptions.ConnectionClosedOK) as e:
            logger.warning(f"Conexão WebSocket fechada: Código={e.code}, Razão={e.reason}")
        except ConnectionRefusedError:
             logger.error("Erro de conexão: Conexão recusada.")
        except websockets.exceptions.InvalidURI:
             logger.error(f"Erro: URI do WebSocket inválida: {config.WS_URL}")
             break # Erro fatal
        except websockets.exceptions.PayloadTooBig:
             logger.error("Erro: Payload da mensagem muito grande.")
        except OSError as e:
             logger.error(f"Erro de Rede/OS na conexão WebSocket: {e}")
        except Exception as e:
            logger.error(f"Erro inesperado no WebSocket: {e}", exc_info=True)

        attempt += 1
        wait_time = min(reconnect_interval * (2 ** min(attempt, 5)), config.RECONNECT_INTERVAL_MAX)
        wait_time += random.uniform(0, 1)
        logger.info(f"Tentando reconectar em {wait_time:.2f} segundos...")
        await asyncio.sleep(wait_time)