"""Configuração do Monitor de Investimentos.

Universo, caminhos e convenções. Tudo que é "premissa de projeto" mora aqui;
os coletores e o builder só leem.
"""
from __future__ import annotations

from pathlib import Path

RAIZ = Path(__file__).resolve().parent
DATA = RAIZ / "data"                  # caches de coleta (gerado; não versionar)
OUTPUT = RAIZ / "output"              # HTML final (gerado)
ESTIMATIVAS = RAIZ / "estimativas"    # minhas estimativas (versionar)
CONSENSO = RAIZ / "consenso"          # consenso (Bloomberg CSV + fallback Yahoo)

VERSAO = "0.1"

# Tipografia: True = todo o texto (HTML e SVG) em itálico, pedido de 25/09/2026. False = romano.
ESTILO_ITALICO = True
# Famílias (Google Fonts, embutidas por tipografia.py): títulos = FONTE_TITULO, corpo/números = FONTE_CORPO.
FONTES_GOOGLE = ["Playfair Display:ital,wght@0,400;0,500;0,600;1,400;1,500;1,600",
                 "Source Sans 3:ital,wght@0,400;0,500;0,600;1,400;1,500;1,600"]
FONTE_TITULO = '"Playfair Display", Georgia, "Times New Roman", serif'
FONTE_CORPO = '"Source Sans 3", "Segoe UI", system-ui, sans-serif'

# Universo. Chave = ticker B3. Preencha 'modelo' com o caminho do Excel quando
# existir uma aba E-A no padrão (ver fontes/modelo_ea.py); None = usa o CSV manual.
UNIVERSO: dict[str, dict] = {
    "RDOR3": {
        "nome": "Rede D'Or",
        "razao": "Rede D'Or São Luiz S.A.",
        "cod_b3": "RDOR",
        "cod_cvm": "24821",
        "cnpj": "06.047.087/0001-39",
        "inicio": 2020,          # 1º ano no COTAHIST (IPO dez/2020)
        "modelo": None,
        "yahoo": "RDOR3.SA",
    },
    "SAUD3": {
        "nome": "Bradsaúde",
        "razao": "Bradsaúde S.A. (Bradesco Saúde + Odontoprev)",
        "cod_b3": "SAUD",
        "cod_cvm": "20125",
        "cnpj": None,
        "inicio": 2026,          # ticker SAUD3 negocia desde mai/2026 (antes era ODPV3, outra entidade)
        "modelo": None,
        "yahoo": "SAUD3.SA",
    },
}

# Anos fiscais acompanhados no painel (corrente e próximo).
ANOS_FISCAIS = [2026, 2027]

# Schema comum de estimativas (minhas e consenso). Valores monetários em R$ milhões,
# EPS e target em R$/ação. 'data' = data da estimativa (para o histórico de revisões).
CAMPOS = ["ticker", "data", "ano", "receita", "ebitda", "lucro", "eps", "target", "n_analistas", "fonte"]

# ---------------------------------------------------------------- camada Macro
# Focus (Olinda): indicadores anuais acompanhados e desde quando puxar o histórico diário.
FOCUS_INDICADORES = ["IPCA", "Selic", "PIB Total", "Câmbio"]
FOCUS_DESDE = "2021-01-01"
# Janela curta para o painel completo (28 indicadores × todos os anos): basta para Δ 4 semanas e Δ 3 meses.
FOCUS_TODOS_DESDE = "2026-05-01"

# Assertividade do Focus: indicadores e 1º ano de referência (histórico da API começa em 2000-2001).
FOCUS_ASSERT_INDICADORES = {"IPCA": (433, "ipca"), "Selic": (432, "selic"), "PIB Total": (7326, "pib"), "Câmbio": (1, "cambio")}
FOCUS_ASSERT_ANO_INI = 2002
FOCUS_ASSERT_HORIZONTES = [24, 18, 12, 9, 6, 3, 1, 0]   # meses antes do fim do ano de referência

