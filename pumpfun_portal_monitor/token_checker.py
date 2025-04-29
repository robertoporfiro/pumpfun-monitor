import logging
import asyncio
import json
import time
from typing import Dict, Any
import aiohttp
from . import config

logger = logging.getLogger(__name__)

async def check_token_reliability(mint_address: str, session: aiohttp.ClientSession) -> Dict[str, Any]:
    """
    Tenta verificar a confiabilidade do token usando APIs externas (ex: RugCheck).
    Implementa polling com timeout em vez de sleep fixo.
    Retorna um dicionário com o status e dados da verificação.
    """
    if not config.RUGCHECK_API_ENDPOINT:
        logger.warning(f"[{mint_address}] RUGCHECK_API_ENDPOINT não configurado. Pulando verificação RugCheck.")
        return {"status": "skipped", "reason": "API endpoint not configured"}

    try:
        # Usa {} como placeholder padrão esperado pelo .format()
        api_url = config.RUGCHECK_API_ENDPOINT.format(mint_address)
    except (IndexError, KeyError) as e:
        logger.error(f"[{mint_address}] Erro ao formatar URL da API RugCheck. Verifique o placeholder em .env (deve ser '{{}}'): {e}")
        return {"status": "error", "reason": "API URL formatting error"}

    start_time = time.monotonic()
    attempt = 0
    logger.info(f"[{mint_address}] Iniciando verificação de confiabilidade via API RugCheck: {api_url}")

    while time.monotonic() - start_time < config.CHECK_MAX_DURATION_SECONDS:
        attempt += 1
        logger.debug(f"[{mint_address}] Tentativa {attempt} de verificação na API RugCheck...")
        try:
            async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=config.API_TIMEOUT)) as response:
                if response.status == 200:
                    data = await response.json()
                    logger.info(f"[{mint_address}] Verificação da API RugCheck bem-sucedida (Status: {response.status}).")
                    logger.debug(f"[{mint_address}] Dados recebidos da API RugCheck: {json.dumps(data)}")
                    score = data.get("score")
                    summary = data.get("summary")
                    is_rug = data.get("rugged")
                    return {"status": "success", "score": score, "summary": summary, "is_rug": is_rug, "raw_data": data}
                elif response.status == 404:
                    # Considera 404 como "ainda não indexado" e continua tentando
                    logger.info(f"[{mint_address}] Token ainda não encontrado na API RugCheck (404). Tentando novamente em {config.CHECK_RETRY_DELAY_SECONDS}s.")
                else:
                    # Outros erros HTTP são logados, mas a tentativa continua
                    response_text = await response.text()
                    logger.warning(f"[{mint_address}] Erro na API RugCheck: Status {response.status}. Resposta: {response_text[:200]}...")

        except aiohttp.ClientConnectionError as e:
            logger.warning(f"[{mint_address}] Erro de conexão ao acessar API RugCheck: {e}")
        except aiohttp.ClientPayloadError as e:
             logger.warning(f"[{mint_address}] Erro no payload da resposta da API RugCheck: {e}")
        except asyncio.TimeoutError:
             logger.warning(f"[{mint_address}] Timeout ({config.API_TIMEOUT}s) ao acessar API RugCheck.")
        except json.JSONDecodeError as e:
             logger.error(f"[{mint_address}] Erro ao decodificar resposta JSON da API RugCheck: {e}")
             # Considera JSON inválido como erro definitivo para esta verificação
             return {"status": "error", "reason": f"RugCheck JSON decode error: {e}"}
        except Exception as e:
            logger.error(f"[{mint_address}] Erro inesperado durante verificação da API RugCheck: {e}", exc_info=True)
            # Considera erro inesperado como definitivo para esta verificação
            return {"status": "error", "reason": f"Unexpected RugCheck API error: {e}"}

        # Espera antes da próxima tentativa, apenas se não for um erro definitivo
        await asyncio.sleep(config.CHECK_RETRY_DELAY_SECONDS)

    # Se saiu do loop, foi por timeout
    logger.warning(f"[{mint_address}] Verificação da API RugCheck excedeu o tempo máximo de {config.CHECK_MAX_DURATION_SECONDS}s.")
    return {"status": "timeout", "reason": f"RugCheck check timed out after {config.CHECK_MAX_DURATION_SECONDS}s"}
