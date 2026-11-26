"""Fundamentos do S&P 500 — quatro fontes, cada uma independente (se uma cair, a seção dela fica como estava).

    shiller     Robert Shiller (shillerdata.com, sucessor de Yale): mensal desde 1871 — preço, dividendo e lucro 12m,
                CPI, juro de 10 anos, CAPE. O link do arquivo muda a cada atualização: é raspado da página.
    damodaran   Aswath Damodaran (NYU): ERP implícito. Mensal desde 2008 (ERPbymonth.xlsx: fluxo dividendo+recompra 12m em
                pontos do índice, crescimento esperado, ERP, retorno esperado) e anual desde 1960 (histimpl.xls).
    spdji       S&P Dow Jones Indices, planilha oficial de recompras (sp-500-buyback.xlsx): lucro operacional e contábil,
                dividendos e recompras trimestrais em US$ bi. Akamai barra headless e requisição "por fora": abre Chrome
                com janela fora da tela e busca de dentro da página (sem login). A cópia pública anda ATRASADA (em
                26/11/26 ia só até o 3T25): o campo `dados_ate` deixa isso à vista.
    yahoo       ^SP500TR / ^GSPC: dividend yield realizado 12m = retorno total ÷ retorno de preço − 1 (checagem cruzada).

    python -m fontes.sp500            # atualiza tudo e imprime o resumo
    data/sp500_fund.json = {"shiller": {...}, "damodaran_mensal": {...}, "damodaran_anual": {...}, "spdji": {...},
                            "yahoo_dy": {...}, "atualizado": ...}
"""
from __future__ import annotations

import base64
import io
import json
import re
import sys
from datetime import date, datetime

import config

ARQ = config.DATA / "sp500_fund.json"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"}
SHILLER_PAG = "https://shillerdata.com/"
DAMO_MENSAL = "https://pages.stern.nyu.edu/~adamodar/pc/implprem/ERPbymonth.xlsx"
DAMO_ANUAL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/histimpl.xls"
SPDJI_PAG = "https://www.spglobal.com/spdji/en/indices/equity/sp-500/"
SPDJI_XLSX = "https://www.spglobal.com/spdji/en/documents/additional-material/sp-500-buyback.xlsx"


def _get(url: str, timeout: int = 90) -> bytes:
    import requests
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r.content


def _num(x):
    if x in (None, "", "NA"):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- Shiller
def shiller() -> dict:
    import xlrd
    html = _get(SHILLER_PAG, 60).decode("utf-8", "replace")
    m = re.search(r'"(//img1\.wsimg\.com/[^"]*?/ie_data\.xls[^"]*)"', html)
    if not m:
        raise RuntimeError("Shiller: link do ie_data.xls não achado na página")
    s = xlrd.open_workbook(file_contents=_get("https:" + m.group(1))).sheet_by_name("Data")
    cab = [str(s.cell_value(7, c)).strip() for c in range(s.ncols)]
    ic = {n: cab.index(n) for n in ("Date", "P", "D", "E", "CPI", "Rate GS10", "CAPE") if n in cab}
    linhas = []
    for i in range(8, s.nrows):
        d = s.cell_value(i, 0)
        if not isinstance(d, float):
            continue
        ano, mes = int(d), int(round((d - int(d)) * 100))
        if not 1 <= mes <= 12:
            continue
        v = lambda k: _num(s.cell_value(i, ic[k])) if k in ic else None
        linhas.append([f"{ano:04d}-{mes:02d}", v("P"), v("D"), v("E"), v("CPI"), v("Rate GS10"), v("CAPE")])
    ult_d = next((l[0] for l in reversed(linhas) if l[2] is not None), None)
    return {"colunas": ["mes", "preco", "dividendo_12m", "lucro_12m", "cpi", "juro10", "cape"], "serie": linhas,
            "dados_ate": {"preco": linhas[-1][0], "dividendo_lucro": ult_d}, "fonte": "shillerdata.com (ie_data.xls)"}


