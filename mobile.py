"""Screening · versão celular (iPhone): uma coluna, só o essencial, mesmos dados do painel completo.

Gera output/mobile.html (publicado como m.html no GitHub Pages). Chamado no fim de build.build(); também roda sozinho:
`python mobile.py`. Sem JS externo, sem fontes externas (fontes do sistema), SVG inline, modo escuro pelo sistema.

Blocos: cabeçalho (versão · hora) → Hoje (índices, câmbio, juros) → Ibovespa minuto a minuto → Fora do padrão (vigia)
→ Cobertura (preço, dia, alvo, sparkline) → Setores do Ibov hoje → Fluxo B3 → Liquidez → Curva DI.
"""
from __future__ import annotations

import html
import json
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
    out = {"hoje": A.get("hora", "")[:10] == hoje, "hora": A.get("hora", ""), "ibov": A.get("ibov") or {}, "alertas": A.get("alertas") or [],
           "papeis": A.get("papeis") or {}, "frac": A.get("fracao_pregao"), "barras": [], "proxy": (I.get("proxy") or {}).get("IBOV")}
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


def svg_curva_di(vert: dict, ant: dict | None = None, W=400, H=150) -> str:
    """Curva de hoje (cheia) e a do pregão anterior (tracejada) nos mesmos vértices; rótulos = taxa de hoje."""
    pts = [(float(k[:-1]), v["taxa"]) for k, v in vert.items() if not k.startswith("_") and isinstance(v, dict) and v.get("taxa")]
    pts.sort()
    if len(pts) < 3:
        return ""
    pa = sorted((float(k[:-1]), v) for k, v in (ant or {}).items() if v)
    ML, MR, MT, MB = 40, 16, 16, 22
    ys = [v for _, v in pts] + [v for _, v in pa]
    lo, hi = min(ys), max(ys); pad = (hi - lo) * 0.25 or 0.2; lo, hi = lo - pad, hi + pad
    X = lambda a: ML + a / 10 * (W - ML - MR); Y = lambda v: MT + (hi - v) / (hi - lo) * (H - MT - MB)
    path = " ".join(f"{'M' if i == 0 else 'L'}{X(a):.1f},{Y(v):.1f}" for i, (a, v) in enumerate(pts))
    o = [f'<svg class="chart" viewBox="0 0 {W} {H}">']
    for a in (1, 2, 3, 5, 7, 10):
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
    return _card("Hoje", f'<div class="tiles">{tiles}</div>', f'vigia {IB["hora"][11:]}' if IB.get("hoje") else "último fechamento")


def bloco_intraday(IB: dict) -> str:
    if not IB.get("barras"):
        return ""
    sub = f'1 min · {IB["barras"][-1][0][11:16]}' + (f' · proxy {IB["proxy"]}' if IB.get("proxy") else "")
    nota = f'<p class="nota">Fechamento anterior {B.num(IB.get("fech_ant"), 0)} ({(IB.get("fech_ant_data") or "")[8:10]}/{(IB.get("fech_ant_data") or "")[5:7]}). Pontos reescalados do {IB.get("proxy") or "índice"}; o feed do índice à vista do MT5 não é confiável.</p>'
    return _card("Ibovespa hoje", svg_intraday(IB["barras"], IB.get("fech_ant")) + nota, sub)


def bloco_alertas(IB: dict) -> str:
    if not IB.get("hoje"):
        return _card("Fora do padrão", '<p class="empty">Sem passada do vigia hoje.</p>')
    al = IB.get("alertas") or []
    ib = IB.get("ibov") or {}
    lin = f'Ibov {B.pct(ib.get("r1"))} · vol {B.num(ib.get("vrel"), 2, suf="x")} · {B.num((IB.get("frac") or 0) * 100, 0)}% do pregão'
    if not al:
        return _card("Fora do padrão", f'<p class="empty">Nenhum papel fora do padrão às {IB["hora"][11:]}.</p><p class="nota">{lin}</p>', IB["hora"][11:])
    rows = "".join(f'<li><b>{a["cod"]}</b><span class="{B.dlt_cls(a.get("r1"))}">{B.pct(a.get("r1"))}</span><small>{html.escape(a.get("txt", ""))}</small></li>' for a in al[:14])
    return _card("Fora do padrão", f'<ul class="lista">{rows}</ul><p class="nota">{lin}. Oscilação ≥ 2σ (60 pregões) ou volume projetado ≥ 2× a média de 21.</p>', f'{len(al)} · {IB["hora"][11:]}')


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


def bloco_setores(C: dict, IB: dict) -> str:
    itens = C.get("itens") or []
    if not itens or not IB.get("hoje"):
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
    return _card("Ibov por setor hoje", svg_hbar(linhas, chaves=[s for s, _ in linhas]) + f'<ul class="lista dupla">{top}</ul>'
                 + f'<p class="nota">Contribuição = peso × variação do papel; soma {("+" if tot > 0 else "")}{B.num(tot, 2)} p.p. com {B.num(peso_ok, 0)}% da carteira cotada. Toque num setor (barra ou lista) para ver os papéis.</p>'
                 + "".join(det) + js, IB["hora"][11:])


