"""Patrimônio líquido e ações em circulação pela CVM (ITR/DFP, dados abertos) para o P/VP do screening.

- PL dos controladores: balanço patrimonial passivo consolidado (BPP_con), conta 2.03 menos a participação dos não
  controladores; se a companhia não publica consolidado, usa o individual (BPP_ind, conta 2.03).
- Ações: composição do capital do mesmo documento (QT_ACAO_TOTAL_CAP_INTEGR − QT_ACAO_TOTAL_TESOURO, ON + PN).
- Ticker -> CNPJ: FCA (valor_mobiliario: Codigo_Negociacao).
VPA = PL ÷ ações em circulação (por ação; unit = soma das ações que a compõem, config UNIT_ACOES).

Saída: data/pl_cvm.json = {ticker: {"data": dt_refer, "pl": R$ mn, "acoes": n, "vpa": R$/ação, "doc": "ITR"|"DFP"}}.

    python -m fontes.cvm_pl
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


def _por_doc(doc: str, ano: int) -> dict[str, dict]:
    """CNPJ -> {data, pl, acoes} do documento mais recente (última versão) do ano."""
    z = _zip(doc, ano)
    if not z:
        return {}
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
    acoes: dict[tuple, float] = {}
    ver_ac: dict[tuple, int] = {}
    for r in _csv(z, f"composicao_capital_{ano}.csv"):
        k = (r["CNPJ_CIA"], r["DT_REFER"])
        ver = int(r.get("VERSAO") or 0)
        tot, tes = _f(r.get("QT_ACAO_TOTAL_CAP_INTEGR")), _f(r.get("QT_ACAO_TOTAL_TESOURO"))
        if tot and ver >= ver_ac.get(k, -1):
            acoes[k], ver_ac[k] = tot - tes, ver
    out: dict[str, dict] = {}
    for (cnpj, dt), regs in pl.items():
        r = regs.get("con") if "total" in (regs.get("con") or {}) else regs.get("ind")
        if not r or "total" not in r or (cnpj, dt) not in acoes:
            continue
        v = r["total"] - r.get("nc", 0.0)
        if cnpj not in out or dt > out[cnpj]["data"]:
            out[cnpj] = {"data": dt, "pl": round(v, 2), "acoes": acoes[(cnpj, dt)], "doc": doc}
    return out


def atualiza(tickers: set[str] | None = None) -> str:
    hoje = date.today()
    mapa = tickers_cnpj(hoje.year)
    base: dict[str, dict] = {}
    for doc, ano in (("DFP", hoje.year - 1), ("ITR", hoje.year - 1), ("ITR", hoje.year)):
        for cnpj, r in _por_doc(doc, ano).items():
            if cnpj not in base or r["data"] > base[cnpj]["data"]:
                base[cnpj] = r
    if tickers is None:
        from fontes import lucros_casas
        C = json.loads((config.DATA / "ibov_comp.json").read_text(encoding="utf-8"))
        tickers = {i["cod"] for i in C.get("itens", [])} | set(config.UNIVERSO) | set(lucros_casas.carrega().get("btg") or {})
    from fontes import b3, lucros_casas
    LB = lucros_casas.carrega().get("btg") or {}
    out = {}
    for tk in sorted(tickers):
        r = base.get(mapa.get(tk, ""))
        if not r or not r["acoes"]:
            continue
        acoes = r["acoes"]
        # algumas companhias informam a composição do capital em milhares de ações (Vale, Itaú, Santander, Axia, Taesa):
        # escolhe 1 ou 1.000 pelo nº de ações implícito no guia do BTG (valor de mercado ÷ preço do papel principal)
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
        vpa = r["pl"] * 1e6 / acoes * UNIT_ACOES.get(tk, 1)
        out[tk] = {**r, "acoes": acoes, "vpa": round(vpa, 4)}
    ARQ.write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8")
    return f"{len(out)} papéis, PL até {max((r['data'] for r in out.values()), default='—')}"


def carrega() -> dict:
    return json.loads(ARQ.read_text(encoding="utf-8")) if ARQ.exists() else {}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(atualiza())
