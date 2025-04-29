import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

def format_analysis_output(mint: str, api_data: Dict[str, Any]) -> str:
    """Formata os dados da API de análise em um resumo legível."""

    if not api_data or api_data.get("status") != "success":
        # Retorna mensagem indicando falha na obtenção dos dados
        reason = api_data.get('reason', 'Status não foi success')
        return f"[{mint}] Análise Indisponível (Status: {api_data.get('status', 'N/A')}, Razão: {reason})"

    data = api_data.get("raw_data", {})
    if not data: # Se raw_data estiver vazio por algum motivo
         return f"[{mint}] Análise Indisponível (Dados brutos da API vazios)"

    # --- Extração Segura de Dados ---
    token_info = data.get("token", {}) or {}
    token_meta = data.get("tokenMeta", {}) or {}
    file_meta = data.get("fileMeta", {}) or {}
    markets = data.get("markets", []) or []
    top_holders = data.get("topHolders", []) or []

    name = token_meta.get("name") or file_meta.get("name", "N/A")
    symbol = token_meta.get("symbol") or file_meta.get("symbol", "N/A")
    creator = data.get("creator", "N/A")
    decimals = token_info.get("decimals", 0)
    if not isinstance(decimals, int) or decimals < 0:
        logger.warning(f"[{mint}] Decimals inválidos recebidos: {decimals}. Usando 0.")
        decimals = 0

    supply_raw = token_info.get("supply")
    supply_ui = None
    market_cap_usd = None
    price_usd = data.get("price") # Preço do RugCheck

    if isinstance(supply_raw, (int, float)) and supply_raw >= 0:
        try:
            supply_ui = float(supply_raw) / (10**decimals)
            if price_usd is not None and isinstance(price_usd, (int, float)) and price_usd >= 0:
                 market_cap_usd = price_usd * supply_ui
        except (ValueError, TypeError, OverflowError) as e:
            logger.warning(f"[{mint}] Erro ao calcular supply UI ou Market Cap (RugCheck data): {e}")
            if isinstance(supply_raw, (int, float)): supply_ui = supply_raw

    mint_authority = data.get("mintAuthority", "Erro ao ler")
    freeze_authority = data.get("freezeAuthority", "Erro ao ler")
    is_mutable = token_meta.get("mutable", None) # Usar None para indicar desconhecido
    risks = data.get("risks", []) or []
    # --- USA score_normalised ---
    score_display = data.get("score_normalised") # Pega o normalizado
    # --- FIM CORREÇÃO ---
    rugged = data.get("rugged", None)
    total_liquidity_usd = data.get("totalMarketLiquidity")
    total_holders_count = data.get("totalHolders", "N/A")

    creator_balance_raw = data.get("creatorBalance", 0)
    creator_balance_ui = 0.0
    if isinstance(creator_balance_raw, (int, float)) and creator_balance_raw > 0:
        try:
            creator_balance_ui = float(creator_balance_raw) / (10**decimals)
        except (ValueError, TypeError, OverflowError):
            logger.warning(f"[{mint}] Não foi possível calcular o UI amount para o saldo do criador: {creator_balance_raw}")
            creator_balance_ui = -1 # Flag para indicar erro

    lp_locked_pct = 0
    lp_pool_address = None
    if markets and isinstance(markets, list) and len(markets) > 0 and markets[0].get("marketType") == "pump_fun":
        lp_info = markets[0].get("lp", {}) or {}
        lp_locked_pct = lp_info.get("lpLockedPct", 0)
        lp_pool_address = markets[0].get("liquidityA")

    holder_concentration_pct = 0.0
    top_non_lp_holders = []
    known_accounts = data.get("knownAccounts", {}) or {}
    creator_address_from_known = None
    amm_addresses_from_known = set()
    for addr, info in known_accounts.items():
        if info.get("type") == "CREATOR":
            creator_address_from_known = addr
        elif info.get("type") == "AMM":
            amm_addresses_from_known.add(addr)

    if lp_pool_address and lp_pool_address not in amm_addresses_from_known:
         amm_addresses_from_known.add(lp_pool_address)
         liquidity_a_account = (markets[0].get("liquidityAAccount", {}) or {}) if markets else {}
         owner_of_lp_pool_address = liquidity_a_account.get("owner")
         if owner_of_lp_pool_address and owner_of_lp_pool_address not in amm_addresses_from_known:
              amm_addresses_from_known.add(owner_of_lp_pool_address)

    num_holders_to_sum = 5
    holders_summed = 0
    if isinstance(top_holders, list):
        for holder in top_holders:
            holder_addr = holder.get("address")
            if holder_addr in amm_addresses_from_known or holder_addr == creator or holder_addr == creator_address_from_known:
                continue
            if holders_summed < num_holders_to_sum:
                holder_pct = holder.get("pct", 0.0)
                try:
                    holder_concentration_pct += float(holder_pct)
                except (ValueError, TypeError):
                    pass
                top_non_lp_holders.append(f"{float(holder_pct):.2f}%" if isinstance(holder_pct, (int, float)) else "N/A")
                holders_summed += 1
            else:
                break

    # --- Montagem do Output ---
    output = [f"--- Análise Rápida do Token [{symbol}] ({mint}) ---"]

    # Formata o score normalizado para exibição
    score_str = f"{score_display:.2f}" if isinstance(score_display, (int, float)) else "N/A"

    output.append("\n**Avaliação de Risco (RugCheck API):**")
    if rugged is True:
        output.append(f"  🔴 ALERTA: API marcou como RUGGED! (Score Norm: {score_str})")
    # Condição para 'Baixo Risco' - Sem riscos E autoridades renunciadas E LP bloqueada
    elif not risks and mint_authority is None and freeze_authority is None and lp_locked_pct == 100:
        output.append(f"  ✅ Baixo Risco Técnico Imediato (Score Norm: {score_str})")
    else:
        # Qualquer outra combinação é 'Indeterminado/Moderado'
        output.append(f"  ⚠️ Risco Indeterminado/Moderado (Score Norm: {score_str}, Rugged: {rugged}) - Revisar detalhes.")

    output.append("\n**Checagens de Segurança:**")
    output.append(f"  - Mint Renunciado: {'✅ Sim' if mint_authority is None else '❌ NÃO' if mint_authority else '❔ Desc.'}")
    output.append(f"  - Freeze Renunciado: {'✅ Sim' if freeze_authority is None else '❌ NÃO' if freeze_authority else '❔ Desc.'}")
    output.append(f"  - Liquidez Inicial Bloqueada: {'✅ Sim (100%)' if lp_locked_pct == 100 else f'⚠️ {lp_locked_pct:.1f}%' if isinstance(lp_locked_pct, (int, float)) and lp_locked_pct >= 0 else '❌ Não/Indisponível'}")
    if risks and isinstance(risks, list):
        risk_descriptions = [str(risk.get('type', risk.get('name', 'Detalhe Indisp.'))) if isinstance(risk, dict) else str(risk) for risk in risks]
        output.append(f"  - Riscos Detectados (API): ❌ Sim: {', '.join(risk_descriptions)}")
    else:
        output.append(f"  - Riscos Detectados (API): ✅ Nenhum")
    output.append(f"  - Metadados Mutáveis: {'⚠️ Sim' if is_mutable is True else '✅ Não' if is_mutable is False else '❔ Desc.'}")

    output.append("\n**Características (Dados RugCheck):**")
    output.append(f"  - Nome: {name}")
    output.append(f"  - Criador: {creator}")
    if supply_ui is not None:
         try:
           supply_formatted = f"{supply_ui:,.{decimals}f} {symbol}" if decimals > 0 else f"{int(supply_ui):,} {symbol}"
         except ValueError:
             supply_formatted = f"{supply_ui} {symbol}" # Fallback
         output.append(f"  - Supply Total: {supply_formatted}")
    else:
         output.append(f"  - Supply Total: N/A")

    if market_cap_usd is not None:
        output.append(f"  - Market Cap (Est.): ${market_cap_usd:,.2f}")
    else:
        output.append(f"  - Market Cap (Est.): N/A")

    output.append(f"  - Liquidez (Est.): ${total_liquidity_usd:,.2f}" if total_liquidity_usd is not None and isinstance(total_liquidity_usd, (int, float)) else "  - Liquidez (Est.): N/A")
    output.append(f"  - Total de Holders: {total_holders_count}")

    output.append("\n**Pontos de Atenção:**")
    output.append(f"  - Concentração (Top {num_holders_to_sum} ex-LP/Criador): {holder_concentration_pct:.2f}% ({', '.join(top_non_lp_holders)})")
    if holder_concentration_pct > 50:
         output.append("    -> ⚠️ ALTA concentração.")
    if total_liquidity_usd is not None and isinstance(total_liquidity_usd, (int, float)) and total_liquidity_usd < 10000:
         output.append(f"    -> ⚠️ BAIXA liquidez (${total_liquidity_usd:,.2f}).")
    if creator_balance_ui > 0:
         try:
             balance_formatted = f"{creator_balance_ui:,.{decimals}f}" if decimals > 0 else f"{int(creator_balance_ui):,}"
         except ValueError:
             balance_formatted = str(creator_balance_ui)
         output.append(f"    -> ⚠️ SALDO CRIADOR: {balance_formatted} {symbol}")
    elif creator_balance_ui == -1: # Indica erro no cálculo UI, mostra raw
         output.append(f"    -> ⚠️ SALDO CRIADOR (RAW): {creator_balance_raw}")

    output.append("\n*Nota: Análise técnica automatizada. Não é conselho financeiro.*")
    output.append("--------------------------------------------------")

    return "\n".join(output)