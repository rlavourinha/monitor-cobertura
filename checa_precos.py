"""Checagem de preços no fim do dia (mercado fechado): o que o site publicou bate com a realidade?

Para cada papel acompanhado (cobertura, acompanhados, trades, BDRs e carteira do Ibovespa) cruza quatro fontes:
  site     último preço publicado pelo vigia (data/alertas_tempo_real.json)
  MT5      último tick do terminal (preço e hora)
  Yahoo    regularMarketPrice (atraso de ~15 min; com o mercado fechado é o fechamento)
  B3       fechamento oficial (COTAHIST_D do dia) — sai à noite; na rodada das 18h15 em geral ainda não existe

Divergência = |fonte − referência| > TOL (referência: B3 se disponível, senão Yahoo), ou tick do MT5 parado antes das 16h45.
Grava data/checagem_precos.json e, se houver divergência em papel da cobertura/trades/acompanhados, avisa no Telegram.
Motivação: 16/10/2026 o MT5 parou às 10:06 em RDOR3/SAUD3/CYRE3/CURY3/XPBR31 e o site mostrou XPBR31 +0,7% num dia de −2,5%.

    python checa_precos.py            # rodada do fim do dia (tarefa "Monitor checagem de precos", 18h15 e 20h30)
    python checa_precos.py --sem-telegram
"""
from __future__ import annotations

import io
import json
import sys
import urllib.request
import zipfile
from datetime import date, datetime

import config
from fontes import mt5, telegram, yahoo

ARQ = config.DATA / "checagem_precos.json"
TOL = 0.005                    # 0,5% contra o fechamento oficial (B3)
TOL_YAHOO = 0.015              # 1,5% contra o Yahoo: o regularMarketPrice para ~17:05, antes do leilão de fechamento (BEEF3/CSNA3/ENGI11
                               # diferiam ~1% em 16/10 com o MT5 vivo até 17:18); serve para pegar feed parado e erro grosso
TICK_MIN = "16:45"             # tick do MT5 anterior a isso num papel líquido = feed parado


def _cotahist_hoje(hoje: date) -> dict[str, float]:
    """{código: fechamento oficial} do COTAHIST_D de hoje (lote-padrão, TPMERC 010); {} se ainda não saiu."""
    url = f"https://bvmf.bmfbovespa.com.br/InstDados/SerHist/COTAHIST_D{hoje.strftime('%d%m%Y')}.ZIP"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as r:
            z = zipfile.ZipFile(io.BytesIO(r.read()))
        raw = z.read(z.namelist()[0])
    except Exception:
        return {}
    out = {}
    for ln in raw.splitlines():
        if ln[:2] != b"01" or ln[24:27] != b"010" or ln[2:10].decode() != hoje.strftime("%Y%m%d"):
            continue
        try:
            out[ln[12:24].strip().decode()] = int(ln[108:121]) / 100.0
        except ValueError:
            continue
    return out


def _papeis() -> tuple[list[str], set[str]]:
    """(todos os papéis checados, os que geram aviso no Telegram)."""
    import mobile
    import trades
    C = json.loads((config.DATA / "ibov_comp.json").read_text(encoding="utf-8"))
    foco = set(getattr(config, "UNIVERSO", {})) | set(getattr(mobile, "ACOMPANHA", {})) | trades.tickers() | set(getattr(config, "BDRS", []))
    todos = sorted(foco | {i["cod"] for i in C.get("itens", [])})
    return todos, foco


def checar(sem_telegram: bool = False) -> dict:
    hoje = date.today()
    h = hoje.isoformat()
    todos, foco = _papeis()
    try:
        TR = json.loads((config.DATA / "alertas_tempo_real.json").read_text(encoding="utf-8"))
    except Exception:
        TR = {}
    site = (TR.get("papeis") or {}) if (TR.get("hora") or "")[:10] == h else {}
    b3 = _cotahist_hoje(hoje)
    linhas, problemas = [], []
    tem_mt5 = mt5.disponivel()
    for c in todos:
        q = mt5.intraday(c) if tem_mt5 else None
        y = yahoo.intraday(f"{c}.SA")
        s = site.get(c) or {}
        r = {"cod": c,
             "site": s.get("preco"), "site_hora": s.get("hora"), "site_fonte": s.get("fonte"),
             "mt5": q and q.get("preco"), "mt5_hora": q and q.get("hora"),
             "yahoo": y and y.get("preco"), "yahoo_hora": y and y.get("hora"),
             "b3": b3.get(c)}
        ref, nome_ref = (r["b3"], "B3") if r["b3"] else ((r["yahoo"], "Yahoo") if r["yahoo"] and (r["yahoo_hora"] or "")[:10] == h else (None, None))
        flags = []
        tol = TOL if nome_ref == "B3" else TOL_YAHOO
        if ref:
            for k in ("site", "mt5"):
                if r[k] and abs(r[k] / ref - 1) > tol:
                    flags.append(f"{k} {r[k]:.2f} vs {nome_ref} {ref:.2f} ({(r[k] / ref - 1) * 100:+.1f}%)")
        if r["mt5_hora"] and r["mt5_hora"][:10] == h and r["mt5_hora"][11:16] < TICK_MIN:
            flags.append(f"MT5 parado desde {r['mt5_hora'][11:16]}")
        r["ref"], r["ref_fonte"], r["flags"] = ref, nome_ref, flags
        linhas.append(r)
        if flags:
            problemas.append(r)
    res = {"hora": datetime.now().strftime("%Y-%m-%d %H:%M"), "b3_disponivel": bool(b3), "n": len(linhas),
           "problemas": [{"cod": r["cod"], "flags": r["flags"], "foco": r["cod"] in foco} for r in problemas], "linhas": linhas}
    ARQ.write_text(json.dumps(res, ensure_ascii=False, indent=0), encoding="utf-8")
    # aviso: só o que importa (preço do site errado em qualquer papel, ou qualquer divergência em papel da cobertura/trades)
    graves = [r for r in problemas if r["cod"] in foco or any(f.startswith("site") for f in r["flags"])]
    print(f"{res['hora']} · {len(linhas)} papéis · referência {'B3 (COTAHIST)' if b3 else 'Yahoo'} · {len(problemas)} com divergência, {len(graves)} relevantes")
    for r in graves:
        print(f"  {r['cod']}: {'; '.join(r['flags'])}")
    if graves and not sem_telegram and telegram.disponivel():
        corpo = "\n".join(f"• <b>{r['cod']}</b>: {'; '.join(r['flags'])}" for r in graves[:15])
        telegram.enviar(f"<b>Checagem de preços · {res['hora'][11:]}</b> (ref. {'B3' if b3 else 'Yahoo'}, tolerância {TOL * 100:.1f}%)\n{corpo}")
    return res


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    checar(sem_telegram="--sem-telegram" in sys.argv)
