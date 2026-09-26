"""Curvas de vencimento constante a partir do Tesouro Direto (CSV do Tesouro Transparente, cache diário).

Prefixada: Tesouro Prefixado (LTN) e Prefixado com Juros Semestrais (NTN-F). Real: Tesouro IPCA+ (NTN-B).
Para cada dia, interpola linearmente a taxa nos prazos-alvo (anos) entre os títulos que cercam o prazo.
Sem extrapolação: prazo fora do intervalo dos títulos disponíveis no dia fica nulo. Série desde 2004.
Saída: data/curva_tesouro.json = {'prazos': [...], 'pre': [[data, t1, t2, ...]], 'real': [[data, ...]]}
"""
from __future__ import annotations

import csv
import json
from datetime import date

import config
from fontes import tesouro

PRAZOS_PRE = [1, 2, 3, 5, 7, 10]
PRAZOS_REAL = [2, 5, 10, 20, 30]


def _iso(s: str) -> str:
    return f"{s[6:]}-{s[3:5]}-{s[:2]}"


EXTRAP_MAX = 1.5   # anos: até onde o vértice pode passar do título mais longo (o Tesouro só lança NTN-F nova a cada 2 anos,
                   # então o prefixado de 10 anos fica 9,x anos durante ~11 meses; sem isso a série de 10 anos abria um buraco)


def _interp(pontos: list[tuple[float, float]], alvo: float) -> float | None:
    """Interpolação linear entre vértices; além do último título, extrapolação linear pelos dois mais longos até EXTRAP_MAX anos."""
    pts = sorted(pontos)
    if not pts or alvo < pts[0][0] - 0.15 or alvo > pts[-1][0] + EXTRAP_MAX:
        return None
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= alvo <= x1:
            return y0 if x1 == x0 else y0 + (y1 - y0) * (alvo - x0) / (x1 - x0)
    if alvo < pts[0][0]:
        return pts[0][1]
    if len(pts) >= 2 and pts[-1][0] > pts[-2][0]:
        (x0, y0), (x1, y1) = pts[-2], pts[-1]
        return y1 + (y1 - y0) * (alvo - x1) / (x1 - x0)
    return pts[-1][1]


def constroi() -> dict:
    if not tesouro.CACHE.exists():
        tesouro._baixa()
    por_dia_pre: dict[str, list] = {}
    por_dia_real: dict[str, list] = {}
    with tesouro.CACHE.open(encoding="latin-1", newline="") as fh:
        for r in csv.DictReader(fh, delimiter=";"):
            tipo = r["Tipo Titulo"]
            if tipo.startswith("Tesouro Prefixado"):
                alvo = por_dia_pre
            elif tipo.startswith("Tesouro IPCA+"):
                alvo = por_dia_real
            else:
                continue
            d, v = _iso(r["Data Base"]), _iso(r["Data Vencimento"])
            try:
                taxa = float(r["Taxa Compra Manha"].replace(",", "."))
            except ValueError:
                continue
            if taxa <= 0:
                continue
            anos = (date.fromisoformat(v) - date.fromisoformat(d)).days / 365.25
            alvo.setdefault(d, []).append((anos, taxa))
    pre = [[d] + [_interp(pts, p) for p in PRAZOS_PRE] for d, pts in sorted(por_dia_pre.items())]
    real = [[d] + [_interp(pts, p) for p in PRAZOS_REAL] for d, pts in sorted(por_dia_real.items())]
    out = {"prazos_pre": PRAZOS_PRE, "prazos_real": PRAZOS_REAL, "pre": pre, "real": real}
    (config.DATA / "curva_tesouro.json").write_text(json.dumps(out), encoding="utf-8")
    return out
