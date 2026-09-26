"""B3, Boletim Diário do Mercado (BDI) em tabelas: https://arquivos.b3.com.br/bdi/tabelas

API (sem autenticação): POST /bdi/table/{Tabela}/{data}/{data}/{página}/{tamanho} com corpo {} -> JSON com `table.values`.
Tabelas usadas:
  SharesInvesVolum         participação dos investidores no volume de ações (compras e vendas em R$ mil, % do total),
                           ACUMULADO DO MÊS até D-2; a B3 só serve os últimos ~20 dias, então a série é acumulada aqui.
  SharesInvesVolumMonthly  participação mensal por segmento (à vista, termo, opções, exercícios, blocos) do mês anterior.
  PreviaQuadrimestral      composição das carteiras de índices (filho OficialWalletIbovespa) do quadrimestre corrente.
Cache: data/fluxo_investidores.json = {"diario": {data: {tipo: [compras, vendas]}}, "mensal": {AAAA-MM: {tipo: {...}}}}.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from datetime import date, timedelta

import config

URL = "https://arquivos.b3.com.br/bdi/table/{tabela}/{d}/{d}/1/{n}"
ARQ = config.DATA / "fluxo_investidores.json"
TIPOS = {"Investidor Estrangeiro": "estrangeiro", "Institucionais": "institucional", "Investidores Individuais": "pessoa física",
         "Instituições Financeiras": "inst. financeira", "Outros": "outros"}


def _post(tabela: str, d: str, n: int = 1000) -> dict | None:
    req = urllib.request.Request(URL.format(tabela=tabela, d=d, n=n), data=b"{}",
                                 headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r).get("table")
    except Exception as e:
        print(f"  BDI {tabela} {d}: {e}", file=sys.stderr)
        return None


def investidores(d: str) -> tuple[str, dict] | None:
    """(data de referência, {tipo: [compras R$ mil, vendas R$ mil]}): acumulado do mês até a data de referência, que a
    B3 informa no texto da tabela ("até o dia dd/mm/aaaa", em geral D-2 da consulta). None se a B3 não tiver o dia."""
    import re
    t = _post("SharesInvesVolum", d, 100)
    vals = (t or {}).get("values") or []
    if not vals:
        return None
    ref = d
    for x in t.get("texts") or []:
        m = re.search(r"at[ée] o dia (\d{2})/(\d{2})/(\d{4})", x.get("textPt", ""))
        if m:
            ref = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return ref, {TIPOS.get(v[0], v[0]): [v[1], v[3]] for v in vals}


def investidores_mensal(d: str) -> dict | None:
    """Participação mensal por segmento (mês anterior a d): {tipo: {segmento: [R$, %]}}."""
    t = _post("SharesInvesVolumMonthly", d, 100)
    vals = (t or {}).get("values") or []
    if not vals:
        return None
    cols = t.get("columns") or []
    segs = [c["friendlyNamePt"] for c in cols if c.get("parentId") is None and c["friendlyNamePt"] != "Tipos de investidores"]
    out = {}
    for v in vals:
        row = {}
        for i, s in enumerate(segs):
            if 1 + 2 * i + 1 < len(v):
                row[s] = [v[1 + 2 * i], v[2 + 2 * i]]
        out[TIPOS.get(v[0], v[0])] = row
    return out


def carteira_ibov_quadrimestre(d: str) -> dict | None:
    """Composição do Ibovespa no BDI (quadrimestre corrente): {cod: [qtde teórica, participação %]} + texto de vigência."""
    t = _post("PreviaQuadrimestral", d, 1000)
    if not t:
        return None
    ch = next((c for c in t.get("children") or [] if c.get("name") == "OficialWalletIbovespa"), None)
    if not ch or not ch.get("values"):
        return None
    return {"papeis": {v[0]: [v[3], v[4]] for v in ch["values"]}, "textos": [x.get("textPt", "") for x in t.get("texts", [])]}


def _dias_uteis(a: date, b: date):
    d = a
    while d <= b:
        if d.weekday() < 5:
            yield d.isoformat()
        d += timedelta(days=1)


def atualiza_fluxo(dias_atras: int = 25) -> tuple[int, int]:
    """Busca o acumulado do mês por investidor para cada dia útil ainda não guardado (últimos `dias_atras` dias) e o
    mensal do último mês fechado. Devolve (dias novos, meses novos)."""
    obj = json.loads(ARQ.read_text(encoding="utf-8")) if ARQ.exists() else {"diario": {}, "mensal": {}, "consultado": {}}
    obj.setdefault("consultado", {})
    hoje = date.today()
    novos = 0
    for d in _dias_uteis(hoje - timedelta(days=dias_atras), hoje):
        if d in obj["consultado"]:
            continue
        r = investidores(d)
        if r:
            ref, dados = r
            obj["consultado"][d] = ref
            if ref not in obj["diario"]:
                obj["diario"][ref] = dados; novos += 1
    novos_m = 0
    for k in range(0, 3):                                        # último mês fechado e os dois anteriores, se a B3 servir
        ref = (hoje.replace(day=1) - timedelta(days=1))
        for _ in range(k):
            ref = ref.replace(day=1) - timedelta(days=1)
        chave = ref.strftime("%Y-%m")
        if chave in obj["mensal"]:
            continue
        r = investidores_mensal(ref.isoformat())
        if r:
            obj["mensal"][chave] = r; novos_m += 1
    ARQ.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return novos, novos_m


def serie_diaria(obj: dict | None = None) -> list[dict]:
    """Converte os acumulados do mês em fluxo diário por tipo: saldo do dia = Δ(compras − vendas) contra o dia útil
    anterior do mesmo mês (o primeiro dia do mês é o próprio acumulado). [{data, tipo: saldo R$ mi, ...}]."""
    obj = obj or (json.loads(ARQ.read_text(encoding="utf-8")) if ARQ.exists() else {"diario": {}})
    dias = sorted(obj.get("diario", {}))
    out, ant = [], None
    for d in dias:
        cur = obj["diario"][d]
        mesmo_mes = bool(ant and ant[0][:7] == d[:7])
        if not mesmo_mes:
            dd = date.fromisoformat(d)                          # sem acumulado anterior no mês: só vale se for o 1º dia útil do mês
            primeiro = all(date(dd.year, dd.month, k).weekday() >= 5 for k in range(1, dd.day))
            if not primeiro:
                ant = (d, cur); continue
        row = {"data": d}
        for tipo, (c, v) in cur.items():
            saldo = c - v
            if mesmo_mes and tipo in ant[1]:
                saldo -= ant[1][tipo][0] - ant[1][tipo][1]
            row[tipo] = saldo / 1000.0                          # R$ mil -> R$ mi
        out.append(row)
        ant = (d, cur)
    # histórico de terceiros (saldos diários já líquidos, R$ mi) para as datas anteriores à nossa coleta
    vistos = {r["data"] for r in out}
    for d, row in (obj.get("historico") or {}).items():
        if d not in vistos:
            out.append({"data": d, **row, "fonte": "historico"})
    out.sort(key=lambda r: r["data"])
    return out
