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


def _fluxos(base: date, venc: date) -> list[tuple[float, float]]:
    """[(anos até o pagamento, fluxo por 100 de VNA)]: cupons semestrais de 6% a.a. (2,9563%) restantes e o principal."""
    cupom = 100 * (1.06 ** 0.5 - 1)
    out, a, m = [], venc.year, venc.month
    while date(a, m, 15) > base:
        dt = date(a, m, 15)
        out.append(((dt - base).days / 365.25, cupom + (100 if dt == venc else 0)))
        m -= 6
        if m <= 0:
            m, a = m + 12, a - 1
    return out


def ntnb_duration(alvo: float = 10.0, desde: str = "2006-01-01") -> list[list]:
    """Juro real com duration constante: em cada dia, a taxa das NTN-B com juros semestrais interpolada linearmente no ponto
    de duration de Macaulay = alvo, entre o título logo abaixo e o logo acima. [[data, taxa%, "venc/venc"], ...].
    Começa em 2006: em 2005 a taxa do Tesouro Direto quase não mudava (171 de 251 dias iguais ao anterior)."""
    if not CACHE.exists():
        return []
    por_dia: dict[str, dict[date, float]] = {}
    with CACHE.open(encoding="latin-1", newline="") as fh:
        for r in csv.DictReader(fh, delimiter=";"):
            if r["Tipo Titulo"] != "Tesouro IPCA+ com Juros Semestrais":
                continue
            d = _iso(r["Data Base"])
            if d < desde:
                continue
            taxa = float(r["Taxa Compra Manha"].replace(",", ".") or 0) or float(r["Taxa Venda Manha"].replace(",", ".") or 0)
            if taxa:
                por_dia.setdefault(d, {})[date.fromisoformat(_iso(r["Data Vencimento"]))] = taxa
    out = []
    for d in sorted(por_dia):
        base = date.fromisoformat(d)
        tit = []
        for venc, taxa in por_dia[d].items():
            if venc <= base:
                continue
            y = 1 + taxa / 100
            fl = [(t, c / y ** t) for t, c in _fluxos(base, venc)]
            tit.append((sum(t * v for t, v in fl) / sum(v for _, v in fl), taxa, venc.year))
        tit.sort()
        baixo = [x for x in tit if x[0] <= alvo]; alto = [x for x in tit if x[0] > alvo]
        if baixo and alto:
            (d0, t0, v0), (d1, t1, v1) = baixo[-1], alto[0]
            out.append([d, round(t0 + (t1 - t0) * (alvo - d0) / (d1 - d0), 3), f"{v0}/{v1}"])
    return out
