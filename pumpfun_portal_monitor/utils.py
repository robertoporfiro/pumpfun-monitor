# pumpportal_monitor/utils.py
import logging
from typing import Dict, Any, Optional, Set, List
from . import config

logger = logging.getLogger(__name__)

def format_analysis_output(mint: str, api_data: Dict[str, Any]) -> str:
    """Formata os dados da API de análise em um resumo legível."""

    if not api_data or api_data.get("status") != "success":
        reason = api_data.get('reason', 'Status não foi success')
        return f"[{mint}] Análise Indisponível (Status: {api_data.get('status', 'N/A')}, Razão: {reason})"

    data = api_data.get("raw_data", {})
    if not data:
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
    if not isinstance(decimals, int) or decimals < 0: decimals = 0

    # --- CORREÇÃO: Extrair token_supply_raw AQUI ---
    supply_raw = token_info.get("supply") # Pode ser None ou número
    # --- FIM DA CORREÇÃO ---

    supply_ui = None
    market_cap_usd = None
    price_usd = data.get("price")

    # Calcula supply_ui e market_cap se supply_raw for válido
    if isinstance(supply_raw, (int, float)) and supply_raw >= 0:
        try:
            supply_ui = float(supply_raw) / (10**decimals)
            if price_usd is not None and isinstance(price_usd, (int, float)) and price_usd >= 0:
                 market_cap_usd = price_usd * supply_ui
        except (ValueError, TypeError, OverflowError) as e:
            logger.warning(f"[{mint}] Erro ao calcular supply UI ou Market Cap: {e}")
            supply_ui = supply_raw # Fallback para raw

    mint_authority = data.get("mintAuthority", "Erro ao ler")
    freeze_authority = data.get("freezeAuthority", "Erro ao ler")
    is_mutable = token_meta.get("mutable", None)
    risks = data.get("risks", []) or []
    score_display = data.get("score_normalised")
    rugged = data.get("rugged", None)
    total_liquidity_usd = data.get("totalMarketLiquidity")
    total_holders_count = data.get("totalHolders", "N/A")

    creator_balance_raw = data.get("creatorBalance", 0)
    creator_balance_ui = 0.0
    creator_holding_pct_calculated = 0.0
    if isinstance(creator_balance_raw, (int, float)) and creator_balance_raw > 0:
        try:
            creator_balance_ui = float(creator_balance_raw) / (10**decimals)
            # --- CORREÇÃO: Usar supply_raw que já foi definido ---
            if isinstance(supply_raw, (int, float)) and supply_raw > 0:
                creator_holding_pct_calculated = (creator_balance_raw / supply_raw) * 100
            # --- FIM DA CORREÇÃO ---
        except (ValueError, TypeError, OverflowError):
            creator_balance_ui = -1

    lp_locked_pct = 0
    lp_pool_address = None
    owner_of_lp_pool_address = None
    if markets and isinstance(markets, list) and len(markets) > 0 and markets[0].get("marketType") == "pump_fun":
        lp_info = markets[0].get("lp", {}) or {}
        lp_locked_pct = lp_info.get("lpLockedPct", 0)
        lp_pool_address = markets[0].get("liquidityA")
        owner_of_lp_pool_address = (markets[0].get("liquidityAAccount", {}) or {}).get("owner")

    holder_concentration_pct = 0.0
    top_non_lp_holders = []
    max_single_holder_pct_found = 0.0
    known_accounts = data.get("knownAccounts", {}) or {}
    creator_address_from_known = None
    amm_addresses_from_known = set()
    for addr, info in known_accounts.items():
        if info.get("type") == "CREATOR": creator_address_from_known = addr
        elif info.get("type") == "AMM": amm_addresses_from_known.add(addr)
    if lp_pool_address: amm_addresses_from_known.add(lp_pool_address)
    if owner_of_lp_pool_address: amm_addresses_from_known.add(owner_of_lp_pool_address)

    num_holders_to_sum = 5
    holders_summed = 0
    if isinstance(top_holders, list):
        for holder in top_holders:
            holder_addr = holder.get("address")
            if holder_addr in amm_addresses_from_known or holder_addr == creator or holder_addr == creator_address_from_known:
                continue
            try:
                current_pct = float(holder.get("pct", 0.0))
                max_single_holder_pct_found = max(max_single_holder_pct_found, current_pct)
                if holders_summed < num_holders_to_sum:
                    holder_concentration_pct += current_pct
                    top_non_lp_holders.append(f"{current_pct:.2f}%")
                    holders_summed += 1
            except (ValueError, TypeError): pass

    insiders_detected = data.get("graphInsidersDetected", 0)

    # --- Montagem do Output ---
    output = [f"--- Análise Rápida do Token [{symbol}] ({mint}) ---"]
    score_str = f"{score_display:.2f}" if isinstance(score_display, (int, float)) else "N/A"

    output.append("\n**Avaliação de Risco (RugCheck API):**")
    if rugged is True: output.append(f"  🔴 ALERTA: API marcou como RUGGED! (Score Norm: {score_str})")
    elif not risks and mint_authority is None and freeze_authority is None and lp_locked_pct == 100: output.append(f"  ✅ Baixo Risco Técnico Imediato (Score Norm: {score_str})")
    else: output.append(f"  ⚠️ Risco Indeterminado/Moderado (Score Norm: {score_str}, Rugged: {rugged}) - Revisar detalhes.")

    output.append("\n**Checagens de Segurança:**")
    output.append(f"  - Mint Renunciado: {'✅ Sim' if mint_authority is None else '❌ NÃO' if mint_authority else '❔ Desc.'}")
    output.append(f"  - Freeze Renunciado: {'✅ Sim' if freeze_authority is None else '❌ NÃO' if freeze_authority else '❔ Desc.'}")
    output.append(f"  - Liquidez Inicial Bloqueada: {'✅ Sim (100%)' if lp_locked_pct == 100 else f'⚠️ {lp_locked_pct:.1f}%' if isinstance(lp_locked_pct, (int, float)) and lp_locked_pct >= 0 else '❌ Não/Indisponível'}")
    if risks and isinstance(risks, list):
        risk_descriptions = [str(risk.get('type', risk.get('name', 'Detalhe Indisp.'))) if isinstance(risk, dict) else str(risk) for risk in risks]
        output.append(f"  - Riscos Detectados (API): ❌ Sim: {', '.join(risk_descriptions)}")
    else: output.append(f"  - Riscos Detectados (API): ✅ Nenhum")
    output.append(f"  - Metadados Mutáveis: {'⚠️ Sim' if is_mutable is True else '✅ Não' if is_mutable is False else '❔ Desc.'}")

    output.append("\n**Características (Dados RugCheck):**")
    output.append(f"  - Nome: {name}")
    output.append(f"  - Criador: {creator}")
    if supply_ui is not None:
         try: supply_formatted = f"{supply_ui:,.{decimals}f} {symbol}" if decimals > 0 else f"{int(supply_ui):,} {symbol}"
         except ValueError: supply_formatted = f"{supply_ui} {symbol}"
         output.append(f"  - Supply Total: {supply_formatted}")
    else: output.append(f"  - Supply Total: N/A (Raw: {supply_raw})") # Mostra raw se UI falhou
    if market_cap_usd is not None: output.append(f"  - Market Cap (Est.): ${market_cap_usd:,.2f}")
    else: output.append(f"  - Market Cap (Est.): N/A")
    output.append(f"  - Liquidez (Est.): ${total_liquidity_usd:,.2f}" if total_liquidity_usd is not None and isinstance(total_liquidity_usd, (int, float)) else "  - Liquidez (Est.): N/A")
    output.append(f"  - Total de Holders: {total_holders_count}")

    output.append("\n**Pontos de Atenção:**")
    output.append(f"  - Concentração (Top {num_holders_to_sum} ex-LP/Criador): {holder_concentration_pct:.2f}% ({', '.join(top_non_lp_holders)})")
    single_holder_warning = "⚠️" if max_single_holder_pct_found > config.FILTER_MAX_SINGLE_HOLDER_PCT else "✅"
    output.append(f"  - Maior Holder Único (ex-LP/Criador): {single_holder_warning} {max_single_holder_pct_found:.2f}% (Limite: {config.FILTER_MAX_SINGLE_HOLDER_PCT}%)")
    insider_warning = "⚠️" if insiders_detected > config.FILTER_MAX_INSIDERS_DETECTED else "✅"
    output.append(f"  - Insiders Detectados (API): {insider_warning} {insiders_detected} (Limite: {config.FILTER_MAX_INSIDERS_DETECTED})")
    if total_liquidity_usd is not None and isinstance(total_liquidity_usd, (int, float)) and total_liquidity_usd < 10000:
         output.append(f"    -> ⚠️ BAIXA liquidez (${total_liquidity_usd:,.2f}).")
    if creator_balance_ui == -1: # Erro no cálculo UI
         output.append(f"    -> ⚠️ SALDO CRIADOR (RAW): {creator_balance_raw} (Limite Pct: {config.FILTER_MAX_CREATOR_HOLDING_PCT}%)")
    elif creator_holding_pct_calculated > config.FILTER_MAX_CREATOR_HOLDING_PCT: # Excede limite
         output.append(f"    -> ⚠️ SALDO CRIADOR: {creator_holding_pct_calculated:.2f}% (Limite: {config.FILTER_MAX_CREATOR_HOLDING_PCT}%)")
    # Não loga nada se for 0 ou abaixo do limite

    output.append("\n*Nota: Análise técnica automatizada. Não é conselho financeiro.*")
    output.append("--------------------------------------------------")

    return "\n".join(output)