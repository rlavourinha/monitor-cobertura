"""Lucro esperado pelas casas (sell-side) por papel, para o P/L 12 meses à frente do screening.

BTG: o "LatAm Valuation Summary" (= BTG Pactual Stock Guide, Excel diário do portal, baixado pela coleta do BTG em
data/btg/pdf/<id>.pdf) traz, por empresa, valor de mercado, último preço e P/L 2026E/2027E. Daí:
    lucro (mn) = valor de mercado ÷ P/L        LPA = último preço ÷ P/L
O LPA fica na unidade do papel cotado no guia (ação ou unit); o mapa abaixo diz a que ticker o preço do guia se refere
e o build confere contra o fechamento B3 do mesmo dia. Regra do usuário: nunca usar múltiplo pronto; guardar o lucro
2026/2027, interpolar por dias até 31/12 e dividir pelo preço de tela.

Saída: data/lucros_casas.json = {"btg": {ticker: [[data_guia, ano0, lpa_ano0, lpa_ano1, lucro_ano0, lucro_ano1, preco_guia, moeda], ...]},
"atualizado": ..., "guias": [datas]}. Só números derivados (lucro/LPA), não o guia.

    python -m fontes.lucros_casas          # relê todos os guias e regrava o JSON
"""
from __future__ import annotations

import io
import json
import re
import sys
from datetime import date
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config

ARQ = config.DATA / "lucros_casas.json"
BTG_DIR = config.DATA / "btg"

# Nome no Stock Guide do BTG -> tickers (o 1º é o papel cujo preço o guia usa; os outros recebem o mesmo LPA por ação).
# Units (11) só com o próprio preço do guia: LPA por unit ≠ LPA por ação.
MAPA_BTG: dict[str, list[str]] = {
    "Ambev": ["ABEV3"], "Allos": ["ALOS3"], "Assaí": ["ASAI3"], "Auren Energia": ["AURE3"], "Axia Energia": ["AXIA3"],
    "Azzas 2154": ["AZZA3"], "B3": ["B3SA3"], "Banco do Brasil": ["BBAS3"], "Bradesco": ["BBDC4", "BBDC3"],
    "BB Seguridade": ["BBSE3"], "Minerva": ["BEEF3"], "Cemig": ["CMIG4"], "CSN Mineração": ["CMIN3"], "Cogna": ["COGN3"],
    "CPFL Energia": ["CPFE3"], "Copel": ["CPLE3"], "Cosan": ["CSAN3"], "Copasa": ["CSMG3"], "CSN": ["CSNA3"],
    "Cury": ["CURY3"], "Caixa Seguridade": ["CXSE3"], "Cyrela": ["CYRE3"], "Direcional": ["DIRR3"],
    "Engie Brasil Energia": ["EGIE3"], "Embraer": ["EMBJ3"], "Eneva": ["ENEV3"], "Energisa": ["ENGI11"],
    "Equatorial Energia": ["EQTL3"], "Fleury": ["FLRY3"], "Gerdau": ["GGBR4"], "Hapvida": ["HAPV3"], "Hypera": ["HYPE3"],
    "Iguatemi": ["IGTI11"], "ISA Energia Brasil": ["ISAE4"], "Itaú Unibanco": ["ITUB4"], "Klabin": ["KLBN11"],
    "Lojas Renner": ["LREN3"], "MBRF": ["MBRF3"], "Magazine Luiza": ["MGLU3"], "Motiva": ["MOTV3"], "MRV": ["MRVE3"],
    "Multiplan": ["MULT3"], "Natura": ["NATU3"], "Petrobras": ["PETR4", "PETR3"], "Marcopolo": ["POMO4"], "PRIO": ["PRIO3"],
    "Porto Seguro": ["PSSA3"], "RD Saúde": ["RADL3"], "Rumo": ["RAIL3"], "Rede D'Or": ["RDOR3"], "Localiza": ["RENT3"],
    "Santander Brasil": ["SANB11"], "Sabesp": ["SBSP3"], "Smart Fit": ["SMFT3"], "Suzano": ["SUZB3"], "Taesa": ["TAEE11"],
    "Tenda": ["TEND3"], "TIM": ["TIMS3"], "Totvs": ["TOTS3"], "Ultrapar": ["UGPA3"], "Usiminas": ["USIM5"], "Vale": ["VALE3"],
    "Vamos": ["VAMO3"], "Vibra Energia": ["VBBR3"], "Vivara": ["VIVA3"], "Telefônica Brasil": ["VIVT3"], "WEG": ["WEGE3"],
    "Yduqs": ["YDUQ3"], "C&A": ["CEAB3"], "Bradsaude": ["SAUD3"], "JBS": ["JBSS32"], "Inter & Co": ["INBR32"],
    # fora do Ibov, mas no screening (cobertura/BDRs/acompanhados)
    "EZTEC": ["EZTC3"], "Brava Energia": ["BRAV3"],
    # cotadas em dólar no guia (NYSE/Nasdaq): BDR na B3, LPA convertido pelo dólar e pela razão do BDR (BDR_RAZAO)
    "Nubank": ["ROXO34"], "XP Inc": ["XPBR31"], "Stone": ["STOC34"], "MercadoLibre": ["MELI34"],
}
# ações americanas por BDR invertido: preço do BDR = preço em US$ × dólar ÷ razão (medido em 19/10/2026)
BDR_RAZAO = {"ROXO34": 6, "XPBR31": 1, "STOC34": 1, "MELI34": 120}
# tickers do formato antigo do guia (aba COMP) para os nomes do mapa
ALIAS_ANTIGO = {"NU US": "Nubank", "XP US": "XP Inc", "STNE US": "Stone", "MELI US": "MercadoLibre"}
# Braskem fica de fora: no guia de out/26 a linha repete valor de mercado e preço do Banrisul (erro do guia). le_guia
# descarta qualquer linha com (valor de mercado, preço) idênticos aos de outra empresa.
# Fora do Stock Guide (holdings e o próprio BTG): BPAC11, BRAP4, GOAU4, ITSA4.


