"""Curva de juros americana (Treasuries nominais, par yield) — fonte primária: U.S. Department of the Treasury,
"Daily Treasury Par Yield Curve Rates", um CSV público por ano (desde 1990), sem chave.

    data/ust_curva.json = {"prazos": ["1 Mo", ...], "serie": {"AAAA-MM-DD": [taxa ou None por prazo]}, "atualizado": ...}

A carga inicial baixa 1990→hoje; depois só o ano corrente (e o anterior em janeiro, para fechar dezembro).
"""
from __future__ import annotations

import csv
import io
import json
import sys
from datetime import date, datetime

import config

ARQ = config.DATA / "ust_curva.json"
URL = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{ano}/all"
       "?type=daily_treasury_yield_curve&field_tdr_date_value={ano}&page&_format=csv")
# prazos exibidos (o CSV tem também 1,5/2/4 meses desde 2022-25; ficam fora para a curva ter os mesmos vértices em toda a série)
PRAZOS = ["1 Mo", "3 Mo", "6 Mo", "1 Yr", "2 Yr", "3 Yr", "5 Yr", "7 Yr", "10 Yr", "20 Yr", "30 Yr"]
ROTULOS = ["1m", "3m", "6m", "1a", "2a", "3a", "5a", "7a", "10a", "20a", "30a"]


def _ano(ano: int) -> dict[str, list]:
    import requests
    r = requests.get(URL.format(ano=ano), headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}, timeout=90)
    r.raise_for_status()
    out = {}
    for row in csv.DictReader(io.StringIO(r.text)):
        try:
            d = datetime.strptime(row["Date"], "%m/%d/%Y").date().isoformat()
        except (KeyError, ValueError):
            continue
        vals = []
        for p in PRAZOS:
            v = (row.get(p) or "").strip()
            try:
                vals.append(float(v))
            except ValueError:
                vals.append(None)
        if any(v is not None for v in vals):
            out[d] = vals
    return out


def ler() -> dict:
    return json.loads(ARQ.read_text(encoding="utf-8")) if ARQ.exists() else {"prazos": PRAZOS, "serie": {}}


def atualiza(desde: int = 1990) -> dict:
    """Baixa os anos que faltam (carga inicial) ou só o corrente; nunca apaga datas já guardadas."""
    D = ler()
    S = D.get("serie", {})
    hoje = date.today()
    anos = range(desde, hoje.year + 1) if not S else sorted({hoje.year} | ({hoje.year - 1} if hoje.month == 1 else set()))
    for a in anos:
        try:
            S.update(_ano(a))
        except Exception as e:
            print(f"  UST {a}: {e}", file=sys.stderr)
    D = {"prazos": PRAZOS, "rotulos": ROTULOS, "serie": dict(sorted(S.items())), "atualizado": datetime.now().isoformat(timespec="minutes")}
    ARQ.write_text(json.dumps(D, separators=(",", ":")), encoding="utf-8")
    return D


if __name__ == "__main__":
    D = atualiza()
    ds = list(D["serie"])
    print(f"{len(ds)} dias, {ds[0]} a {ds[-1]}: {dict(zip(ROTULOS, D['serie'][ds[-1]]))}")
