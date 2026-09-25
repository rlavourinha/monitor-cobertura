"""Tesouro Transparente: taxas do Tesouro Direto (NTN-B = 'Tesouro IPCA+'), juro real de mercado.

CSV único (~15 MB, ';', latin-1, datas dd/mm/aaaa, decimais com vírgula), não ordenado.
Cache diário em data/tesouro_direto.csv.
"""
from __future__ import annotations

import csv
import sys
import urllib.request
from datetime import date

import config

URL = ("https://www.tesourotransparente.gov.br/ckan/dataset/df56aa42-484a-4a59-8184-7676580c81e3/"
       "resource/796d2059-14e9-44e3-80c9-2d9e30b405c1/download/PrecoTaxaTesouroDireto.csv")
CACHE = config.DATA / "tesouro_direto.csv"


def _iso(s: str) -> str:
    return f"{s[6:]}-{s[3:5]}-{s[:2]}"


def _baixa() -> bool:
    if CACHE.exists() and date.fromtimestamp(CACHE.stat().st_mtime) == date.today():
        return True
    try:
        req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=300) as r, CACHE.open("wb") as f:
            f.write(r.read())
        return True
    except Exception as e:
        print(f"  Tesouro Direto: {e}", file=sys.stderr)
        return CACHE.exists()


def ntnb() -> dict:
    """{'data': ..., 'curva': [[vencimento, taxa_compra%], ...]} para Tesouro IPCA+ (principal e com juros)."""
    if not _baixa():
        return {}
    with CACHE.open(encoding="latin-1", newline="") as fh:
        rows = [r for r in csv.DictReader(fh, delimiter=";") if r["Tipo Titulo"].startswith("Tesouro IPCA+")]
    if not rows:
        return {}
    ultima = max(_iso(r["Data Base"]) for r in rows)
    curva = {}
    for r in rows:
        if _iso(r["Data Base"]) != ultima:
            continue
        venc = _iso(r["Data Vencimento"])
        taxa = float(r["Taxa Compra Manha"].replace(",", "."))
        curva.setdefault(venc, taxa)  # se há principal e com juros no mesmo vencimento, fica o 1º
    return {"data": ultima, "curva": sorted([[v, t] for v, t in curva.items()])}


def ntnb_historico(vencimento_prefixo: str = "2035") -> list[list]:
    """Série histórica da taxa de compra da NTN-B de um vencimento (ex.: 2035) — juro real longo."""
    if not CACHE.exists():
        return []
    with CACHE.open(encoding="latin-1", newline="") as fh:
        pts = {}
        for r in csv.DictReader(fh, delimiter=";"):
            if r["Tipo Titulo"].startswith("Tesouro IPCA+") and r["Data Vencimento"].endswith(vencimento_prefixo):
                d = _iso(r["Data Base"])
                pts.setdefault(d, float(r["Taxa Compra Manha"].replace(",", ".")))
    return sorted([[d, t] for d, t in pts.items()])
