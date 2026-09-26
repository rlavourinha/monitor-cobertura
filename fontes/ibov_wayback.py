"""Carteiras históricas do Ibovespa recuperadas do Wayback Machine (páginas antigas da B3, servidas até 2026):

  bvmf.bmfbovespa.com.br/indices/ResumoCarteiraTeorica.aspx?Indice=IBOV      -> "Carteira do Dia": quantidade teórica do dia
  bvmf.bmfbovespa.com.br/indices/ResumoCarteiraQuadrimestre.aspx?Indice=IBOV -> carteira válida para o quadrimestre (quantidades iniciais)

Cada captura vira uma fotografia em data/ibov_carteira/AAAA-MM-DD.json (q, peso, redutor), no mesmo formato da fotografia
diária que a coleta grava. A B3 não publica esse histórico; o Wayback tem ~80 capturas entre 2019 e 2026.
Uso: python -m fontes.ibov_wayback  (idempotente; não sobrescreve fotografias vindas da B3 ao vivo)
"""
from __future__ import annotations

import gzip
import html as html_
import json
import re
import sys
import urllib.request
from datetime import date

import config

PAGINAS = {
    "teorica": "bvmf.bmfbovespa.com.br/indices/ResumoCarteiraTeorica.aspx*",
    "quadrimestre": "bvmf.bmfbovespa.com.br/indices/ResumoCarteiraQuadrimestre.aspx*",
}
CDX = "http://web.archive.org/cdx/search/cdx?url={u}&output=json&fl=timestamp,original,statuscode&filter=statuscode:200&limit=3000"
MESES = {"jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6, "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12}


def _get(url: str, timeout: int = 90) -> bytes:
    raw = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=timeout).read()
    return gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw


def capturas(tipo: str) -> list[tuple[str, str]]:
    r = json.loads(_get(CDX.format(u=PAGINAS[tipo])))[1:]
    out = [(ts, u) for ts, u, st in r if "IBOV" in u.upper()]
    return sorted(out)


def _num(s: str) -> float:
    return float(s.replace(".", "").replace(",", "."))


def _primeiro_pregao(ano: int, mes: int, calend: list[str]) -> str:
    pref = f"{ano}-{mes:02d}-"
    d = next((x for x in calend if x.startswith(pref)), None)
    if d:
        return d
    dia = 1                                                   # sem calendário: 1º dia útil, pulando 1/jan e 1/mai
    while True:
        x = date(ano, mes, dia)
        if x.weekday() < 5 and not (mes in (1, 5) and dia == 1):
            return x.isoformat()
        dia += 1