def _num(v):
    return float(v) if isinstance(v, (int, float)) else None


GUIAS_ANTIGOS = BTG_DIR / "guias"           # guias buscados para trás no portal (baixa_guias_antigos), fora do índice da coleta


def baixa_guias_antigos(meses: int = 6) -> str:
    """Busca no portal do BTG (sessão Playwright da coleta, fontes/btg.py) os LatAm Valuation Summary dos últimos `meses`
    que ainda não estão no disco e grava em data/btg/guias/<id>.xlsx. Pagina a lista de documentos (100 por vez)."""
    from datetime import timedelta
    from fontes.btg import Portal
    GUIAS_ANTIGOS.mkdir(parents=True, exist_ok=True)
    tem = {f.stem for f in (BTG_DIR / "pdf").glob("*.pdf")} | {f.stem for f in GUIAS_ANTIGOS.glob("*.xlsx")}
    lim = (date.today() - timedelta(days=int(meses * 30.5))).isoformat()
    novos, vistos, ini = [], 0, 1
    pi = GUIAS_ANTIGOS / "indice.json"
    idx = json.loads(pi.read_text(encoding="utf-8")) if pi.exists() else {}
    with Portal(headless=True) as p:
        if not p.abrir_lista():
            raise RuntimeError("sessão do portal BTG expirada (python btg_relatorios.py --login)")
        while True:
            docs = p.listar(100, ini)
            if not docs:
                break
            vistos += len(docs)
            for d in docs:
                if (d.get("company_sector") or "").strip() == "LatAm Valuation Summary" and str(d["document_id"]) not in tem:
                    novos.append((d["publish_date"][:10], d["document_id"]))
            for d in docs:
                if (d.get("company_sector") or "").strip() == "LatAm Valuation Summary":
                    idx[str(d["document_id"])] = d["publish_date"][:10]
            if min(d["publish_date"][:10] for d in docs) < lim:
                break
            ini += 100
        (GUIAS_ANTIGOS / "indice.json").write_text(json.dumps(dict(sorted(idx.items())), indent=0), encoding="utf-8")
        for dt, id_ in sorted(novos):
            if dt < lim:
                continue
            try:
                (GUIAS_ANTIGOS / f"{id_}.xlsx").write_bytes(p.pdf(id_))
            except Exception as e:
                print(f"  guia {id_} ({dt}): {e}", file=sys.stderr)
    return f"{len([n for n in novos if n[0] >= lim])} guias novos de {vistos} documentos listados (desde {lim})"


def guias_btg() -> list[tuple[str, Path]]:
    """(data de fechamento do guia, caminho) de cada Stock Guide já baixado, em ordem de data. A data vem da 2ª linha
    ("Closing prices as of dd/mm/aaaa"); o arquivo é .pdf no nome mas é xlsx (zip)."""
    p = BTG_DIR / "indice.json"
    if not p.exists():
        return []
    I = json.loads(p.read_text(encoding="utf-8"))
    out = []
    for k, r in I.items():
        if (r.get("empresa") or "") != "LatAm Valuation Summary":
            continue
        f = BTG_DIR / "pdf" / f"{k}.pdf"
        if f.exists() and f.read_bytes()[:2] == b"PK":
            out.append((r.get("data") or "", f))
    pi = GUIAS_ANTIGOS / "indice.json"
    idx = json.loads(pi.read_text(encoding="utf-8")) if pi.exists() else {}
    for f in GUIAS_ANTIGOS.glob("*.xlsx") if GUIAS_ANTIGOS.exists() else []:
        if f.read_bytes()[:2] == b"PK":
            out.append((idx.get(f.stem, ""), f))
    return sorted(out, key=lambda t: (t[0], t[1].stem))