# ---------------------------------------------------------------- Damodaran
def damodaran_mensal() -> dict:
    import openpyxl
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ws = openpyxl.load_workbook(io.BytesIO(_get(DAMO_MENSAL)), data_only=True)["Historical ERP"]
    rows = list(ws.iter_rows(values_only=True))
    cab = [str(h).strip() if h else "" for h in rows[0]]
    col = lambda nome: next(i for i, h in enumerate(cab) if h.startswith(nome))
    mapa = {"indice": col("S&P 500"), "tbond": col("T.Bond Rate"), "rf": col("$ Riskfree"), "cf_10a": col("Ten-year average CF"),
            "cf_12m": col("CF (Trailing"), "cf_normal": col("Normalized CF"), "g_esperado": col("Expected growth"),
            "erp": col("ERP (T12m)"), "retorno_esperado": col("Expected Return")}
    serie = []
    for r in rows[1:]:
        if not isinstance(r[0], datetime):
            continue
        serie.append([r[0].strftime("%Y-%m")] + [_num(r[mapa[k]]) for k in mapa])
    return {"colunas": ["mes"] + list(mapa), "serie": serie, "dados_ate": serie[-1][0],
            "nota": "cf = dividendos + recompras em pontos do índice; erp = ERP (T12m) de Damodaran; g_esperado = crescimento "
                    "de lucro esperado p/ 5 anos; perpetuidade cresce ao juro livre de risco", "fonte": DAMO_MENSAL}


def damodaran_anual() -> dict:
    import xlrd
    sh = xlrd.open_workbook(file_contents=_get(DAMO_ANUAL)).sheet_by_index(0)
    ih = next(i for i in range(sh.nrows) if str(sh.cell_value(i, 0)).strip() == "Year")
    cab = [str(x).strip() for x in sh.row_values(ih)]
    pega = {"earnings_yield": "Earnings Yield", "dividend_yield": "Dividend Yield", "indice": "S&P 500", "lucro": "Earnings*",
            "dividendos": "Dividends*", "div_mais_recompra": "Dividends + Buybacks", "tbond": "T.Bond Rate",
            "g_analistas": "Analyst Growth Estimate", "erp_fcfe": "Implied ERP (FCFE)"}
    idx = {k: cab.index(v) for k, v in pega.items() if v in cab}
    serie = []
    for i in range(ih + 1, sh.nrows):
        a = sh.cell_value(i, 0)
        if not isinstance(a, float):
            break
        serie.append([int(a)] + [_num(sh.cell_value(i, idx[k])) if k in idx else None for k in pega])
    return {"colunas": ["ano"] + list(pega), "serie": serie, "dados_ate": serie[-1][0], "fonte": DAMO_ANUAL}


# ---------------------------------------------------------------- S&P DJI (Chrome fora da tela)
_JS_FETCH = """async u => { const r = await fetch(u, {credentials: 'include'}); const b = new Uint8Array(await r.arrayBuffer());
  let s = ''; for (let i = 0; i < b.length; i += 32768) s += String.fromCharCode.apply(null, b.subarray(i, i + 32768));
  return {st: r.status, b64: btoa(s)}; }"""


def _spdji_bytes() -> bytes:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=False,
                              args=["--disable-blink-features=AutomationControlled", "--window-position=-32000,-32000", "--window-size=1200,800"])
        try:
            pg = b.new_context(locale="en-US").new_page()
            pg.goto(SPDJI_PAG, timeout=60000, wait_until="domcontentloaded")
            pg.wait_for_timeout(4000)
            if "denied" in pg.title().lower() or "error" in pg.title().lower():
                raise RuntimeError(f"S&P DJI barrou a página ({pg.title()[:40]})")
            res = pg.evaluate(_JS_FETCH, SPDJI_XLSX)
        finally:
            b.close()
    dados = base64.b64decode(res["b64"]) if res.get("b64") else b""
    if res.get("st") != 200 or dados[:2] != b"PK":
        raise RuntimeError(f"S&P DJI: status {res.get('st')}, {len(dados)} bytes")
    return dados


