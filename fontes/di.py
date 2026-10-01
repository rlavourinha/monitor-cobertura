"""Curva DI (futuros DI1 da B3) pelo MT5 da Genial.

Os contratos DI1 negociam a taxa (% a.a., base 252) até o 1º dia útil do mês de vencimento (letra do mês + ano). A
liquidez concentra-se nos vencimentos de janeiro (F) e nos primeiros meses; o resto tem preço parado/largo. Por isso:
  - a curva do dia usa só contratos com negócios no dia >= MIN_NEGOCIOS (corte de volume) e tick de hoje;
  - o que se acompanha no tempo são VÉRTICES de prazo constante (0,5; 1; 2; 3; 5; 7; 10 anos), interpolados entre os
    contratos líquidos por flat-forward (log-linear no fator (1+i)^(du/252)); prazo além do último contrato líquido é
    extrapolado plano e marcado;
  - histórico: fechamentos diários (D1) de cada contrato em data/di_hist.json -> série diária de cada vértice.
Validação: vértices prefixados da ETTJ ANBIMA (data/ettj/{data}.json: [du, ipca, pré, ...]), coletados pela janela diária.

    python -m fontes.di            # fotografia agora (contratos líquidos + vértices) e comparação com a ANBIMA
"""
from __future__ import annotations

import json
import math
import re
import sys
from datetime import date, datetime, timedelta

import config

ARQ = config.DATA / "di_curva.json"        # fotografia mais recente (o vigia grava a cada passada no pregão)
HIST = config.DATA / "di_hist.json"        # {data: {contrato: fechamento}} — barras D1 do MT5
MESES = {"F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6, "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12}
ALVOS = [0.5, 1, 2, 3, 5, 7, 10]           # anos
MIN_NEGOCIOS = 300                          # corte de liquidez: negócios no dia
RE_COD = re.compile(r"^DI1([FGHJKMNQUVXZ])(\d\d)$")


# ----------------------------------------------------------------------------- dias úteis (B3)
def _pascoa(ano: int) -> date:
    a, b, c = ano % 19, ano // 100, ano % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = ((h + l - 7 * m + 114) % 31) + 1
    return date(ano, mes, dia)


def feriados(ano: int) -> set[date]:
    p = _pascoa(ano)
    fixos = {(1, 1), (4, 21), (5, 1), (9, 7), (10, 12), (11, 2), (11, 15), (12, 25)}
    if ano >= 2024:
        fixos.add((11, 20))
    return {date(ano, m, d) for m, d in fixos} | {p - timedelta(days=48), p - timedelta(days=47), p - timedelta(days=2), p + timedelta(days=60)}


def util(d: date) -> bool:
    return d.weekday() < 5 and d not in feriados(d.year)


_CUM: dict[date, int] = {}       # dias úteis acumulados de 2010-01-01 até a data (inclusive), calculado uma vez


def _cum(d: date) -> int:
    if not _CUM:
        n, x = 0, date(2010, 1, 1)
        fim = date(2050, 12, 31)
        while x <= fim:
            if util(x):
                n += 1
            _CUM[x] = n
            x += timedelta(days=1)
    return _CUM[d]


def du(a: date, b: date) -> int:
    """Dias úteis entre a (exclusive) e b (exclusive) — convenção dos contratos DI. O(1) com a tabela acumulada."""
    if b <= a:
        return 0
    return _cum(b) - _cum(a) - (1 if util(b) else 0)


def vencimento(cod: str) -> date | None:
    m = RE_COD.match(cod)
    if not m:
        return None
    d = date(2000 + int(m.group(2)), MESES[m.group(1)], 1)
    while not util(d):
        d += timedelta(days=1)
    return d


# ----------------------------------------------------------------------------- MT5
def contratos() -> list[dict]:
    """Todos os DI1 do terminal: código, vencimento, du, taxa (último; senão meio do bid/ask), negócios/volume do dia,
    hora do último tick. Ordenados por prazo."""
    from fontes import mt5
    if not mt5.disponivel():
        return []
    M = mt5._mt5
    out = []
    for s in M.symbols_get("DI1*") or []:
        if not RE_COD.match(s.name):
            continue
        venc = vencimento(s.name)
        if not venc or venc <= date.today():
            continue
        M.symbol_select(s.name, True)
        t, i = M.symbol_info_tick(s.name), M.symbol_info(s.name)
        if not t:
            continue
        taxa = float(t.last) if t.last else ((float(t.bid) + float(t.ask)) / 2 if (t.bid and t.ask) else None)
        out.append({"cod": s.name, "venc": venc.isoformat(), "du": du(date.today(), venc), "taxa": taxa,
                    "bid": float(t.bid) if t.bid else None, "ask": float(t.ask) if t.ask else None,
                    "negocios": int(i.session_deals) if i else 0, "volume": float(i.session_volume) if i else 0.0,
                    "hora": mt5._hora(t.time).strftime("%Y-%m-%d %H:%M") if t.time else None})
    out.sort(key=lambda c: c["du"])
    return out


