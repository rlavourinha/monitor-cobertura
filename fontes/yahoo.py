"""Yahoo Finance: cotação intraday com atraso (~15 min na B3) e consenso de FALLBACK.

Consenso do Yahoo é usado só quando não há linha Bloomberg para o ticker no
consenso.csv. Entrega EPS/receita (não entrega EBITDA nem lucro em R$ — o lucro
é derivado como EPS × ações da B3) e target médio. Marcado com fonte='yahoo'.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from datetime import date, datetime

import config

CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1d&interval=5m"


def intraday(sym: str) -> dict | None:
    req = urllib.request.Request(CHART.format(sym=sym), headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            m = json.load(r)["chart"]["result"][0]["meta"]
    except Exception as e:
        print(f"  Yahoo intraday {sym}: {e}", file=sys.stderr)
        return None
    ts = m.get("regularMarketTime")
    return {
        "preco": m.get("regularMarketPrice"),
        "fech_anterior": m.get("chartPreviousClose") or m.get("previousClose"),
        "max_dia": m.get("regularMarketDayHigh"),
        "min_dia": m.get("regularMarketDayLow"),
        "max_52s": m.get("fiftyTwoWeekHigh"),
        "min_52s": m.get("fiftyTwoWeekLow"),
        "volume": m.get("regularMarketVolume"),
        "hora": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else None,
    }


def cotacoes(simbolos: dict[str, str]) -> dict[str, dict]:
    """{chave: {nome, preco, fech_anterior, var_dia, hora}} para índices, commodities, câmbio e ações avulsas."""
    out = {}
    for chave, sym in simbolos.items():
        m = intraday(sym)
        if m and m.get("preco"):
            fa = m.get("fech_anterior")
            out[chave] = {"simbolo": sym, "preco": m["preco"], "fech_anterior": fa,
                          "var_dia": (m["preco"] / fa - 1) if fa else None, "hora": m.get("hora")}
    return out


def historico(sym: str, range_: str = "2y") -> list[list]:
    """[[YYYY-MM-DD, fechamento], ...] diário (Yahoo chart API)."""
    # range=max devolve granularidade mensal; para diário completo usar period1/period2
    if range_ == "max":
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1=0&period2={int(datetime.now().timestamp())}&interval=1d"
    else:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={range_}&interval=1d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.load(r)["chart"]["result"][0]
        ts = res["timestamp"]
        cl = res["indicators"]["quote"][0]["close"]
        return [[datetime.fromtimestamp(t).strftime("%Y-%m-%d"), round(c, 4)] for t, c in zip(ts, cl) if c is not None]
    except Exception as e:
        print(f"  Yahoo histórico {sym}: {e}", file=sys.stderr)
        return []


def dividendos(sym: str) -> list[list]:
    """[[data-ex, valor por ação], ...] desde o início (Yahoo chart API, events=div). Valores ajustados por desdobramento,
    na base de ações de hoje; dividendos e JCP vêm misturados e brutos. Fonte secundária: só para reconstruir histórico."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1=0&period2={int(datetime.now().timestamp())}&interval=1d&events=div"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.load(r)["chart"]["result"][0]
        ev = (res.get("events") or {}).get("dividends") or {}
        out = [[datetime.fromtimestamp(int(v.get("date") or k)).strftime("%Y-%m-%d"), float(v["amount"])] for k, v in ev.items() if v.get("amount")]
        return sorted(out)
    except Exception as e:
        print(f"  Yahoo dividendos {sym}: {e}", file=sys.stderr)
        return []


def desdobramentos(sym: str) -> list[list]:
    """[[data-ex, numerador, denominador], ...] (Yahoo chart API, events=split): desdobramentos, grupamentos e também
    bonificações (110:100 = bonificação de 10%). Cobre o que a B3 só mantém por ~13 meses."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1=0&period2={int(datetime.now().timestamp())}&interval=1d&events=split"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.load(r)["chart"]["result"][0]
        ev = (res.get("events") or {}).get("splits") or {}
        out = []
        for k, v in ev.items():
            num, den = float(v.get("numerator") or 0), float(v.get("denominator") or 0)
            if num > 0 and den > 0 and num != den:
                out.append([datetime.fromtimestamp(int(v.get("date") or k)).strftime("%Y-%m-%d"), num, den])
        return sorted(out)
    except Exception as e:
        print(f"  Yahoo desdobramentos {sym}: {e}", file=sys.stderr)
        return []


def consenso(sym: str, ticker: str, acoes: int | None) -> list[dict]:
    """Linhas no schema config.CAMPOS para os anos fiscais corrente e próximo."""
    try:
        import yfinance as yf
    except ImportError:
        print("  yfinance não instalado (pip install yfinance); pulando fallback", file=sys.stderr)
        return []
    tk = yf.Ticker(sym)
    hoje = date.today().isoformat()
    linhas = []
    try:
        eps = tk.earnings_estimate
        rev = tk.revenue_estimate
        tgt = tk.analyst_price_targets or {}
    except Exception as e:
        print(f"  Yahoo consenso {sym}: {e}", file=sys.stderr)
        return []
    # Yahoo indexa por '0y' (ano fiscal corrente) e '+1y' (próximo). Assume FY = ano civil.
    for per, ano in (("0y", config.ANOS_FISCAIS[0]), ("+1y", config.ANOS_FISCAIS[1])):
        try:
            e = float(eps.loc[per, "avg"]) if per in eps.index else None
            r = float(rev.loc[per, "avg"]) / 1e6 if per in rev.index else None
            n = int(eps.loc[per, "numberOfAnalysts"]) if per in eps.index else None
        except Exception:
            e, r, n = None, None, None
        linhas.append({
            "ticker": ticker, "data": hoje, "ano": ano,
            "receita": round(r, 1) if r else None,
            "ebitda": None,
            "lucro": round(e * acoes / 1e6, 1) if (e and acoes) else None,
            "eps": round(e, 4) if e else None,
            "target": tgt.get("mean"),
            "n_analistas": n,
            "fonte": "yahoo",
        })
    return linhas
