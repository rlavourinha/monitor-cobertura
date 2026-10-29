"""Screening · versão celular (iPhone): uma coluna, só o essencial, mesmos dados do painel completo.

Gera output/mobile.html (publicado como m.html no GitHub Pages). Chamado no fim de build.build(); também roda sozinho:
`python mobile.py`. Sem JS externo, sem fontes externas (fontes do sistema), SVG inline, modo escuro pelo sistema.

Blocos: cabeçalho (versão · hora) → Hoje (índices, câmbio, juros) → Ibovespa minuto a minuto → Fora do padrão (vigia)
→ Cobertura (preço, dia, alvo, sparkline) → Setores do Ibov hoje → Fluxo B3 → Liquidez → Curva DI.
"""
from __future__ import annotations

import html
import json
import math
from datetime import date, datetime, timedelta

import config
import build as B
from fontes import b3

SAIDA = config.OUTPUT / "mobile.html"
ACOMPANHA = {"RENT3": "Localiza", "CYRE3": "Cyrela", "CURY3": "Cury"}   # fora da cobertura formal, mas acompanhados
MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


def _j(nome: str, default=None):
    p = config.DATA / nome
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default
    except Exception:
        return default


def _tile(l: str, v: str, d: str = "", cls: str = "") -> str:
    return f'<div class="tile"><div class="l">{l}</div><div class="v {cls}">{v}</div><div class="d">{d}</div></div>'


def _card(titulo: str, corpo: str, sub: str = "") -> str:
    return f'<section class="card"><h2>{titulo}{f"<small>{sub}</small>" if sub else ""}</h2>{corpo}</section>'


# ----------------------------------------------------------------------------------------------------- dados
def _ibov_hoje(M: dict) -> dict:
    """Ibovespa de hoje: proxy BOVA11 do vigia (r1, vrel, hora) e barras de 1 min de data/intraday_hoje.json."""
    A = _j("alertas_tempo_real.json", {}) or {}
    I = _j("intraday_hoje.json", {}) or {}
    hoje = date.today().isoformat()
    sessao = A.get("hora", "")[:10]
    try:
        from fontes import di
        d = date.today() - timedelta(days=1)
        while not di.util(d):
            d -= timedelta(days=1)
        ult_util = d.isoformat()
    except Exception:
        ult_util = ""
    # "hoje" = passada do vigia de hoje; "ultimo" = fora do pregão (fim de semana, feriado, antes das 10h): mostra o último pregão
    out = {"hoje": sessao == hoje, "ultimo": sessao != hoje and sessao == ult_util, "sessao": sessao, "hora": A.get("hora", ""),
           "ibov": A.get("ibov") or {}, "alertas": A.get("alertas") or [], "papeis": A.get("papeis") or {}, "frac": A.get("fracao_pregao"),
           "barras": [], "proxy": (I.get("proxy") or {}).get("IBOV")}
    out["rotulo"] = f'vigia {out["hora"][11:]}' if out["hoje"] else (f'fechamento {sessao[8:10]}/{sessao[5:7]}' if out["ultimo"] else "último fechamento")
    if I.get("data") == hoje:
        out["barras"] = (I.get("series") or {}).get("IBOV") or []
    h = [p for p in (M.get("hist", {}).get("Ibovespa") or []) if p[1] and p[0] < hoje]
    out["fech_ant"] = h[-1][1] if h else None
    out["fech_ant_data"] = h[-1][0] if h else None
    if out["barras"] and out["fech_ant"]:
        out["ultimo"] = out["barras"][-1][1]
        out["r1_pontos"] = out["ultimo"] / out["fech_ant"] - 1
    return out