def _le_guia_comp(wb, data_pub: str | None) -> tuple[str | None, list[dict]]:
    """Formato antigo do Stock Guide (aba COMP, até ago/2026): Company, Ticker, Rating, Currency, Price, Target, Upside,
    Mkt Cap (USD bn), ..., EV/EBITDA, P/E, Div. Yield, P/BV, Net Debt (USD bn), Net Debt/EBITDA (2 anos cada), Origin.
    Sem data no arquivo (vem do índice do portal). P/L arredondado em 1 casa: o lucro implícito tem ruído de até
    0,05 ÷ P/L. Valor de mercado em dólar: o lucro em R$ mn é preenchido depois (LPA × ações do guia novo)."""
    ws = wb["COMP"]
    rows = list(ws.iter_rows(values_only=True))
    hi = next((i for i, r in enumerate(rows) if r and "Ticker" in r and "Company" in r), None)
    if hi is None:
        return None, []
    h1, h2 = rows[hi], rows[hi + 1]
    col = {}
    atual = None
    for i, (x, y) in enumerate(zip(h1, h2)):
        if x is not None:
            atual = str(x).strip()
        if atual and y is not None:
            col[(atual, str(y).strip())] = i
        elif x is not None:
            col[(atual, "")] = i
    anos = sorted({int(k[1][:4]) for k in col if k[0] == "P/E" and k[1][:4].isdigit()})[:2]
    if len(anos) < 2:
        return None, []
    g = lambda r, k: r[col[k]] if k in col and col[k] < len(r) else None
    out = []
    for r in rows[hi + 2:]:
        tk = str(g(r, ("Ticker", "")) or "").strip().upper()
        if not r or (g(r, ("Origin", "")) != "Brazil" and tk not in ALIAS_ANTIGO):
            continue
        if tk in ALIAS_ANTIGO:
            tk = MAPA_BTG[ALIAS_ANTIGO[tk]][0]
        if tk not in REV:
            continue
        out.append({"nome": REV[tk][0], "ticker": tk, "setor": None, "moeda": g(r, ("Share", "Currency")), "mcap": None,
                    "preco": _num(g(r, ("Share", "Price"))), "ano0": anos[0], "arred": True,
                    "pe0": _num(g(r, ("P/E", f"{anos[0]}E"))), "pe1": _num(g(r, ("P/E", f"{anos[1]}E"))),
                    "ev0": None, "ev1": None, "nd0": None, "nd1": None,
                    "dy0": _num(g(r, ("Div. Yield", f"{anos[0]}E"))), "dy1": _num(g(r, ("Div. Yield", f"{anos[1]}E")))})
    return data_pub, out


