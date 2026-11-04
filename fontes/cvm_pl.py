"""Patrimônio líquido e ações em circulação pela CVM (ITR/DFP, dados abertos) para o P/VP do screening.

- PL dos controladores: balanço patrimonial passivo consolidado (BPP_con), conta 2.03 menos a participação dos não
  controladores; se a companhia não publica consolidado, usa o individual (BPP_ind, conta 2.03).
- Ações: composição do capital do mesmo documento (QT_ACAO_TOTAL_CAP_INTEGR − QT_ACAO_TOTAL_TESOURO, ON + PN).
- Ticker -> CNPJ: FCA (valor_mobiliario: Codigo_Negociacao).
VPA = PL ÷ ações em circulação (por ação; unit = soma das ações que a compõem, config UNIT_ACOES).

Saída: data/pl_cvm.json = {ticker: {"data": dt_refer, "pl": R$ mn, "acoes": n, "vpa": R$/ação, "doc": "ITR"|"DFP"}}.
Histórico (P/VP diário do slide "Valuation · por empresa"): data/pl_cvm_hist.json = {"series": {ticker: [[dt_refer, dt_receb,
vpa, pl], ...]}} desde 1T20 (a composição do capital só existe nos dados abertos a partir de 2020); dt_receb = entrega na
CVM, data em que o mercado passou a conhecer aquele patrimônio (o P/VP de um dia usa o último balanço já entregue).

    python -m fontes.cvm_pl           # último balanço (semanal)
    python -m fontes.cvm_pl --hist    # histórico trimestral (mensal; incremental: só o ano corrente e o anterior)
    python -m fontes.cvm_pl --hist --tudo   # refaz o histórico inteiro desde 2020
"""
from __future__ import annotations

import csv
import io
import json
import math
import sys
import zipfile
from datetime import date
from pathlib import Path

import requests

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config

ARQ = config.DATA / "pl_cvm.json"
ARQ_HIST = config.DATA / "pl_cvm_hist.json"
BASE = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC"
# ações por unit (B3): 1 ON + n PN
UNIT_ACOES = {"KLBN11": 5, "SANB11": 2, "TAEE11": 3, "ENGI11": 5, "IGTI11": 3, "BPAC11": 3, "ALUP11": 3, "SAPR11": 5}


def _zip(doc: str, ano: int) -> zipfile.ZipFile | None:
    r = requests.get(f"{BASE}/{doc}/DADOS/{doc.lower()}_cia_aberta_{ano}.zip", timeout=180)
    if r.status_code != 200:
        return None
    return zipfile.ZipFile(io.BytesIO(r.content))


def _csv(z: zipfile.ZipFile, sufixo: str) -> list[dict]:
    nome = next((n for n in z.namelist() if n.endswith(sufixo)), None)
    if not nome:
        return []
    return list(csv.DictReader(io.TextIOWrapper(z.open(nome), encoding="latin-1"), delimiter=";"))


def _f(x) -> float:
    try:
        return float(str(x).replace(",", "."))
    except ValueError:
        return 0.0


def tickers_cnpj(ano: int) -> dict[str, str]:
    """Codigo_Negociacao -> CNPJ pelo FCA (valor mobiliário) do ano (cai para o anterior se ainda não houver)."""
    for a in (ano, ano - 1):
        z = _zip("FCA", a)
        if not z:
            continue
        out = {}
        for r in _csv(z, f"valor_mobiliario_{a}.csv"):
            cod = (r.get("Codigo_Negociacao") or "").strip().upper()
            if cod:
                out[cod] = r["CNPJ_Companhia"]
        if out:
            return out
    return {}


def _bpp(z: zipfile.ZipFile, ano: int) -> dict[tuple, dict]:
    """(CNPJ, DT_REFER) -> {"con"/"ind": {versao, total, conta, nc}} do balanço passivo (última versão)."""
    pl: dict[tuple, dict] = {}
    for tipo in ("con", "ind"):
        for r in _csv(z, f"BPP_{tipo}_{ano}.csv"):
            if r.get("ORDEM_EXERC") != "ÚLTIMO":
                continue
            k = (r["CNPJ_CIA"], r["DT_REFER"])
            esc = 1e-3 if r.get("ESCALA_MOEDA", "").upper().startswith("MIL") else 1e-6     # -> R$ mn
            cc, ds, v = r["CD_CONTA"], (r.get("DS_CONTA") or "").lower(), _f(r["VL_CONTA"]) * esc
            reg = pl.setdefault(k, {}).setdefault(tipo, {"versao": -1})
            ver = int(r.get("VERSAO") or 0)
            if ver < reg["versao"]:
                continue
            if ver > reg["versao"]:
                reg.clear(); reg["versao"] = ver
            if cc.count(".") == 1 and ds.startswith("patrimônio líquido"):     # 2.03 nas empresas, 2.08 nos bancos
                reg["total"], reg["conta"] = v, cc
            elif cc.count(".") == 2 and "não controlador" in ds and cc.startswith(reg.get("conta", "#") + "."):
                reg["nc"] = v
    return pl