# Grupos do painel completo do Focus (nome exato do indicador na API; ' · ' separa IndicadorDetalhe).
FOCUS_GRUPOS = {
    "Inflação, juros e câmbio": ["IPCA", "IPCA Livres", "IPCA Administrados", "IPCA Serviços", "IPCA Bens industrializados",
                                 "IPCA Alimentação no domicílio", "IGP-M", "Selic", "Câmbio"],
    "Atividade e emprego": ["PIB Total", "PIB Agropecuária", "PIB Indústria", "PIB Serviços", "PIB Despesa de consumo das famílias",
                            "PIB Despesa de consumo da administração pública", "PIB Formação Bruta de Capital Fixo",
                            "PIB Exportação de bens e serviços", "PIB Importação de bens e serviços", "Taxa de desocupação"],
    "Setor externo e fiscal": ["Balança comercial · Exportações", "Balança comercial · Importações", "Balança comercial · Saldo",
                               "Conta corrente", "Investimento direto no país", "Resultado primário", "Resultado nominal",
                               "Dívida líquida do setor público", "Dívida bruta do governo geral"],
}
FOCUS_UNIDADES = {"Câmbio": "R$/US$", "Balança comercial · Exportações": "US$ bi", "Balança comercial · Importações": "US$ bi",
                  "Balança comercial · Saldo": "US$ bi", "Conta corrente": "US$ bi", "Investimento direto no país": "US$ bi",
                  "Resultado primário": "% PIB", "Resultado nominal": "% PIB", "Dívida líquida do setor público": "% PIB",
                  "Dívida bruta do governo geral": "% PIB"}

# Realizado (SGS): código -> (rótulo, desde)
SGS_SERIES = {
    433: ("IPCA mensal (%)", "2015-01-01"),
    432: ("Selic meta (% a.a.)", "2015-01-01"),
    1: ("USD/BRL venda", "2015-01-01"),
    12: ("CDI diário (% a.d.)", "2023-01-01"),
    7326: ("PIB variação anual (%)", "2000-01-01"),
    24364: ("IBC-Br (índice dessaz.)", "2015-01-01"),
    20539: ("Crédito saldo total (R$ mi)", "2015-01-01"),
    28763: ("CAGED estoque de empregos", "2020-01-01"),
}

# Mercado (Yahoo, atraso): chave -> símbolo. Minério (TIO=F) ficou fora: contrato ilíquido, preço inconsistente.
MERCADO = {
    "Ibovespa": "^BVSP", "S&P 500": "^GSPC", "Nasdaq": "^IXIC", "Euro Stoxx 50": "^STOXX50E",
    "USD/BRL": "BRL=X", "Brent (US$)": "BZ=F", "VIX": "^VIX", "Treasury 10a (%)": "^TNX",
}
# Papéis avulsos para o quadro de movimentos recentes (fora da cobertura).
MOVIMENTOS = {"ENEV3": "ENEV3.SA", "PRIO3": "PRIO3.SA", "NVDA": "NVDA", "NU": "NU"}

# Papéis que trocaram de código sem alterar a ação (novo -> antigo): a série do novo é emendada com a do antigo
# antes da troca. Necessário para a decomposição do Ibovespa em janelas longas.
ALIAS_TICKER = {"AXIA3": "ELET3", "EMBJ3": "EMBR3", "NATU3": "NTCO3", "MOTV3": "CCRO3", "MBRF3": "MRFG3"}

# Fundos cuja carteira é lida na CVM (CDA mensal, sigilo de 180 dias). Usar o CNPJ do MASTER (o feeder só tem cotas).
FUNDOS_CVM = {"Dynamo Cougar": "37.916.879/0001-26"}

# P/L 12m à frente do Ibovespa (para Ibov = Lucro/(Ke-g)). None = aguardando Bloomberg (IBOV Index BEST_PE_RATIO).
IBOV_PE_FWD: float | None = None

# Ponte Bloomberg: campos BDP usados no template (fontes/bloomberg.py).
BLOOMBERG_CAMPOS = {
    "receita": "BEST_SALES",
    "ebitda": "BEST_EBITDA",
    "lucro": "BEST_NET_INCOME",
    "eps": "BEST_EPS",
    "target": "BEST_TARGET_PRICE",
    "n_analistas": "TOT_ANALYST_REC",
}

for p in (DATA, OUTPUT, ESTIMATIVAS, CONSENSO):
    p.mkdir(exist_ok=True)
