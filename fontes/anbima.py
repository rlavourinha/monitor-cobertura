"""ANBIMA: estrutura a termo (ETTJ) prefixada e IPCA por vértice, com inflação implícita e parâmetros de Svensson.

POST https://www.anbima.com.br/informacoes/est-termo/CZ-down.asp  (Idioma=PT, Dt_Ref=dd/mm/aaaa, saida=csv)
Só serve os últimos ~5 pregões; por isso cada dia coletado vira fotografia em data/ettj/{data}.json.
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from datetime import date, timedelta

import config

URL = "https://www.anbima.com.br/informacoes/est-termo/CZ-down.asp"
CACHE = config.DATA / "ettj"


def _br(v: str) -> float | None:
    try:
        return float(v.replace(".", "").replace(",", "."))
    except (AttributeError, ValueError):
        return None


def ettj(d: date) -> dict | None:
    """{'data', 'svensson': {'PREF': [b1,b2,b3,b4,l1,l2], 'IPCA': [...]}, 'vertices': [[du, ipca, pre, implicita], ...]} ou None."""
    dados = urllib.parse.urlencode({"Idioma": "PT", "Dt_Ref": d.strftime("%d/%m/%Y"), "saida": "csv"}).encode()
    req = urllib.request.Request(URL, data=dados, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            txt = r.read().decode("latin-1")
    except Exception as e:
        print(f"  ANBIMA ETTJ {d}: {e}", file=sys.stderr)
        return None
    if not txt.strip():
        return None
    sv, vert = {}, []
    for ln in txt.splitlines():
        p = [x.strip() for x in ln.split(";")]
        if len(p) == 7 and p[0] in ("PREFIXADOS", "IPCA"):
            sv[p[0]] = [float(x.replace(",", ".")) for x in p[1:]]
        elif len(p) == 4 and p[0].replace(".", "").isdigit():
            du = int(p[0].replace(".", ""))
            vert.append([du, _br(p[1]), _br(p[2]), _br(p[3])])
    if not vert:
        return None
    return {"data": d.isoformat(), "svensson": sv, "vertices": vert}


def atualiza(dias: int = 7) -> list[str]:
    """Fotografa os pregões dos últimos `dias` que ainda não estão no cache. Devolve as datas gravadas."""
    CACHE.mkdir(parents=True, exist_ok=True)
    hoje = date.today()
    novas = []
    for i in range(dias, -1, -1):
        d = hoje - timedelta(days=i)
        if d.weekday() >= 5 or (CACHE / f"{d.isoformat()}.json").exists():
            continue
        r = ettj(d)
        if r:
            (CACHE / f"{d.isoformat()}.json").write_text(json.dumps(r), encoding="utf-8")
            novas.append(d.isoformat())
    return novas


def fotos() -> dict[str, dict]:
    if not CACHE.exists():
        return {}
    return {f.stem: json.loads(f.read_text(encoding="utf-8")) for f in sorted(CACHE.glob("*.json"))}
