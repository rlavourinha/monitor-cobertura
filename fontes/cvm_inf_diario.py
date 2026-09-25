"""CVM, dados abertos: informe diário de fundos (cota, PL, captação/resgate).

https://dados.cvm.gov.br/dados/FI/DOC/INF_DIARIO/DADOS/inf_diario_fi_{AAAAMM}.zip (um CSV por mês, todos os fundos, ~20 MB).
Só guardamos a série do fundo pedido: data/inf_diario/{cnpj_limpo}.json = [[data, cota, pl], ...].
Meses já fechados não são rebaixados; o mês corrente é rebaixado a cada chamada (a CVM regrava o arquivo diariamente).
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

URL = "https://dados.cvm.gov.br/dados/FI/DOC/INF_DIARIO/DADOS/inf_diario_fi_{aaaamm}.zip"
CACHE = config.DATA / "inf_diario"


def _limpo(cnpj: str) -> str:
    return "".join(c for c in cnpj if c.isdigit())


def _meses(desde: str) -> list[str]:
    y, m = int(desde[:4]), int(desde[5:7])
    hoje = date.today()
    out = []
    while (y, m) <= (hoje.year, hoje.month):
        out.append(f"{y}{m:02d}")
        m += 1
        if m == 13:
            m = 1; y += 1
    return out


def _mes_cvm(cnpj: str, aaaamm: str) -> list[list]:
    """[[data, cota, pl]] do fundo no mês, lendo o zip da CVM em streaming (não grava o zip)."""
    req = urllib.request.Request(URL.format(aaaamm=aaaamm), headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            raw = r.read()
    except Exception as e:
        print(f"  INF_DIARIO {aaaamm}: {e}", file=sys.stderr)
        return []
    z = zipfile.ZipFile(io.BytesIO(raw))
    out = []
    for nome in z.namelist():
        if not nome.endswith(".csv"):
            continue
        rd = csv.DictReader(io.StringIO(z.read(nome).decode("latin-1")), delimiter=";")
        for row in rd:
            if row.get("CNPJ_FUNDO_CLASSE", row.get("CNPJ_FUNDO", "")) != cnpj:
                continue
            try:
                out.append([row["DT_COMPTC"], float(row["VL_QUOTA"]), float(row.get("VL_PATRIM_LIQ") or 0)])
            except (KeyError, ValueError):
                pass
    return sorted(out)


def serie(cnpj: str, desde: str) -> list[list]:
    """Série diária [[data, cota, pl]] do fundo desde `desde` (YYYY-MM-DD), com cache incremental por mês."""
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{_limpo(cnpj)}.json"
    cache = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"meses": {}, "serie": []}
    hoje = date.today().strftime("%Y%m")
    mudou = False
    for m in _meses(desde):
        if m in cache["meses"] and m != hoje:
            continue
        pts = _mes_cvm(cnpj, m)
        if pts or m != hoje:
            cache["meses"][m] = pts
            mudou = True
            print(f"  INF_DIARIO {m}: {len(pts)} dias", file=sys.stderr)
    if mudou:
        cache["serie"] = sorted(p for pts in cache["meses"].values() for p in pts if p[0] >= desde)
        f.write_text(json.dumps(cache), encoding="utf-8")
    return cache["serie"]