def _acoes(z: zipfile.ZipFile, ano: int) -> dict[tuple, float]:
    """(CNPJ, DT_REFER) -> ações em circulação (integralizadas − tesouraria), última versão."""
    acoes: dict[tuple, float] = {}
    ver_ac: dict[tuple, int] = {}
    for r in _csv(z, f"composicao_capital_{ano}.csv"):
        k = (r["CNPJ_CIA"], r["DT_REFER"])
        ver = int(r.get("VERSAO") or 0)
        tot, tes = _f(r.get("QT_ACAO_TOTAL_CAP_INTEGR")), _f(r.get("QT_ACAO_TOTAL_TESOURO"))
        if tot and ver >= ver_ac.get(k, -1):
            acoes[k], ver_ac[k] = tot - tes, ver
    return acoes


def _receb(z: zipfile.ZipFile, doc: str, ano: int) -> dict[tuple, str]:
    """(CNPJ, DT_REFER) -> data de entrega na CVM da 1ª versão do documento (quando o patrimônio ficou público)."""
    out: dict[tuple, str] = {}
    for r in _csv(z, f"{doc.lower()}_cia_aberta_{ano}.csv"):
        k, d = (r["CNPJ_CIA"], r["DT_REFER"]), (r.get("DT_RECEB") or "")[:10]
        if d and (k not in out or d < out[k]):
            out[k] = d
    return out


def _todos(doc: str, ano: int) -> dict[tuple, dict]:
    """(CNPJ, DT_REFER) -> {data, pl (R$ mn, controladores), acoes, doc, receb} de todos os trimestres do arquivo do ano."""
    z = _zip(doc, ano)
    if not z:
        return {}
    pl, acoes, receb = _bpp(z, ano), _acoes(z, ano), _receb(z, doc, ano)
    out: dict[tuple, dict] = {}
    for (cnpj, dt), regs in pl.items():
        r = regs.get("con") if (regs.get("con") or {}).get("total") else regs.get("ind")   # consolidado zerado (TIM 2024) -> individual
        if not r or not r.get("total") or (cnpj, dt) not in acoes:
            continue
        out[(cnpj, dt)] = {"data": dt, "pl": round(r["total"] - r.get("nc", 0.0), 2), "acoes": acoes[(cnpj, dt)], "doc": doc,
                           "receb": receb.get((cnpj, dt), "")}
    return out


def _por_doc(doc: str, ano: int) -> dict[str, dict]:
    """CNPJ -> {data, pl, acoes} do documento mais recente (última versão) do ano."""
    out: dict[str, dict] = {}
    for (cnpj, dt), r in _todos(doc, ano).items():
        if cnpj not in out or dt > out[cnpj]["data"]:
            out[cnpj] = {k: v for k, v in r.items() if k != "receb"}
    return out


def _escala_acoes(tk: str, acoes: float, r: dict, LB: dict) -> tuple[float, dict]:
    """Algumas companhias informam a composição do capital em milhares de ações (Vale, Itaú, Santander, Axia, Taesa):
    escolhe 1 ou 1.000 pelo nº de ações implícito no guia do BTG (valor de mercado ÷ preço do papel principal); se a
    composição destoa > 25% do guia (desatualizada/parcial, TEND3), usa o nº do guia e marca a fonte."""
    from fontes import b3, lucros_casas
    pt = (LB.get(tk) or [None])[-1]
    prim = lucros_casas.MAPA_BTG.get(lucros_casas.REV.get(tk, ("", 0))[0], [tk])[0]
    cp = dict(b3.serie(prim)).get(pt[0]) if pt else None
    if pt and len(pt) > 8 and pt[8] and pt[6] and cp and abs(pt[6] / cp - 1) <= 0.03:   # só se o preço do guia é do papel principal
        ref = pt[8] * 1e6 / pt[6] * UNIT_ACOES.get(prim, 1)
        if abs(math.log(acoes * 1000 / ref)) < abs(math.log(acoes / ref)):
            acoes *= 1000
        if abs(acoes / ref - 1) > 0.25:           # composição do capital desatualizada/parcial (TEND3 57 mi × 123 mi)
            acoes, r = ref, {**r, "acoes_fonte": "guia BTG (mcap ÷ preço)"}
    elif acoes < 2e7:
        acoes *= 1000
    return acoes, r


