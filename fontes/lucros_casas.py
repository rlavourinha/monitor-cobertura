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
}
# Braskem fica de fora: no guia de out/26 a linha repete valor de mercado e preço do Banrisul (erro do guia). le_guia
# descarta qualquer linha com (valor de mercado, preço) idênticos aos de outra empresa.
# Fora do Stock Guide (holdings e o próprio BTG): BPAC11, BRAP4, GOAU4, ITSA4.


def _num(v):
    return float(v) if isinstance(v, (int, float)) else None


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
    return sorted(out)


def le_guia(f: Path) -> tuple[str | None, list[dict]]:
    """Linhas do Brasil de um Stock Guide: nome, setor, moeda, mcap (mn), preço, P/L 26/27."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(f.read_bytes()), read_only=True, data_only=True)
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
        if not row or row[cab["Country"]] != "Brazil":
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
    datas = []
    nao_mapeados = set()
    for _, f in guias_btg():
        try:
            d, linhas = le_guia(f)
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
                   m, e0, e1, dl0, dl1, dps(l["dy0"]), dps(l["dy1"])]
            for tk in tks:
                btg.setdefault(tk, {})[d] = reg
    out = {"atualizado": date.today().isoformat(), "guias": sorted(set(datas)), "arquivos": sorted(f.stem for _, f in guias_btg()),
           "fonte": "BTG Pactual Stock Guide (LatAm Valuation Summary): LPA = último preço ÷ P/L; lucro = valor de mercado ÷ P/L; EBITDA = valor de mercado ÷ (EV/EBITDA − DL/EBITDA); DL = DL/EBITDA × EBITDA; DPA = DY × preço (R$ mn)",
           "campos": ["data_guia", "ano0", "lpa_ano0", "lpa_ano1", "lucro_ano0", "lucro_ano1", "preco_guia", "moeda",
                      "mcap", "ebitda_ano0", "ebitda_ano1", "div_liq_ano0", "div_liq_ano1", "dpa_ano0", "dpa_ano1"],
           "btg": {tk: [v for _, v in sorted(s.items())] for tk, s in sorted(btg.items())}}
    ant = json.loads(ARQ.read_text(encoding="utf-8")) if ARQ.exists() else {}
    # preserva pontos antigos cujo guia não está mais no disco (data/btg/pdf é local e pode ser limpo)
    for tk, pts in (ant.get("btg") or {}).items():
        tem = {p[0] for p in out["btg"].get(tk, [])}
        extra = [p for p in pts if p[0] not in tem and len(p) == 15]
        if extra:
            out["btg"][tk] = sorted(out["btg"].get(tk, []) + extra)
    out["guias"] = sorted(set(out["guias"]) | set(ant.get("guias") or []))
    if "bba" in ant:
        out["bba"] = ant["bba"]
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
        cp = (fech.get(prim) or {}).get(d)
        if pg and cp and abs(pg / cp - 1) <= TOL_PRECO:
            out.append((d, a0, l0, l1, "guia"))
            continue
        c = (fech.get(tk) or {}).get(d)
        if not (pg and c):
            continue
        f = c / pg
        out.append((d, a0, l0 * f if l0 else None, l1 * f if l1 else None, "fechamento"))
    return out


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
    """EV/EBITDA 12m à frente: (mcap do guia × preço do principal ÷ preço de referência + DL 12m) ÷ EBITDA 12m."""
    if not (x and preco_prim and x["ref"] and x["mcap"] and x["e0"] and x["e1"] and x["dl0"] is not None and x["dl1"] is not None):
        return None
    if int(d[:4]) != x["a0"]:
        return None
    w = peso_ano(d)
    eb = w * x["e0"] + (1 - w) * x["e1"]
    ev = x["mcap"] * preco_prim / x["ref"] + w * x["dl0"] + (1 - w) * x["dl1"]
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
    F, acc, fatores = {}, 1.0, {}
    for i, d in enumerate(datas):
        if i:
            d0 = datas[i - 1]
            c, vistos = Counter(), set()
            for tk, s in por.items():
                nome = REV.get(tk, (tk, 0))[0]
                if d in s and d0 in s and nome not in vistos and s[d] != s[d0]:
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
        pares = [(datas[i - 1], d) for i, d in enumerate(datas) if i and d in s and datas[i - 1] in s]
        if tk not in usd and len(pares) >= 4 and sum(1 for a, b in pares if s[a] != s[b]) >= 0.6 * len(pares):
            mtm.add(tk)
    return F, usd, mtm


def revisao(tk: str, L: dict, FX: tuple | None = None) -> float | None:
    """Revisão do lucro do ano seguinte entre o 1º e o último guia do ticker, sem o efeito cambial nas empresas que o
    BTG modela em dólar (lucro ÷ índice do fator cambial do guia)."""
    pts = (L.get("btg") or {}).get(tk) or []
    if len(pts) < 2:
        return None
    F, usd, mtm = FX or fator_cambio(L)
    (d0, b0), (d1, b1) = (pts[0][0], pts[0][5]), (pts[-1][0], pts[-1][5])
    if not (b0 and b1 and b0 > 0 and b1 > 0):
        return None
    if tk in usd or tk in mtm:
        b0, b1 = b0 / F.get(d0, 1.0), b1 / F.get(d1, 1.0)
    return b1 / b0 - 1


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
    R = atualiza()
    print(f"{len(R['guias'])} guias ({R['guias'][0] if R['guias'] else '—'} → {R['guias'][-1] if R['guias'] else '—'}), {len(R['btg'])} papéis")
    print("sem mapa:", len(R["_nao_mapeados"]))
