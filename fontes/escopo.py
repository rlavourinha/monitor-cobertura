"""Escopo do research: o que entra nas rotinas (coletores, leitura do sell-side, jornais). Fonte única = cobertura.json.

Pedido de 27/11/2026: "reduzir as rotinas para a minha cobertura atual". Entra:
  - as empresas de cobertura.json (padrões em COB_RX);
  - os setores dessas empresas (saúde, bancos/financeiro, seguros, telecom, meios de pagamento, aluguel de carros, academias);
  - macro e estratégia do Brasil (juros, inflação, fiscal, atividade, emprego, câmbio, fluxo, crédito), insumo do Ke e das teses.
Fica fora: as demais empresas e setores (construção, utilities, petróleo, mineração, varejo, agro...), LatAm ex-Brasil e diários.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

COBERTURA_JSON = Path(__file__).resolve().parent.parent / "cobertura.json"

# Regex por ticker para achar a empresa coberta no texto. Vários nomes ("BTG Pactual", "Bradesco", "Telefônica") também
# são CASAS de research ou outro emissor; por isso alguns exigem o ticker ou excluem o contexto da casa.
COB_RX = {
    "RDOR3": r"Rede D[’']?Or|RDOR3", "MATD3": r"Mater ?Dei|MATD3",
    "SAUD3": r"Bradsa[úu]de|Bradesco Sa[úu]de|Odontoprev|SAUD3", "RENT3": r"Localiza|RENT3",
    "SMFT3": r"Smart ?Fit|SMFT3", "TIMS3": r"\bTIM\b|TIMS3", "VIVT3": r"\bVivo\b|Telef[ôo]nica Brasil|VIVT3",
    "BBAS3": r"Banco do Brasil|BBAS3", "ITUB4": r"ITUB[34]|Ita[úu] Unibanco(?!\s+(?:Group|Holding|S\.?A|Corretora))",
    "BBDC4": r"Banco Bradesco|BBDC[34]|Bradesco(?! BBI| Corretora| Sa[úu]de)",
    "SANB11": r"Santander Brasil|SANB11", "BPAC11": r"\bBPAC(?:11|3)?\b",
    "CEF": r"Caixa Econ[ôo]mica Federal|\bCEF\b", "PSSA3": r"Porto Seguro|PSSA3",
    "BBSE3": r"BB Seguridade|BBSE3", "CXSE3": r"Caixa Seguridade|CXSE3", "IRBR3": r"\bIRB\b|IRBR3",
    "NU": r"Nubank|Nu Holdings|ROXO34", "XP": r"XP Inc|XPBR31|\bXP\b",
    "INTR": r"Inter ?& ?Co|Banco Inter|INBR32|\bINTR\b", "V": r"\bVisa\b",
    "MA": r"Mastercard|Master ?Card", "VZ": r"Verizon|\bVZ\b",
    "TEF": r"Telef[óo]nica S\.?A|Telef[óo]nica Espanha|\bTEF\b", "TIT": r"Telecom Italia|\bTIT\b",
}

# Setores das empresas cobertas (relatório setorial entra mesmo sem citar a empresa no título)
SETORES_COB = {
    "Saúde": r"health ?care|\bhealth\b|sa[úu]de|hospita|\bHMO\b|dental|planos? de sa[úu]de|medical",
    "Financeiro": r"\bbanks?\b|\bbanking\b|banc[oá]|financials|fintech|brokerage|corretora|asset management|\bNPL\b|"
                  r"cr[ée]dito banc|spreads? banc|BCB measures|macroprudential|macroprudenciais",
    "Seguros": r"insurance|insurer|seguro|segurad|reinsur|resseguro|previd[êe]ncia|capitaliza[çc]",
    "Telecom": r"telecom|\btelcos?\b|mobile pricing|wireless|fibra|fiber|spectrum|espectro|\bTMT\b",
    "Pagamentos": r"payments?|pagamentos|acquir|adquir[êe]n|\bcards?\b|cart[ãa]o|cart[õo]es|interchange|\bPix\b",
    "Aluguel de carros": r"car rental|rent-?a-?car|aluguel de (carros|ve[íi]culos)|loca[çc][ãa]o de (carros|ve[íi]culos)|"
                         r"\bfleet\b|\bMovida\b|used cars?|seminovos",
    "Academias": r"\bgyms?\b|fitness|academias?",
}

# Macro e estratégia do Brasil
MACRO_BR = (r"Brazil(ian)? (Macro|Economics|Strategy|Equity Strategy)|Macroeconomic Research\s*-\s*Brazil|Brazil Macro|"
            r"Equity Strategy|Brazil Strategy|Scenario Review|\bBRAZIL\s*[–-]|\bBRASIL\s*[–-]|"
            r"\bCopom\b|\bSelic\b|\bIPCA\b|\bIGP|\bCAGED\b|\bPNAD\b|IBC-?Br|fiscal|arcabou[çc]o|Tesouro|"
            r"current account|conta corrente|balan[çc]a comercial|c[âa]mbio|\bBRL\b|Ibovespa|\bIbov\b|"
            r"\bBCB\b|Banco Central|Central Bank|household debt|endividamento|elei[çc]")

FORA = (r"LatAm ex-Brasil|Latam Talking Points|\bChile\b|Argentin|Colombia|\bPeru\b|\bMexico\b|M[ée]xico|Paraguay|Uruguay|"
        r"Andean|Global Earlybird|Daily Roadmap|Daily Fuel|Daily Briefing|Week Ahead|Market Data Monitor|BanRep|IMACEC|"
        r"Falabella|Alpek|Mallplaza|CABA\b|Buenos Aires|FIBRA")


def tickers() -> list[str]:
    try:
        return list(json.loads(COBERTURA_JSON.read_text(encoding="utf-8"))["empresas"])
    except Exception:
        return list(COB_RX)


def cobertura() -> dict[str, str]:
    """{ticker: regex} só dos tickers de cobertura.json; ticker sem padrão curado usa ticker + 1ª palavra do nome."""
    try:
        emp = json.loads(COBERTURA_JSON.read_text(encoding="utf-8"))["empresas"]
    except Exception:
        return dict(COB_RX)
    return {tk: COB_RX.get(tk) or rf"{re.escape(tk)}|{re.escape((v.get('nome') or tk).split()[0])}" for tk, v in emp.items()}


def lista_texto() -> str:
    """Cobertura por extenso, para os prompts: 'RDOR3 (Rede D'Or), ...'."""
    try:
        emp = json.loads(COBERTURA_JSON.read_text(encoding="utf-8"))["empresas"]
        return ", ".join(f"{tk} ({(v.get('nome') or tk)})" for tk, v in emp.items())
    except Exception:
        return ", ".join(COB_RX)


def destaque_rx() -> re.Pattern:
    """Um regex só (título/empresa/tipo do relatório) para os coletores marcarem destaque = dentro do escopo."""
    partes = list(cobertura().values()) + list(SETORES_COB.values()) + [MACRO_BR]
    return re.compile("|".join(f"(?:{p})" for p in partes), re.I)


def classifica(base: str, amostra: str = "") -> dict:
    """base = título + empresa + tipo; amostra = base + começo do texto. Devolve cobertura, setores_cob, macro, dentro."""
    cob = [tk for tk, rx in cobertura().items() if re.search(rx, amostra or base, re.I)]
    setores = [s for s, rx in SETORES_COB.items() if re.search(rx, base, re.I)]
    macro = bool(re.search(MACRO_BR, base, re.I))
    fora = bool(re.search(FORA, base, re.I))
    return {"cobertura": cob, "setores_cob": setores, "macro": macro, "dentro": (bool(cob) or bool(setores) or macro) and not fora}


def no_escopo(base: str, amostra: str = "") -> bool:
    return classifica(base, amostra)["dentro"]