# ----------------------------------------------------------------------------- interpolação
def _interp(pontos: list[tuple[int, float]], du_alvo: int) -> tuple[float, bool]:
    """(taxa % a.a., extrapolado) por flat-forward entre os pontos (du, taxa %) ordenados."""
    if du_alvo <= pontos[0][0]:
        return pontos[0][1], du_alvo < pontos[0][0]
    if du_alvo >= pontos[-1][0]:
        return pontos[-1][1], du_alvo > pontos[-1][0]
    for (d1, r1), (d2, r2) in zip(pontos, pontos[1:]):
        if d1 <= du_alvo <= d2:
            if d1 == du_alvo:
                return r1, False
            lf1, lf2 = math.log((1 + r1 / 100) ** (d1 / 252)), math.log((1 + r2 / 100) ** (d2 / 252))
            lf = lf1 + (lf2 - lf1) * (du_alvo - d1) / (d2 - d1)
            return (math.exp(lf * 252 / du_alvo) - 1) * 100, False
    return pontos[-1][1], True


def vertices(cs: list[dict], alvos=ALVOS, min_negocios: int = MIN_NEGOCIOS, hoje: str | None = None) -> dict:
    """{'1a': {'taxa', 'extrapolado', 'entre': [cod, cod]}, ...} a partir dos contratos líquidos (negócios do dia e tick
    de hoje). Com menos de 2 líquidos, usa todos os que têm taxa (e avisa em 'base')."""
    hoje = hoje or date.today().isoformat()
    # líquido = tick de hoje e (negócios no dia >= corte OU contrato de janeiro com bid/ask de 1 bp): o feed da Genial
    # zera os negócios do DI1F28 mesmo com preço andando, e sem ele o vértice de 1 ano sai torto. Os meses fora de
    # janeiro (J, N, V) e os janeiros longos com spread largo ficam de fora — cotação parada/indicativa.
    def liquido(c):
        if not c.get("taxa") or (c.get("hora") or "")[:10] != hoje:
            return False
        apertado = c.get("bid") and c.get("ask") and 0 < (c["ask"] - c["bid"]) <= 0.0101 and c["cod"][3] == "F"
        return c.get("negocios", 0) >= min_negocios or bool(apertado)
    liq = [c for c in cs if liquido(c)]
    base = "liquidos"
    if len(liq) < 2:
        liq, base = [c for c in cs if c.get("taxa")], "todos"
    if len(liq) < 2:
        return {}
    pts = [(c["du"], c["taxa"]) for c in liq]
    out = {"_base": base, "_contratos": [c["cod"] for c in liq]}
    for a in alvos:
        du_a = round(a * 252)
        taxa, ext = _interp(pts, du_a)
        entre = [c["cod"] for c in liq if c["du"] <= du_a][-1:] + [c["cod"] for c in liq if c["du"] >= du_a][:1]
        out[_rot(a)] = {"taxa": round(taxa, 4), "du": du_a, "extrapolado": ext, "entre": entre}
    return out


def _rot(anos: float) -> str:
    return f"{anos:g}a"


# ----------------------------------------------------------------------------- fotografia e histórico
def snapshot(gravar: bool = True) -> dict:
    cs = contratos()
    v = vertices(cs)
    snap = {"hora": datetime.now().strftime("%Y-%m-%d %H:%M"), "min_negocios": MIN_NEGOCIOS, "contratos": cs, "vertices": v}
    if gravar and cs:
        config.DATA.mkdir(parents=True, exist_ok=True)
        ARQ.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    return snap