def bloco_fluxo() -> str:
    F = _j("fluxo_investidores.json", {}) or {}
    h = F.get("historico") or {}
    if not h:
        return ""
    ds = sorted(h)[-6:]
    mes = ds[-1][:7]
    mtd = {k: sum((h[d].get(k) or 0) for d in h if d.startswith(mes)) / 1e3 for k in ("estrangeiro", "institucional", "pessoa física")}
    rows = "".join(f'<tr><td>{d[8:10]}/{d[5:7]}</td>' + "".join(f'<td class="{B.dlt_cls(h[d].get(k))}">{B.num((h[d].get(k) or 0) / 1e3, 2)}</td>' for k in ("estrangeiro", "institucional", "pessoa física")) + "</tr>" for d in ds[::-1])
    rows = f'<tr class="tot"><td>{MESES[int(mes[5:]) - 1]}/{mes[2:4]} até aqui</td>' + "".join(f'<td class="{B.dlt_cls(v)}">{B.num(v, 1)}</td>' for v in mtd.values()) + "</tr>" + rows
    return _card("Fluxo na B3 (R$ bi)", f'<table><thead><tr><th>Dia</th><th>Estrang.</th><th>Instit.</th><th>P. física</th></tr></thead><tbody>{rows}</tbody></table><p class="nota">Saldo diário por tipo de investidor (B3, compilação Dados de Mercado); publicado com 2 dias de atraso.</p>', f'até {ds[-1][8:10]}/{ds[-1][5:7]}')


def bloco_liquidez(IB: dict) -> str:
    try:
        from fontes import b3_volume
        S = b3_volume.serie()
    except Exception:
        S = []
    if not S:
        return ""
    d, r = S[-1]
    m21 = sum(x["total"] for _, x in S[-21:]) / min(21, len(S)) / 1e9
    ano = [x["total"] for dd, x in S if dd[:4] == d[:4]]
    ib = IB.get("ibov") or {}
    tiles = (_tile(f"B3 à vista {d[8:10]}/{d[5:7]}", B.num(r["total"] / 1e9, 1, "R$ ", " bi"), f'{B.num(r["total"] / 1e9 / m21, 2, suf="x")} da média de 21 pregões')
             + _tile("Média 2026", B.num(sum(ano) / len(ano) / 1e9, 1, "R$ ", " bi"), f'{len(ano)} pregões · 2025: R$ 21,4 bi')
             + _tile("Ibov hoje (projetado)", B.num(ib.get("vrel"), 2, suf="x") if IB.get("hoje") else "—", "volume dos papéis do índice vs média de 21, projetado para o pregão")
             + _tile("Negócios", B.num(r["negocios"] / 1e6, 2, suf=" mi"), f'{int(r["papeis"])} códigos negociados'))
    return _card("Liquidez", f'<div class="tiles">{tiles}</div>')


def bloco_di(DI: dict) -> str:
    vert = DI.get("vertices") or {}
    if not vert:
        return ""
    from fontes import di
    d_ant, ant = di.vertices_anterior((DI.get("hora") or "")[:10] or None, contratos_base=vert.get("_contratos"))
    sub = f'{(DI.get("hora") or "")[11:16]} · tracejado = {d_ant[8:10]}/{d_ant[5:7]}' if d_ant else "% a.a."
    return _card("Curva DI", svg_curva_di(vert, ant) + tabela_di(vert, ant, d_ant)
                 + '<p class="nota">Vértices de prazo constante interpolados (flat-forward) dos contratos líquidos (B3 via MT5). Ontem = fechamento D1 dos mesmos contratos. Δ em pontos-base; verde = taxa caiu.</p>', sub)


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
.chart .tick{font-size:11px;fill:var(--mut)}.chart .lab{font-size:12px;fill:var(--ink2)}.chart .endlab{font-size:12px;font-weight:600;fill:var(--ink)}
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
"""

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
    IB = _ibov_hoje(M)
    corpo = "".join([bloco_hoje(M, IB, DI), bloco_intraday(IB), bloco_alertas(IB), bloco_cobertura(mercado, minhas, cons, IB),
                     bloco_setores(C, IB), bloco_fluxo(), bloco_liquidez(IB), bloco_di(DI)])
    page = PAGE.replace("/*CSS*/", CSS).replace("/*VERSAO*/", B.versao()).replace("/*DATA*/", B.carimbo_build()).replace("/*CORPO*/", corpo)
    config.OUTPUT.mkdir(parents=True, exist_ok=True)
    SAIDA.write_text(page, encoding="utf-8")
    print(f"ok -> {SAIDA} ({len(page) // 1024} KB)")


if __name__ == "__main__":
    build_mobile()
