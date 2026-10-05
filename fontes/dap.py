"""Curva de juro real (futuros de Cupom de IPCA, DAP, da B3) pelo MT5 da Genial — o par do fontes/di.py.

Os DAP negociam a taxa real (% a.a., base 252) até o dia 15 do mês de vencimento (letra do mês + ano); os vencimentos
espelham as NTN-Bs (K = maio, Q = agosto). A liquidez é muito menor que a do DI1: poucas dezenas de negócios por dia
nos vencimentos de referência (DAPQ28, DAPK35, DAPK45) e nada nos demais. Por isso:
  - o preço de cada contrato é o último negócio só quando o tick é de hoje e cabe no bid/ask; senão é o meio do
    bid/ask (o "último" do MT5 fica velho por dias nos vencimentos parados);
  - a curva do dia usa contratos com negócios >= MIN_NEGOCIOS ou bid/ask apertado;
  - o que se acompanha no tempo são VÉRTICES de prazo constante (2, 3, 5, 7, 10, 15, 20, 30 anos), interpolados por
    flat-forward entre os contratos líquidos (mesma rotina do DI);
  - histórico: fechamentos diários (D1) de cada contrato em data/dap_hist.json -> série diária de cada vértice.
Validação: vértices IPCA da ETTJ ANBIMA (data/ettj/{data}.json: [du, ipca, pré, implícita]).

    python -m fontes.dap           # fotografia agora (contratos + vértices) e comparação com a ANBIMA
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime, timedelta

import config
from fontes.di import MESES, _interp, du, util

ARQ = config.DATA / "dap_curva.json"       # fotografia mais recente (o vigia grava a cada passada no pregão)
HIST = config.DATA / "dap_hist.json"       # {data: {contrato: fechamento}} — barras D1 do MT5
ALVOS = [2, 3, 5, 7, 10, 15, 20, 30]       # anos
MIN_NEGOCIOS = 5                            # corte de liquidez: negócios no dia (o DAP negocia dezenas, não milhares)
SPREAD_OK = 0.15                            # bid/ask (p.p.) que vale como cotação viva mesmo sem negócio
RE_COD = re.compile(r"^DAP([FGHJKMNQUVXZ])(\d\d)$")


def vencimento(cod: str) -> date | None:
    """Dia 15 do mês de vencimento, ou o dia útil seguinte."""
    m = RE_COD.match(cod)
    if not m:
        return None
    d = date(2000 + int(m.group(2)), MESES[m.group(1)], 15)
    while not util(d):
        d += timedelta(days=1)
    return d


# ----------------------------------------------------------------------------- MT5
def _preco(t, hoje: str):
    """(taxa, origem) a partir do tick: último negócio se for de hoje e couber no bid/ask; senão meio do bid/ask."""
    from fontes import mt5
    last = float(t.last) if t.last else None
    bid = float(t.bid) if t.bid else None
    ask = float(t.ask) if t.ask else None
    mid = (bid + ask) / 2 if (bid and ask and ask >= bid) else None
    tick_hoje = bool(t.time) and mt5._hora(t.time).strftime("%Y-%m-%d") == hoje
    if mid is not None and (ask - bid) <= 0.40:
        if last and tick_hoje and bid - 0.02 <= last <= ask + 0.02:
            return last, "last"
        return round(mid, 4), "mid"
    return last, "last"


def contratos() -> list[dict]:
    """Todos os DAP do terminal com vencimento a mais de 3 meses: código, vencimento, du, taxa, bid/ask, negócios,
    volume, hora do último tick e origem do preço. Ordenados por prazo."""
    from fontes import mt5
    if not mt5.disponivel():
        return []
    M = mt5._mt5
    hoje = date.today()
    out = []
    for s in M.symbols_get("DAP*") or []:
        if not RE_COD.match(s.name):
            continue
        venc = vencimento(s.name)
        if not venc or venc <= hoje + timedelta(days=90):
            continue
        M.symbol_select(s.name, True)
        t, i = M.symbol_info_tick(s.name), M.symbol_info(s.name)
        if not t:
            continue
        taxa, origem = _preco(t, hoje.isoformat())
        out.append({"cod": s.name, "venc": venc.isoformat(), "du": du(hoje, venc), "taxa": taxa, "origem": origem,
                    "bid": float(t.bid) if t.bid else None, "ask": float(t.ask) if t.ask else None,
                    "negocios": int(i.session_deals) if i else 0, "volume": float(i.session_volume) if i else 0.0,
                    "hora": mt5._hora(t.time).strftime("%Y-%m-%d %H:%M") if t.time else None})
    out.sort(key=lambda c: c["du"])
    return out


# ----------------------------------------------------------------------------- vértices
def _rot(anos: float) -> str:
    return f"{anos:g}a"


def vertices(cs: list[dict], alvos=ALVOS, min_negocios: int = MIN_NEGOCIOS, hoje: str | None = None) -> dict:
    """{'5a': {'taxa', 'extrapolado', 'entre': [cod, cod]}, ...} a partir dos contratos líquidos."""
    hoje = hoje or date.today().isoformat()

    def liquido(c):
        if not c.get("taxa") or c["taxa"] <= 0:
            return False
        spread = (c["ask"] - c["bid"]) if (c.get("bid") and c.get("ask")) else None
        apertado = spread is not None and 0 <= spread <= SPREAD_OK
        # ponta longa (2045-2055): um ou dois negócios por dia com bid/ask de ~0,25 p.p.; entra pelo meio do spread
        longo_vivo = spread is not None and 0 <= spread <= 2 * SPREAD_OK and c.get("negocios", 0) >= 1
        return c.get("negocios", 0) >= min_negocios or apertado or longo_vivo
    liq = [c for c in cs if liquido(c)]
    base = "liquidos"
    if len(liq) < 2:
        liq, base = [c for c in cs if c.get("taxa") and c["taxa"] > 0], "todos"
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


# ----------------------------------------------------------------------------- fotografia e histórico
def snapshot(gravar: bool = True) -> dict:
    cs = contratos()
    v = vertices(cs)
    snap = {"hora": datetime.now().strftime("%Y-%m-%d %H:%M"), "min_negocios": MIN_NEGOCIOS, "contratos": cs, "vertices": v}
    if gravar and cs:
        config.DATA.mkdir(parents=True, exist_ok=True)
        ARQ.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    return snap


def atualiza_historico(desde: date | None = None) -> int:
    """Fechamentos diários (D1) de todos os DAP do terminal -> data/dap_hist.json. Sem `desde`: 15 dias (emenda) ou
    desde 2016 se o arquivo não existe. Devolve nº de datas no arquivo."""
    from fontes import mt5
    if not mt5.disponivel():
        return 0
    H = json.loads(HIST.read_text(encoding="utf-8")) if HIST.exists() else {}
    if desde is None:
        desde = date.today() - timedelta(days=15) if H else date(2016, 1, 1)
    M = mt5._mt5
    for s in M.symbols_get("DAP*") or []:
        if not RE_COD.match(s.name):
            continue
        for r in mt5.historico_diario(s.name, datetime.combine(desde, datetime.min.time())):
            d, f = str(r.get("data", ""))[:10], r.get("fechamento")
            if d and f and float(f) > 0:
                H.setdefault(d, {})[s.name] = float(f)
    HIST.write_text(json.dumps(H, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return len(H)


def _pontos_em(H: dict, d: str, vencs: dict, contratos_base: list[str] | None = None) -> list[tuple[int, float]]:
    dd = date.fromisoformat(d)
    pts = []
    for cod, taxa in H[d].items():
        if contratos_base and cod not in contratos_base:
            continue
        v = vencs.get(cod) or vencs.setdefault(cod, vencimento(cod))
        if v and v > dd + timedelta(days=90) and taxa and taxa > 0:
            pts.append((du(dd, v), float(taxa)))
    pts.sort()
    return pts


def serie_vertice(anos: float) -> list[list]:
    """[[data, taxa real % a.a.], ...] do vértice de prazo constante, dia a dia, a partir de data/dap_hist.json."""
    if not HIST.exists():
        return []
    H = json.loads(HIST.read_text(encoding="utf-8"))
    out, vencs = [], {}
    du_a = round(anos * 252)
    for d in sorted(H):
        try:
            pts = _pontos_em(H, d, vencs)
        except ValueError:
            continue
        if len(pts) >= 2 and pts[0][0] <= du_a * 1.25 and pts[-1][0] >= du_a * 0.8:   # só onde a curva alcança o prazo (±25%)
            taxa, _ = _interp(pts, du_a)
            out.append([d, round(taxa, 4)])
    return out


def vertices_em(d: str, alvos=ALVOS, contratos_base: list[str] | None = None) -> dict:
    """{'5a': taxa, ...} de uma data do histórico; com `contratos_base`, só esses contratos (os líquidos de hoje)."""
    if not HIST.exists():
        return {}
    H = json.loads(HIST.read_text(encoding="utf-8"))
    if d not in H:
        return {}
    pts = _pontos_em(H, d, {}, contratos_base)
    if len(pts) < 2 and contratos_base:
        return vertices_em(d, alvos, None)
    out = {}
    for a in alvos:
        du_a = round(a * 252)
        if len(pts) >= 2 and pts[0][0] <= du_a * 1.25 and pts[-1][0] >= du_a * 0.8:
            taxa, _ = _interp(pts, du_a)
            out[_rot(a)] = round(taxa, 4)
    return out


def vertices_anterior(hoje: str | None = None, contratos_base: list[str] | None = None) -> tuple[str | None, dict]:
    """(data, {'5a': taxa, ...}) do último pregão anterior a `hoje` com histórico; (None, {}) se não há."""
    hoje = hoje or date.today().isoformat()
    if not HIST.exists():
        return None, {}
    H = json.loads(HIST.read_text(encoding="utf-8"))
    ds = [d for d in H if d < hoje]
    if not ds:
        return None, {}
    d = max(ds)
    return d, vertices_em(d, contratos_base=contratos_base)


def anbima_ipca(d: str | None = None) -> tuple[str | None, list[tuple[int, float]]]:
    """(data, [(du, taxa real IPCA % a.a.), ...]) da última ETTJ ANBIMA em cache (ou da data pedida)."""
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
    return J.get("data"), [(int(v[0]), float(v[1])) for v in J.get("vertices", []) if len(v) > 1 and v[1] is not None]


if __name__ == "__main__":
    s = snapshot()
    print(f"{s['hora']}: {len(s['contratos'])} contratos; líquidos (>= {MIN_NEGOCIOS} negócios ou spread <= {SPREAD_OK}): {s['vertices'].get('_contratos')}")
    for c in s["contratos"]:
        print(f"  {c['cod']} venc {c['venc']} du {c['du']:5d} taxa {c['taxa']} ({c['origem']}) bid/ask {c['bid']}/{c['ask']} negócios {c['negocios']:5d} hora {c['hora']}")
    da, ipca = anbima_ipca()
    apts = sorted(ipca)
    print(f"vértices (MT5 agora) vs ANBIMA IPCA {da}:")
    for a in ALVOS:
        v = s["vertices"].get(_rot(a), {})
        ref = _interp(apts, round(a * 252))[0] if len(apts) >= 2 else None
        print(f"  {_rot(a):>4} {v.get('taxa')}{'*' if v.get('extrapolado') else ' '} | ANBIMA {ref and round(ref, 3)} | entre {v.get('entre')}")
    from fontes import mt5
    mt5.desligar()