def le_guia(f: Path, data_pub: str | None = None) -> tuple[str | None, list[dict]]:
    """Linhas do Brasil de um Stock Guide: nome, setor, moeda, mcap (mn), preço, P/L 26/27 (formato novo, aba única
    'Stock Guide', desde ago/2026) ou o formato antigo (aba COMP)."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(f.read_bytes()), read_only=True, data_only=True)
    if "COMP" in wb.sheetnames:
        try:
            return _le_guia_comp(wb, data_pub)
        finally:
            wb.close()
    ws = wb.worksheets[0]
    data, cab, out = None, None, []
    for row in ws.iter_rows(values_only=True):
        cel = [c for c in row if c is not None]
        if data is None and cel and isinstance(cel[0], str):
            m = re.search(r"as of (\d{2})/(\d{2})/(\d{4})", cel[0])
            if m:
                data = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
                continue
        if cab is None:
            if row and row[0] == "Company":
                cab = {str(c).strip(): i for i, c in enumerate(row) if c is not None}
                anos = sorted(int(m.group(1)) for k in cab for m in [re.fullmatch(r"P/E (\d{4})E", k)] if m)[:2]
            continue
        if not row or (row[cab["Country"]] != "Brazil" and str(row[0] or "").strip() not in MAPA_BTG):   # MercadoLibre = Argentina
            continue
        g = lambda k: row[cab[k]] if k in cab and cab[k] < len(row) else None
        out.append({"nome": str(g("Company")).strip(), "setor": g("Sector"), "moeda": g("Currency"),
                    "mcap": _num(g("Market Cap (mn)")), "preco": _num(g("Last Price")),
                    "ano0": anos[0], "pe0": _num(g(f"P/E {anos[0]}E")), "pe1": _num(g(f"P/E {anos[1]}E")),
                    "ev0": _num(g(f"EV/EBITDA {anos[0]}E")), "ev1": _num(g(f"EV/EBITDA {anos[1]}E")),
                    "nd0": _num(g(f"Net Debt/EBITDA {anos[0]}E")), "nd1": _num(g(f"Net Debt/EBITDA {anos[1]}E")),
                    "dy0": _num(g(f"Dividend Yield {anos[0]}E")), "dy1": _num(g(f"Dividend Yield {anos[1]}E"))})
    wb.close()
    from collections import Counter
    dup = Counter((l["mcap"], l["preco"]) for l in out)
    return data, [l for l in out if dup[(l["mcap"], l["preco"])] == 1]


def atualiza() -> dict:
    """Relê todos os guias do BTG e regrava data/lucros_casas.json. Um ponto por data de fechamento do guia."""
    btg: dict[str, dict[str, list]] = {}
    setor_guia: dict[str, str] = {}
    datas = []
    nao_mapeados = set()
    for dpub, f in guias_btg():
        try:
            d, linhas = le_guia(f, dpub or None)
        except Exception as e:
            print(f"  guia {f.name}: {e}", file=sys.stderr)
            continue
        if not d:
            continue
        datas.append(d)
        for l in linhas:
            tks = MAPA_BTG.get(l["nome"])
            if not tks:
                nao_mapeados.add(l["nome"])
                continue
            p, m = l["preco"], l["mcap"]
            lpa = lambda pe: round(p / pe, 6) if (p and pe and pe > 0) else None
            luc = lambda pe: round(m / pe, 2) if (m and pe and pe > 0) else None
            def ebitda_dl(x, n):
                """EV/EBITDA = x e DL/EBITDA = n com EV = mcap + DL  ->  EBITDA = mcap ÷ (x − n), DL = n × EBITDA."""
                if not (m and x and n is not None and x - n > 0):
                    return None, None
                e = m / (x - n)
                return round(e, 2), round(n * e, 2)
            e0, dl0 = ebitda_dl(l["ev0"], l["nd0"])
            e1, dl1 = ebitda_dl(l["ev1"], l["nd1"])
            dps = lambda dy: round(dy * p, 6) if (p and dy is not None) else None
            reg = [d, l["ano0"], lpa(l["pe0"]), lpa(l["pe1"]), luc(l["pe0"]), luc(l["pe1"]), p, l["moeda"],
                   m, e0, e1, dl0, dl1, dps(l["dy0"]), dps(l["dy1"]), bool(l.get("arred"))]
            if l.get("ticker"):                          # formato antigo: preço é do ticker da linha
                tks = [l["ticker"]] + [t for t in tks if t != l["ticker"] and not t.endswith("11")]
            for tk in tks:
                btg.setdefault(tk, {})[d] = reg
                setor_guia[tk] = l["setor"]
    # empresas cotadas em dólar: o BTG deixa o P/L em branco em alguns guias (28/09 e 16/10/2026) -> repete o último
    from datetime import timedelta as _td
    for tk, ss in btg.items():
        ant = None
        for d in sorted(ss):
            v = ss[d]
            if v[7] == "USD" and v[3] is None and ant and ant[3] is not None and \
                    date.fromisoformat(d) - date.fromisoformat(ant[0]) <= _td(days=21) and v[1] == ant[1]:
                ss[d] = v = [v[0], v[1], ant[2], ant[3], ant[4], ant[5]] + v[6:]
            if v[3] is not None:
                ant = v
    # formato antigo não tem valor de mercado em R$: lucro (R$ mn) = LPA × ações implícitas no guia novo mais recente
    for tk, ss in btg.items():
        novos = [v for _, v in sorted(ss.items()) if not v[15] and v[8] and v[6]]
        if not novos:
            continue
        acoes = novos[-1][8] / novos[-1][6]              # mn de ações (ou units) na unidade do preço do guia
        for v in ss.values():
            if v[15]:
                v[4] = round(v[2] * acoes, 2) if v[2] else None
                v[5] = round(v[3] * acoes, 2) if v[3] else None
    out = {"atualizado": date.today().isoformat(), "guias": sorted(set(datas)), "arquivos": sorted(f.stem for _, f in guias_btg()),
           "fonte": "BTG Pactual Stock Guide (LatAm Valuation Summary): LPA = último preço ÷ P/L; lucro = valor de mercado ÷ P/L; EBITDA = valor de mercado ÷ (EV/EBITDA − DL/EBITDA); DL = DL/EBITDA × EBITDA; DPA = DY × preço (R$ mn)",
           "campos": ["data_guia", "ano0", "lpa_ano0", "lpa_ano1", "lucro_ano0", "lucro_ano1", "preco_guia", "moeda",
                      "mcap", "ebitda_ano0", "ebitda_ano1", "div_liq_ano0", "div_liq_ano1", "dpa_ano0", "dpa_ano1", "pl_arredondado"],
           "btg": {tk: [v for _, v in sorted(s.items())] for tk, s in sorted(btg.items())}}
    ant = json.loads(ARQ.read_text(encoding="utf-8")) if ARQ.exists() else {}
    # preserva pontos antigos cujo guia não está mais no disco (data/btg/pdf é local e pode ser limpo)
    for tk, pts in (ant.get("btg") or {}).items():
        tem = {p[0] for p in out["btg"].get(tk, [])}
        extra = [p for p in pts if p[0] not in tem and len(p) == 16]
        if extra:
            out["btg"][tk] = sorted(out["btg"].get(tk, []) + extra)
    out["guias"] = sorted(set(out["guias"]) | set(ant.get("guias") or []))
    if "bba" in ant:
        out["bba"] = ant["bba"]
    out["setor_guia"] = {**(ant.get("setor_guia") or {}), **setor_guia}
    try:
        out["eventos"] = eventos_revisao(out)
    except Exception as e:
        print(f"  eventos de revisão: {e}", file=sys.stderr)
        out["eventos"] = ant.get("eventos") or []
    ARQ.write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8")
    out["_nao_mapeados"] = sorted(nao_mapeados)
    return out


def atualiza_se_novo() -> bool:
    """Reprocessa só se há Stock Guide no disco que o JSON ainda não leu (chamado pelo vigia a cada ciclo)."""
    feitos = set(carrega().get("arquivos") or [])
    if {f.stem for _, f in guias_btg()} <= feitos:
        return False
    atualiza()
    return True


def carrega() -> dict:
    return json.loads(ARQ.read_text(encoding="utf-8")) if ARQ.exists() else {}


def peso_ano(d: str) -> float:
    """Fração do lucro do ano corrente no 12 meses à frente: dias corridos até 31/12 ÷ 365."""
    ano = int(d[:4])
    return max(0.0, min(1.0, (date(ano, 12, 31) - date.fromisoformat(d)).days / 365))


def lpa_12m(pt: list, d: str) -> float | None:
    """LPA 12 meses à frente na data d com o ponto do guia pt (ano0 e ano0+1 interpolados por dias até 31/12).
    None quando d não está no ano0 do guia (na virada, o guia passa a trazer o par seguinte)."""
    a0, l0, l1 = pt[1], pt[2], pt[3]
    if l0 is None or l1 is None or int(d[:4]) != a0:
        return None
    w = peso_ano(d)
    return w * l0 + (1 - w) * l1


REV = {tk: (nome, i) for nome, tks in MAPA_BTG.items() for i, tk in enumerate(tks)}
TOL_PRECO = 0.03


_DOLAR: dict[str, float] = {}


def dolar(d: str) -> float | None:
    """USD/BRL de fechamento na data (ou no último dia antes dela), série do macro.json (Yahoo)."""
    if not _DOLAR:
        try:
            M = json.loads((config.DATA / "macro.json").read_text(encoding="utf-8"))
            _DOLAR.update({x[0]: x[1] for x in (M.get("hist") or {}).get("USD/BRL", []) if x[1]})
        except Exception:
            return None
    ds = [k for k in _DOLAR if k <= d]
    return _DOLAR[max(ds)] if ds else None


def lpas(tk: str, L: dict, fech: dict[str, dict[str, float]]) -> list[tuple[str, int, float | None, float | None, str]]:
    """Pontos (data_guia, ano0, LPA ano0, LPA ano1, base) do ticker. LPA = preço do guia ÷ P/L quando o preço do guia
    bate (±3%) com o fechamento B3 do papel principal da empresa no mesmo dia (vale para as outras classes: lucro por
    ação é o mesmo). Se não bate (unit cotada de outro jeito, BDR, preço velho no guia), o P/L do guia é aplicado ao
    fechamento do próprio ticker: LPA = fechamento ÷ P/L."""
    out = []
    nome, _ = REV.get(tk, (None, 0))
    prim = MAPA_BTG[nome][0] if nome else tk
    for pt in (L.get("btg") or {}).get(tk, []):
        d, a0, l0, l1, pg = pt[0], pt[1], pt[2], pt[3], pt[6]
        if pt[7] == "USD" and tk in BDR_RAZAO:
            f = (dolar(d) or 0) / BDR_RAZAO[tk]
            if f:
                out.append((d, a0, l0 * f if l0 else None, l1 * f if l1 else None, "dólar"))
            continue
        cp = (fech.get(prim) or {}).get(d)
        if pg and cp and abs(pg / cp - 1) <= TOL_PRECO:
            out.append((d, a0, l0, l1, "guia"))
            continue
        c = (fech.get(tk) or {}).get(d)
        if not (pg and c):
            continue
        f = c / pg
        out.append((d, a0, l0 * f if l0 else None, l1 * f if l1 else None, "fechamento"))
    ok = {p[0] for p in pts_validos(tk, L)}
    return [x for x in out if x[0] in ok]


def pts_validos(tk: str, L: dict) -> list[list]:
    """Pontos do guia do ticker sem os do formato antigo (até ago/2026) que não servem: (a) o ticker inteiro quando o
    lucro antigo não emenda com o 1º ponto do formato novo (diferença > 25%: unidade diferente, ex. COGN3, BRAV3);
    (b) pontos que destoam mais de 30% da mediana de ±10 vizinhos antigos (trechos com P/L absurdo, ex. SBSP3 05/2026)."""
    from statistics import median
    pts = (L.get("btg") or {}).get(tk, [])
    velhos = [p for p in pts if len(p) > 15 and p[15]]
    if not velhos:
        return pts
    novos = [p for p in pts if not (len(p) > 15 and p[15])]
    if novos and velhos[-1][3] and novos[0][3] and not (0.75 < novos[0][3] / velhos[-1][3] < 1.33):
        return novos
    bons = []
    for i, x in enumerate(velhos):
        viz = [v[3] for v in velhos[max(0, i - 10):i + 11] if v[3] and v is not x]
        if x[3] and viz and abs(x[3] / median(viz) - 1) > 0.30:
            continue
        bons.append(x)
    return bons + novos


def _filtra_antigos(tk: str, L: dict, out: list[tuple]) -> list[tuple]:
    """Pontos do formato antigo (até ago/2026) só entram se (a) emendam com o 1º ponto do formato novo (LPA do ano
    seguinte a menos de 25%: senão a unidade era outra, ex. COGN3, BRAV3) e (b) não destoam mais de 30% da mediana
    de ±3 vizinhos antigos (dias isolados com P/L absurdo no guia antigo, ex. SBSP3 30/04/2026)."""
    from statistics import median
    arred = {p[0] for p in (L.get("btg") or {}).get(tk, []) if len(p) > 15 and p[15]}
    if not arred:
        return out
    novos = [x for x in out if x[0] not in arred]
    velhos = [x for x in out if x[0] in arred]
    if not velhos:
        return out
    if novos and velhos[-1][3] and novos[0][3]:
        r = novos[0][3] / velhos[-1][3]
        if not (0.75 < r < 1.33):
            return novos
    bons = []
    for i, x in enumerate(velhos):
        viz = [v[3] for v in velhos[max(0, i - 3):i + 4] if v[3] and v is not x]
        if x[3] and viz and abs(x[3] / median(viz) - 1) > 0.30:
            continue
        bons.append(x)
    return sorted(bons + novos)


def extras(tk: str, L: dict, fech: dict[str, dict[str, float]]) -> list[dict]:
    """Por guia: EBITDA, dívida líquida, valor de mercado e preço de referência da empresa (papel principal) e DPA na
    unidade do ticker. EV/EBITDA é da empresa: o valor de mercado anda com o preço do papel principal."""
    out = []
    nome, _ = REV.get(tk, (None, 0))
    prim = MAPA_BTG[nome][0] if nome else tk
    for pt in (L.get("btg") or {}).get(tk, []):
        if len(pt) < 15:
            continue
        d, a0, pg, mcap = pt[0], pt[1], pt[6], pt[8]
        cp = (fech.get(prim) or {}).get(d)
        ok = bool(pg and cp and abs(pg / cp - 1) <= TOL_PRECO)
        ref = pg if ok else cp                       # preço do papel principal que corresponde ao mcap do guia
        c = (fech.get(tk) or {}).get(d)
        f = 1.0 if ok else ((c / pg) if (c and pg) else None)
        out.append({"d": d, "a0": a0, "mcap": mcap, "ref": ref, "prim": prim, "e0": pt[9], "e1": pt[10], "dl0": pt[11], "dl1": pt[12],
                    "dpa0": pt[13] * f if (pt[13] is not None and f) else None, "dpa1": pt[14] * f if (pt[14] is not None and f) else None})
    return out


def ev_ebitda12(x: dict, preco_prim: float | None, d: str) -> float | None:
    """EV atual ÷ EBITDA 12m à frente (decisão do usuário, 19/10/2026). EV = valor de mercado do guia andando com o
    preço de tela do papel principal + dívida líquida do fim do ano corrente projetada pelo BTG (o ponto do guia mais
    próximo de hoje; o guia não traz a dívida do último balanço). EBITDA = ano corrente e seguinte interpolados."""
    if not (x and preco_prim and x["ref"] and x["mcap"] and x["e0"] and x["e1"] and x["dl0"] is not None):
        return None
    if int(d[:4]) != x["a0"]:
        return None
    w = peso_ano(d)
    eb = w * x["e0"] + (1 - w) * x["e1"]
    ev = x["mcap"] * preco_prim / x["ref"] + x["dl0"]
    return ev / eb if eb > 0 else None


def dy12(x: dict, preco: float | None, d: str) -> float | None:
    """Dividend yield 12m à frente: DPA de ano0 e ano0+1 interpolado ÷ preço."""
    if not (x and preco and x["dpa0"] is not None and x["dpa1"] is not None) or int(d[:4]) != x["a0"]:
        return None
    w = peso_ano(d)
    return (w * x["dpa0"] + (1 - w) * x["dpa1"]) / preco


def lpa12(pt: tuple, d: str) -> tuple[float | None, str]:
    """LPA 12 meses à frente em d: ano0 e ano0+1 interpolados por dias até 31/12. Sem ano0 (n.a. no guia, prejuízo ou
    ano de transição), usa só o ano seguinte e marca o rótulo."""
    _, a0, l0, l1, _ = pt
    if int(d[:4]) != a0 or l1 is None:
        return None, ""
    if l0 is None:
        return l1, f"só {a0 + 1}"
    w = peso_ano(d)
    return w * l0 + (1 - w) * l1, ""


def serie_pl(tk: str, L: dict, fech: dict[str, dict[str, float]], desde: str | None = None) -> list[tuple[str, float]]:
    """P/L 12m à frente diário: fechamento do dia ÷ LPA 12m do guia mais recente com data ≤ dia. P/L ≤ 0 fica de fora."""
    P = lpas(tk, L, fech)
    if not P:
        return []
    out, j = [], -1
    for d in sorted(fech.get(tk) or {}):
        if d < P[0][0] or (desde and d < desde):
            continue
        while j + 1 < len(P) and P[j + 1][0] <= d:
            j += 1
        e, _ = lpa12(P[j], d)
        if e and e > 0:
            out.append((d, fech[tk][d] / e))
    return out


def fator_cambio(L: dict) -> tuple[dict[str, float], set[str]]:
    """O BTG modela algumas empresas em dólar (Petrobras, Vale, Embraer, PRIO, Brava) e converte o lucro para reais no
    guia: entre dois guias o lucro delas muda pelo MESMO fator, sem revisão nenhuma (08/10/2026: −3,77% em todas).
    Por par de guias, fator = razão de lucro do ano seguinte compartilhada por ≥3 empresas distintas (≠ 1). Devolve
    ({data_guia: índice acumulado, 1º guia = 1}, tickers em dólar = os que seguem o fator em ≥60% dos pares com fator,
    tickers marcados a mercado = lucro muda em ≥60% dos guias sem seguir só o fator)."""
    from collections import Counter
    B = L.get("btg") or {}
    datas = sorted({p[0] for pts in B.values() for p in pts})
    por = {tk: {p[0]: p[5] for p in pts if p[5] and p[5] > 0} for tk, pts in B.items()}
    arred = {(tk, p[0]): bool(len(p) > 15 and p[15]) for tk, pts in B.items() for p in pts}   # formato antigo: ruído, fora da detecção
    F, acc, fatores = {}, 1.0, {}
    for i, d in enumerate(datas):
        if i:
            d0 = datas[i - 1]
            c, vistos = Counter(), set()
            for tk, s in por.items():
                nome = REV.get(tk, (tk, 0))[0]
                if d in s and d0 in s and nome not in vistos and s[d] != s[d0] and not (arred.get((tk, d)) or arred.get((tk, d0))):
                    vistos.add(nome)
                    c[round(s[d] / s[d0], 4)] += 1
            f, n = (c.most_common(1)[0] if c else (1.0, 0))
            fatores[d] = f if n >= 3 else 1.0
            acc *= fatores[d]
        F[d] = acc
    usd = set()
    com = [d for d, f in fatores.items() if f != 1.0]
    for tk, s in por.items():
        segue = [d for d in com if d in s and datas[datas.index(d) - 1] in s
                 and abs(s[d] / s[datas[datas.index(d) - 1]] / fatores[d] - 1) < 2e-4]
        if com and len(segue) >= 0.6 * len(com):
            usd.add(tk)
    # lucro que muda em quase todo guia sem seguir só o câmbio (PRIO: ~2× o fator, marcação de câmbio e Brent)
    mtm = set()
    for tk, s in por.items():
        pares = [(datas[i - 1], d) for i, d in enumerate(datas) if i and d in s and datas[i - 1] in s
                 and not (arred.get((tk, d)) or arred.get((tk, datas[i - 1])))]
        if tk not in usd and len(pares) >= 4 and sum(1 for a, b in pares if s[a] != s[b]) >= 0.6 * len(pares):
            mtm.add(tk)
    return F, usd, mtm


def revisao(tk: str, L: dict, FX: tuple | None = None) -> float | None:
    """Revisão do lucro do ano seguinte entre o 1º e o último guia do ticker, sem o efeito cambial nas empresas que o
    BTG modela em dólar (lucro ÷ índice do fator cambial do guia)."""
    pts = (L.get("btg") or {}).get(tk) or []
    if len(pts) < 2:
        return None
    SA = FX if isinstance(FX, dict) else serie_ajustada(L)
    s5 = (SA.get(tk) or {}).get(5) or []
    if len(s5) < 2:
        return None
    return s5[-1][1] / s5[0][1] - 1


# palavras do campo "empresa"/título dos relatórios do BTG por setor do guia (para achar o relatório de uma revisão setorial)
SETOR_CHAVES = {
    "Real Estate": ["homebuilder", "real estate", "malls", "incorporad"], "Oil & Gas": ["oil", "gas", "petro"],
    "Metal & Mining": ["mining", "steel", "metal", "iron ore"], "Pulp & Paper": ["pulp", "paper"],
    "Banks": ["bank"], "Financials (ex-Banks)": ["insur", "financ", "exchange", "payment"],
    "Utilities": ["utilit", "energy", "power", "sanitation", "water"], "Retail": ["retail", "consumer", "apparel", "drugstore", "pharmac"],
    "Food & Beverage": ["food", "beverage", "protein", "meat"], "Healthcare": ["health", "hospital", "pharma"],
    "Education": ["educa"], "Telecom": ["telecom"], "Technology": ["tech", "software"], "Capital Goods": ["capital goods", "industrial"],
    "Car Rental": ["rental", "car rental", "localiza", "leasing"], "Infrastructure": ["infra", "toll", "railroad", "logistic"],
    "Logistics": ["logistic"], "Agribusiness": ["agri", "sugar", "ethanol"],
}


def serie_ajustada(L: dict) -> dict[str, dict[int, list[tuple[str, float, float]]]]:
    """Lucro de cada ticker por guia (i = 4 ano corrente, 5 ano seguinte, R$ mn) SEM os movimentos comuns que não são
    revisão de analista, para medir revisões:
    - formato novo: as empresas que o BTG modela em dólar mudam juntas com o câmbio do guia;
    - formato antigo (até ago/2026): o P/L era calculado em dólar com câmbio de projeção no lucro e à vista no valor de
      mercado (hipótese não confirmada: em 19/10/2026 a mediana entre guias antigos ficou em 0,999; o que há é ruído de
      arredondamento do P/L, tratado pelo campo de ruído).
    Por par de guias consecutivos, cada razão de lucro é dividida pela mediana das razões do seu grupo (dólar × reais;
    mínimo 3 empresas). Na emenda formato antigo → novo (25/08/2026), salto > 10% corta a série (unidade diferente). A série é encadeada e reancorada no último guia (nível = lucro real de hoje). Cada ponto leva o
    ruído de arredondamento do P/L (formato antigo: 0,05 ÷ P/L; novo: 0)."""
    from statistics import median
    F, usd, mtm = fator_cambio(L)
    B = L.get("btg") or {}
    prims = {MAPA_BTG[n][0] for n in MAPA_BTG if MAPA_BTG[n][0] in B}
    def ruido(pt, i):
        lpa = pt[2] if i == 4 else pt[3]
        return (0.05 * lpa / pt[6]) if (len(pt) > 15 and pt[15] and lpa and pt[6]) else 0.0
    rz: dict[tuple, list[float]] = {}
    V = {tk: pts_validos(tk, L) for tk in B}
    for tk in prims:
        pts = V[tk]
        g = "usd" if tk in usd else "brl"
        for a, b in zip(pts, pts[1:]):
            if a[1] != b[1]:
                continue
            for i in (4, 5):
                if a[i] and b[i] and a[i] > 0 and b[i] > 0:
                    rz.setdefault((a[0], b[0], g, i), []).append(b[i] / a[i])
    med = {k: (median(v) if len(v) >= 3 else 1.0) for k, v in rz.items()}
    out = {}
    for tk in B:
        pts = V[tk]
        if not pts:
            continue
        g = "usd" if tk in usd else "brl"
        res = {}
        for i in (4, 5):
            ult = pts[-1]
            if not (ult[i] and ult[i] > 0):
                res[i] = []
                continue
            serie = [(ult[0], ult[i], ruido(ult, i))]
            nivel = ult[i]
            for a, b in zip(reversed(pts[:-1]), reversed(pts[1:])):
                if a[1] != b[1] or not (a[i] and b[i] and a[i] > 0 and b[i] > 0):
                    break
                adj = (b[i] / a[i]) / med.get((a[0], b[0], g, i), 1.0)
                troca = len(a) > 15 and len(b) > 15 and a[15] and not b[15]
                if (troca and abs(adj - 1) > 0.10) or not (0.2 < adj < 5):
                    break                       # emenda do formato antigo com unidade diferente (COGN3, IGTI11, BRAV3) ou erro: corta a série
                nivel = nivel / adj
                serie.append((a[0], nivel, ruido(a, i)))
            res[i] = serie[::-1]
        out[tk] = res
    return out


def revisao_janela(serie: list[tuple[str, float, float]], dias: int, fim: str | None = None) -> tuple[float, float] | None:
    """(revisão, ruído) entre o ponto mais recente e o último ponto ≥ `dias` antes dele."""
    from datetime import timedelta
    if not serie:
        return None
    d1, v1, r1 = serie[-1]
    lim = (date.fromisoformat(fim or d1) - timedelta(days=dias)).isoformat()
    base = next(((d, v, r) for d, v, r in reversed(serie) if d <= lim), None)
    if not base:
        return None
    return v1 / base[1] - 1, r1 + base[2]


def eventos_revisao(L: dict, limiar: float = 0.01) -> list[dict]:
    """Toda mudança de lucro (ano corrente ou seguinte) ≥ limiar entre dois guias consecutivos, sem o efeito cambial,
    por empresa (1º ticker do mapa), com o relatório do BTG publicado entre os dois guias que trata da empresa (nome no
    campo empresa/título) ou do setor dela (SETOR_CHAVES). O índice dos relatórios só existe neste PC (data/btg)."""
    FX = fator_cambio(L)
    F, usd, mtm = FX
    SA = serie_ajustada(L)
    p = BTG_DIR / "indice.json"
    I = list(json.loads(p.read_text(encoding="utf-8")).values()) if p.exists() else []
    out = []
    for nome, tks in MAPA_BTG.items():
        tk = tks[0]
        pts = pts_validos(tk, L)
        sa = {i: {d: (v, r) for d, v, r in (SA.get(tk) or {}).get(i, [])} for i in (4, 5)}
        for a, b in zip(pts, pts[1:]):
            if a[1] != b[1]:
                continue
            var = {}
            for k, i in (("ano0", 4), ("ano1", 5)):
                x0, x1 = a[i], b[i]
                if x0 and x1 and x0 > 0 and x1 > 0:
                    if a[0] not in sa[i] or b[0] not in sa[i]:
                        continue
                    (v0, r0), (v1, r1) = sa[i][a[0]], sa[i][b[0]]
                    v = v1 / v0 - 1
                    if abs(v) >= limiar + r0 + r1:
                        var[k] = round(v, 4)
                # lucro que some num guia e volta no seguinte (16-17/09/2026) é buraco do guia, não revisão: ignorado
            if not var:
                continue
            # relatório publicado entre o guia anterior (inclusive: o guia daquele dia ainda não o incorporava) e o guia da revisão
            cand = [r for r in I if a[0] <= (r.get("data") or "") <= b[0] and (r.get("empresa") or "") != "LatAm Valuation Summary"]
            n = nome.lower().split()[0]
            setor = (L.get("setor_guia") or {}).get(tk, "")
            chaves = SETOR_CHAVES.get(setor, [])
            def nota(r):
                emp, txt = (r.get("empresa") or "").lower(), f'{r.get("empresa") or ""} {r.get("titulo") or ""}'.lower()
                if re.search(rf"\b{re.escape(n)}", txt):
                    return 2
                periodico = re.search(r"daily|weekly|monitor|tracker|summary|morning|guide|macro", txt)
                return 1 if (any(re.search(rf"\b{re.escape(c)}", emp) for c in chaves) and not periodico) else 0
            cand = sorted([r for r in cand if nota(r)], key=lambda r: (-nota(r), r.get("data") or ""))
            rel = cand[0] if cand else None
            out.append({"tk": tk, "empresa": nome, "setor_guia": setor, "de": a[0], "data": b[0], "ano0": b[1],
                        "var": var, "lucro_ano1": [a[5], b[5]], "cambio": tk in usd or tk in mtm,
                        "relatorio": {"id": rel["id"], "data": rel.get("data"), "empresa": rel.get("empresa"), "titulo": rel.get("titulo")} if rel else None})
    return sorted(out, key=lambda e: e["data"])


def lucro_sem_cambio(tk: str, pt: list, FX: tuple) -> float | None:
    """Lucro do ano seguinte do ponto pt, dividido pelo índice cambial do guia se a empresa é modelada em dólar."""
    F, usd, mtm = FX
    if not pt[5]:
        return None
    return pt[5] / F.get(pt[0], 1.0) if (tk in usd or tk in mtm) else pt[5]


def pl12_hoje(tk: str, preco: float | None, hoje: str | None = None, L: dict | None = None) -> dict | None:
    """P/L 12m à frente de um papel com o preço dado e o último guia: {valor, detalhe} (mesmo formato de trades.multiplo)."""
    from fontes import b3
    L = L or carrega()
    if not preco or tk not in (L.get("btg") or {}):
        return None
    nome = REV.get(tk, (None, 0))[0]
    prim = MAPA_BTG[nome][0] if nome else tk
    fech = {t: dict(b3.serie(t)) for t in {tk, prim}}
    P = lpas(tk, L, fech)
    if not P:
        return None
    e, obs = lpa12(P[-1], hoje or date.today().isoformat())
    if not e or e <= 0:
        return None
    return {"rotulo": "P/E 12m", "valor": preco / e,
            "detalhe": f"LPA 12m {e:.2f} (BTG Stock Guide de {P[-1][0][8:]}/{P[-1][0][5:7]}{', ' + obs if obs else ''})"}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if "--historico" in sys.argv:
        print(baixa_guias_antigos(int(sys.argv[sys.argv.index("--historico") + 1]) if len(sys.argv) > sys.argv.index("--historico") + 1 else 6))
    R = atualiza()
    print(f"{len(R['guias'])} guias ({R['guias'][0] if R['guias'] else '—'} → {R['guias'][-1] if R['guias'] else '—'}), {len(R['btg'])} papéis")
    print("sem mapa:", len(R["_nao_mapeados"]))
