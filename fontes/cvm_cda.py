"""CVM, dados abertos: composição mensal das carteiras de fundos (CDA).

https://dados.cvm.gov.br/dados/FI/DOC/CDA/DADOS/cda_fi_{AAAAMM}.zip (15–30 MB; BLC_4 = ações; CONFID = posições sob sigilo).
Sigilo: o gestor pode manter as posições confidenciais por até 180 dias (campo DT_CONFID_APLIC); passado o prazo, a CVM
regrava o arquivo do mês com as posições abertas. Ou seja: carteira completa com ~6 meses de defasagem.
Cache por fundo e mês em data/cda/{cnpj_limpo}_{AAAAMM}.json; um mês só é rebaixado enquanto ainda tiver sigilo.
"""
from __future__ import annotations

import csv
import io
import json
import sys
import urllib.request
import zipfile
from datetime import date

import config

URL = "https://dados.cvm.gov.br/dados/FI/DOC/CDA/DADOS/cda_fi_{aaaamm}.zip"
CACHE = config.DATA / "cda"


def _limpo(cnpj: str) -> str:
    return "".join(c for c in cnpj if c.isdigit())


def carteira(cnpj: str, aaaamm: str) -> dict | None:
    """{'cnpj', 'nome', 'mes', 'pl', 'sigilo_ate', 'acoes': [[cod, nome, valor, qtd]], 'total_acoes'} ou None se o mês não existir."""
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{_limpo(cnpj)}_{aaaamm}.json"
    if f.exists():
        obj = json.loads(f.read_text(encoding="utf-8"))
        if not obj.get("sigilo_ate") or obj["sigilo_ate"] < date.today().isoformat():
            return obj
    zpath = CACHE / f"cda_fi_{aaaamm}.zip"
    if not zpath.exists():
        req = urllib.request.Request(URL.format(aaaamm=aaaamm), headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=600) as r, zpath.open("wb") as fh:
                fh.write(r.read())
        except Exception as e:
            print(f"  CDA {aaaamm}: {e}", file=sys.stderr)
            return None
    z = zipfile.ZipFile(zpath)
    out = {"cnpj": cnpj, "mes": aaaamm, "nome": None, "pl": None, "sigilo_ate": None, "acoes": [], "total_acoes": 0.0}
    for nome in z.namelist():
        if not nome.endswith(".csv"):
            continue
        if not ("BLC_4" in nome or "CONFID" in nome or "_PL_" in nome):
            continue
        rd = csv.DictReader(io.StringIO(z.read(nome).decode("latin-1")), delimiter=";")
        for r in rd:
            if r.get("CNPJ_FUNDO_CLASSE", r.get("CNPJ_FUNDO", "")) != cnpj:
                continue
            out["nome"] = out["nome"] or r.get("DENOM_SOCIAL")
            if "_PL_" in nome:
                out["pl"] = float(r.get("VL_PATRIM_LIQ") or 0)
            elif "CONFID" in nome:
                d = r.get("DT_CONFID_APLIC")
                if d and (not out["sigilo_ate"] or d > out["sigilo_ate"]):
                    out["sigilo_ate"] = d
            else:
                v = float(r.get("VL_MERC_POS_FINAL") or 0)
                if v > 0 and r.get("CD_ATIVO"):
                    out["acoes"].append([r["CD_ATIVO"].strip(), (r.get("DS_ATIVO") or "").strip(), v, float(r.get("QT_POS_FINAL") or 0)])
    # agrega linhas repetidas do mesmo ticker (posição + empréstimo etc.)
    agg: dict[str, list] = {}
    for cod, nm, v, q in out["acoes"]:
        a = agg.setdefault(cod, [cod, nm, 0.0, 0.0])
        a[2] += v; a[3] += q
    out["acoes"] = sorted(agg.values(), key=lambda x: -x[2])
    out["total_acoes"] = sum(a[2] for a in out["acoes"])
    if out["nome"] is None:
        return None
    f.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def meses_disponiveis(n: int = 4) -> list[str]:
    """Últimos n meses cujo sigilo de 180 dias já venceu (competência ≤ hoje − 7 meses, por folga)."""
    hoje = date.today()
    y, m = hoje.year, hoje.month - 7
    while m <= 0:
        m += 12; y -= 1
    out = []
    for _ in range(n):
        out.append(f"{y}{m:02d}")
        m -= 1
        if m == 0:
            m = 12; y -= 1
    return out[::-1]
