"""Coleta por JANELA de atualização. Cada fonte grava seu próprio arquivo e registra estado em data/estado.json;
se uma fonte falha, o dado anterior fica e o painel mostra o carimbo antigo.

    python coletar.py --janela intraday   # cotações com atraso (Yahoo): universo, índices, movimentos       ~10 s
    python coletar.py --janela diario     # COTAHIST do dia (B3), nº de ações, Tesouro, SGS diários, Yahoo 5a,
                                          # consenso Yahoo, minhas estimativas (modelo E-A)                    ~1 min
    python coletar.py --janela semanal    # Focus completo (sai toda segunda) + séries longas do ano corrente ~2 min
    python coletar.py --janela mensal     # SGS mensais/anuais (IPCA, PIB, IBC-Br, crédito, CAGED) + realizado ~1 min
    python coletar.py --janela tudo       # todas as janelas (primeira carga)
    python coletar.py --ponte / --consenso-bloomberg   # sob demanda, na máquina com terminal
    python coletar.py --janela diario --so-macro       # compatibilidade: --so-macro = semanal + mensal + diario(macro)

Cadência sugerida (agendar.ps1): intraday a cada hora 10h–18h em dias úteis; diário 19h30; semanal segunda 08h30;
mensal dia 5 às 08h. Depois de cada janela, `python build.py`.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import traceback
from datetime import date, datetime

import config
from fontes import anbima, b3, b3_bdi, bcb, bloomberg, curva, cvm_cda, cvm_inf_diario, ibov_wayback, modelo_ea, tesouro, yahoo

MINHAS = config.ESTIMATIVAS / "minhas.csv"
CONS = config.CONSENSO / "consenso.csv"
ESTADO = config.DATA / "estado.json"
MACRO = config.DATA / "macro.json"
MERCADO = config.DATA / "mercado.json"


# ----------------------------------------------------------------------------- utilitários
def _le_json(p, padrao):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else padrao


def _grava_json(p, obj) -> None:
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _atualiza_macro(**chaves) -> None:
    """Atualiza só as chaves informadas em macro.json (as demais janelas não se sobrescrevem)."""
    m = _le_json(MACRO, {})
    m.update(chaves)
    m["gerado_em"] = date.today().isoformat()
    hoje = date.today()
    m.setdefault("anos", [hoje.year, hoje.year + 1])
    _grava_json(MACRO, m)


def _le_csv(p) -> list[dict]:
    if not p.exists():
        return []
    with p.open(encoding="utf-8", newline="") as fh:
        return [r for r in csv.DictReader(fh) if r.get("ticker")]


def _grava_csv(p, linhas: list[dict]) -> None:
    with p.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=config.CAMPOS)
        w.writeheader()
        for r in linhas:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in config.CAMPOS})


def _acrescenta(p, novas: list[dict]) -> int:
    """Acrescenta linhas; substitui se já existir (ticker, ano, data, fonte) igual."""
    atual = _le_csv(p)
    chave = lambda r: (r["ticker"], str(r["ano"]), r["data"], r["fonte"])
    idx = {chave(r): i for i, r in enumerate(atual)}
    n = 0
    for r in novas:
        k = chave(r)
        if k in idx:
            atual[idx[k]] = r
        else:
            atual.append(r)
            n += 1
    atual.sort(key=lambda r: (r["ticker"], str(r["ano"]), r["data"]))
    _grava_csv(p, atual)
    return n


def _passo(nome: str, fn) -> bool:
    """Executa uma fonte isoladamente e registra o estado (ok/erro + hora). Falha não derruba as demais."""
    est = _le_json(ESTADO, {})
    agora = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        msg = fn()
        est[nome] = {"em": agora, "ok": True, "msg": str(msg or "")}
        print(f"  [ok] {nome}: {msg or ''}")
        ok = True
    except Exception as e:
        est[nome] = {**est.get(nome, {}), "ok": False, "erro_em": agora, "msg": f"{type(e).__name__}: {e}"}
        print(f"  [ERRO] {nome}: {type(e).__name__}: {e}", file=sys.stderr)
        traceback.print_exc(limit=1)
        ok = False
    _grava_json(ESTADO, est)
    return ok


# ----------------------------------------------------------------------------- janela INTRADAY
def intraday_universo():
    m = _le_json(MERCADO, {"tickers": {}})
    for tk, v in config.UNIVERSO.items():
        intr = yahoo.intraday(v["yahoo"])
        if intr and intr.get("preco"):
            m["tickers"].setdefault(tk, {})["intraday"] = intr
    m["gerado_em"] = date.today().isoformat()
    _grava_json(MERCADO, m)
    return ", ".join(f"{tk} {m['tickers'][tk]['intraday']['preco']}" for tk in config.UNIVERSO if m["tickers"].get(tk, {}).get("intraday"))


def intraday_mercado():
    _atualiza_macro(mercado=yahoo.cotacoes(config.MERCADO), movimentos=yahoo.cotacoes(config.MOVIMENTOS))
    return f"{len(config.MERCADO)} índices, {len(config.MOVIMENTOS)} papéis"


# ----------------------------------------------------------------------------- janela DIÁRIA
def diario_cotahist():
    n = b3.atualiza_cotahist_diario()
    return "anual rebaixado" if n < 0 else f"+{n} pregões"


def diario_acoes():
    m = _le_json(MERCADO, {"tickers": {}})
    for tk, v in config.UNIVERSO.items():
        a = b3.numero_acoes(v["cod_b3"])
        if a:
            m["tickers"].setdefault(tk, {})["acoes"] = a
    _grava_json(MERCADO, m)
    return ", ".join(f"{tk} {m['tickers'][tk].get('acoes', 0):,}".replace(",", ".") for tk in config.UNIVERSO)


def diario_tesouro():
    c = tesouro.ntnb()
    _atualiza_macro(ntnb=c, ntnb_2035=tesouro.ntnb_historico("2035"))
    return f"curva {c.get('data')} ({len(c.get('curva', []))} venc.)"


def diario_ettj():
    novas = anbima.atualiza(7)
    return f"+{len(novas)} fotografias ({', '.join(novas) or 'nenhuma nova'}); total {len(anbima.fotos())}"


def diario_curva_tesouro():
    c = curva.constroi()
    return f"pré {len(c['pre'])} dias desde {c['pre'][0][0] if c['pre'] else '—'}; real {len(c['real'])} dias"


def diario_sgs():
    sgs = _le_json(MACRO, {}).get("sgs", {})
    for cod in (432, 1, 12):
        rotulo, desde = config.SGS_SERIES[cod]
        sgs[str(cod)] = {"rotulo": rotulo, "serie": bcb.sgs(cod, desde)}
    _atualiza_macro(sgs=sgs)
    return "Selic meta, PTAX e CDI diário"


def diario_yahoo_hist():
    hist = {k: yahoo.historico(v, "max") for k, v in {"Ibovespa": "^BVSP", "S&P 500": "^GSPC", "USD/BRL": "BRL=X", "Brent (US$)": "BZ=F", "VIX": "^VIX", "Treasury 10a (%)": "^TNX"}.items()}
    _atualiza_macro(hist=hist, mov_hist={k: yahoo.historico(v, "3mo") for k, v in config.MOVIMENTOS.items()})
    return f"{sum(len(v) for v in hist.values())} pontos"


def diario_ibov_comp():
    """Carteira do Ibovespa (B3) + fechamentos oficiais de cada papel (COTAHIST, cache) -> data/ibov_comp.json.
    O último ponto intraday vem à parte (intraday_ibov_comp, Yahoo), como no universo coberto."""
    from datetime import timedelta
    c = b3.carteira_ibov()
    antigo = _le_json(config.DATA / "ibov_comp.json", {})
    _grava_json(config.DATA / "ibov_comp.json", {"data": c["data"], "itens": c["itens"], "redutor": c.get("redutor"),
                                                 "intraday": antigo.get("intraday", {}), "proventos": antigo.get("proventos", {}),
                                                 "prov_yahoo": antigo.get("prov_yahoo", {})})   # histórico do Yahoo vem do passo semanal
    # fotografia diária (quantidades teóricas + redutor): base da decomposição exata entre duas datas
    fdir = config.DATA / "ibov_carteira"; fdir.mkdir(exist_ok=True)
    if c.get("data"):
        _grava_json(fdir / f"{c['data']}.json", {"data": c["data"], "redutor": c.get("redutor"), "q": {it["cod"]: it["q"] for it in c["itens"]},
                                                 "peso": {it["cod"]: it["peso"] for it in c["itens"]}, "fonte": "B3 GetPortfolioDay"})
    b3.atualiza_cotahist_diario()          # garante que o cache cobre a carteira (rebaixa o anual uma vez se preciso)
    cods = {it["cod"] for it in c["itens"]}
    for f in fdir.glob("*.json"):                               # papéis de fotografias antigas (já saíram do índice): preço na data inicial
        cods |= set(_le_json(f, {}).get("q", {}))
    desde_h = f"{getattr(config, 'COTAHIST_DESDE', 2021)}-01-01"
    hist = {cod: [[d, v] for d, v in b3.serie(cod) if d >= desde_h] for cod in sorted(cods)}   # base do clique no gráfico do Painel
    hist = {k: v for k, v in hist.items() if v}
    ibov_wayback.preenche_redutores(hist, _le_json(MACRO, {}).get("hist", {}).get("Ibovespa", []))
    hist = {k: v for k, v in hist.items() if v}
    # proventos com data-com nos últimos 13 meses: reconstrução da quantidade teórica (janelas curtas) e dividend yield 12 m
    desde = (date.today() - timedelta(days=400)).isoformat()
    prov = {}
    for it in c["itens"]:
        prov[it["cod"]] = b3.proventos_ticker(it["cod"], it.get("classe", "ON"), desde)
    obj = _le_json(config.DATA / "ibov_comp.json", {}); obj["hist"] = hist; obj["proventos"] = prov
    _grava_json(config.DATA / "ibov_comp.json", obj)
    return f"{len(c['itens'])} papéis, {len(hist)} com histórico B3, {sum(len(v) for v in prov.values())} proventos recentes, carteira de {c['data']}"


DY_ARQ = config.DATA / "ibov_dy.json"


def diario_ibov_dy():
    """Dividend yield 12 m do Ibovespa pela identidade do índice: DY = Σ q_i·D_i(12 m) / Σ q_i·p_i (q = quantidade teórica
    da B3, D = dividendos + JCP brutos por papel com data-com nos últimos 12 meses, p = último fechamento oficial).
    Guarda a série diária exata em data/ibov_dy.json (uma linha por dia, sobrescrita se rodar de novo no mesmo dia)."""
    from datetime import timedelta
    C = _le_json(config.DATA / "ibov_comp.json", {})
    itens, hist, prov = C.get("itens", []), C.get("hist", {}), C.get("proventos", {})
    if not itens or not hist:
        return "sem carteira/histórico; rode a janela diária completa"
    t = max(h[-1][0] for h in hist.values() if h)
    t0 = (date.fromisoformat(t) - timedelta(days=365)).isoformat()
    num = den = 0.0
    papeis = []
    for it in itens:
        h = hist.get(it["cod"]) or []
        if not h:
            continue
        p = h[-1][1]
        d12 = sum(x["valor"] for x in prov.get(it["cod"], []) if x.get("valor") and t0 < x.get("com", "") <= t)
        q = float(it.get("q") or 0)
        num += q * d12; den += q * p
        papeis.append([it["cod"], it["setor"], it["peso"], d12 / p * 100 if p else None])
    dy = num / den * 100 if den else None
    for r in papeis:                                             # contribuição = peso × DY do papel (em p.p. do DY do índice)
        r.append(r[2] / 100 * r[3] if r[3] is not None else None)
    D = _le_json(DY_ARQ, {"exato": [], "aprox_mensal": []})
    D["exato"] = [x for x in D.get("exato", []) if x[0] != t] + [[t, round(dy, 4)]] if dy is not None else D.get("exato", [])
    D["exato"].sort()
    D["papeis"] = sorted(papeis, key=lambda r: -(r[4] or 0)); D["data"] = t
    _grava_json(DY_ARQ, D)
    return f"DY 12 m {dy:.2f}% em {t}" if dy is not None else "sem DY"


def semanal_ibov_dy_hist():
    """Histórico mensal aproximado do DY do Ibovespa: dividendos (Yahoo, events=div) e fechamentos (Yahoo, ajustados por
    desdobramento) de cada papel da carteira ATUAL, ponderados pela quantidade teórica de hoje. Aproximado porque a
    composição muda a cada quadrimestre e o Yahoo mistura JCP/dividendos; serve para ver o nível relativo ao passado."""
    from datetime import timedelta
    C = _le_json(config.DATA / "ibov_comp.json", {})
    itens = C.get("itens", [])
    if not itens:
        return "sem carteira"
    divs, closes = {}, {}
    for it in itens:
        sym = it["cod"] + ".SA"
        divs[it["cod"]] = yahoo.dividendos(sym)
        closes[it["cod"]] = yahoo.historico(sym, "max")
    # guarda os dividendos históricos (data ex, valor) em ibov_comp.json: a decomposição usa-os para reconstruir a
    # quantidade teórica em datas anteriores à cobertura do canal da B3 (~13 meses)
    obj = _le_json(config.DATA / "ibov_comp.json", {})
    obj["prov_yahoo"] = {k: [[d, v] for d, v in vs if d >= "2019-01-01"] for k, vs in divs.items() if vs}
    _grava_json(config.DATA / "ibov_comp.json", obj)
    ini = date(2010, 1, 31)
    meses = []
    m = ini
    while m <= date.today():
        meses.append(m.isoformat())
        nxt = (m.replace(day=1) + timedelta(days=32)).replace(day=1)
        m = (nxt + timedelta(days=32)).replace(day=1) - timedelta(days=1)   # último dia do mês seguinte
    serie = []
    for me in meses:
        t0 = (date.fromisoformat(me) - timedelta(days=365)).isoformat()
        num = den = 0.0; n = 0
        for it in itens:
            h = closes.get(it["cod"]) or []
            p = next((v for d, v in reversed(h) if d <= me), None)
            if not p or h[0][0] > t0:                        # papel sem 12 meses completos de história na data
                continue
            d12 = sum(v for d, v in divs.get(it["cod"], []) if t0 < d <= me)
            q = float(it.get("q") or 0)
            num += q * d12; den += q * p; n += 1
        if den and n >= len(itens) * 0.6:
            serie.append([me, round(num / den * 100, 4), n])
    D = _le_json(DY_ARQ, {"exato": []})
    D["aprox_mensal"] = serie; D["aprox_em"] = date.today().isoformat()
    _grava_json(DY_ARQ, D)
    return f"{len(serie)} meses ({serie[0][0] if serie else '—'} a {serie[-1][0] if serie else '—'})"


def intraday_ibov_comp():
    """Cotação com atraso dos papéis do Ibovespa (Yahoo) -> último ponto da decomposição no dia."""
    obj = _le_json(config.DATA / "ibov_comp.json", {})
    intr = {}
    for it in obj.get("itens", []):
        q = yahoo.intraday(it["cod"] + ".SA")
        if q and q.get("preco") and q.get("hora"):
            intr[it["cod"]] = {"preco": q["preco"], "data": q["hora"][:10], "hora": q["hora"]}
    obj["intraday"] = intr
    _grava_json(config.DATA / "ibov_comp.json", obj)
    return f"{len(intr)} papéis"


def diario_consenso_yahoo():
    m = _le_json(MERCADO, {"tickers": {}})
    novas = []
    for tk, v in config.UNIVERSO.items():
        novas += yahoo.consenso(v["yahoo"], tk, m["tickers"].get(tk, {}).get("acoes"))
    return f"+{_acrescenta(CONS, novas)} linhas (Yahoo)"


def diario_minhas():
    if not MINHAS.exists():
        _grava_csv(MINHAS, [])
    novas = []
    for tk, v in config.UNIVERSO.items():
        if v.get("modelo"):
            novas += modelo_ea.le_modelo(tk, v["modelo"])
    n = _acrescenta(MINHAS, novas) if novas else 0
    return f"+{n} do modelo; {len(_le_csv(MINHAS))} linhas"


# ----------------------------------------------------------------------------- janela SEMANAL (Focus)
def semanal_focus():
    hoje = date.today()
    anos = [hoje.year, hoje.year + 1]
    focus = bcb.focus_anuais(config.FOCUS_INDICADORES, anos, config.FOCUS_DESDE)
    d1, m1 = bcb.focus_snapshot("ExpectativaMercadoMensais", "Indicador,DataReferencia,Mediana,Media,Minimo,Maximo,numeroRespondentes", "baseCalculo eq 0")
    d2, t2 = bcb.focus_snapshot("ExpectativasMercadoTrimestrais", "Indicador,DataReferencia,Mediana,Media,Minimo,Maximo,numeroRespondentes", "baseCalculo eq 0")
    d3, a3 = bcb.focus_snapshot("ExpectativasMercadoTop5Anuais", "Indicador,DataReferencia,tipoCalculo,Mediana,Media")
    d4, s4 = bcb.focus_snapshot("ExpectativasMercadoTop5Selic", "indicador,reuniao,tipoCalculo,mediana")
    _atualiza_macro(anos=anos, focus=focus, focus_selic=bcb.focus_selic_reunioes(),
                    focus_todos=bcb.focus_anuais_completo(config.FOCUS_TODOS_DESDE),
                    focus_mensais={"data": d1, "linhas": m1}, focus_trimestrais={"data": d2, "linhas": t2},
                    focus_horizonte=bcb.focus_inflacao_horizonte(config.FOCUS_DESDE),
                    focus_top5={"data": d3, "linhas": a3}, focus_top5_selic={"data": d4, "linhas": s4})
    ult = max((s[-1][0] for i in focus.values() for s in i.values() if s), default="—")
    return f"Focus de {ult}"


def semanal_focus_longo():
    hoje = date.today()
    n = 0
    for ind in config.FOCUS_ASSERT_INDICADORES:
        for ano in range(config.FOCUS_ASSERT_ANO_INI, hoje.year + 2):
            n += len(bcb.focus_serie_anual(ind, ano, config.DATA / "focus_longo"))
    return f"{n} pontos (anos passados em cache)"


# ----------------------------------------------------------------------------- janela MENSAL (SGS de baixa frequência)
def mensal_sgs():
    sgs = _le_json(MACRO, {}).get("sgs", {})
    for cod, (rotulo, desde) in config.SGS_SERIES.items():
        if cod in (432, 1):
            continue
        sgs[str(cod)] = {"rotulo": rotulo, "serie": bcb.sgs(cod, desde)}
    _atualiza_macro(sgs=sgs)
    return ", ".join(f"{c} {len(sgs[str(c)]['serie'])}" for c in config.SGS_SERIES if c not in (432, 1))


BRENT_ARQ = config.DATA / "brent_curva.json"
MESES_FUT = "FGHJKMNQUVXZ"      # códigos de vencimento dos futuros: F=jan … Z=dez


def diario_brent_curva(meses: int = 30):
    """Curva futura do Brent (ICE via Yahoo, contratos BZ{mês}{ano}.NYM = Brent Last Day Financial): preço de cada
    vencimento dos próximos `meses` meses. Guarda uma fotografia por dia em data/brent_curva.json (histórico da curva)."""
    hoje = date.today()
    y, m = hoje.year, hoje.month + 1          # primeiro vencimento negociável: mês seguinte
    pts = []
    for _ in range(meses):
        if m > 12:
            m -= 12; y += 1
        sym = f"BZ{MESES_FUT[m - 1]}{str(y)[2:]}.NYM"
        q = yahoo.intraday(sym)
        if q and q.get("preco"):
            pts.append([f"{y}-{m:02d}", round(float(q["preco"]), 2), int(q.get("volume") or 0)])   # preço e contratos negociados no dia (liquidez)
        m += 1
    obj = _le_json(BRENT_ARQ, {"fotos": {}})
    if pts:
        obj["fotos"][hoje.isoformat()] = pts
        _grava_json(BRENT_ARQ, obj)
    return f"{len(pts)} vencimentos ({pts[0][0] if pts else '—'} a {pts[-1][0] if pts else '—'}); {len(obj['fotos'])} fotografias"


def diario_fluxo_investidores():
    """Participação dos investidores no volume de ações (B3/BDI): acumulado do mês por tipo, um ponto por dia de referência."""
    n, m = b3_bdi.atualiza_fluxo(30)
    return f"+{n} dias, +{m} meses"


def diario_cotas_fundos():
    """Cota diária (CVM, informe diário) dos fundos com ficha em cartas/*.json que tenham cnpj e inicio."""
    n = 0
    for f in sorted((config.RAIZ / "cartas").glob("*.json")):
        C = json.loads(f.read_text(encoding="utf-8"))
        if C.get("cnpj") and C.get("inicio"):
            n += len(cvm_inf_diario.serie(C["cnpj"], C["inicio"]))
    return f"{n} cotas"


def mensal_carteiras_cvm():
    """Carteiras dos fundos de config.FUNDOS_CVM nos últimos meses já sem sigilo (CDA da CVM, ~25 MB por mês, cache)."""
    n = 0
    for nome, cnpj in config.FUNDOS_CVM.items():
        for m in cvm_cda.meses_disponiveis(6):
            c = cvm_cda.carteira(cnpj, m)
            if c and c["acoes"]:
                n += 1
    return f"{n} carteiras abertas"


def mensal_realizado():
    hoje = date.today()
    ini = f"{config.FOCUS_ASSERT_ANO_INI - 1}-01-01"
    real = {}
    ipca = bcb.sgs(433, ini)
    fator, meses = {}, {}
    for d, v in ipca:
        fator[d[:4]] = fator.get(d[:4], 1.0) * (1 + v / 100)
        meses[d[:4]] = meses.get(d[:4], 0) + 1
    real["ipca"] = {a: round((f - 1) * 100, 2) for a, f in fator.items() if meses.get(a) == 12}
    real["selic"] = {d[:4]: v for d, v in bcb.sgs(432, ini)}
    real["cambio"] = {d[:4]: v for d, v in bcb.sgs(1, ini)}
    real["pib"] = {d[:4]: v for d, v in bcb.sgs(7326, ini)}
    for k in ("selic", "cambio"):
        real[k].pop(str(hoje.year), None)
    _grava_json(config.DATA / "realizado.json", real)
    return ", ".join(f"{k} até {max(v)}" for k, v in real.items() if v)


# ----------------------------------------------------------------------------- janelas
JANELAS = {
    "intraday": [("cotações universo", intraday_universo), ("cotações mercado", intraday_mercado), ("cotações Ibovespa", intraday_ibov_comp)],
    "diario": [("B3 COTAHIST", diario_cotahist), ("B3 ações", diario_acoes), ("Tesouro NTN-B", diario_tesouro),
               ("ANBIMA ETTJ", diario_ettj), ("curva Tesouro", diario_curva_tesouro),
               ("SGS diários", diario_sgs), ("Yahoo histórico", diario_yahoo_hist), ("Ibovespa composição", diario_ibov_comp),
               ("consenso Yahoo", diario_consenso_yahoo), ("cotas de fundos CVM", diario_cotas_fundos), ("Ibovespa DY", diario_ibov_dy), ("fluxo por investidor", diario_fluxo_investidores), ("curva do Brent", diario_brent_curva),
               ("minhas estimativas", diario_minhas), ("cotações universo", intraday_universo), ("cotações mercado", intraday_mercado),
               ("cotações Ibovespa", intraday_ibov_comp)],
    "semanal": [("Focus", semanal_focus), ("Focus longo", semanal_focus_longo), ("Ibovespa DY histórico", semanal_ibov_dy_hist)],
    "mensal": [("SGS mensais", mensal_sgs), ("realizado anual", mensal_realizado), ("carteiras CVM", mensal_carteiras_cvm)],
}
JANELAS["tudo"] = JANELAS["mensal"] + JANELAS["semanal"] + JANELAS["diario"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--janela", choices=list(JANELAS), default=None)
    ap.add_argument("--ponte", action="store_true")
    ap.add_argument("--consenso-bloomberg", action="store_true")
    ap.add_argument("--so-macro", action="store_true", help="compat.: semanal + mensal + parte macro do diário")
    ap.add_argument("--so-focus-longo", action="store_true", help="compat.: só séries longas + realizado")
    a = ap.parse_args()

    if a.ponte:
        bloomberg.gera_ponte()
        return 0
    if a.consenso_bloomberg:
        _passo("consenso Bloomberg", lambda: f"+{_acrescenta(CONS, bloomberg.le_ponte())} linhas; macro {bloomberg.le_ponte_macro()}")
        return 0
    if a.so_focus_longo:
        passos = [("Focus longo", semanal_focus_longo), ("realizado anual", mensal_realizado)]
    elif a.so_macro:
        passos = JANELAS["semanal"] + JANELAS["mensal"] + [("Tesouro NTN-B", diario_tesouro), ("SGS diários", diario_sgs),
                                                           ("Yahoo histórico", diario_yahoo_hist), ("cotações mercado", intraday_mercado)]
    else:
        passos = JANELAS[a.janela or "tudo"]
    nome = a.janela or ("so-focus-longo" if a.so_focus_longo else "so-macro" if a.so_macro else "tudo")
    print(f"Janela: {nome} ({len(passos)} passos)")
    falhas = sum(0 if _passo(n, fn) else 1 for n, fn in passos)
    est = _le_json(ESTADO, {})
    est["_janelas"] = {**est.get("_janelas", {}), nome: datetime.now().strftime("%Y-%m-%d %H:%M")}
    _grava_json(ESTADO, est)
    print(f"Concluído: {len(passos) - falhas} ok, {falhas} com erro")
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
