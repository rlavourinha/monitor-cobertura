"""BCB: expectativas Focus (API Olinda, OData) e séries realizadas (SGS).

Focus: https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata/
  - ExpectativasMercadoAnuais: Indicador (IPCA, Selic, PIB Total, Câmbio, IGP-M...), DataReferencia=ano,
    Mediana, numeroRespondentes, baseCalculo (0 = todos os respondentes, 1 = últimos 30 dias). Diária.
  - ExpectativasMercadoSelic: por reunião do Copom (Reuniao = 'R1/2027').
SGS: https://api.bcb.gov.br/dados/serie/bcdata.sgs.{cod}/dados?formato=json (limite de 10 anos por chamada em série diária).
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from datetime import date

OLINDA = "https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata/"
SGS = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{cod}/dados?formato=json&dataInicial={d0}&dataFinal={d1}"
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def _get(url: str, tentativas: int = 4):
    """GET JSON com novas tentativas: o SGS devolve 502/corpo vazio de forma intermitente."""
    import time
    erro = None
    for i in range(tentativas):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except Exception as e:
            erro = e
            time.sleep(1.5 * (i + 1))
    raise erro


def _odata(recurso: str, filtro: str, select: str, top: int = 20000, orderby: str = "Data") -> list[dict]:
    q = {"$top": top, "$format": "json", "$select": select, "$filter": filtro, "$orderby": orderby}
    url = OLINDA + recurso + "?" + urllib.parse.urlencode(q, quote_via=urllib.parse.quote)
    try:
        return _get(url)["value"]
    except Exception as e:
        print(f"  Focus {recurso}: {e}", file=sys.stderr)
        return []


def focus_anuais(indicadores: list[str], anos: list[int], desde: str) -> dict[str, dict[str, list[list]]]:
    """{indicador: {ano: [[data, mediana, n, media, minimo, maximo], ...]}} — todos os respondentes (baseCalculo 0)."""
    fi = " or ".join(f"Indicador eq '{i}'" for i in indicadores)
    fa = " or ".join(f"DataReferencia eq '{a}'" for a in anos)
    filtro = f"baseCalculo eq 0 and Data ge '{desde}' and ({fi}) and ({fa})"
    rows = _odata("ExpectativasMercadoAnuais", filtro, "Indicador,Data,DataReferencia,Mediana,numeroRespondentes,Media,Minimo,Maximo")
    out: dict[str, dict[str, list]] = {}
    for r in rows:
        out.setdefault(r["Indicador"], {}).setdefault(r["DataReferencia"], []).append(
            [r["Data"], r["Mediana"], r["numeroRespondentes"], r.get("Media"), r.get("Minimo"), r.get("Maximo")])
    for i in out.values():
        for s in i.values():
            s.sort()
    return out


def focus_ultima_data(recurso: str, filtro_extra: str = "") -> str | None:
    rows = _odata(recurso, filtro_extra or "Data ge '2000-01-01'", "Data", top=1, orderby="Data desc")
    return rows[0]["Data"] if rows else None


def focus_snapshot(recurso: str, campos: str, filtro_extra: str = "") -> tuple[str | None, list[dict]]:
    """Todas as linhas da última data disponível de um recurso (ex.: mensais, trimestrais, Top5)."""
    d = focus_ultima_data(recurso, filtro_extra)
    if not d:
        return None, []
    f = f"Data eq '{d}'" + (f" and {filtro_extra}" if filtro_extra else "")
    return d, _odata(recurso, f, campos, top=20000)


def focus_anuais_completo(desde: str) -> dict[str, dict[str, list[list]]]:
    """Todos os indicadores anuais, todos os anos de referência, desde `desde`.
    {indicador[ · detalhe]: {ano: [[data, mediana, media, dp, min, max, n], ...]}}"""
    rows = _odata("ExpectativasMercadoAnuais", f"baseCalculo eq 0 and Data ge '{desde}'",
                  "Indicador,IndicadorDetalhe,Data,DataReferencia,Mediana,Media,DesvioPadrao,Minimo,Maximo,numeroRespondentes", top=200000)
    out: dict[str, dict[str, list]] = {}
    for r in rows:
        nome = r["Indicador"] + (f" · {r['IndicadorDetalhe']}" if r.get("IndicadorDetalhe") else "")
        out.setdefault(nome, {}).setdefault(r["DataReferencia"], []).append(
            [r["Data"], r["Mediana"], r["Media"], r["DesvioPadrao"], r["Minimo"], r["Maximo"], r["numeroRespondentes"]])
    for i in out.values():
        for s in i.values():
            s.sort()
    return out


def focus_inflacao_horizonte(desde: str) -> dict[str, dict[str, list[list]]]:
    """Expectativa de inflação 12 e 24 meses à frente (suavizada), histórico. {'12m': {ind: [[data, mediana, n]]}, '24m': ...}"""
    out = {}
    for chave, rec in (("12m", "ExpectativasMercadoInflacao12Meses"), ("24m", "ExpectativasMercadoInflacao24Meses")):
        rows = _odata(rec, f"baseCalculo eq 0 and Suavizada eq 'S' and Data ge '{desde}'", "Indicador,Data,Mediana,numeroRespondentes,Media,Minimo,Maximo", top=50000)
        d: dict[str, list] = {}
        for r in rows:
            d.setdefault(r["Indicador"], []).append([r["Data"], r["Mediana"], r["numeroRespondentes"], r.get("Media"), r.get("Minimo"), r.get("Maximo")])
        for s in d.values():
            s.sort()
        out[chave] = d
    return out


def focus_serie_anual(indicador: str, ano: int, cache_dir) -> list[list]:
    """Série diária completa do Focus para um indicador e ano de referência (≈5 anos de pesquisas).
    Cache em {cache_dir}/{ind}_{ano}.json; anos já encerrados nunca são rebaixados."""
    import json as _json
    from pathlib import Path
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    f = cache_dir / f"{indicador.replace(' ', '_')}_{ano}.json"
    if f.exists() and ano < date.today().year:
        return _json.loads(f.read_text(encoding="utf-8"))
    rows = _odata("ExpectativasMercadoAnuais", f"Indicador eq '{indicador}' and DataReferencia eq '{ano}' and baseCalculo eq 0",
                  "Data,Mediana,Media,Minimo,Maximo,numeroRespondentes", top=6000)
    out = sorted([[r["Data"], r["Mediana"], r.get("Media"), r.get("Minimo"), r.get("Maximo"), r.get("numeroRespondentes")] for r in rows])
    if out:
        f.write_text(_json.dumps(out), encoding="utf-8")
    return out


def focus_selic_reunioes() -> list[dict]:
    """Trajetória esperada da Selic por reunião do Copom, na última data disponível."""
    rows = _odata("ExpectativasMercadoSelic", "baseCalculo eq 0", "Data,Reuniao,Mediana,numeroRespondentes", top=400, orderby="Data desc")
    if not rows:
        return []
    ultima = max(r["Data"] for r in rows)
    sel = [r for r in rows if r["Data"] == ultima]
    # 'R3/2027' -> (2027, 3)
    sel.sort(key=lambda r: (int(r["Reuniao"].split("/")[1]), int(r["Reuniao"][1:].split("/")[0])))
    return [{"reuniao": r["Reuniao"], "mediana": r["Mediana"], "n": r["numeroRespondentes"], "data": ultima} for r in sel]


def sgs(cod: int, desde: str, ate: str | None = None) -> list[list]:
    """[[YYYY-MM-DD, valor], ...] de uma série SGS. Janelas de 9 anos (limite de 10 anos/chamada em série diária)."""
    ate = ate or date.today().isoformat()
    out: dict[str, float] = {}
    ini = date.fromisoformat(desde)
    fim_total = date.fromisoformat(ate)
    while ini <= fim_total:
        fim = min(date(ini.year + 9, 12, 31), fim_total)
        d0, d1 = ini.strftime("%d/%m/%Y"), fim.strftime("%d/%m/%Y")
        try:
            rows = _get(SGS.format(cod=cod, d0=d0, d1=d1))
        except Exception as e:
            print(f"  SGS {cod} {d0}-{d1}: {e}", file=sys.stderr)
            rows = []
        for r in rows:
            d, m, a = r["data"].split("/")
            try:
                out[f"{a}-{m}-{d}"] = float(r["valor"])
            except ValueError:
                pass
        ini = date(fim.year + 1, 1, 1)
    return sorted([[d, v] for d, v in out.items()])
