"""Perfil intradiário de volume da B3: que fração do volume do dia já foi negociada até cada hora.

O vigia projeta o volume do dia inteiro dividindo o acumulado até agora pela fração esperada; a fração LINEAR (tempo
decorrido ÷ duração do pregão) supõe volume uniforme, e o pregão não é uniforme: a última hora com o leilão de fechamento
concentra 27–29% do dia, e às 15h a fração real é ~58% contra 71% linear (7h) — a projeção linear subestimava o volume
do dia em ~17% a tarde inteira. O total diário não muda com a sessão de 8h (razão 0.99 na troca de 09/03/2026 com os
mesmos papéis), só se espalha: o volume por hora cai ~13%.

Perfil = mediana, entre dias × papéis (30 maiores pesos do Ibovespa), da fração acumulada em nós de 30 min, um por
regime (7h = fechamento 17h, 8h = 18h; config.fecha_pregao), medido nas barras de 1 min do MT5 (data/mt5/m1, janela
"mt5" do coletar). Interpolação linear entre nós. Sem arquivo (ou regime sem amostra), cai na fração linear.

    python -m fontes.perfil_volume        # recalcula data/perfil_volume.json a partir do cache M1
"""
from __future__ import annotations

import csv
import glob
import gzip
import io
import json
import statistics as st
import sys
from collections import defaultdict
from datetime import date, datetime, time

import config

ARQ = config.DATA / "perfil_volume.json"
NOS = [h * 60 + m for h in range(10, 19) for m in (0, 30)]        # minutos do dia, 10:00 … 18:30
MIN_DIAS = 15                                                       # amostra mínima (dias) por regime


def _regime(d: date) -> str:
    return "8h" if config.fecha_pregao(d).hour == 18 else "7h"


def calcula(tickers: list[str], pasta=None, anos: tuple[int, ...] | None = None) -> str:
    """Regrava data/perfil_volume.json: {"7h": {"600": 0.0, "630": 0.049, ...}, "8h": {...}, "amostra": {...}}."""
    pasta = (pasta or config.DATA / "mt5") / "m1"
    anos = anos or (date.today().year - 1, date.today().year)
    acum = {"7h": defaultdict(list), "8h": defaultdict(list)}
    dias = {"7h": set(), "8h": set()}
    for tk in tickers:
        for ano in anos:
            for p in glob.glob(str(pasta / f"{tk}_{ano}.csv.gz")):
                por_dia = defaultdict(list)
                with gzip.open(p) as fh:
                    for r in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8")):
                        h = r["hora"]
                        por_dia[h[:10]].append((int(h[11:13]) * 60 + int(h[14:16]), int(r["quantidade"])))
                for d, L in por_dia.items():
                    tot = sum(v for _, v in L)
                    if tot <= 0 or len(L) < 200 or d == date.today().isoformat():      # dia incompleto ou ilíquido fora
                        continue
                    reg = _regime(date.fromisoformat(d))
                    dias[reg].add(d)
                    for n in NOS:
                        acum[reg][n].append(sum(v for m, v in L if m < n) / tot)
    out = {"gerado": datetime.now().strftime("%Y-%m-%d %H:%M"), "papeis": len(tickers), "amostra": {}}
    for reg in ("7h", "8h"):
        if len(dias[reg]) >= MIN_DIAS:
            fecha = 17 * 60 if reg == "7h" else 18 * 60
            perfil = {str(n): round(min(1.0, st.median(acum[reg][n])), 4) for n in NOS if n <= fecha}
            perfil[str(fecha)] = 1.0
            out[reg] = perfil
        out["amostra"][reg] = {"dias": len(dias[reg]), "dias_papeis": len(acum[reg][NOS[1]])}
    ARQ.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return "perfil de volume: " + ", ".join(f"{r} {out['amostra'][r]['dias']} dias" + ("" if r in out else " (insuficiente)") for r in ("7h", "8h"))


def carrega() -> dict:
    try:
        return json.loads(ARQ.read_text(encoding="utf-8"))
    except Exception:
        return {}


def fracao(agora: datetime, abre: time, fecha: time, perfil: dict | None = None) -> float:
    """Fração esperada do volume do dia já negociada em `agora`: perfil empírico do regime (interpolado) ou, sem perfil,
    a fração linear do tempo. Sempre em [0, 1]."""
    m = agora.hour * 60 + agora.minute
    a, f = abre.hour * 60 + abre.minute, fecha.hour * 60 + fecha.minute
    if m <= a:
        return 0.0
    if m >= f:
        return 1.0
    P = (perfil if perfil is not None else carrega()).get(_regime(agora.date()))
    if not P:
        return (m - a) / (f - a)
    nos = sorted((int(k), v) for k, v in P.items())
    for (n0, v0), (n1, v1) in zip(nos, nos[1:]):
        if n0 <= m <= n1:
            return v0 + (v1 - v0) * (m - n0) / (n1 - n0) if n1 > n0 else v1
    return (m - a) / (f - a)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    C = json.loads((config.DATA / "ibov_comp.json").read_text(encoding="utf-8"))
    top = [i["cod"] for i in sorted(C["itens"], key=lambda i: -(i.get("peso") or 0))][:30]
    print(calcula(top))
    P = carrega()
    for hhmm in ("11:00", "13:00", "15:00", "16:30", "17:30"):
        t = datetime.combine(date.today(), time.fromisoformat(hhmm))
        print(hhmm, f"perfil {fracao(t, config.ABRE_PREGAO, config.fecha_pregao(), P):.3f}  linear {fracao(t, config.ABRE_PREGAO, config.fecha_pregao(), {}):.3f}")