def _universo() -> set[str]:
    from fontes import lucros_casas
    C = json.loads((config.DATA / "ibov_comp.json").read_text(encoding="utf-8"))
    return ({i["cod"] for i in C.get("itens", [])} | set(config.UNIVERSO) | set(getattr(config, "VALUATION_EXTRA", []))
            | set(lucros_casas.carrega().get("btg") or {}))


def atualiza(tickers: set[str] | None = None) -> str:
    hoje = date.today()
    mapa = tickers_cnpj(hoje.year)
    base: dict[str, dict] = {}
    for doc, ano in (("DFP", hoje.year - 1), ("ITR", hoje.year - 1), ("ITR", hoje.year)):
        for cnpj, r in _por_doc(doc, ano).items():
            if cnpj not in base or r["data"] > base[cnpj]["data"]:
                base[cnpj] = r
    if tickers is None:
        tickers = _universo()
    from fontes import lucros_casas
    LB = lucros_casas.carrega().get("btg") or {}
    out = {}
    for tk in sorted(tickers):
        r = base.get(mapa.get(tk, ""))
        if not r or not r["acoes"]:
            continue
        acoes, r = _escala_acoes(tk, r["acoes"], r, LB)
        vpa = r["pl"] * 1e6 / acoes * UNIT_ACOES.get(tk, 1)
        out[tk] = {**r, "acoes": acoes, "vpa": round(vpa, 4)}
    ARQ.write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8")
    return f"{len(out)} papéis, PL até {max((r['data'] for r in out.values()), default='—')}"


def _sem_saltos(regs: list[dict], lim: float = 5.0) -> list[dict]:
    """Tira trimestres isolados com PL > lim× (ou < 1/lim) do anterior E do seguinte (linha em outra escala, ex. 1,09 entre
    1.295 e 1.129). Uma mudança que persiste (fusão, aumento de capital) fica."""
    out = []
    for i, r in enumerate(regs):
        if 0 < i < len(regs) - 1 and r["pl"] > 0 and regs[i - 1]["pl"] > 0 and regs[i + 1]["pl"] > 0:
            a, b = r["pl"] / regs[i - 1]["pl"], r["pl"] / regs[i + 1]["pl"]
            if (a > lim and b > lim) or (a < 1 / lim and b < 1 / lim):
                continue
        out.append(r)
    return out


