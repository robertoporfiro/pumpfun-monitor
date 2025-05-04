# pumpportal_monitor/utils.py
# (ou utils_reverted.py, ajuste o nome e a importação em websocket_client.py)
import logging
from typing import Dict, Any, Optional, List
from . import config

logger = logging.getLogger(__name__)

def format_analysis_output(mint: str, rugcheck_data: Dict[str, Any]) -> str:
    """Formata os dados da API RugCheck em um resumo legível (versão sem GMGN)."""

    # --- Bloco inicial de validação ---
    if not rugcheck_data or rugcheck_data.get("status") != "success":
        reason = rugcheck_data.get('reason', '?'); return f"[{mint}] Análise RC Indisponível (Status: {rugcheck_data.get('status', '?')}, Razão: {reason})\n-"*50
    data_rc = rugcheck_data.get("raw_data", {})
    if not data_rc: return f"[{mint}] Análise Indisponível (RC raw_data vazio)\n-"*50

    # --- Extração Dados RugCheck ---
    token_info = data_rc.get("token", {}) or {}; token_meta = data_rc.get("tokenMeta", {}) or {}
    file_meta = data_rc.get("fileMeta", {}) or {}; markets_rc = data_rc.get("markets", []) or []
    top_holders_rc = data_rc.get("topHolders", []) or []; name = token_meta.get("name") or file_meta.get("name", "N/A")
    symbol = token_meta.get("symbol") or file_meta.get("symbol", "N/A"); creator_rc = data_rc.get("creator", "N/A")
    decimals = token_info.get("decimals", 0);
    if not isinstance(decimals, int) or decimals < 0: decimals = 0
    supply_raw_rc = token_info.get("supply"); supply_ui_rc = None; market_cap_rc = None; price_rc = data_rc.get("price")
    if isinstance(supply_raw_rc, (int, float)) and supply_raw_rc >= 0:
        try:
            supply_ui_rc = float(supply_raw_rc) / (10**decimals) if decimals >= 0 else float(supply_raw_rc)
            if price_rc is not None and isinstance(price_rc, (int, float)) and price_rc >= 0: market_cap_rc = price_rc * supply_ui_rc
        except Exception: pass
    mint_auth_rc = data_rc.get("mintAuthority"); freeze_auth_rc = data_rc.get("freezeAuthority")
    mutable_rc = token_meta.get("mutable", None); risks_rc = data_rc.get("risks", []) or []
    score_norm_rc = data_rc.get("score_normalised"); rugged_rc = data_rc.get("rugged", None)
    liquidity_rc = data_rc.get("totalMarketLiquidity"); holders_rc = data_rc.get("totalHolders", "N/A")
    creator_balance_raw_rc = data_rc.get("creatorBalance", 0); insiders_rc = data_rc.get("graphInsidersDetected", 0)
    lp_locked_pct_rc = 0.0; lp_pool_address = None; owner_of_lp_pool_address = None
    if markets_rc and isinstance(markets_rc, list) and len(markets_rc) > 0 and markets_rc[0].get("marketType") == "pump_fun":
        lp_info = markets_rc[0].get("lp", {}) or {}; lp_locked_pct_rc = float(lp_info.get("lpLockedPct", 0.0))
        lp_pool_address = markets_rc[0].get("liquidityA"); owner_of_lp_pool_address = (markets_rc[0].get("liquidityAAccount", {}) or {}).get("owner")

    # --- Cálculos Holders ---
    holder_concentration_pct = 0.0; top_non_lp_holders = []; max_single_holder_pct_found = 0.0
    known_accounts = data_rc.get("knownAccounts", {}) or {}; creator_address_from_known = None; amm_addresses = set()
    for addr, info in known_accounts.items():
        if info.get("type") == "CREATOR": creator_address_from_known = addr
        elif info.get("type") == "AMM": amm_addresses.add(addr)
    if lp_pool_address: amm_addresses.add(lp_pool_address)
    if owner_of_lp_pool_address: amm_addresses.add(owner_of_lp_pool_address)
    num_holders_to_sum = 5; holders_summed = 0
    if isinstance(top_holders_rc, list):
        for holder in top_holders_rc:
            addr = holder.get("address");
            if addr in amm_addresses or addr == creator_rc or addr == creator_address_from_known: continue
            try:
                pct = float(holder.get("pct", 0.0)); max_single_holder_pct_found = max(max_single_holder_pct_found, pct)
                if holders_summed < num_holders_to_sum: holder_concentration_pct += pct; top_non_lp_holders.append(f"{pct:.2f}%"); holders_summed += 1
            except (ValueError, TypeError): pass
    creator_holding_pct_calculated = 0.0
    if isinstance(creator_balance_raw_rc, (int, float)) and creator_balance_raw_rc > 0 and isinstance(supply_raw_rc, (int, float)) and supply_raw_rc > 0:
        try: creator_holding_pct_calculated = (creator_balance_raw_rc / supply_raw_rc) * 100
        except Exception: pass

    # --- Montagem do Output ---
    output = [f"--- Análise Rápida do Token [{symbol}] ({mint}) ---"]
    score_str_rc = f"{score_norm_rc:.2f}" if isinstance(score_norm_rc, (int, float)) else "N/A"

    output.append("\n**Avaliação de Risco (RugCheck API):**")
    if rugged_rc is True: output.append(f"  🔴 ALERTA: API marcou como RUGGED! (Score Norm: {score_str_rc})")
    elif not risks_rc and mint_auth_rc is None and freeze_auth_rc is None and lp_locked_pct_rc == 100: output.append(f"  ✅ Baixo Risco Técnico Imediato (Score Norm: {score_str_rc})")
    else: output.append(f"  ⚠️ Risco Indeterminado/Moderado (Score Norm: {score_str_rc}, Rugged: {rugged_rc})")

    output.append("\n**Checagens de Segurança:**")
    rc_score_ok = score_norm_rc is not None and score_norm_rc >= config.MIN_RUGCHECK_SCORE
    output.append(f"  - Score Norm OK: {'✅ ' if rc_score_ok else '❌ '} ({score_str_rc} vs {config.MIN_RUGCHECK_SCORE})") # Apenas emoji
    output.append(f"  - Mint Renunciado: {'✅ Sim' if mint_auth_rc is None else '❌ NÃO'}")
    output.append(f"  - Freeze Renunciado: {'✅ Sim' if freeze_auth_rc is None else '❌ NÃO'}")
    output.append(f"  - LP Bloqueada: {'✅ 100%' if lp_locked_pct_rc == 100 else f'⚠️ {lp_locked_pct_rc:.1f}%'}")
    if risks_rc: risk_desc=[str(r.get('type',r.get('name','?'))) if isinstance(r,dict) else str(r) for r in risks_rc]; output.append(f"  - Riscos Contrato: ❌ {', '.join(risk_desc)}")
    else: output.append(f"  - Riscos Contrato: ✅ Nenhum")
    output.append(f"  - Metadados Mutáveis: {'⚠️ Sim' if mutable_rc is True else '✅ Não' if mutable_rc is False else '❔'}")
    ins_ok = insiders_rc <= config.FILTER_MAX_INSIDERS_DETECTED
    output.append(f"  - Insiders Detectados: {'✅ ' if ins_ok else '⚠️ '} {insiders_rc} (Limite: {config.FILTER_MAX_INSIDERS_DETECTED})") # Apenas emoji

    output.append("\n**Características e Holders (Dados RugCheck):**")
    output.append(f"  - Nome: {name} ({symbol}) | Criador: {creator_rc}")
    if supply_ui_rc is not None:
        try: supply_fmt = f"{supply_ui_rc:,.{decimals}f}" if decimals > 0 else f"{int(supply_ui_rc):,}"
        except ValueError: supply_fmt = f"{supply_ui_rc}"
        output.append(f"  - Supply: {supply_fmt}")
    else: output.append(f"  - Supply: N/A")
    if market_cap_rc is not None: output.append(f"  - MCap (Est): ${market_cap_rc:,.2f}")
    liq_ok = liquidity_rc is not None and (config.MIN_INITIAL_LIQUIDITY <= 0 or liquidity_rc >= config.MIN_INITIAL_LIQUIDITY)
    output.append(f"  - Liquidez (Est): ${liquidity_rc:,.2f} (Min: {config.MIN_INITIAL_LIQUIDITY:,.0f}) -> {'OK' if liq_ok else 'FAIL'}" if liquidity_rc is not None else "  - Liquidez: N/A") # OK/FAIL útil aqui? Mantido por enquanto.
    output.append(f"  - Holders: {holders_rc}")

    output.append("\n**Pontos de Atenção (Holders):**")
    # --- FORMATAÇÃO REVISADA (SEM -> OK/FAIL TEXTUAL) ---
    # Linha 1: Mostra apenas a soma e a lista dos Top X, e o limite de referência
    output.append(f"  - Concentração (Soma Top {holders_summed} ex-LP/Creator): {holder_concentration_pct:.2f}% [{', '.join(top_non_lp_holders)}] (Limite Holder Único: {config.FILTER_MAX_SINGLE_HOLDER_PCT}%)")
    # Linha 2: Mostra apenas emoji e valor do maior holder único
    sh_ok = max_single_holder_pct_found <= config.FILTER_MAX_SINGLE_HOLDER_PCT; sh_warn = "✅ " if sh_ok else "⚠️ "
    output.append(f"  - Maior Holder Único (Todos ex-LP/Creator): {sh_warn} {max_single_holder_pct_found:.2f}%")
    # Linha 3: Saldo Criador com emoji
    cb_ok = creator_holding_pct_calculated <= config.FILTER_MAX_CREATOR_HOLDING_PCT; cb_warn = "✅ " if cb_ok else "⚠️ "
    output.append(f"  - Saldo Criador: {cb_warn} {creator_holding_pct_calculated:.2f}% (Max: {config.FILTER_MAX_CREATOR_HOLDING_PCT}%)")
    # Linha 4: Alerta de baixa liquidez (mantido)
    if liquidity_rc is not None and liquidity_rc < 10000: output.append(f"    -> ⚠️ BAIXA liquidez (${liquidity_rc:,.2f}).")
    # --- FIM DAS MODIFICAÇÕES ---

    output.append("\n*Nota: Análise automatizada. Não é conselho financeiro.*")
    output.append("--------------------------------------------------")
    return "\n".join(output)