def svg_intraday(barras: list, fech_ant: float | None, W=400, H=150) -> str:
    """Linha de 1 min do dia com o fechamento anterior tracejado; eixo de horas."""
    pts = [(datetime.fromisoformat(h), v) for h, v in barras if v]
    if len(pts) < 2:
        return '<p class="empty">Sem barras de hoje ainda.</p>'
    ML, MR, MT, MB = 8, 62, 10, 20
    ys = [v for _, v in pts] + ([fech_ant] if fech_ant else [])
    lo, hi = min(ys), max(ys)
    pad = (hi - lo) * 0.08 or 1
    lo, hi = lo - pad, hi + pad
    d = pts[0][0].replace(hour=10, minute=0, second=0); f = pts[0][0].replace(hour=17, minute=35, second=0)
    span = (f - d).total_seconds()
    X = lambda t: ML + max(0.0, min(1.0, (t - d).total_seconds() / span)) * (W - ML - MR)
    Y = lambda v: MT + (hi - v) / (hi - lo) * (H - MT - MB)
    path = " ".join(f"{'M' if i == 0 else 'L'}{X(t):.1f},{Y(v):.1f}" for i, (t, v) in enumerate(pts))
    cor = "var(--up)" if (fech_ant and pts[-1][1] >= fech_ant) else "var(--dn)"
    o = [f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="Ibovespa intraday">']
    for hh in (10, 11, 12, 13, 14, 15, 16, 17):
        x = X(d.replace(hour=hh))
        o.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{MT}" y2="{H - MB}" class="grid"/><text class="tick" x="{x:.1f}" y="{H - 5}" text-anchor="middle">{hh}h</text>')
    if fech_ant:
        y = Y(fech_ant)
        o.append(f'<line x1="{ML}" x2="{W - MR}" y1="{y:.1f}" y2="{y:.1f}" class="ref"/><text class="tick" x="{W - MR + 4}" y="{y + 4:.1f}">{B.num(fech_ant, 0)}</text>')
    o.append(f'<path class="line" style="stroke:{cor}" d="{path}"/>')
    xl, yl = X(pts[-1][0]), Y(pts[-1][1])
    o.append(f'<circle cx="{xl:.1f}" cy="{yl:.1f}" r="3.5" style="fill:{cor}"/><text class="endlab" x="{min(xl + 6, W - MR + 4):.1f}" y="{yl - 6:.1f}">{B.num(pts[-1][1], 0)}</text>')
    o.append("</svg>")
    return "".join(o)


def svg_volume(serie: list[list], mm21: list[list], W=400, H=150) -> str:
    """Volume diário por pregão e média de 21 pregões; eixo de anos (série longa) ou de trimestres (até ~1 ano), grade em R$ bi."""
    if len(serie) < 2:
        return ""
    ML, MR, MT, MB = 30, 50, 8, 18
    n = len(serie); hi = max(v for _, v in serie) * 1.05
    X = lambda i: ML + i / (n - 1) * (W - ML - MR)
    Y = lambda v: MT + (hi - v) / hi * (H - MT - MB)
    o = [f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="Volume diário da B3">']
    passo = 20 if hi > 60 else 10
    for t in range(0, int(hi) + 1, passo):
        o.append(f'<line x1="{ML}" x2="{W - MR}" y1="{Y(t):.1f}" y2="{Y(t):.1f}" class="grid"/><text class="tick" x="{ML - 4}" y="{Y(t) + 4:.1f}" text-anchor="end">{t}</text>')
    curta = len(serie) <= 300     # até ~1 ano: rótulos por trimestre (mês/ano)
    marca = None
    for i, (d, _) in enumerate(serie):
        m = d[:7] if curta else d[:4]
        if m != marca:
            marca = m
            if not curta:
                o.append(f'<text class="tick" x="{X(i) + 2:.1f}" y="{H - 5}">{m[2:]}</text>')
            elif i > 0 and int(m[5:]) % 3 == 1:
                o.append(f'<line x1="{X(i):.1f}" x2="{X(i):.1f}" y1="{MT}" y2="{H - MB}" class="grid"/><text class="tick" x="{X(i):.1f}" y="{H - 5}" text-anchor="middle">{m[5:]}/{m[2:4]}</text>')
    for pts, cor, w in ((serie, "var(--mut)", 0.8), (mm21, "var(--s2)", 1.8)):
        path = " ".join(f"{'M' if i == 0 else 'L'}{X(i):.1f},{Y(v):.1f}" for i, (_, v) in enumerate(pts))
        o.append(f'<path class="line" style="stroke:{cor};stroke-width:{w}" d="{path}"/>')
    for v, cor, dy in ((serie[-1][1], "var(--mut)", -4), (mm21[-1][1], "var(--s2)", 10)):
        o.append(f'<text class="endlab" x="{W - MR + 4}" y="{Y(v) + dy:.1f}" style="fill:{cor}">{B.num(v, 1)}</text>')
    o.append("</svg>")
    return "".join(o)


def svg_spark(pts: list[list], cor: str, dec: int, W=150, H=40) -> str:
    pts = [p for p in pts if p[1] is not None]
    if len(pts) < 2:
        return ""
    ys = [p[1] for p in pts]; lo, hi = min(ys), max(ys); pad = (hi - lo) * 0.1 or 1; lo, hi = lo - pad, hi + pad
    n = len(pts)
    X = lambda i: 2 + i / (n - 1) * (W - 4); Y = lambda v: 3 + (hi - v) / (hi - lo) * (H - 6)
    path = " ".join(f"{'M' if i == 0 else 'L'}{X(i):.1f},{Y(p[1]):.1f}" for i, p in enumerate(pts))
    return f'<svg class="spark" viewBox="0 0 {W} {H}"><path class="line" style="stroke:{cor}" d="{path}"/><circle cx="{X(n - 1):.1f}" cy="{Y(pts[-1][1]):.1f}" r="2.5" style="fill:{cor}"/></svg>'


def svg_hbar(linhas: list[tuple[str, float]], W=400, RH=22, ML=132, dec=2, suf=" p.p.", chaves: list[str] | None = None) -> str:
    if not linhas:
        return ""
    vmax = max(abs(v) for _, v in linhas) or 1
    H = RH * len(linhas) + 4
    x0 = ML + (W - ML - 60) / 2
    o = [f'<svg class="chart" viewBox="0 0 {W} {H}">']
    o.append(f'<line x1="{x0:.1f}" x2="{x0:.1f}" y1="0" y2="{H}" class="grid"/>')
    for i, (lab, v) in enumerate(linhas):
        y = i * RH + 3
        w = abs(v) / vmax * (W - ML - 60) / 2
        x = x0 if v >= 0 else x0 - w
        ds = f' data-setor="{html.escape(chaves[i])}"' if chaves else ""
        o.append(f'<g{ds} class="{"row" if chaves else ""}"><rect x="0" y="{y}" width="{W}" height="{RH}" style="fill:transparent"/>')
        o.append(f'<text class="lab" x="{ML - 6}" y="{y + RH - 8}" text-anchor="end">{html.escape(lab)}</text>')
        o.append(f'<rect x="{x:.1f}" y="{y + 3}" width="{max(w, 1):.1f}" height="{RH - 9}" rx="3" style="fill:{"var(--up)" if v >= 0 else "var(--dn)"}"/>')
        # valor sempre do lado direito do eixo: depois da barra (positivo) ou logo à direita do eixo (negativo), sem invadir o rótulo
        o.append(f'<text class="tick" x="{(x0 + w + 5) if v >= 0 else (x0 + 5):.1f}" y="{y + RH - 8}" text-anchor="start">{("+" if v > 0 else "") + B.num(v, dec)}{suf}</text></g>')
    o.append("</svg>")
    return "".join(o)


def svg_curva_di(vert: dict, ant: dict | None = None, W=400, H=150, maxa: float = 10, ticks=(1, 2, 3, 5, 7, 10)) -> str:
    """Curva de hoje (cheia) e a do pregão anterior (tracejada) nos mesmos vértices; rótulos = taxa de hoje.
    `maxa`/`ticks` = alcance do eixo x em anos (DI: 10; DAP/juro real: 30)."""
    pts = [(float(k[:-1]), v["taxa"]) for k, v in vert.items() if not k.startswith("_") and isinstance(v, dict) and v.get("taxa")]
    pts.sort()
    if len(pts) < 3:
        return ""
    pa = sorted((float(k[:-1]), v) for k, v in (ant or {}).items() if v)
    ML, MR, MT, MB = 40, 16, 16, 22
    ys = [v for _, v in pts] + [v for _, v in pa]
    lo, hi = min(ys), max(ys); pad = (hi - lo) * 0.25 or 0.2; lo, hi = lo - pad, hi + pad
    X = lambda a: ML + a / maxa * (W - ML - MR); Y = lambda v: MT + (hi - v) / (hi - lo) * (H - MT - MB)
    path = " ".join(f"{'M' if i == 0 else 'L'}{X(a):.1f},{Y(v):.1f}" for i, (a, v) in enumerate(pts))
    o = [f'<svg class="chart" viewBox="0 0 {W} {H}">']
    for a in ticks:
        o.append(f'<text class="tick" x="{X(a):.1f}" y="{H - 6}" text-anchor="middle">{a}a</text>')
    if len(pa) >= 2:
        pth = " ".join(f"{'M' if i == 0 else 'L'}{X(a):.1f},{Y(v):.1f}" for i, (a, v) in enumerate(pa))
        o.append(f'<path class="line ref" style="stroke:var(--mut);stroke-width:1.5" d="{pth}"/>')
    o.append(f'<path class="line" style="stroke:var(--s1)" d="{path}"/>')
    for a, v in pts:
        o.append(f'<circle cx="{X(a):.1f}" cy="{Y(v):.1f}" r="3" style="fill:var(--s1)"/><text class="tick" x="{X(a):.1f}" y="{Y(v) - 7:.1f}" text-anchor="middle">{B.num(v, 2)}</text>')
    o.append("</svg>")
    return "".join(o)


def tabela_di(vert: dict, ant: dict, d_ant: str | None) -> str:
    """Vértice · hoje · ontem · Δ em bps."""
    linhas = []
    for k, v in vert.items():
        if k.startswith("_") or not isinstance(v, dict) or not v.get("taxa"):
            continue
        t, o = v["taxa"], (ant or {}).get(k)
        d = (t - o) * 100 if o else None
        linhas.append(f'<tr><td>{k[:-1].replace(".", ",")} ano{"s" if float(k[:-1]) > 1 else ""}</td><td>{B.num(t, 2)}</td><td>{B.num(o, 2)}</td>'
                      f'<td class="{B.dlt_cls(-d) if d is not None else ""}">{("+" if d > 0 else "") + B.num(d, 0) if d is not None else "—"}</td></tr>')
    if not linhas:
        return ""
    cab = f'{d_ant[8:10]}/{d_ant[5:7]}' if d_ant else "ontem"
    return f'<table><thead><tr><th>Vértice</th><th>Hoje</th><th>{cab}</th><th>Δ bps</th></tr></thead><tbody>{"".join(linhas)}</tbody></table>'


# ----------------------------------------------------------------------------------------------------- blocos
def bloco_hoje(M: dict, IB: dict, DI: dict) -> str:
    mk = M.get("mercado", {})
    def mkt(nome, dec=2, pref="", suf="", rotulo=None):
        q = mk.get(nome) or {}
        f = q.get("fonte") or ""
        tag = (" · " + f.split("/")[1]) if f.startswith("vigia/") else (" · Yahoo" if "yahoo" in f else "")
        return _tile(rotulo or nome, B.num(q.get("preco"), dec, pref, suf), f'{B.pct(q.get("var_dia"))} · {(q.get("hora") or "")[11:16]}{tag}', B.dlt_cls(q.get("var_dia")))
    ib = IB.get("ibov") or {}
    if IB.get("hoje") and ib.get("r1") is not None:
        v = IB.get("ultimo"); r1 = IB.get("r1_pontos", ib["r1"])
        t_ib = _tile("Ibovespa", B.num(v, 0) if v else B.pct(r1), f'{B.pct(r1)} · vol {B.num(ib.get("vrel"), 2, suf="x")} da média · {IB["hora"][11:]}', B.dlt_cls(r1))
    else:
        t_ib = mkt("Ibovespa", 0)
    vert = DI.get("vertices") or {}
    def di(k):
        v = (vert.get(k) or {}).get("taxa")
        return B.num(v, 2, suf="%")
    n35 = M.get("ntnb_2035") or []
    dap = mk.get("Juro real 2035 (DAP)") or {}
    if dap.get("preco"):
        t_real = _tile("Juro real 2035", B.num(dap["preco"], 2, suf="%"), f'{("+" if (dap.get("preco") - (dap.get("fech_anterior") or dap["preco"])) * 100 >= 0 else "")}{B.num((dap["preco"] - (dap.get("fech_anterior") or dap["preco"])) * 100, 0)} bps · {(dap.get("hora") or "")[11:16]} · DAP K35', "")
    else:
        t_real = _tile("NTN-B 2035", B.num(n35[-1][1], 2, suf="%") if n35 else "—", f'real · Tesouro {n35[-1][0][8:10] + "/" + n35[-1][0][5:7] if n35 else ""}')
    tiles = (t_ib + mkt("S&P 500", 0, rotulo="S&P 500 (futuro)") + mkt("USD/BRL", 3, rotulo="Dólar (futuro)") + mkt("Brent (US$)", 1)
             + _tile("DI 1 ano", di("1a"), f'DI 10 anos {di("10a")} · {(DI.get("hora") or "")[11:16]}')
             + t_real + mkt("VIX", 1) + mkt("Treasury 10a (%)", 2, suf="%"))
    return _card("Hoje" if IB.get("hoje") else "Último pregão", f'<div class="tiles">{tiles}</div>', IB["rotulo"])


def bloco_intraday(IB: dict) -> str:
    if not IB.get("barras"):
        return ""
    sub = f'1 min · {IB["barras"][-1][0][11:16]}' + (f' · proxy {IB["proxy"]}' if IB.get("proxy") else "")
    nota = f'<p class="nota">Fechamento anterior {B.num(IB.get("fech_ant"), 0)} ({(IB.get("fech_ant_data") or "")[8:10]}/{(IB.get("fech_ant_data") or "")[5:7]}). Pontos reescalados do {IB.get("proxy") or "índice"}; o feed do índice à vista do MT5 não é confiável.</p>'
    return _card("Ibovespa hoje", svg_intraday(IB["barras"], IB.get("fech_ant")) + nota, sub)


def bloco_alertas(IB: dict) -> str:
    if not (IB.get("hoje") or IB.get("ultimo")):
        return _card("Fora do padrão", '<p class="empty">Sem passada do vigia hoje.</p>')
    al = IB.get("alertas") or []
    ib = IB.get("ibov") or {}
    lin = f'Ibov {B.pct(ib.get("r1"))} · vol {B.num(ib.get("vrel"), 2, suf="x")} · {B.num((IB.get("frac") or 0) * 100, 0)}% do pregão'
    if not al:
        return _card("Fora do padrão", f'<p class="empty">Nenhum papel fora do padrão às {IB["hora"][11:]}.</p><p class="nota">{lin}</p>', IB["rotulo"])
    rows = "".join(f'<li><b>{a["cod"]}</b><span class="{B.dlt_cls(a.get("r1"))}">{B.pct(a.get("r1"))}</span><small>{html.escape(a.get("txt", ""))}</small></li>' for a in al[:14])
    return _card("Fora do padrão", f'<ul class="lista">{rows}</ul><p class="nota">{lin}. Oscilação ≥ 2σ (60 pregões) ou volume projetado ≥ 2× a média de 21.</p>', f'{len(al)} · {IB["rotulo"]}')


def bloco_cobertura(mercado: dict, minhas: list, cons: list, IB: dict) -> str:
    TR = B._tempo_real_papeis()
    a0 = config.ANOS_FISCAIS[0]
    rows = []
    for tk in list(config.UNIVERSO) + list(ACOMPANHA):
        s = b3.serie(tk)
        q = TR.get(tk) or {}
        intr = (mercado.get("tickers", {}).get(tk) or {}).get("intraday") or {}
        preco = q.get("preco") or intr.get("preco") or (s[-1][1] if s else None)
        hora = q.get("hora") or intr.get("hora") or ""
        ant = [v for d, v in s if d < (hora[:10] or date.today().isoformat())]
        fech_ant = ant[-1] if ant else None
        var = (preco / fech_ant - 1) if (preco and fech_ant) else None
        ano = next((v for d, v in s if d >= f"{date.today().year}-01-01"), None)
        var_ano = (preco / ano - 1) if (preco and ano) else None
        m = B.ultima(minhas, tk, a0); c = B.ultima(cons, tk, a0, prefer=("bloomberg", "yahoo"))
        tm = (m or {}).get("target"); tc = (c or {}).get("target")
        alvo = []
        if tm:
            alvo.append(f'meu {B.num(tm, 2)} (<span class="{B.dlt_cls(tm / preco - 1)}">{B.pct(tm / preco - 1 if preco else None)}</span>)')
        if tc:
            alvo.append(f'cons. {B.num(tc, 2)} (<span class="{B.dlt_cls(tc / preco - 1)}">{B.pct(tc / preco - 1 if preco else None)}</span>)')
        sp = [[d, v] for d, v in s[-60:]]
        if preco and sp and hora[:10] > sp[-1][0]:
            sp.append([hora[:10], preco])
        nome = config.UNIVERSO.get(tk, {}).get("nome") or ACOMPANHA.get(tk, tk)
        rows.append(f'<li><div class="r1"><b>{tk}</b><small>{html.escape(nome)}</small><span class="px">{B.num(preco, 2, "R$ ")}</span>'
                    f'<span class="{B.dlt_cls(var)}">{B.pct(var)}</span></div>'
                    f'<div class="r2">{svg_spark(sp, "var(--s1)", 2)}<span><small>ano {B.pct(var_ano)}</small><br><small>{" · ".join(alvo) or "sem alvo"}</small></span></div></li>')
    return _card("Cobertura", f'<ul class="cob">{"".join(rows)}</ul><p class="nota">Preço do vigia (MT5) no pregão; sparkline de 60 pregões (COTAHIST, sem ajuste). Alvo meu: estimativas/minhas.csv; consenso Bloomberg ou Yahoo.</p>', "RDOR3 · SAUD3 + acompanhados")


def bloco_trades(M: dict) -> str:
    """Trades executados (trades.json): resultado do book e de cada trade, marcado pelo vigia no pregão."""
    try:
        import trades
        R = trades.marca(ibov_hist=(M.get("hist") or {}).get("Ibovespa"))
    except Exception as e:
        return ""
    T = [x for x in R["trades"] if x.get("preco_atual") is not None and not x.get("saida")]   # só o que está aberto
    if not T:
        return ""
    bk, cv = R["book"], R["curva"]
    def sg(v, dec=0, suf=""):
        return B.num(v, dec, "+" if (v or 0) > 0 else "", suf)
    tiles = (_tile("Realizado", f'R$ {sg(bk.get("realizado"))}', f'{bk.get("n_encerrados", 0)} trades zerados', B.dlt_cls(bk.get("realizado")))
             + _tile("Em aberto", f'R$ {sg(bk["resultado"])}', f'{sg((bk["ret"] or 0) * 100, 2, "%")} do bruto · {bk["n_abertos"]} abertos', B.dlt_cls(bk["resultado"]))
             + _tile("Resultado do dia", f'R$ {sg(bk.get("res_dia"))}', "contra o fechamento anterior", B.dlt_cls(bk.get("res_dia")))
             + _tile("Comprado / vendido", f'{B.num(bk["comprado"] / 1000, 0)} / {B.num(bk["vendido"] / 1000, 0)} mil', f'CDI {sg(cv["cdi"][-1][1], 2, "%") if cv.get("cdi") else "—"} · Ibov {sg(cv["ibov"][-1][1], 1, "%") if cv.get("ibov") else "—"} no período'))
    rows = "".join(f'<tr><td><b>{x["ticker"]}</b> <small class="{"up" if x["sinal"] > 0 else "dn"}">{x["lado"][:4]}</small></td><td>{B.num(x["preco_atual"], 2)}</td>'
                   f'<td class="{B.dlt_cls(x.get("dia"))}">{B.pct(x.get("dia"))}</td><td class="{B.dlt_cls(x["ret"])}">{B.pct(x["ret"])}</td><td class="{B.dlt_cls(x["resultado"])}">{sg(x["resultado"])}</td><td>{B.num((x.get("mult") or {}).get("valor"), 1, suf="x") if x.get("mult") else "—"}</td></tr>'
                   for x in sorted(T, key=lambda x: -x["resultado"]))
    tab = f'<table><thead><tr><th>Trade</th><th>Preço</th><th>Dia</th><th>Entrada</th><th>R$</th><th>Múlt.</th></tr></thead><tbody>{rows}</tbody></table>'
    hora = R["hora"]
    return _card("Trades", f'<div class="tiles">{tiles}</div>{tab}<p class="nota">Posições da corretora (trades.json) marcadas pelo vigia{" às " + hora[11:16] if hora else " no fechamento"}; vendido com sinal invertido. Sem custos nem aluguel.</p>', f'{len(T)} trades')


def bloco_setores(C: dict, IB: dict) -> str:
    itens = C.get("itens") or []
    if not itens or not (IB.get("hoje") or IB.get("ultimo")):
        return ""
    P = IB.get("papeis") or {}
    contrib: dict[str, float] = {}; peso_ok = 0.0; movs = []
    for it in itens:
        q = P.get(it["cod"]) or {}
        r1 = q.get("r1")
        if r1 is None:
            continue
        w = float(it.get("peso") or 0)
        contrib[it["setor"]] = contrib.get(it["setor"], 0.0) + w * r1
        peso_ok += w; movs.append((it["cod"], r1, w * r1))
    if not contrib:
        return ""
    linhas = sorted(contrib.items(), key=lambda x: -x[1])
    movs.sort(key=lambda x: -x[2])
    top = "".join(f'<li><b>{c}</b><span class="{B.dlt_cls(r)}">{B.pct(r)}</span><small>{("+" if k > 0 else "")}{B.num(k, 2)} p.p.</small></li>' for c, r, k in movs[:4] + movs[-4:][::-1])
    tot = sum(contrib.values())
    # papéis de cada setor: dia (vigia), 5 pregões e ano (COTAHIST) — acordeão nativo, sem JS obrigatório
    hoje = date.today().isoformat(); ano0 = f"{date.today().year}-01-01"
    def jan(it):
        q = P.get(it["cod"]) or {}
        preco, r1 = q.get("preco"), q.get("r1")
        s = [(d, v) for d, v in b3.serie(it["cod"]) if d < hoje and v]
        r5 = (preco / s[-5][1] - 1) if (preco and len(s) >= 5) else None
        base = next((v for d, v in reversed(s) if d < ano0), None)
        ry = (preco / base - 1) if (preco and base) else None
        return r1, r5, ry
    det = []
    for setor, c in linhas:
        its = sorted([it for it in itens if it["setor"] == setor and (P.get(it["cod"]) or {}).get("r1") is not None], key=lambda i: -float(i.get("peso") or 0))
        rows = ""
        for it in its:
            r1, r5, ry = jan(it)
            rows += (f'<tr><td>{it["cod"]}</td><td>{B.num(float(it.get("peso") or 0), 1)}%</td><td class="{B.dlt_cls(r1)}">{B.pct(r1)}</td>'
                     f'<td class="{B.dlt_cls(r5)}">{B.pct(r5)}</td><td class="{B.dlt_cls(ry)}">{B.pct(ry)}</td><td>{("+" if (float(it.get("peso") or 0) * r1) > 0 else "")}{B.num(float(it.get("peso") or 0) * r1, 2)}</td></tr>')
        det.append(f'<details data-setor="{html.escape(setor)}"><summary><span>{html.escape(setor)}</span><small>{len(its)} {'papel' if len(its) == 1 else 'papéis'}</small><b class="{B.dlt_cls(c)}">{("+" if c > 0 else "")}{B.num(c, 2)} p.p.</b></summary>'
                   f'<table><thead><tr><th>Papel</th><th>Peso</th><th>Dia</th><th>5 d</th><th>Ano</th><th>p.p.</th></tr></thead><tbody>{rows}</tbody></table></details>')
    js = ("<script>document.querySelectorAll('svg g[data-setor]').forEach(function(g){g.addEventListener('click',function(){"
          "var d=[].slice.call(document.querySelectorAll('details[data-setor]')).filter(function(x){return x.dataset.setor===g.dataset.setor;})[0];if(!d)return;"
          "document.querySelectorAll('details[data-setor]').forEach(function(x){if(x!==d)x.open=false;});d.open=true;d.scrollIntoView({behavior:'smooth',block:'center'});});});</script>")
    return _card("Ibov por setor" + (" hoje" if IB.get("hoje") else f' · {IB["sessao"][8:10]}/{IB["sessao"][5:7]}'), svg_hbar(linhas, chaves=[s for s, _ in linhas]) + f'<ul class="lista dupla">{top}</ul>'
                 + f'<p class="nota">Contribuição = peso × variação do papel; soma {("+" if tot > 0 else "")}{B.num(tot, 2)} p.p. com {B.num(peso_ok, 0)}% da carteira cotada. Toque num setor (barra ou lista) para ver os papéis.</p>'
                 + "".join(det) + js, IB["rotulo"])


def bloco_fluxo() -> str:
    from fontes import b3_bdi
    F = _j("fluxo_investidores.json", {}) or {}
    serie = b3_bdi.serie_diaria(F) if F else []          # diário coletado + histórico de terceiros (antes era só o histórico, parava no mês anterior)
    if not serie:
        return ""
    by = {r["data"]: r for r in serie}
    ds = [r["data"] for r in serie][-6:]
    mes = ds[-1][:7]
    tipos = ("estrangeiro", "institucional", "pessoa física", "inst. financeira", "outros")   # as 5 categorias da B3: a linha soma zero
    mtd = {k: sum((by[d].get(k) or 0) for d in by if d.startswith(mes)) / 1e3 for k in tipos}
    rows = "".join(f'<tr><td>{d[8:10]}/{d[5:7]}</td>' + "".join(f'<td class="{B.dlt_cls(by[d].get(k))}">{B.num((by[d].get(k) or 0) / 1e3, 2)}</td>' for k in tipos) + "</tr>" for d in ds[::-1])
    rows = f'<tr class="tot"><td>{MESES[int(mes[5:]) - 1]}/{mes[2:4]} até aqui</td>' + "".join(f'<td class="{B.dlt_cls(v)}">{B.num(v, 1)}</td>' for v in mtd.values()) + "</tr>" + rows
    return _card("Fluxo na B3 (R$ bi)", f'<table><thead><tr><th>Dia</th><th>Estr.</th><th>Inst.</th><th>PF</th><th>IF</th><th>Outros</th></tr></thead><tbody>{rows}</tbody></table><p class="nota">Saldo diário por tipo de investidor (B3): estrangeiro, institucional, pessoa física, instituição financeira e outros (empresas, governo, clubes). Cada linha soma zero: todo negócio tem um comprador e um vendedor. Publicado com 2 dias de atraso.</p>', f'até {ds[-1][8:10]}/{ds[-1][5:7]}')


def bloco_liquidez(IB: dict) -> str:
    try:
        from fontes import b3_volume
        S = b3_volume.serie()
    except Exception:
        S = []
    if not S:
        return ""
    d, r = S[-1]
    liq = lambda x: x["total"] - x.get("indice", 0)          # sem o IBOV11 (exercício de opções de Ibovespa no vencimento)
    serie = [[dd, liq(x) / 1e9] for dd, x in S]
    m21 = sum(v for _, v in serie[-22:-1]) / 21
    ano = [v for dd, v in serie if dd[:4] == d[:4]]
    ant = [v for dd, v in serie if dd[:4] == str(int(d[:4]) - 1)]
    ib = IB.get("ibov") or {}
    ex = f' · fora R$ {B.num(r["indice"] / 1e9, 1)} bi de exercício de opções de Ibovespa' if r.get("indice", 0) > 1e9 else ""
    tiles = (_tile(f"B3 à vista {d[8:10]}/{d[5:7]}", B.num(serie[-1][1], 1, "R$ ", " bi"), f'{B.num(serie[-1][1] / m21, 2, suf="x")} da média de 21 pregões{ex}')
             + _tile(f"Média {d[:4]}", B.num(sum(ano) / len(ano), 1, "R$ ", " bi"), f'{len(ano)} pregões' + (f' · {int(d[:4]) - 1}: R$ {B.num(sum(ant) / len(ant), 1)} bi' if ant else ""))
             + _tile("Ibov hoje (projetado)", B.num(ib.get("vrel"), 2, suf="x") if IB.get("hoje") else "—", "volume dos papéis do índice vs média de 21, projetado para o pregão")
             + _tile("Negócios", B.num(r["negocios"] / 1e6, 2, suf=" mi"), f'{int(r["papeis"])} códigos negociados'))
    mm21 = [[serie[i][0], sum(v for _, v in serie[max(0, i - 20):i + 1]) / len(serie[max(0, i - 20):i + 1])] for i in range(len(serie))]
    g = svg_volume(serie[-252:], mm21[-252:])                  # últimos 12 meses (pedido 14/10: a série desde 2019 ficava longa demais no celular)
    return _card("Liquidez", f'<div class="tiles">{tiles}</div>{g}<p class="nota">Volume diário de toda a B3 à vista (cinza) e média de 21 pregões (laranja), R$ bi, últimos 12 meses. '
                 'Sem o IBOV11: no vencimento de opções de Ibovespa o exercício entra como negócio à vista e infla o dia.</p>')


def bloco_ust() -> str:
    """Curva de Treasuries (U.S. Treasury): hoje vs 1 semana, 1 mês e 1 ano; Δ em bps por vértice numa tabela curta."""
    from fontes import ust
    D = ust.ler()
    S = D.get("serie") or {}
    if len(S) < 260:
        return ""
    rot = D.get("rotulos") or ust.ROTULOS
    ds = list(S)
    d0 = ds[-1]
    def antes(dias):
        alvo = (date.fromisoformat(d0) - timedelta(days=dias)).isoformat()
        return next((d for d in reversed(ds) if d <= alvo), None)
    VERDE, AMBAR = "#1baf7a", "#c98500"                      # o CSS do celular não tem --s3/--s4/--axis
    g = B.svg_curva_ust(W=400, H=170, cab=False).replace("var(--axis)", "var(--mut)").replace("var(--s3)", VERDE).replace("var(--s4)", AMBAR)
    cores = [("hoje", "var(--s1)", d0), ("dia ant.", AMBAR, ds[-2] if len(ds) > 1 else None), ("1 semana", "var(--s2)", antes(7)),
             ("1 mês", VERDE, antes(30)), ("1 ano", "var(--mut)", antes(365))]
    leg = "".join(f'<span style="margin-right:10px;white-space:nowrap"><i style="display:inline-block;width:12px;height:3px;background:{c};vertical-align:3px;margin-right:4px"></i>{l} {d[8:]}/{d[5:7]}{"/" + d[2:4] if l == "1 ano" else ""}</span>' for l, c, d in cores if d)
    h = S[d0]
    rows = ""
    for k in ("2a", "10a", "30a"):
        i = rot.index(k)
        cel = "".join(f'<td class="{B.dlt_cls(-(h[i] - S[d][i]))}">{(h[i] - S[d][i]) * 100:+.0f}</td>' if d and S[d][i] is not None and h[i] is not None else "<td>—</td>" for _, _, d in cores[1:])
        rows += f'<tr><td>{k}</td><td>{B.num(h[i], 2)}%</td>{cel}</tr>'
    i2, i10 = rot.index("2a"), rot.index("10a")
    s2s10 = (h[i10] - h[i2]) * 100
    tab = f'<table><thead><tr><th>Prazo</th><th>Hoje</th><th>Δ 1 dia</th><th>Δ 1 sem</th><th>Δ 1 mês</th><th>Δ 1 ano</th></tr></thead><tbody>{rows}</tbody></table>'
    return _card("Juros americanos", f'<p class="nota" style="margin-top:0">{leg}</p>{g}{tab}<p class="nota">Treasuries nominais (par yield, U.S. Treasury). Δ em bps. Inclinação 2a–10a: {s2s10:+.0f} bps.</p>', f"{d0[8:]}/{d0[5:7]}")


def bloco_juro_real(M: dict) -> str:
    """Juro real de 10 anos: Brasil (NTN-B com duration constante de 10 anos) × EUA (TIPS) × Reino Unido (BoE), série inteira
    desde 2006, e tabela com o nível de hoje e as variações; prêmio do Brasil sobre o TIPS na última linha."""
    br = [[r[0], r[1]] for r in (M.get("ntnb_d10") or [])]
    if len(br) < 2:
        return ""
    VERDE = "#1baf7a"
    tips = [r for r in (M.get("tips10") or []) if r[0] >= br[0][0]]
    uk = [r for r in (M.get("uk_real10") or []) if r[0] >= br[0][0]]
    prem, med = B.premio_real(M)
    S = [("Brasil", "var(--s1)", br), ("EUA (TIPS)", "var(--s2)", tips), ("Reino Unido", VERDE, uk)]
    S = [x for x in S if x[2]]
    W, H, ML, MR, MT, MB = 400, 170, 26, 44, 8, 18
    t0, t1 = date.fromisoformat(br[0][0]), date.fromisoformat(max(x[2][-1][0] for x in S))
    lo = min(0, min(v for _, _, p in S for _, v in p)); hi = max(v for _, _, p in S for _, v in p) * 1.05
    X = lambda d: ML + (date.fromisoformat(d) - t0).days / max(1, (t1 - t0).days) * (W - ML - MR)
    Y = lambda v: MT + (hi - v) / (hi - lo) * (H - MT - MB)
    o = [f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="Juro real de 10 anos: Brasil, EUA e Reino Unido">']
    for t in range(int(lo) - (lo < 0), int(hi) + 1, 2):
        if lo <= t <= hi:
            o.append(f'<line x1="{ML}" x2="{W - MR}" y1="{Y(t):.1f}" y2="{Y(t):.1f}" class="grid"/><text class="tick" x="{ML - 4}" y="{Y(t) + 4:.1f}" text-anchor="end">{t}</text>')
    for a in range(t0.year + 1, t1.year + 1, 4):
        o.append(f'<text class="tick" x="{X(f"{a}-01-01"):.1f}" y="{H - 5}" text-anchor="middle">{str(a)[2:]}</text>')
    for _, cor, p in S:
        passo = max(1, len(p) // 900)                       # ~900 pontos por linha bastam na largura do celular
        pts = p[::passo] + ([p[-1]] if (len(p) - 1) % passo else [])
        o.append(f'<path class="line" style="stroke:{cor};stroke-width:1.4" d="{" ".join(f"{chr(77) if i == 0 else chr(76)}{X(d):.1f},{Y(v):.1f}" for i, (d, v) in enumerate(pts))}"/>')
    ys = sorted(((Y(p[-1][1]), cor, p[-1][1]) for _, cor, p in S), key=lambda x: x[0])
    ult = -99
    for y, cor, v in ys:                                     # rótulos de fim de linha sem sobreposição
        y = max(y, ult + 12); ult = y
        o.append(f'<text class="endlab" x="{W - MR + 4}" y="{y + 4:.1f}" style="fill:{cor}">{B.num(v, 2)}</text>')
    o.append("</svg>")
    leg = "".join(f'<span style="margin-right:10px;white-space:nowrap"><i style="display:inline-block;width:12px;height:3px;background:{c};vertical-align:3px;margin-right:4px"></i>{n}</span>' for n, c, _ in S)
    def antes(p, dias):
        alvo = (date.fromisoformat(p[-1][0]) - timedelta(days=dias)).isoformat()
        return next((v for d, v in reversed(p) if d <= alvo), None)
    def linha(nome, p, suf="%"):
        h = p[-1][1]
        cel = "".join(f'<td class="{B.dlt_cls(-(h - a))}">{(h - a) * 100:+.0f}</td>' if a is not None else "<td>—</td>"
                      for a in (p[-2][1] if len(p) > 1 else None, antes(p, 30), antes(p, 365)))
        return f'<tr><td>{nome}</td><td>{B.num(h, 2)}{suf}</td>{cel}</tr>'
    rows = "".join(linha(n, p) for n, _, p in S) + (linha("Prêmio BR−EUA", prem, " p.p.") if prem else "")
    tab = f'<table><thead><tr><th></th><th>Hoje</th><th>Δ 1 dia</th><th>Δ 1 mês</th><th>Δ 1 ano</th></tr></thead><tbody>{rows}</tbody></table>'
    d = br[-1][0]
    return _card("Juro real 10 anos", f'<p class="nota" style="margin-top:0">{leg}</p>{"".join(o)}{tab}'
                 f'<p class="nota">% a.a. acima da inflação. Brasil: NTN-B com juros semestrais interpolada no ponto de duration de 10 anos (Tesouro Direto, taxa da manhã). '
                 f'EUA: TIPS de 10 anos (FRED). Reino Unido: curva real spot de 10 anos do Bank of England (gilts indexados ao RPI). '
                 f'Δ em bps. Prêmio BR−EUA médio desde 2006: {B.num(med, 1)} p.p.</p>', f"{d[8:]}/{d[5:7]}")


def svg_linhas_m(S: list[tuple[str, str, list[list]]], W=340, H=160, dec=1, passo_anos=4, zero=False, rotulo="") -> str:
    """Linhas estáticas para o celular (série inteira, sem interação): eixo de anos, grade e rótulo de fim de linha.
    S = [(nome, cor, [[data ISO, v], ...]), ...]; 4º item opcional = estilo CSS extra da linha."""
    S = [s for s in S if len(s[2]) > 1]
    if not S:
        return ""
    ML, MR, MT, MB = 30, 44, 8, 18
    t0 = min(date.fromisoformat(s[2][0][0]) for s in S); t1 = max(date.fromisoformat(s[2][-1][0]) for s in S)
    vs = [v for s in S for _, v in s[2]]
    lo, hi = min(vs + ([0] if zero else [])), max(vs + ([0] if zero else []))
    pad = (hi - lo) * 0.06 or 1; lo, hi = lo - pad, hi + pad
    X = lambda d: ML + (date.fromisoformat(d) - t0).days / max(1, (t1 - t0).days) * (W - ML - MR)
    Y = lambda v: MT + (hi - v) / (hi - lo) * (H - MT - MB)
    passo = next(p for p in (1, 2, 2.5, 5, 10, 20, 25, 50, 100) if (hi - lo) / p <= 5)
    o = [f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="{rotulo}">']
    t = math.ceil(lo / passo) * passo
    while t <= hi:
        o.append(f'<line x1="{ML}" x2="{W - MR}" y1="{Y(t):.1f}" y2="{Y(t):.1f}" class="{"ref" if zero and abs(t) < 1e-9 else "grid"}"/>'
                 + (f'<text class="tick" x="{ML - 4}" y="{Y(t) + 4:.1f}" text-anchor="end">{B.num(t, 0 if passo >= 1 else 1)}</text>'
                    if Y(t) < H - MB - 6 else ""))                    # colado no eixo x: só a grade (o rótulo bateria no ano)
        t += passo
    for a in range(t0.year + 1, t1.year + 1, passo_anos):
        o.append(f'<text class="tick" x="{X(f"{a}-01-01"):.1f}" y="{H - 5}" text-anchor="middle">{str(a)[2:]}</text>')
    for s in S:
        o.append(f'<path class="line" style="stroke:{s[1]};stroke-width:1.5;{s[3] if len(s) > 3 else ""}" '
                 f'd="{" ".join(f"{chr(77) if i == 0 else chr(76)}{X(d):.1f},{Y(v):.1f}" for i, (d, v) in enumerate(s[2]))}"/>')
    ult = -99
    for y, cor, v in sorted(((Y(s[2][-1][1]), s[1], s[2][-1][1]) for s in S), key=lambda x: x[0]):
        y = max(y, ult + 14); ult = y                       # endlab de 12 px: 14 px de folga entre rótulos
        o.append(f'<text class="endlab" x="{W - MR + 4}" y="{y + 4:.1f}" style="fill:{cor}">{B.num(v, dec)}</text>')
    o.append("</svg>")
    return "".join(o)


def _legenda_m(S) -> str:
    return '<p class="nota" style="margin:2px 0 0">' + "".join(
        f'<span style="margin-right:10px;white-space:nowrap"><i style="display:inline-block;width:12px;height:3px;background:{c};vertical-align:3px;margin-right:4px"></i>{n}</span>'
        for n, c, *_ in S) + "</p>"


def bloco_contas_nacionais(M: dict) -> str:
    """Contas Nacionais Trimestrais do IBGE: PIB (t/t−1, contra o ano anterior, 4 trimestres), componentes, investimento e
    poupança; mesmos dados dos slides contas-* do Painel (data/contas_nacionais.json)."""
    from fontes import ibge
    D = ibge.ler()
    T = D.get("taxas") or {}
    if not T.get("4t", {}).get("pib"):
        return ""
    I, R, tq = ibge.indicadores(D), D["rotulos"], ibge.tri(D["ultimo"])
    ult = lambda k, v, n=0: (T[v].get(k) or [[None, None]] * (n + 1))[-1 - n][1]
    sg = lambda v, dec=1: B.num(v, dec, "+" if v and v > 0 else "", "%") if v is not None else "—"
    inv, poup, fin = I.get("inv_4t") or [], I.get("poup_4t") or [], I.get("fin_4t") or []
    fp = (M.get("focus_todos") or {}).get("PIB Total") or {}
    a0 = str((M.get("anos") or [date.today().year])[0])
    f0 = (fp.get(a0) or [[None, None]])[-1][1]; f1 = (fp.get(str(int(a0) + 1)) or [[None, None]])[-1][1]
    tiles = (_tile(f"PIB t/t−1 ({tq})", sg(ult("pib", "tt")), f"dessaz.; anterior {sg(ult('pib', 'tt', 1))}", B.dlt_cls(ult("pib", "tt")))
             + _tile("PIB 4 trimestres", sg(ult("pib", "4t")), f"contra {tq[:2]}{int(tq[2:]) - 1:02d}: {sg(ult('pib', 'aa'))}")
             + _tile("Investimento", B.num(inv[-1][1], 1, suf="% PIB") if inv else "—", "FBCF, 4 trimestres")
             + _tile("Poupança", B.num(poup[-1][1], 1, suf="% PIB") if poup else "—",
                     (f"necessidade de financ. {B.num(-fin[-1][1], 1, suf='%')}" if fin[-1][1] < 0 else f"capacidade de financ. {B.num(fin[-1][1], 1, suf='%')}") if fin else ""))
    S1 = [("acumulado em 4 tri", "var(--ink)", T["4t"]["pib"]), ("contra o ano anterior", "var(--s1)", T["aa"]["pib"], "stroke-width:1")]
    g1 = svg_linhas_m(S1, zero=True, rotulo="PIB: crescimento em volume")
    rows = []
    for k in ["pib", "agro", "industria", "servicos", "familias", "governo", "fbcf", "exportacao", "importacao"]:
        if T["tt"].get(k):
            v = [ult(k, "tt"), ult(k, "aa"), ult(k, "4t")]
            nome = "<b>PIB</b>" if k == "pib" else R[k].replace("Investimento (FBCF)", "Investimento").replace("Consumo das famílias", "Famílias").replace("Consumo do governo", "Governo")
            rows.append(f'<tr><td>{nome}</td>{"".join(f"<td class={chr(34)}{B.dlt_cls(x)}{chr(34)}>{sg(x)}</td>" for x in v)}</tr>')
            if k == "servicos":
                rows.append('<tr><td colspan="4" style="color:var(--mut);font-size:12px;padding-top:8px">Demanda</td></tr>')
            if k == "pib":
                rows.append('<tr><td colspan="4" style="color:var(--mut);font-size:12px;padding-top:8px">Oferta</td></tr>')
    tab = f'<table><thead><tr><th></th><th>t/t−1</th><th>a/a</th><th>4 tri</th></tr></thead><tbody>{"".join(rows)}</tbody></table>'
    S2 = [("investimento", "#1baf7a", inv), ("poupança", "var(--s1)", poup)]
    g2 = svg_linhas_m(S2, rotulo="Investimento e poupança, % do PIB")
    foco = f"Focus PIB {a0}: {B.num(f0, 1, suf='%')} · {int(a0) + 1}: {B.num(f1, 1, suf='%')}. " if f0 is not None else ""
    return _card("Contas nacionais", f'<div class="tiles">{tiles}</div><p class="nota" style="margin:10px 0 0"><b>PIB, crescimento em volume (%)</b></p>'
                 f'{_legenda_m(S1)}{g1}{tab}<p class="nota" style="margin:12px 0 0"><b>Investimento e poupança (% do PIB, 4 trimestres)</b></p>{_legenda_m(S2)}{g2}'
                 f'<p class="nota">{foco}IBGE, Contas Nacionais Trimestrais (SIDRA 5932, 1846 e 2072), desde 1996. t/t−1 com ajuste sazonal; a/a = contra o mesmo '
                 f'trimestre do ano anterior; 4 tri = acumulado em 4 trimestres. Investimento = FBCF ÷ PIB; a diferença para a poupança é o financiamento externo.</p>', tq)


def bloco_divida(M: dict) -> str:
    """Dívida pública: bruta (DBGG) e líquida (DLSP) em % do PIB, a dinâmica r − g e o Focus por ano de referência
    (mesmos dados dos slides focus-divida e divida-rg do Painel)."""
    sgs = M.get("sgs") or {}
    db, dl = (sgs.get("13762") or {}).get("serie") or [], (sgs.get("4513") or {}).get("serie") or []
    if len(db) < 13:
        return ""
    D = B._divida_rg(sgs)
    u = D[-1] if D else None
    mes = f"{db[-1][0][5:7]}/{db[-1][0][2:4]}"
    sg = lambda v, dec=1, suf=" p.p.": B.num(v, dec, "+" if v and v > 0 else "", suf)
    tiles = (_tile("Dívida bruta", B.num(db[-1][1], 1, suf="% PIB"), f"{sg(db[-1][1] - db[-13][1])} em 12 meses")
             + _tile("Dívida líquida", B.num(dl[-1][1], 1, suf="% PIB"), f"{sg(dl[-1][1] - dl[-13][1])} em 12 meses" if len(dl) > 13 else ""))
    if u:
        tiles += (_tile("r − g", sg(u["rg"]), f"r {B.num(u['r'], 1, suf='%')} · g {B.num(u['g'], 1, suf='%')}")
                  + _tile("Primário que falta", sg(u["est"] - u["prim"], 1, " p.p."), f"efetivo&nbsp;{B.num(u['prim'], 1, suf='%')} · estabiliza&nbsp;{B.num(u['est'], 1, suf='%')}"))
    S1 = [("bruta (DBGG)", "var(--ink)", db), ("líquida (DLSP)", "var(--s1)", dl)]
    dirp = config.DATA / "focus_longo"
    a0 = (M.get("anos") or [date.today().year])[0]
    rows = []
    for ano in range(a0, a0 + 5):
        cel = []
        for ind in ("Dívida bruta do governo geral", "Dívida líquida do setor público"):
            f = dirp / f"{ind.replace(' ', '_')}_{ano}.json"
            s = [r for r in (json.loads(f.read_text(encoding="utf-8")) if f.exists() else []) if r[1] is not None]
            if not s:
                cel += ["<td>—</td>", "<td>—</td>"]; continue
            alvo = (date.fromisoformat(s[-1][0]) - timedelta(days=91)).isoformat()
            a3 = next((r[1] for r in reversed(s) if r[0] <= alvo), None)
            d3 = round(s[-1][1] - a3, 1) + 0.0 if a3 is not None else None
            cel += [f"<td>{B.num(s[-1][1], 1, suf='%')}</td>", f'<td class="{B.dlt_cls(-(d3 or 0))}">{sg(d3, 1, "") if d3 is not None else "—"}</td>']
        rows.append(f"<tr><td>{ano}</td>{''.join(cel)}</tr>")
    tab = ('<table><thead><tr><th>Focus</th><th>Bruta</th><th>Δ 3m</th><th>Líquida</th><th>Δ 3m</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table>")
    # alavancagem: dívida em R$ × PIB nominal (crescimento de 12 meses) e os últimos 8 trimestres
    P = B._divida_pib(sgs)
    alav = ""
    if P:
        g = [("dívida bruta (R$)", "var(--s2)", [[f"{x['m']}-01", x["g_db"]] for x in P]), ("PIB nominal", "var(--s1)", [[f"{x['m']}-01", x["g_pib"]] for x in P])]
        tri = [x for x in P if x["m"][5:] in ("03", "06", "09", "12")][-8:][::-1]
        cel = lambda v, suf="%", sinal=1: f'<td class="{B.dlt_cls(sinal * v)}">{sg(v, 1, suf)}</td>'
        tab_a = ('<table><thead><tr><th>Tri</th><th>Bruta a/a</th><th>PIB a/a</th><th>Bruta ÷ PIB</th><th>Δ 12m</th></tr></thead><tbody>'
                 + "".join(f'<tr><td>{int(x["m"][5:]) // 3}T{x["m"][2:4]}</td><td>{sg(x["g_db"], 1, "%")}</td>{cel(x["g_pib"])}<td>{B.num(x["dbp"], 1, suf="%")}</td>{cel(x["d_dbp"], "", -1)}</tr>' for x in tri)
                 + "</tbody></table>")
        u2 = P[-1]
        alav = (f'<h3 class="sub" style="margin:16px 0 6px">Dívida × PIB · crescimento em 12 meses</h3>'
                f'<div class="tiles">{_tile("Dívida bruta (R$)", sg(u2["g_db"], 1, "%"), B.num(u2["db"] / 1e6, 2, "R$&nbsp;", "&nbsp;tri"))}'
                f'{_tile("PIB nominal", sg(u2["g_pib"], 1, "%"), B.num(u2["pib"] / 1e6, 2, "R$&nbsp;", "&nbsp;tri em 12m"))}</div>'
                f'{_legenda_m(g)}{svg_linhas_m(g, passo_anos=3, zero=True, rotulo="Dívida × PIB")}{tab_a}')
    return _card("Dívida pública", f'<div class="tiles">{tiles}</div>{_legenda_m(S1)}{svg_linhas_m(S1, passo_anos=3, rotulo="Dívida pública")}{tab}{alav}'
                 f'<p class="nota">BCB SGS 13762 (DBGG, metodologia 2008) e 4513 (DLSP), % do PIB, mensal. r = juro implícito (NFSP nominal − primário, '
                 f'sobre a dívida bruta média de 12 meses); g = PIB nominal de 12 meses; primário que estabiliza = d × (r − g)/(1 + g). '
                 f'Focus: mediana por ano de referência; Δ 3m em p.p. (vermelho = expectativa de dívida subiu). Dívida × PIB: 13761 (DBGG, R$) e 4382 (PIB de 12 meses, R$ correntes; '
                 f'o último mês é estimativa do BCB); Δ 12m da razão em p.p., vermelho = alavancagem subiu.</p>', mes)


def bloco_di(DI: dict) -> str:
    vert = DI.get("vertices") or {}
    if not vert:
        return ""
    from fontes import di
    d_ant, ant = di.vertices_anterior((DI.get("hora") or "")[:10] or None, contratos_base=vert.get("_contratos"))
    sub = f'{(DI.get("hora") or "")[11:16]} · tracejado = {d_ant[8:10]}/{d_ant[5:7]}' if d_ant else "% a.a."
    return _card("Curva DI", svg_curva_di(vert, ant) + tabela_di(vert, ant, d_ant)
                 + '<p class="nota">Vértices de prazo constante interpolados (flat-forward) dos contratos líquidos (B3 via MT5). Ontem = fechamento D1 dos mesmos contratos. Δ em pontos-base; verde = taxa caiu.</p>', sub)


def bloco_dap(DP: dict) -> str:
    """Curva de juro real (futuros DAP = cupom de IPCA, a NTN-B sintética): mesmo formato do card da curva DI."""
    vert = DP.get("vertices") or {}
    if not vert:
        return ""
    from fontes import dap
    d_ant, ant = dap.vertices_anterior((DP.get("hora") or "")[:10] or None, contratos_base=vert.get("_contratos"))
    sub = f'{(DP.get("hora") or "")[11:16]} · tracejado = {d_ant[8:10]}/{d_ant[5:7]}' if d_ant else "% a.a. + IPCA"
    return _card("Curva de juro real (DAP)", svg_curva_di(vert, ant, maxa=30, ticks=(2, 5, 10, 15, 20, 30)) + tabela_di(vert, ant, d_ant)
                 + '<p class="nota">Cupom de IPCA (DAP, B3 via MT5) = juro real da NTN-B sintética, % a.a. acima do IPCA. Vértices de prazo constante (flat-forward) dos contratos com negócio ou bid/ask vivo; a ponta de 2045-2055 vem do meio do spread. Δ em pontos-base; verde = taxa caiu.</p>', sub)


# ----------------------------------------------------------------------------------------------------- página
CSS = r"""
:root{color-scheme:light dark;--bg:#f4f1ea;--sf:#fbfaf6;--ink:#15171b;--ink2:#4b4e54;--mut:#8a8d92;--grid:#e6e2d8;--ring:rgba(21,23,27,.09);--s1:#2f5fd0;--s2:#e06a2a;--up:#1f8a4c;--dn:#c8362b;--chip:#ebe7dd}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0e1013;--sf:#161920;--ink:#f1eee6;--ink2:#c2bfb6;--mut:#858892;--grid:#262a32;--ring:rgba(255,255,255,.09);--s1:#5b8cff;--s2:#ff8a4c;--up:#4cc06a;--dn:#ff6b5e;--chip:#1e222b}}
:root[data-theme="dark"]{--bg:#0e1013;--sf:#161920;--ink:#f1eee6;--ink2:#c2bfb6;--mut:#858892;--grid:#262a32;--ring:rgba(255,255,255,.09);--s1:#5b8cff;--s2:#ff8a4c;--up:#4cc06a;--dn:#ff6b5e;--chip:#1e222b}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.4 -apple-system,"SF Pro Text","Segoe UI",system-ui,sans-serif;padding:env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left)}
main{max-width:560px;margin:0 auto;padding:0 16px 40px}
header.top{position:sticky;top:0;z-index:2;background:color-mix(in srgb,var(--bg) 88%,transparent);backdrop-filter:blur(10px);padding:12px 0 8px;display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
header.top h1{font:600 20px/1.2 -apple-system,"SF Pro Display",system-ui,sans-serif;margin:0;letter-spacing:-.01em}
.chip{font:12px/1 system-ui;background:var(--chip);color:var(--ink2);padding:5px 8px;border-radius:999px;white-space:nowrap}
header.top a{margin-left:auto;font-size:13px;color:var(--s1);text-decoration:none}
.card{background:var(--sf);border:1px solid var(--ring);border-radius:16px;padding:14px 14px 10px;margin:12px 0}
.card h2{font:600 15px/1.2 -apple-system,system-ui,sans-serif;margin:0 0 10px;display:flex;align-items:baseline;gap:8px}
.card h2 small{font-weight:400;color:var(--mut);font-size:12px;margin-left:auto}
.tiles{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.tile{background:var(--bg);border-radius:12px;padding:9px 10px;min-height:64px}
.tile .l{font-size:12px;color:var(--mut);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tile .v{font:600 20px/1.15 -apple-system,"SF Pro Display",system-ui,sans-serif;font-variant-numeric:tabular-nums;margin:2px 0}
.tile .d{font-size:12px;color:var(--ink2);line-height:1.25}
.up{color:var(--up)}.dn{color:var(--dn)}.flat{color:var(--ink2)}
.chart{width:100%;height:auto;display:block}
.chart .line{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}.chart .grid{stroke:var(--grid);stroke-width:1}.chart .ref{stroke:var(--mut);stroke-width:1;stroke-dasharray:4 4}
.chart .tick{font-size:11px;fill:var(--mut)}.chart .cap{font-size:11px;fill:var(--ink2);paint-order:stroke;stroke:var(--sf);stroke-width:3px}.chart .dot{stroke:var(--sf);stroke-width:1.2}.chart .lab{font-size:12px;fill:var(--ink2)}.chart .endlab{font-size:12px;font-weight:600;fill:var(--ink)}
.spark{width:150px;height:40px;flex:none}.spark .line{fill:none;stroke-width:1.6}
ul.lista{list-style:none;margin:0;padding:0}ul.lista li{display:grid;grid-template-columns:72px 64px 1fr;gap:6px;align-items:baseline;padding:8px 0;border-top:1px solid var(--grid);font-variant-numeric:tabular-nums}
ul.lista li:first-child{border-top:0}ul.lista li b{font-weight:600}ul.lista li small{color:var(--ink2);font-size:13px;line-height:1.3}
ul.lista.dupla li{grid-template-columns:72px 64px 1fr;padding:5px 0}
ul.cob{list-style:none;margin:0;padding:0}ul.cob li{padding:9px 0;border-top:1px solid var(--grid)}ul.cob li:first-child{border-top:0}
.cob .r1{display:flex;align-items:baseline;gap:8px;font-variant-numeric:tabular-nums}.cob .r1 b{font-size:16px}.cob .r1 small{color:var(--mut);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}
.cob .r1 .px{font-weight:600}.cob .r2{display:flex;gap:10px;align-items:center;margin-top:4px}.cob .r2 small{color:var(--ink2);font-size:12.5px}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:14px}th{font-weight:500;color:var(--mut);text-align:right;padding:4px 0;border-bottom:1px solid var(--grid)}th:first-child,td:first-child{text-align:left}td{text-align:right;padding:6px 0;border-top:1px solid var(--grid)}tr.tot td{font-weight:600;border-top:0}
.nota{font-size:12px;color:var(--mut);margin:8px 0 2px;line-height:1.35}.empty{color:var(--mut);margin:6px 0}
svg g.row{cursor:pointer}svg g.row:active rect:first-child{fill:var(--chip)}
details{border-top:1px solid var(--grid)}details summary{list-style:none;display:flex;align-items:baseline;gap:8px;padding:10px 0;cursor:pointer;font-variant-numeric:tabular-nums;-webkit-tap-highlight-color:transparent}
details summary::-webkit-details-marker{display:none}details summary::before{content:"›";color:var(--mut);width:10px;display:inline-block;transition:transform .15s}details[open] summary::before{transform:rotate(90deg)}
details summary span{flex:1;font-weight:600}details summary small{color:var(--mut);font-size:12px}details summary b{font-weight:600}details table{margin:0 0 8px;font-size:13.5px}details td,details th{padding:4px 0}
footer{font-size:12px;color:var(--mut);padding:8px 2px 0;line-height:1.4}footer a{color:var(--s1)}
.vset summary .n{font-size:15px}.vset summary .n,.vcab .n{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.vset summary>b,.vset summary>span:not(.n),.vcab>b,.vcab>span:not(.n){width:48px;flex:none;text-align:right}
.vcab{display:flex;gap:8px;font-size:12px;color:var(--mut);padding:8px 0 4px 18px}.vcab b{font-weight:500}.vset table{font-size:13px;margin:0 0 10px}
h3.sub{font:600 13px/1.2 -apple-system,system-ui,sans-serif;color:var(--ink2);margin:14px 0 4px}
"""

def bloco_valuation(M: dict) -> str:
    """Múltiplos 12m à frente do Ibov (BTG Stock Guide + PL da CVM) e por setor; tocar no setor abre as empresas (<details>)."""
    try:
        V = B.valuation_memo(M)
    except Exception:
        V = None
    if not V or not V.get("ibov_hoje"):
        return ""
    ib, ini = V["ibov"], V["ini"]
    dd = f"{ini[8:]}/{ini[5:7]}"
    dx = lambda v: B.num(v, 1, "+" if (v or 0) > 0 else "", "x")
    x1 = lambda v: B.num(v, 1, suf="x")
    ntnb = ((M.get("ntnb_2035") or [[None, None]])[-1] or [None, None])[1]
    ey = 1 / V["ibov_hoje"]
    tiles = (_tile("Ibov P/L 12m", x1(V["ibov_hoje"]), f'{dx(V["ibov_hoje"] - ib[0][1])} desde {dd}' if ib else "")
             + _tile("Ex-Petro e Vale", x1(V["ibov_ex_hoje"]), f'EV/EBITDA {x1(V.get("ibov_ev"))}')
             + _tile("P/VP · DY 12m", f'{B.num(V.get("ibov_pvp"), 2, suf="x")}', f'DY {B.pct(V.get("ibov_dy"), 1, sinal=False)}')
             + _tile("Earnings yield", B.pct(ey, 1, sinal=False), f'EY − NTN-B 2035: {B.num(ey * 100 - ntnb, 1, suf=" pp")}' if ntnb else ""))
    lin = {r["cod"]: r for r in V["linhas"]}
    a1 = V["ano0"] + 1
    def emp(tk):
        r = lin[tk]
        return (f'<tr><td>{tk}</td><td><b>{x1(r["pl12"])}</b></td><td>{x1(r["ev12"])}</td><td>{B.num(r["pvp"], 1, suf="x")}</td>'
                f'<td>{B.pct(r["dy12"], 0, sinal=False)}</td><td class="{B.dlt_cls(r["rev"])}">{B.pct(r["rev"], 0)}</td></tr>')
    sets = ""
    for r in V["setores"]:
        d_pl = (r["pl"] - r["pl_ini"]) if (r["pl"] and r["pl_ini"]) else None
        filhos = "".join(emp(tk) for tk in sorted(r["tks"], key=lambda t: lin[t]["pl12"] or 999))
        filhos += "".join(emp(tk).replace(f"<td>{tk}</td>", f"<td>{tk}<small> fora</small></td>", 1) for tk in sorted(r.get("fora") or [], key=lambda t: lin[t]["pl12"] or 999))
        sets += (f'<details class="vset"><summary><span class="n">{html.escape(r["setor"])}</span><b>{x1(r["pl"])}</b>'
                 f'<span class="{B.dlt_cls(-d_pl if d_pl is not None else None)}">{dx(d_pl)}</span><span class="{B.dlt_cls(r["rev"])}">{B.pct(r["rev"])}</span></summary>'
                 f'<table><thead><tr><th>Papel</th><th>P/L</th><th>EV/EB.</th><th>P/VP</th><th>DY</th><th>Rev.</th></tr></thead><tbody>{filhos}</tbody></table></details>')
    RV = V.get("revisoes") or {}
    jan = "1 mês" if any(m["cel"].get("1 mês") for m in RV.get("mapa", [])) else "1 sem"
    COR = {"cortes disseminados": "var(--dn)", "corte pontual": "var(--dn)", "upgrades disseminados": "var(--up)", "upgrade pontual": "var(--up)"}
    rv = sorted([(m["setor"], m["cel"][jan]) for m in RV.get("mapa", []) if m["cel"].get(jan) and m["cel"][jan]["rot"] != "estável"], key=lambda x: x[1]["inten"])
    li_rv = "".join(f'<li><b>{html.escape(st)}</b><span style="color:{COR[c["rot"]]}">{B.pct(c["inten"])}</span><small>{c["rot"]} · ↓{len(c["dn"])} ↑{len(c["up"])} de {c["n"]}'
                    f'{" · " + c["drv"] if c.get("drv") else ""}</small></li>' for st, c in rv) or '<li><small>nenhum setor com revisão ≥ 1%</small></li>'
    def _ev(e):
        k = "ano1" if "ano1" in e["var"] else "ano0"
        v = e["var"][k]
        r = e.get("relatorio")
        return (f'<li><b>{e["tk"]}</b><span class="{B.dlt_cls(v)}">{B.pct(v, 0) if v is not None else "n.s."}</span>'
                f'<small>{e["data"][8:]}/{e["data"][5:7]} · {html.escape((r["empresa"] + ": " + (r["titulo"] or ""))[:60]) if r else "sem relatório identificado"}</small></li>')
    li_ev = "".join(_ev(e) for e in (RV.get("eventos") or [])[:5])
    bloco_rv = (f'<h3 class="sub">Revisões de lucro {V["ano0"] + 1} · {jan}</h3><ul class="lista dupla">{li_rv}</ul>'
                + (f'<h3 class="sub">Maiores revisões do mês</h3><ul class="lista dupla">{li_ev}</ul>' if li_ev else ""))
    cab = f'<div class="vset vcab"><span class="n">Setor (toque abre)</span><b>P/L</b><span>Δ</span><span>Rev. {str(a1)[2:]}</span></div>'
    return _card("Valuation · múltiplos 12m", f'<div class="tiles">{tiles}</div>{cab}{sets}{bloco_rv}'
                 f'<p class="nota">Preço de tela ÷ lucro, EBITDA e dividendo de {a1 - 1} e {a1} do BTG Stock Guide, interpolados por dias até 31/12; P/VP com o PL dos controladores do último ITR (CVM). '
                 f'Agregados com as quantidades teóricas da carteira do Ibovespa. Δ e revisão desde {dd}; Δ negativo = ficou mais barato.</p>',
                 (f'preços {V["tempo_real"][11:16]} · ' if V.get("tempo_real") else "") + f'guia {V["guia"][8:]}/{V["guia"][5:7]}')


PAGE = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="apple-mobile-web-app-capable" content="yes">
<meta name="theme-color" content="#f4f1ea" media="(prefers-color-scheme: light)"><meta name="theme-color" content="#0e1013" media="(prefers-color-scheme: dark)">
<title>Screening · celular</title><style>/*CSS*/</style></head><body><main>
<header class="top"><h1>Screening</h1><span class="chip">v/*VERSAO*/</span><span class="chip">/*DATA*/</span><a href="./">versão completa</a></header>
/*CORPO*/
<footer>Dados: MT5 (vigia, a cada 5 min no pregão), B3 COTAHIST e Boletim, BCB, Yahoo. Build /*DATA*/ · <a href="./">painel completo</a>.</footer>
</main></body></html>"""


def build_mobile() -> None:
    M = B._macro_tempo_real(_j("macro.json", {}) or {})       # Ibov, dólar, S&P e juro real pelo vigia (MT5); Brent/VIX/Treasury pelo Yahoo
    mercado = B._mercado_tickers_tempo_real(_j("mercado.json", {"tickers": {}}) or {"tickers": {}})
    minhas, cons = B.le_csv(B.MINHAS), B.le_csv(B.CONS)
    C = _j("ibov_comp.json", {}) or {}
    DI = _j("di_curva.json", {}) or {}
    DP = _j("dap_curva.json", {}) or {}                        # juro real (DAP = cupom de IPCA), gravado pelo vigia como o DI
    IB = _ibov_hoje(M)
    corpo = "".join([bloco_hoje(M, IB, DI), bloco_intraday(IB), bloco_alertas(IB), bloco_cobertura(mercado, minhas, cons, IB), bloco_trades(M),
                     bloco_setores(C, IB), bloco_valuation(M), bloco_fluxo(), bloco_liquidez(IB), bloco_di(DI), bloco_dap(DP), bloco_juro_real(M), bloco_ust(), bloco_divida(M), bloco_contas_nacionais(M)])
    page = PAGE.replace("/*CSS*/", CSS).replace("/*VERSAO*/", B.versao()).replace("/*DATA*/", B.carimbo_build()).replace("/*CORPO*/", corpo)
    config.OUTPUT.mkdir(parents=True, exist_ok=True)
    SAIDA.write_text(page, encoding="utf-8")
    (config.OUTPUT / "m.html").write_text(page, encoding="utf-8")     # mesmo nome do GitHub Pages: o link "Versão celular" do Painel funciona local e no ar
    print(f"ok -> {SAIDA} ({len(page) // 1024} KB)")


if __name__ == "__main__":
    build_mobile()