def spdji() -> dict:
    import openpyxl
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(io.BytesIO(_spdji_bytes()), data_only=True, read_only=True)
    rows = [r for r in wb["TABLE"].iter_rows(values_only=True)]
    tri = []
    for r in rows:
        p = r[0]
        if isinstance(p, datetime):
            d = p.date().isoformat()
        elif isinstance(p, str) and re.match(r"\d{1,2}/\d{1,2}/\d{4}", p.strip()):
            mm, dd, aa = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", p.strip()).groups()
            d = f"{aa}-{int(mm):02d}-{int(dd):02d}"
        elif isinstance(p, str) and re.match(r"\d{4}-\d{2}-\d{2}", p.strip()):
            d = p.strip()[:10]
        else:
            continue
        v = [_num(x) for x in r[1:9]]
        if v[0] is None:
            continue
        tri.append([d] + v + [("E" in str(p)) or ("prelim" in str(p).lower())])
    tri.sort(key=lambda x: x[0])
    setor = []
    for r in wb["SECTOR YIELDS"].iter_rows(values_only=True):
        if isinstance(r[0], str) and _num(r[1]) is not None and _num(r[3]) is not None:
            setor.append([r[0].strip(), _num(r[1]), _num(r[2]), _num(r[3])])
        elif setor:
            break
    return {"colunas": ["fim_tri", "valor_mercado_bi", "lucro_operacional_bi", "lucro_contabil_bi", "dividendos_bi", "recompras_bi",
                        "dividend_yield", "buyback_yield", "yield_total", "preliminar"],
            "trimestres": tri, "setores_yield": setor, "dados_ate": tri[-1][0] if tri else None,
            "fonte": SPDJI_XLSX + " (cópia pública; pode estar atrasada)"}


# ---------------------------------------------------------------- Yahoo (DY realizado)
def yahoo_dy() -> dict:
    from fontes import yahoo
    tr = dict(yahoo.historico("^SP500TR", "max"))
    px = dict(yahoo.historico("^GSPC", "max"))
    dias = sorted(set(tr) & set(px))
    if len(dias) < 300:
        raise RuntimeError(f"Yahoo: só {len(dias)} dias em comum")
    fim_mes = {}
    for d in dias:
        fim_mes[d[:7]] = d                         # último pregão de cada mês
    meses = sorted(fim_mes)
    serie = []
    for i in range(12, len(meses)):
        d1, d0 = fim_mes[meses[i]], fim_mes[meses[i - 12]]
        serie.append([meses[i], round((tr[d1] / tr[d0]) / (px[d1] / px[d0]) - 1, 6)])
    return {"colunas": ["mes", "dy_realizado_12m"], "serie": serie, "dados_ate": serie[-1][0],
            "fonte": "Yahoo ^SP500TR / ^GSPC (retorno total ÷ preço − 1, 12 meses)"}


FONTES = {"shiller": shiller, "damodaran_mensal": damodaran_mensal, "damodaran_anual": damodaran_anual, "spdji": spdji, "yahoo_dy": yahoo_dy}


def ler() -> dict:
    return json.loads(ARQ.read_text(encoding="utf-8")) if ARQ.exists() else {}


def atualiza(so: list[str] | None = None) -> dict:
    D = ler()
    status = {}
    for nome, fn in FONTES.items():
        if so and nome not in so:
            continue
        try:
            D[nome] = fn() | {"coletado": datetime.now().strftime("%Y-%m-%d %H:%M")}
            status[nome] = f"ok até {D[nome].get('dados_ate')}"
        except Exception as e:                      # mantém a seção anterior
            status[nome] = f"FALHOU ({str(e)[:90]}); mantido {(D.get(nome) or {}).get('dados_ate', 'nada')}"
            print(f"  sp500/{nome}: {e}", file=sys.stderr)
    D["atualizado"] = datetime.now().strftime("%Y-%m-%dT%H:%M")
    D["status"] = status
    ARQ.parent.mkdir(parents=True, exist_ok=True)
    ARQ.write_text(json.dumps(D, ensure_ascii=False), encoding="utf-8")
    return D


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    D = atualiza(sys.argv[1:] or None)
    for k, v in D["status"].items():
        print(f"{k:18s} {v}")