def atualiza_historico(desde: date | None = None, so_janeiro: bool = True) -> int:
    """Fechamentos diários (D1) dos contratos no terminal -> data/di_hist.json. Sem `desde`: 15 dias (emenda) ou desde
    2016 se o arquivo não existe. Devolve nº de datas no arquivo."""
    from fontes import mt5
    if not mt5.disponivel():
        return 0
    H = json.loads(HIST.read_text(encoding="utf-8")) if HIST.exists() else {}
    if desde is None:
        desde = date.today() - timedelta(days=15) if H else date(2016, 1, 1)
    for c in contratos() if not so_janeiro else [c for c in contratos() if c["cod"][3] == "F"]:
        for r in mt5.historico_diario(c["cod"], datetime.combine(desde, datetime.min.time())):
            d, f = str(r.get("data", ""))[:10], r.get("fechamento")
            if d and f:
                H.setdefault(d, {})[c["cod"]] = float(f)
    HIST.write_text(json.dumps(H, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return len(H)


def serie_vertice(anos: float) -> list[list]:
    """[[data, taxa % a.a.], ...] do vértice de prazo constante, dia a dia, a partir de data/di_hist.json
    (sem corte de liquidez: o D1 só existe em dia com negócio)."""
    if not HIST.exists():
        return []
    H = json.loads(HIST.read_text(encoding="utf-8"))
    out = []
    vencs = {}
    for d in sorted(H):
        try:
            dd = date.fromisoformat(d)
        except ValueError:
            continue
        pts = []
        for cod, taxa in H[d].items():
            v = vencs.get(cod) or vencs.setdefault(cod, vencimento(cod))
            if v and v > dd and taxa:
                pts.append((du(dd, v), float(taxa)))
        pts.sort()
        du_a = round(anos * 252)
        if len(pts) >= 2 and pts[0][0] <= du_a * 1.25 and pts[-1][0] >= du_a * 0.8:   # só onde a curva alcança o prazo (±25%)
            taxa, _ = _interp(pts, du_a)
            out.append([d, round(taxa, 4)])
    return out


def vertices_em(d: str, alvos=ALVOS, contratos_base: list[str] | None = None) -> dict:
    """{'1a': taxa, ...} de uma data do histórico (fechamentos D1 em data/di_hist.json). Com `contratos_base`, usa só
    esses contratos (os líquidos da fotografia de hoje), para a curva de ontem ser comparável à de hoje."""
    if not HIST.exists():
        return {}
    H = json.loads(HIST.read_text(encoding="utf-8"))
    if d not in H:
        return {}
    dd = date.fromisoformat(d)
    pts = []
    for cod, taxa in H[d].items():
        if contratos_base and cod not in contratos_base:
            continue
        v = vencimento(cod)
        if v and v > dd and taxa:
            pts.append((du(dd, v), float(taxa)))
    if len(pts) < 2 and contratos_base:                       # ontem não tem os líquidos de hoje: usa todos
        return vertices_em(d, alvos, None)
    pts.sort()
    out = {}
    for a in alvos:
        du_a = round(a * 252)
        if len(pts) >= 2 and pts[0][0] <= du_a * 1.25 and pts[-1][0] >= du_a * 0.8:
            taxa, _ = _interp(pts, du_a)
            out[_rot(a)] = round(taxa, 4)
    return out


def vertices_anterior(hoje: str | None = None, contratos_base: list[str] | None = None) -> tuple[str | None, dict]:
    """(data, {'1a': taxa, ...}) do último pregão anterior a `hoje` com histórico; (None, {}) se não há."""
    hoje = hoje or date.today().isoformat()
    if not HIST.exists():
        return None, {}
    H = json.loads(HIST.read_text(encoding="utf-8"))
    ds = [d for d in H if d < hoje]
    if not ds:
        return None, {}
    d = max(ds)
    return d, vertices_em(d, contratos_base=contratos_base)


def anbima_pre(d: str | None = None) -> tuple[str | None, list[tuple[int, float]]]:
    """(data, [(du, taxa pré % a.a.), ...]) da última ETTJ ANBIMA em cache (ou da data pedida)."""
    pasta = config.DATA / "ettj"
    if not pasta.exists():
        return None, []
    arqs = sorted(pasta.glob("*.json"))
    if not arqs:
        return None, []
    p = pasta / f"{d}.json" if d else arqs[-1]
    if not p.exists():
        return None, []
    J = json.loads(p.read_text(encoding="utf-8"))
    return J.get("data"), [(int(v[0]), float(v[2])) for v in J.get("vertices", []) if len(v) > 2 and v[2] is not None]


if __name__ == "__main__":
    s = snapshot()
    print(f"{s['hora']}: {len(s['contratos'])} contratos; líquidos (>= {MIN_NEGOCIOS} negócios): {s['vertices'].get('_contratos')}")
    for c in s["contratos"]:
        print(f"  {c['cod']} venc {c['venc']} du {c['du']:5d} taxa {c['taxa']} negócios {c['negocios']:7d} hora {c['hora']}")
    da, pre = anbima_pre()
    apts = sorted(pre)
    print(f"vértices (MT5 agora) vs ANBIMA {da}:")
    for a in ALVOS:
        v = s["vertices"].get(_rot(a), {})
        ref = _interp(apts, round(a * 252))[0] if len(apts) >= 2 else None
        print(f"  {_rot(a):>4} {v.get('taxa')}{'*' if v.get('extrapolado') else ' '} | ANBIMA {ref and round(ref, 3)} | entre {v.get('entre')}")
    from fontes import mt5
    mt5.desligar()
