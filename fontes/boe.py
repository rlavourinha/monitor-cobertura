"""Bank of England: curva de juro real do Reino Unido (gilts indexados, spot), vértice de 10 anos, diária.

Planilhas "GLC Real daily data" (aba "4. spot curve": linha "years:" com os prazos, uma linha por dia). O arquivo histórico
(glcrealddata.zip, ~25 MB, 1985 em diante) só é baixado quando data/boe_real10.json não existe; no dia a dia basta o do mês
corrente (latest-yield-curve-data.zip, ~0,4 MB), e o JSON acumula. Juro real contra o RPI (não o CPI), o que deixa a taxa
~0,5-1 p.p. abaixo do que seria contra o CPI até a reforma do RPI (2030).
"""
from __future__ import annotations

import io
import json
import sys
import urllib.request
import zipfile
from datetime import datetime

import config

BASE = "https://www.bankofengland.co.uk/-/media/boe/files/statistics/yield-curves/{}.zip"
ARQ = config.DATA / "boe_real10.json"
PRAZO = 10


def _baixa(nome: str) -> zipfile.ZipFile:
    req = urllib.request.Request(BASE.format(nome), headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return zipfile.ZipFile(io.BytesIO(r.read()))


def _le_planilha(conteudo: bytes, desde: str) -> dict[str, float]:
    """{data ISO: taxa real spot de 10 anos} da aba '4. spot curve'."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(conteudo), read_only=True, data_only=True)
    ws = wb["4. spot curve"]
    col, out = None, {}
    for row in ws.iter_rows(values_only=True):
        if col is None:
            if row and isinstance(row[0], str) and row[0].strip().lower().startswith("years"):
                col = next((j for j, v in enumerate(row) if isinstance(v, (int, float)) and abs(v - PRAZO) < 1e-6), None)
            continue
        d, v = row[0], (row[col] if col < len(row) else None)
        if isinstance(d, datetime) and isinstance(v, (int, float)):
            iso = d.date().isoformat()
            if iso >= desde:
                out[iso] = round(float(v), 4)
    wb.close()
    return out


def atualiza(desde: str = "2006-01-01") -> list[list]:
    """Atualiza data/boe_real10.json e devolve [[data, taxa], ...]."""
    S = {d: v for d, v in json.loads(ARQ.read_text(encoding="utf-8"))} if ARQ.exists() else {}
    fontes = (["glcrealddata"] if not S else []) + ["latest-yield-curve-data"]
    for nome in fontes:
        try:
            z = _baixa(nome)
        except Exception as e:
            print(f"  BoE {nome}: {e}", file=sys.stderr)
            continue
        for n in z.namelist():
            if "Real" in n and n.endswith(".xlsx") and (nome == "latest-yield-curve-data" or n[-9:-5] >= desde[:4] or "present" in n):
                S.update(_le_planilha(z.read(n), desde))
    serie = sorted([[d, v] for d, v in S.items()])
    if serie:
        ARQ.write_text(json.dumps(serie), encoding="utf-8")
    return serie


def ler() -> list[list]:
    return json.loads(ARQ.read_text(encoding="utf-8")) if ARQ.exists() else []
