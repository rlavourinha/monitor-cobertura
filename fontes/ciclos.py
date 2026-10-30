"""Séries dos slides de ciclos do PIB que não estão no SGS: termos de troca (FUNCEX, via Ipeadata) e desemprego (IBGE,
PNAD Contínua, SIDRA 6381: taxa de desocupação do trimestre móvel). Saída: data/ciclos.json.

Recessões: datação oficial do CODACE/FGV (pico -> vale, trimestres). O período sombreado vai do trimestre seguinte ao
pico até o fim do trimestre do vale.
"""
from __future__ import annotations

import json
import time
from datetime import date

import config

ARQ = config.DATA / "ciclos.json"
IPEA = "http://www.ipeadata.gov.br/api/odata4/ValoresSerie(SERCODIGO='{cod}')"
PNAD = "https://apisidra.ibge.gov.br/values/t/6381/n1/all/v/4099/p/all"
# CODACE/FGV: (pico, vale) em trimestres "AAAAT"
CODACE = [("19974", "19991"), ("20011", "20014"), ("20024", "20032"), ("20083", "20091"), ("20141", "20164"), ("20194", "20202")]


def _get(url: str):
    import requests
    erro = None
    for tent in range(4):
        try:
            r = requests.get(url, timeout=120, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200:
                return r.json()
            erro = RuntimeError(f"{r.status_code}: {r.text[:120]}")
        except Exception as e:
            erro = e
        time.sleep(3 + 5 * tent)
    raise erro


def recessoes() -> list[list[str]]:
    """[[início, fim, rótulo], ...]: do 1º dia do trimestre seguinte ao pico ao último dia do trimestre do vale."""
    out = []
    for pico, vale in CODACE:
        a, t = int(pico[:4]), int(pico[4])
        a, t = (a + 1, 1) if t == 4 else (a, t + 1)
        av, tv = int(vale[:4]), int(vale[4])
        fim = date(av, tv * 3, 30 if tv in (2, 3) else 31).isoformat()
        rot = f"{a}" if a == av else f"{a}–{str(av)[2:]}"
        out.append([date(a, t * 3 - 2, 1).isoformat(), fim, rot])
    return out


def atualiza() -> dict:
    antigo = ler()
    D = {"gerado_em": date.today().isoformat()}
    try:
        tt = _get(IPEA.format(cod="FUNCEX12_TTR12"))["value"]
        D["termos_troca"] = [[x["VALDATA"][:10], round(x["VALVALOR"], 2)] for x in tt if x.get("VALVALOR") is not None]
    except Exception as e:
        print(f"  termos de troca: {e}")
    try:
        pn = _get(PNAD)[1:]
        D["desemprego"] = [[f'{r["D3C"][:4]}-{r["D3C"][4:]}-01', float(r["V"])] for r in pn if r["V"] not in ("...", "-", "")]
    except Exception as e:
        print(f"  desemprego: {e}")
    for k in ("termos_troca", "desemprego"):          # sem regredir: fonte fora do ar mantém a série anterior
        if len(D.get(k) or []) < len(antigo.get(k) or []):
            D[k] = antigo[k]
    ARQ.write_text(json.dumps(D, ensure_ascii=False), encoding="utf-8")
    return D


def ler() -> dict:
    return json.loads(ARQ.read_text(encoding="utf-8")) if ARQ.exists() else {}