def parse(h: str, tipo: str, calend: list[str]) -> dict | None:
    txt = html_.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h)))
    if tipo == "teorica":
        m = re.search(r"v[áa]lida para (\d{2})/(\d{2})/(\d{2})", txt)
        if not m:
            return None
        d = f"20{m.group(3)}-{m.group(2)}-{m.group(1)}"
    else:
        m = re.search(r"quadrimestre (\w{3})\.? a (\w{3})\.? (\d{4})", txt, re.I)
        if not m:
            return None
        d = _primeiro_pregao(int(m.group(3)), MESES[m.group(1).lower()[:3]], calend)
    red = None
    mr = re.search(r"Redutor.{0,80}?([\d\.]{6,}) ([\d\.]+,\d+)", txt)
    if mr:
        red = _num(mr.group(2))
    q, peso = {}, {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", h, re.S):
        if "lblCodigo" not in tr:
            continue
        cels = [html_.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(cels) < 5:
            continue
        cod = cels[0]
        try:
            q[cod] = _num(cels[3]); peso[cod] = _num(cels[4])
        except ValueError:
            continue
    if len(q) < 50 or not red:
        return None
    return {"data": d, "redutor": red, "q": q, "peso": peso}


def ingerir(desde: str = "2019-01-01", calend: list[str] | None = None) -> list[str]:
    """Baixa todas as capturas, grava as fotografias novas e devolve as datas gravadas."""
    fdir = config.DATA / "ibov_carteira"; fdir.mkdir(parents=True, exist_ok=True)
    if calend is None:
        try:
            from fontes import b3
            calend = [d for d, _ in b3.serie("PETR4")]
        except Exception:
            calend = []
    novas = []
    for tipo in PAGINAS:
        for ts, u in capturas(tipo):
            if ts[:4] + "-" + ts[4:6] + "-" + ts[6:8] < desde:
                continue
            try:
                obj = parse(_get(f"http://web.archive.org/web/{ts}id_/{u}").decode("utf-8", "replace"), tipo, calend)
            except Exception as e:
                print(f"  {tipo} {ts}: {e}", file=sys.stderr); continue
            if not obj:
                continue
            f = fdir / f"{obj['data']}.json"
            if f.exists() and "Wayback" not in json.loads(f.read_text(encoding="utf-8")).get("fonte", "Wayback"):
                continue                                      # fotografia ao vivo da B3 tem prioridade
            obj["fonte"] = f"Wayback {ts} {tipo}"
            f.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
            novas.append(obj["data"])
            print(f"  {tipo} {ts} -> {obj['data']}: {len(obj['q'])} papéis, redutor {obj['redutor']:.2f}")
    return sorted(set(novas))


def preenche_redutores(hist: dict, ibov: list[list], tolerancia: float = 0.005) -> int:
    """Valida cada fotografia contra o índice do dia: réplica = Σ q_i·p_i(d) ÷ redutor deve bater com o Ibovespa(d).
    Sem redutor (arquivo IBOVDia truncado) ou com réplica fora da tolerância: deriva redutor = Σ q·p ÷ Ibovespa e marca
    `redutor_derivado`. Se mesmo assim não fecha (quantidades erradas), marca `ok: false` e a fotografia é ignorada.
    Exige preço para ≥ 97% do peso. Devolve quantas fotografias foram alteradas."""
    fdir = config.DATA / "ibov_carteira"
    alias = getattr(config, "ALIAS_TICKER", {})
    ib = {d: v for d, v in ibov}
    n = 0
    for f in sorted(fdir.glob("*.json")) if fdir.exists() else []:
        obj = json.loads(f.read_text(encoding="utf-8"))
        d = obj["data"]
        if d not in ib:
            continue
        soma, peso_ok, peso_tot = 0.0, 0.0, 0.0
        for cod, q in obj["q"].items():
            w = (obj.get("peso") or {}).get(cod) or 0
            peso_tot += w
            h = hist.get(cod) or hist.get(alias.get(cod, "")) or hist.get(next((k for k, v in alias.items() if v == cod), "")) or []
            p = next((v for dd, v in reversed(h) if dd <= d), None)
            if p and h and h[0][0] <= d:
                soma += q * p; peso_ok += w
        if peso_tot and peso_ok / peso_tot < 0.97 or soma <= 0:
            continue                                            # sem preços suficientes para julgar
        antes = dict(obj)
        red = obj.get("redutor")
        erro = abs(soma / red / ib[d] - 1) if red else None
        if erro is None or erro > tolerancia:
            obj["redutor"] = soma / ib[d]; obj["redutor_derivado"] = True
            obj["erro_replica_original"] = erro
        obj["ok"] = True
        # com o redutor derivado a réplica fecha por construção; a fotografia só é rejeitada se a soma dos pesos
        # declarados não bate com os valores (quantidades erradas)
        if obj.get("peso"):
            pesos_calc = {cod: q * (next((v for dd, v in reversed(hist.get(cod) or []) if dd <= d), 0)) / soma * 100 for cod, q in obj["q"].items()}
            desvio = sum(abs(pesos_calc[c] - (obj["peso"].get(c) or 0)) for c in pesos_calc if pesos_calc[c]) / 2
            obj["desvio_pesos"] = round(desvio, 2)
            if desvio > 5:                                      # mais de 5 p.p. de peso fora do lugar: quantidades não são desse dia
                obj["ok"] = False
        if obj != antes:
            f.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8"); n += 1
    return n


if __name__ == "__main__":
    n = ingerir()
    print(f"{len(n)} fotografias: {n[:3]} ... {n[-3:]}")