def historico(tickers: set[str] | None = None, desde: int = 2020, tudo: bool = False) -> str:
    """Patrimônio dos controladores e VPA de todos os trimestres desde `desde` (ITR + DFP), por ticker, em ARQ_HIST.
    O nº de ações é o de cada trimestre (o P/VP usa o preço sem ajuste da B3, então desdobramentos ficam certos sem
    ajuste); a escala (unidades × milhares) é resolvida por continuidade a partir do balanço mais recente. Fica de fora o
    que não tem composição do capital nos dados abertos (antes de 2020). Com o arquivo já existente, baixa só o que pode
    ter mudado (ITR do ano, DFP e ITR do ano anterior) e guarda o resto; `tudo` refaz desde `desde`."""
    hoje = date.today()
    mapa = tickers_cnpj(hoje.year)
    if tickers is None:
        tickers = _universo()
    cnpjs = {mapa[tk] for tk in tickers if tk in mapa}
    antigo = {} if tudo else carrega_hist()
    anos = range(desde, hoje.year + 1) if (tudo or not antigo) else range(hoje.year - 1, hoje.year + 1)
    todos: dict[tuple, dict] = {}
    for ano in anos:
        for doc in ("ITR", "DFP"):
            if doc == "DFP" and ano == hoje.year:
                continue
            for k, r in _todos(doc, ano).items():
                if k[0] in cnpjs and (k not in todos or r["doc"] == "DFP"):       # DFP (ano) manda sobre ITR na mesma data
                    todos[k] = r
    lim = f"{anos[0]}-01-01"
    for tk, ser in antigo.items():                     # trimestres guardados de antes da janela baixada entram como estão
        if tk in mapa:                                 # (VPA por ticker: SAPR3/SAPR4/SAPR11 dividem o CNPJ, não o VPA)
            for p in ser:
                if p[0] < lim and (mapa[tk], p[0]) not in todos:
                    todos[(mapa[tk], p[0])] = {"data": p[0], "pl": p[3], "acoes": None, "doc": "cache", "receb": p[1], "vpa": {}}
                if p[0] < lim and todos[(mapa[tk], p[0])]["doc"] == "cache":
                    todos[(mapa[tk], p[0])]["vpa"][tk] = p[2]
    from fontes import lucros_casas
    LB = lucros_casas.carrega().get("btg") or {}
    atual = carrega()
    out: dict[str, list] = {}
    for tk in sorted(tickers):
        cnpj = mapa.get(tk)
        if not cnpj:
            continue
        regs = sorted((r for (c, _), r in todos.items() if c == cnpj and (r["acoes"] or (r.get("vpa") or {}).get(tk))), key=lambda r: r["data"])
        regs = [r for r in regs if r["pl"] > 0]        # patrimônio negativo ou zerado: P/VP sem sentido (e linha quebrada no cadastro)
        novos = _sem_saltos([r for r in regs if r["acoes"]])
        if not regs:
            continue
        adj = {}
        if novos:        # sem trimestre novo com PL positivo (patrimônio negativo hoje: Braskem, Sequoia): só o guardado
            ult = novos[-1]
            acoes_u, _ = _escala_acoes(tk, ult["acoes"], ult, LB)
            k = 1000.0 if abs(math.log(acoes_u / (ult["acoes"] * 1000))) < abs(math.log(acoes_u / ult["acoes"])) else 1.0
            # escala por trimestre, do mais novo para o mais velho: a companhia troca de unidade (Petrobras: milhares até 1T21,
            # unidades depois) e um grupamento/desdobramento (≤ 30×) fica longe de 1.000× — escolhe 1, 1.000 ou 1/1.000 por continuidade
            ref = ult["acoes"] * k
            for r in reversed(novos):
                f = min((1.0, 1000.0, 0.001), key=lambda f: abs(math.log(r["acoes"] * f / ref)))
                adj[r["data"]] = ref = r["acoes"] * f
        serie = []
        for r in regs:
            if not r["acoes"]:                          # trimestre guardado (fora da janela baixada): VPA como estava
                serie.append([r["data"], r["receb"] or r["data"], r["vpa"][tk], r["pl"]])
                continue
            if r["data"] not in adj:
                continue
            vpa = r["pl"] * 1e6 / adj[r["data"]] * UNIT_ACOES.get(tk, 1)
            serie.append([r["data"], r["receb"] or r["data"], round(vpa, 4), r["pl"]])
        at = atual.get(tk) or {}
        if at.get("acoes_fonte") and at.get("pl", 0) > 0:   # composição ruim no balanço atual: o ponto atual vem do pl_cvm.json
            rec = next((p[1] for p in serie if p[0] == at["data"]), at["data"])
            serie = [p for p in serie if p[0] < at["data"]] + [[at["data"], rec, at["vpa"], at["pl"]]]
        if serie:
            out[tk] = serie
    ARQ_HIST.write_text(json.dumps({"atualizado": hoje.isoformat(), "series": out}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    n = sum(len(v) for v in out.values())
    return f"{len(out)} papéis, {n} trimestres, de {min((v[0][0] for v in out.values()), default='—')} a {max((v[-1][0] for v in out.values()), default='—')}"


def carrega() -> dict:
    return json.loads(ARQ.read_text(encoding="utf-8")) if ARQ.exists() else {}


def carrega_hist() -> dict[str, list]:
    """{ticker: [[dt_refer, dt_receb, vpa, pl], ...]} ou {}."""
    try:
        return (json.loads(ARQ_HIST.read_text(encoding="utf-8")).get("series") or {}) if ARQ_HIST.exists() else {}
    except Exception:
        return {}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(historico(tudo="--tudo" in sys.argv[1:]) if "--hist" in sys.argv[1:] else atualiza())
