import logging
import asyncio
import json
import time
from typing import Dict, Any
import aiohttp
from . import config # Usar import relativo

logger = logging.getLogger(__name__)

async def check_token_reliability(mint_address: str, session: aiohttp.ClientSession) -> Dict[str, Any]:
    """
    Tenta verificar a confiabilidade do token usando APIs externas (ex: RugCheck).
    Implementa polling com timeout em vez de sleep fixo.
    Retorna um dicionário com o status e dados da verificação.
    """
    if not config.RUGCHECK_API_ENDPOINT:
        logger.warning(f"[{mint_address}] RUGCHECK_API_ENDPOINT não configurado no .env. Pulando verificação de API.")
        return {"status": "skipped", "reason": "API endpoint not configured"}

    # --- USA .format() PARA INSERIR O ENDEREÇO ---
    # Garante que o placeholder {} esteja no RUGCHECK_API_ENDPOINT do .env
    try:
        # Usa {} como placeholder padrão esperado pelo .format()
        api_url = config.RUGCHECK_API_ENDPOINT.format(mint_address)
    except (IndexError, KeyError) as e:
        logger.error(f"[{mint_address}] Erro ao formatar URL da API. Verifique o placeholder em RUGCHECK_API_ENDPOINT no .env (deve ser '{{}}'): {e}")
        logger.error(f"Endpoint configurado: {config.RUGCHECK_API_ENDPOINT}")
        return {"status": "error", "reason": "API URL formatting error"}
    # --- FIM DA ALTERAÇÃO ---

    start_time = time.monotonic()
    attempt = 0

    logger.info(f"[{mint_address}] Iniciando verificação de confiabilidade via API: {api_url}")

    while time.monotonic() - start_time < config.CHECK_MAX_DURATION_SECONDS:
        attempt += 1
        logger.debug(f"[{mint_address}] Tentativa {attempt} de verificação na API...")
        try:
            async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    data = await response.json()
                    logger.info(f"[{mint_address}] Verificação da API bem-sucedida (Status: {response.status}).")
                    logger.debug(f"[{mint_address}] Dados recebidos da API: {json.dumps(data)}")
                    score = data.get("score")
                    summary = data.get("summary")
                    is_rug = data.get("rugged")
                    return {"status": "success", "score": score, "summary": summary, "is_rug": is_rug, "raw_data": data}
                elif response.status == 404:
                    logger.info(f"[{mint_address}] Token ainda não encontrado na API (404). Tentando novamente em {config.CHECK_RETRY_DELAY_SECONDS}s.")
                else:
                    response_text = await response.text()
                    logger.warning(f"[{mint_address}] Erro na API: Status {response.status}. Resposta: {response_text[:200]}...")

        except aiohttp.ClientConnectionError as e:
            logger.warning(f"[{mint_address}] Erro de conexão ao acessar API: {e}")
        except aiohttp.ClientPayloadError as e:
             logger.warning(f"[{mint_address}] Erro no payload da resposta da API: {e}")
        except asyncio.TimeoutError:
             logger.warning(f"[{mint_address}] Timeout ({config.CHECK_RETRY_DELAY_SECONDS}s) ao acessar API.")
        except json.JSONDecodeError as e:
             logger.error(f"[{mint_address}] Erro ao decodificar resposta JSON da API: {e}")
             return {"status": "error", "reason": f"JSON decode error: {e}"}
        except Exception as e:
            logger.error(f"[{mint_address}] Erro inesperado durante verificação da API: {e}", exc_info=True)
            return {"status": "error", "reason": f"Unexpected error: {e}"}

        await asyncio.sleep(config.CHECK_RETRY_DELAY_SECONDS)

    logger.warning(f"[{mint_address}] Verificação da API excedeu o tempo máximo de {config.CHECK_MAX_DURATION_SECONDS}s.")
    return {"status": "timeout", "reason": f"Check timed out after {config.CHECK_MAX_DURATION_SECONDS}s"}