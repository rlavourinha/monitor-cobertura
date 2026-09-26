"""Gera output/monitor.html: resumo + uma aba por empresa (preço, eu vs consenso, revisões).

HTML autocontido: SVG inline gerado aqui, JS puro para hover/abas, sem CDN.
Templates usam .replace(sentinela) — não str.format (CSS/JS têm chaves).

    python build.py            # usa data/ + estimativas/ + consenso/
"""
from __future__ import annotations

import csv
import json
import math
import os
from datetime import date, datetime, timedelta

import config

MINHAS = config.ESTIMATIVAS / "minhas.csv"
CONS = config.CONSENSO / "consenso.csv"
SAIDA = config.OUTPUT / "monitor.html"

# Paleta (dataviz/references/palette.md): Minha = slot 1 azul, Consenso = slot 2 laranja.
COR = {"minha": "var(--s1)", "cons": "var(--s2)"}


# ----------------------------------------------------------------------------- utils
def _f(x) -> float | None:
    try:
        return float(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


def num(x, dec=1, pref="", suf="") -> str:
    """pt-BR: 4.974,7"""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    s = f"{x:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{pref}{s}{suf}"


def pct(x, dec=1, sinal=True) -> str:
    if x is None:
        return "—"
    s = num(x * 100, dec, suf="%")
    return ("+" + s) if (sinal and x > 0) else s


def dlt_cls(x) -> str:
    if x is None:
        return ""
    return "up" if x > 0 else ("dn" if x < 0 else "")


def le_csv(p) -> list[dict]:
    if not p.exists():
        return []
    with p.open(encoding="utf-8", newline="") as fh:
        rows = []
        for r in csv.DictReader(fh):
            if not r.get("ticker"):
                continue
            for k in ("receita", "ebitda", "lucro", "eps", "target"):
                r[k] = _f(r.get(k))
            r["ano"] = int(r["ano"])
            r["n_analistas"] = int(r["n_analistas"]) if r.get("n_analistas") else None
            rows.append(r)
        return rows


def ultima(rows: list[dict], tk: str, ano: int, prefer: tuple[str, ...] = ()) -> dict | None:
    """Última estimativa (por data) do ticker/ano; se `prefer` dado, prioriza essas fontes."""
    c = [r for r in rows if r["ticker"] == tk and r["ano"] == ano and any(v is not None for v in (r["lucro"], r["eps"], r["target"]))]
    if not c:
        return None
    for p in prefer:
        cp = [r for r in c if r["fonte"].startswith(p)]
        if cp:
            return max(cp, key=lambda r: r["data"])
    return max(c, key=lambda r: r["data"])


def ticks(lo: float, hi: float, n=5, from_zero=False) -> list[float]:
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(s * mag for s in (1, 2, 2.5, 5, 10) if s * mag >= raw)
    t0 = (math.floor if from_zero else math.ceil)(lo / step) * step
    out, t = [], t0
    while t <= hi + 1e-9:
        out.append(round(t, 6))
        t += step
    return out


# ----------------------------------------------------------------------------- charts
def svg_preco(cid: str, serie: list[tuple[str, float]], intraday: dict | None, alvos: dict[str, float | None]) -> str:
    """Linha de preço (série única) + linhas de referência de target coloridas. Caso particular de svg_linhas."""
    if not serie:
        return '<div class="empty">Sem série de preço no cache. Rode <code>python coletar.py</code>.</div>'
    pts = [[d, v] for d, v in serie]
    if intraday and intraday.get("preco") and intraday.get("hora"):
        d = intraday["hora"][:10]
        if d > pts[-1][0]:
            pts.append([d, float(intraday["preco"])])
    refs = [("alvo meu" if k == "minha" else "alvo consenso", v, COR[k]) for k, v in alvos.items() if v]
    return svg_linhas(cid, [("Fechamento", "var(--s1)", pts)], 2, pref="R$ ", W=960, H=320, refs=refs)


def _xticks(d0: int, d1: int, X, y: float, largura: float = 900) -> list[str]:
    """Rótulos do eixo de datas: anos se a janela passa de 2 anos, senão meses. Passo dos anos cabe na largura."""
    a0, a1 = date.fromordinal(d0).year, date.fromordinal(d1).year
    out = []
    if a1 - a0 >= 2:
        passo = max(1, math.ceil(38 * (a1 - a0) / largura))
        for a in range(a0, a1 + 1):
            o = date(a, 1, 1).toordinal()
            if d0 <= o <= d1 and (a - a0) % passo == 0:
                out.append(f'<text class="tick" x="{X(o):.1f}" y="{y}" text-anchor="middle">{a}</text>')
    else:
        meses = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
        for a in range(a0, a1 + 1):
            for m in range(1, 13):
                o = date(a, m, 1).toordinal()
                if d0 <= o <= d1:
                    out.append(f'<text class="tick" x="{X(o):.1f}" y="{y}" text-anchor="middle">{meses[m - 1] + ("/" + str(a)[2:] if m == 1 else "")}</text>')
    return out


def svg_linhas(cid: str, series: list[tuple[str, str, list[list]]], dec=1, pref="", suf="", W=470, H=220,
               refs: list[tuple[str, float]] | None = None, titulo: str = "",
               bandas: dict[int, list[list]] | None = None, extras: list[str] | None = None, ini: int = 0,
               curtas: bool = False, sombra_desde: str | None = None, gap: int = 45) -> str:
    """Gráfico de linhas genérico (1 a 4 séries), crosshair via JS. series = [(nome, cor_css, [[data, v, ...extras], ...])].
    bandas = {índice da série: [[data, mínimo, máximo], ...]} desenha a faixa sombreada na cor da série (formato mín–máx + média).
    extras = nomes dos campos adicionais de cada ponto (p[2:]), exibidos no tooltip.
    gap = lacuna máxima (dias) entre pontos ligados por linha; séries trimestrais precisam de gap maior (ex.: 120)."""
    ML, MR, MT, MB = 56, 16, 22 if titulo else 12, 30
    series = [(n, c, [p for p in pts if p[1] is not None]) for n, c, pts in series]
    series = [s for s in series if s[2]]
    if not series:
        return f'<div class="empty small">{titulo or "Série"}: sem dados.</div>'
    d0 = min(datetime.fromisoformat(s[2][0][0]).toordinal() for s in series)
    d1 = max(datetime.fromisoformat(s[2][-1][0]).toordinal() for s in series)
    span = max(d1 - d0, 30)
    bandas = {k: [b for b in v if b[1] is not None and b[2] is not None] for k, v in (bandas or {}).items()}
    ys = [p[1] for s in series for p in s[2]] + [r[1] for r in (refs or []) if r[1] is not None] + [x for b in bandas.values() for _, lo_, hi_ in b for x in (lo_, hi_)]
    lo, hi = min(ys), max(ys)
    pad = (hi - lo) * 0.08 or abs(hi) * 0.05 or 1
    lo, hi = lo - pad, hi + pad
    yt = ticks(lo, hi, 4)
    X = lambda o: ML + (o - d0) / span * (W - ML - MR)
    Y = lambda v: MT + (hi - v) / (hi - lo) * (H - MT - MB)
    out = [f'<text class="sub" x="{ML}" y="12">{titulo}</text>'] if titulo else []
    for t in yt:
        if lo <= t <= hi:
            out.append(f'<line class="grid" x1="{ML}" x2="{W - MR}" y1="{Y(t):.1f}" y2="{Y(t):.1f}"/>'
                       f'<text class="tick" x="{ML - 6}" y="{Y(t) + 4:.1f}" text-anchor="end">{num(t, 0 if abs(t) >= 1000 else dec)}</text>')
    out += _xticks(d0, d1, X, H - 10, W - ML - MR)
    refs = [(r[0], r[1], r[2] if len(r) > 2 else "var(--mut)") for r in (refs or []) if r[1] is not None]
    itens = sorted(refs, key=lambda r: r[1])
    for i, (lab, v, cor) in enumerate(itens):
        abaixo = i == 0 and len(itens) > 1 and abs(Y(itens[1][1]) - Y(v)) < 16
        esq = cor == "var(--mut)"          # referência neutra: rótulo à esquerda, longe dos rótulos de fim de linha
        out.append(f'<line class="ref" style="stroke:{cor}" x1="{ML}" x2="{W - MR}" y1="{Y(v):.1f}" y2="{Y(v):.1f}"/>'
                   f'<text class="reflab" x="{(ML + 4) if esq else (W - MR)}" y="{Y(v) + (12 if abaixo else -4):.1f}" text-anchor="{"start" if esq else "end"}">{lab}{"" if esq else (" " + pref + num(v, dec) + suf)}</text>')
    for k, b in bandas.items():
        if k < len(series) and b:
            cor = series[k][1]
            ida = " ".join(f"{'M' if i == 0 else 'L'}{X(datetime.fromisoformat(p[0]).toordinal()):.1f},{Y(p[2]):.1f}" for i, p in enumerate(b))
            volta = " ".join(f"L{X(datetime.fromisoformat(p[0]).toordinal()):.1f},{Y(p[1]):.1f}" for p in reversed(b))
            out.append(f'<path d="{ida} {volta} Z" style="fill:{cor};opacity:.13;stroke:none"/>')
    labels = []  # (xl, yl, texto, cor, acima?)
    for n, cor, pts in series:
        segs, prev = [], None                     # quebra a linha em lacunas > 45 dias (série sem título no prazo)
        for p in pts:
            o = datetime.fromisoformat(p[0]).toordinal()
            segs.append(f"{'L' if prev is not None and o - prev <= gap else 'M'}{X(o):.1f},{Y(p[1]):.1f}")
            prev = o
        out.append(f'<path class="line" style="stroke:{cor}" d="{" ".join(segs)}"/>')
        xl, yl = X(datetime.fromisoformat(pts[-1][0]).toordinal()), Y(pts[-1][1])
        out.append(f'<circle class="dot" cx="{xl:.1f}" cy="{yl:.1f}" r="4" style="fill:{cor}"/>')
        # rótulo acima, salvo se a linha recente sobe até ele (então vai abaixo)
        rec = [p[1] for p in pts[-max(3, len(pts) // 12):]]
        acima = not (max(rec) > pts[-1][1] + (hi - lo) * 0.04)
        labels.append([xl, yl, f"{pref}{num(pts[-1][1], dec)}{suf}", acima])
    # afasta rótulos de séries que terminam juntas
    labels.sort(key=lambda l: l[1])
    ys_lab = []
    for xl, yl, txt, acima in labels:          # empilha de cima para baixo com folga mínima de 13px
        y = yl - 8 if acima else yl + 15
        if ys_lab and y < ys_lab[-1] + 17:
            y = ys_lab[-1] + 17
        ys_lab.append(y)
    # a pilha não invade o eixo x (séries que convergem no fim): sobe o conjunto até caber acima dos ticks
    if ys_lab and ys_lab[-1] > H - MB - 2:
        dy = ys_lab[-1] - (H - MB - 2)
        ys_lab = [y - dy for y in ys_lab]
    for (xl, yl, txt, acima), y in zip(labels, ys_lab):
        out.append(f'<text class="endlab" x="{xl - 7:.1f}" y="{y:.1f}" text-anchor="end">{txt}</text>')
    hdots = "".join(f'<circle class="hdot" r="5" style="fill:{cor}"/>' for _, cor, _ in series)
    # dados completos para o renderizador JS (o seletor de janela redesenha o gráfico no navegador)
    data_js = json.dumps({"series": [{"n": n, "cor": c, "pts": pts} for n, c, pts in series], "bandas": {str(k): v for k, v in bandas.items()},
                          "refs": [[r[0], r[1], r[2]] for r in refs], "pref": pref, "suf": suf, "dec": dec, "extras": extras or [],
                          "titulo": titulo, "W": W, "H": H, "ML": ML, "MR": MR, "MT": MT, "MB": MB, "gap": gap})
    anos_total = (d1 - d0) / 365.25
    ini = ini if (ini and ini < anos_total) else 0        # janela inicial (anos); 0 = máx
    chips = "".join(f'<button data-a="{a}"{" disabled" if a >= anos_total else ""}{" class=on" if a == ini else ""}>{a}a</button>' for a in ((2, 3, 5, 10) if curtas else (1, 2, 3, 5, 10)))
    if curtas:   # pregões (1, 5, 21), mês corrente, ano corrente e 12 meses (o Painel liga a decomposição do Ibovespa a esta mesma janela)
        chips = '<button data-d="1">1 d</button><button data-d="5">5 d</button><button data-d="21">21 d</button><button data-mtd="1">MTD</button><button data-ytd="1">YTD</button><button data-m="12">12 m</button>' + chips
    return f'''<div class="lin"><div class="janela" data-for="{cid}">{chips}<button data-a="0"{"" if ini else " class=on"}>máx</button></div><svg class="chart" id="{cid}" viewBox="0 0 {W} {H}" data-x0="{d0}" data-span="{span}" data-lo="{lo}" data-hi="{hi}" data-ini="{ini}"{f' data-desde="{sombra_desde}"' if sombra_desde else ""}
  data-ml="{ML}" data-mr="{MR}" data-mt="{MT}" data-mb="{MB}" data-w="{W}" data-h="{H}" role="img" aria-label="{titulo}">
  <g class="corpo">{"".join(out)}</g>
  <g class="hover" style="display:none"><line class="xh" y1="{MT}" y2="{H - MB}"/>{hdots}</g>
  <rect class="hit" x="{ML}" y="{MT}" width="{W - ML - MR}" height="{H - MT - MB}" fill="transparent"/>
  <script type="application/json" class="data">{data_js}</script>
</svg><div class="tip" id="{cid}-tip"></div></div>'''


def svg_curva(pontos: list[tuple[str, float]], titulo: str, suf="%", W=470, H=200) -> str:
    """Curva por vencimento (eixo x = ano do vencimento), pontos ligados, rótulo em cada ponto (poucos)."""
    ML, MR, MT, MB = 50, 16, 22, 30
    if not pontos:
        return f'<div class="empty small">{titulo}: sem dados.</div>'
    xs = [int(p[0][:4]) + (int(p[0][5:7]) - 1) / 12 for p in pontos]
    ys = [p[1] for p in pontos]
    x0, x1 = min(xs) - 1, max(xs) + 1
    lo, hi = min(ys) - 0.3, max(ys) + 0.3
    X = lambda x: ML + (x - x0) / (x1 - x0) * (W - ML - MR)
    Y = lambda v: MT + (hi - v) / (hi - lo) * (H - MT - MB)
    out = [f'<text class="sub" x="{ML}" y="12">{titulo}</text>']
    for t in ticks(lo, hi, 3):
        if lo <= t <= hi:
            out.append(f'<line class="grid" x1="{ML}" x2="{W - MR}" y1="{Y(t):.1f}" y2="{Y(t):.1f}"/><text class="tick" x="{ML - 6}" y="{Y(t) + 4:.1f}" text-anchor="end">{num(t, 1)}</text>')
    ux = -1e9
    for p, x in zip(pontos, xs):
        if X(x) - ux < 34:   # evita colisão de rótulos de vencimentos próximos
            continue
        ux = X(x)
        out.append(f'<text class="tick" x="{X(x):.1f}" y="{H - 10}" text-anchor="middle">{p[0][:4]}</text>')
    out.append('<path class="line" style="stroke:var(--s1)" d="%s"/>' % " ".join(f"{'M' if i == 0 else 'L'}{X(x):.1f},{Y(y):.1f}" for i, (x, y) in enumerate(zip(xs, ys))))
    for p, x, y in zip(pontos, xs, ys):
        out.append(f'<circle class="dot" data-tip="{p[0]}: {num(y, 2)}{suf}" cx="{X(x):.1f}" cy="{Y(y):.1f}" r="4" style="fill:var(--s1)"/>'
                   f'<text class="cap" x="{X(x):.1f}" y="{Y(y) - 9:.1f}" text-anchor="middle">{num(y, 2)}</text>')
    return f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="{titulo}">{"".join(out)}</svg>'


def svg_colunas(titulo: str, cats: list[str], vals: list[float | None], cores: list[str] | str, dec=2, suf="",
                W=960, H=260, rotulos=True, passo_rotulo_x=1) -> str:
    """Colunas por categoria (meses, trimestres). `cores` = cor única ou lista por barra (ex.: realizado cinza, esperado azul)."""
    ML, MR, MT, MB = 56, 16, 26, 34
    v = [x for x in vals if x is not None]
    if not v:
        return f'<div class="empty small">{titulo}: sem dados.</div>'
    lo, hi = min(v + [0]), max(v + [0])
    yt = ticks(lo, hi, 4, from_zero=True)
    lo, hi = min(lo, yt[0]), max(hi, yt[-1]) * 1.12 if hi > 0 else max(hi, yt[-1])
    if lo < 0:
        lo = lo * 1.12
    Y = lambda x: MT + (hi - x) / (hi - lo) * (H - MT - MB)
    n = len(cats)
    slot = (W - ML - MR) / n
    bw = min(24, slot * 0.6)
    out = [f'<text class="sub" x="{ML}" y="12">{titulo}</text>']
    for t in yt:
        out.append(f'<line class="grid" x1="{ML}" x2="{W - MR}" y1="{Y(t):.1f}" y2="{Y(t):.1f}"/><text class="tick" x="{ML - 6}" y="{Y(t) + 4:.1f}" text-anchor="end">{num(t, dec if abs(t) < 10 else 0)}</text>')
    for i, (c, x) in enumerate(zip(cats, vals)):
        cx = ML + slot * (i + 0.5)
        if i % passo_rotulo_x == 0:
            out.append(f'<text class="tick" x="{cx:.1f}" y="{H - 12}" text-anchor="middle">{c}</text>')
        if x is None:
            continue
        cor = cores[i] if isinstance(cores, list) else cores
        y, y0 = Y(max(x, 0)), Y(min(x, 0))
        out.append(f'<rect class="bar" data-tip="{c}: {num(x, dec)}{suf}" x="{cx - bw / 2:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{max(y0 - y, 1):.1f}" rx="3" style="fill:{cor}"/>')
        if rotulos:
            out.append(f'<text class="cap" x="{cx:.1f}" y="{y - 5 if x >= 0 else y0 + 13:.1f}" text-anchor="middle">{num(x, dec)}</text>')
    out.append(f'<line class="axis" x1="{ML}" x2="{W - MR}" y1="{Y(0):.1f}" y2="{Y(0):.1f}"/>')
    return f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="{titulo}">{"".join(out)}</svg>'


def svg_dispersao(linhas: list[tuple[str, float, float, float, float, int, int, str]], W=960) -> str:
    """Faixa mínimo–máximo dos respondentes, com mediana (ponto) e média (traço). Uma linha por indicador/ano, escala própria.
    linhas = [(rótulo, min, mediana, média, max, n, dec, sufixo)]"""
    RH, ML, MR = 44, 250, 140
    H = 16 + RH * len(linhas)
    out = []
    for i, (lab, mn, md, me, mx, n, dec, suf) in enumerate(linhas):
        y = 16 + RH * i + RH / 2
        span = (mx - mn) or 1
        X = lambda v: ML + (v - mn) / span * (W - ML - MR)
        out.append(f'<text class="tick" style="font-size:12.5px;fill:var(--ink)" x="{ML - 64}" y="{y + 4:.1f}" text-anchor="end">{lab}</text>')
        out.append(f'<line x1="{X(mn):.1f}" x2="{X(mx):.1f}" y1="{y:.1f}" y2="{y:.1f}" style="stroke:var(--grid);stroke-width:10;stroke-linecap:round"/>')
        out.append(f'<text class="tick" x="{X(mn) - 8:.1f}" y="{y + 4:.1f}" text-anchor="end">{num(mn, dec)}</text><text class="tick" x="{X(mx) + 8:.1f}" y="{y + 4:.1f}">{num(mx, dec)}{suf}</text>')
        out.append(f'<line x1="{X(me):.1f}" x2="{X(me):.1f}" y1="{y - 9:.1f}" y2="{y + 9:.1f}" style="stroke:var(--s2);stroke-width:2"/>')
        out.append(f'<circle class="dot" data-tip="{lab}: mediana {num(md, dec)}{suf} · média {num(me, dec)}{suf} · {n} respondentes" cx="{X(md):.1f}" cy="{y:.1f}" r="6" style="fill:var(--s1)"/>')
        out.append(f'<text class="cap" x="{X(md):.1f}" y="{y - 12:.1f}" text-anchor="middle">{num(md, dec)}{suf}</text>')
        out.append(f'<text class="tick" x="{W - 4}" y="{y + 4:.1f}" text-anchor="end">n={n}</text>')
    return f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="Dispersão do consenso">{"".join(out)}</svg>'



def svg_spark(titulo: str, pts: list[list], cor: str, dec: int, pref="", suf="", W=300, H=66) -> str:
    """Sparkline estática: linha, mínimo/máximo do período e último valor."""
    pts = [p for p in pts if p[1] is not None]
    if len(pts) < 2:
        return f'<div class="empty small">{titulo}</div>'
    ML, MR, MT, MB = 6, 60, 18, 8
    ys = [p[1] for p in pts]
    lo, hi = min(ys), max(ys)
    pad = (hi - lo) * 0.1 or 1
    lo, hi = lo - pad, hi + pad
    d0 = datetime.fromisoformat(pts[0][0]).toordinal(); d1 = datetime.fromisoformat(pts[-1][0]).toordinal(); span = max(d1 - d0, 1)
    X = lambda o: ML + (o - d0) / span * (W - ML - MR); Y = lambda v: MT + (hi - v) / (hi - lo) * (H - MT - MB)
    path = " ".join(f"{'M' if i == 0 else 'L'}{X(datetime.fromisoformat(p[0]).toordinal()):.1f},{Y(p[1]):.1f}" for i, p in enumerate(pts))
    imax = max(range(len(pts)), key=lambda i: pts[i][1]); imin = min(range(len(pts)), key=lambda i: pts[i][1])
    xl, yl = X(d1), Y(pts[-1][1])
    return (f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="{titulo}"><text class="sub" x="{ML}" y="11">{titulo}</text>'
            f'<path class="line" style="stroke:{cor};stroke-width:1.6" d="{path}"/>'
            f'<circle class="dot" cx="{xl:.1f}" cy="{yl:.1f}" r="3" style="fill:{cor}"/><text class="endlab" x="{xl + 6:.1f}" y="{yl + 4:.1f}">{pref}{num(pts[-1][1], dec)}{suf}</text>'
            f'<text class="tick" x="{X(datetime.fromisoformat(pts[imax][0]).toordinal()):.1f}" y="{Y(pts[imax][1]) - 4:.1f}" text-anchor="middle">{num(pts[imax][1], dec)}</text>'
            f'<text class="tick" x="{X(datetime.fromisoformat(pts[imin][0]).toordinal()):.1f}" y="{Y(pts[imin][1]) + 11:.1f}" text-anchor="middle">{num(pts[imin][1], dec)}</text></svg>')


JANELAS_IBOV = [("dia", "1 d", 1), ("5d", "5 d", 5), ("21d", "21 d", 21), ("ano", "ano", None)]
UNIT_COMP = {"BPAC11": 3, "ENGI11": 5, "IGTI11": 3, "KLBN11": 5, "SANB11": 2, "TAEE11": 3}   # units com composição conhecida (o valor por unit já vem somado da coleta)


def _q_inicio(cod: str, q_atual: float, proventos: list[dict], calend: list[str], t0: str, t: str, classe: str, preco_em=None) -> tuple[float, bool]:
    """Quantidade teórica em t0 reconstruída dos eventos com data ex em (t0, t]:
    dividendo/JCP: q(t0) = q(t) / (Pcum / (Pcum − D)), Pcum = fechamento oficial na data-com;
    bonificação/desdobramento: q(t0) = q(t) / (1 + fator/100). Units usam a composição ON+PN (aproximação se faltar PN)."""
    q, exato = q_atual, True
    for p in proventos or []:
        com, ex = p.get("com"), p.get("ex")                      # B3: data-com (ex = pregão seguinte); Yahoo: data ex direta
        if ex is None:
            if not com:
                continue
            ex = next((d for d in calend if d > com), None)
        elif com is None:
            com = next((d for d in reversed(calend) if d < ex), None)
        if not ex or not (t0 < ex <= t):
            continue
        if p.get("fator") is not None:
            acao = (p.get("acao") or "").upper()
            if "BONIFIC" in acao or "DESDOBR" in acao:       # quantidade sobe 1 + fator/100
                q = q / (1 + p["fator"] / 100)
            elif "GRUPAM" in acao and p["fator"]:            # grupamento n:1 -> antes havia n vezes mais ações
                q = q * (p["fator"] if p["fator"] >= 1 else 1 / p["fator"])
            # outros eventos (resgate, subscrição) não alteram a quantidade teórica aqui
            continue
        D = p.get("valor") or 0
        pcum = p.get("pcum") or (preco_em(cod, com) if (preco_em and com) else None)   # fechamento oficial na data-com (B3) ou da série
        if pcum and pcum > D > 0:
            q = q / (pcum / (pcum - D))
        elif D > 0:
            exato = False
    if classe == "UNT" and cod not in UNIT_COMP:
        exato = False
    return q, exato


def _q_avanca(cod: str, q_s: float, proventos: list[dict], calend: list[str], s: str, t0: str, classe: str, preco_em=None) -> tuple[float, bool]:
    """Inverso de _q_inicio: leva a quantidade teórica de uma fotografia em s para t0 > s (eventos com data ex em (s, t0])."""
    q, exato = q_s, True
    for p in proventos or []:
        com, ex = p.get("com"), p.get("ex")
        if ex is None:
            if not com:
                continue
            ex = next((d for d in calend if d > com), None)
        elif com is None:
            com = next((d for d in reversed(calend) if d < ex), None)
        if not ex or not (s < ex <= t0):
            continue
        if p.get("fator") is not None:
            acao = (p.get("acao") or "").upper()
            if "BONIFIC" in acao or "DESDOBR" in acao:
                q = q * (1 + p["fator"] / 100)
            elif "GRUPAM" in acao and p["fator"]:            # grupamento n:1 -> depois há n vezes menos ações
                q = q / (p["fator"] if p["fator"] >= 1 else 1 / p["fator"])
            continue
        D = p.get("valor") or 0
        pcum = p.get("pcum") or (preco_em(cod, com) if (preco_em and com) else None)
        if pcum and pcum > D > 0:
            q = q * (pcum / (pcum - D))
        elif D > 0:
            exato = False
    if classe == "UNT" and cod not in UNIT_COMP:
        exato = False
    return q, exato


def _fator_preco(proventos: list[dict], calend: list[str], a: str, b: str) -> float:
    """Fator que leva o preço em b para a base de ações de a (eventos com fator e data ex em (a, b]): desdobramento/bonificação
    multiplica por 1 + fator/100, grupamento n:1 divide por n. Serve para a variação exibida não mostrar um desdobramento como queda."""
    f = 1.0
    for p in proventos or []:
        if p.get("fator") is None:
            continue
        ex = p.get("ex") or next((d for d in calend if d > p["com"]), None) if (p.get("ex") or p.get("com")) else None
        if not ex or not (a < ex <= b):
            continue
        acao = (p.get("acao") or "").upper()
        if "BONIFIC" in acao or "DESDOBR" in acao:
            f *= 1 + p["fator"] / 100
        elif "GRUPAM" in acao and p["fator"]:
            f /= p["fator"] if p["fator"] >= 1 else 1 / p["fator"]
    return f


def saltos_inferidos(hist: dict, prov: dict) -> dict:
    """Grupamentos/desdobramentos que a B3 não cobre (só ~13 meses): um salto de preço de um dia para o outro maior que
    +60% ou menor que −40%, sem evento com fator naquela data, vira evento inferido com razão inteira (15:1, 1:5...).
    {cod: [{"ex": data, "fator": n, "acao": "GRUPAMENTO (inferido)"} | {"ex": data, "fator": (n-1)*100, "acao": "DESDOBRAMENTO (inferido)"}]}"""
    out = {}
    for cod, h in hist.items():
        evs = []
        datas_ev = {p.get("ex") or p.get("com") for p in prov.get(cod, []) if p.get("fator") is not None}
        for i in range(1, len(h)):
            p0, p1 = h[i - 1][1], h[i][1]
            if not p0 or not p1:
                continue
            r = p1 / p0
            d = h[i][0]
            if any(abs((date.fromisoformat(d) - date.fromisoformat(x)).days) <= 3 for x in datas_ev if x):
                continue
            if r >= 1.6:
                n = round(r)
                if abs(r / n - 1) <= 0.12:
                    evs.append({"ex": d, "fator": float(n), "acao": "GRUPAMENTO (inferido)", "fonte": "salto"})
            elif r <= 0.6:
                n = round(1 / r)
                if abs((1 / r) / n - 1) <= 0.12:
                    evs.append({"ex": d, "fator": (n - 1) * 100.0, "acao": "DESDOBRAMENTO (inferido)", "fonte": "salto"})
        if evs:
            out[cod] = evs
    return out


def proventos_completos(C: dict) -> dict:
    """Proventos por papel: B3 (data-com, ~13 meses, inclui bonificações) e, antes da cobertura da B3, os dividendos do
    Yahoo (data ex, valores na base de ações de hoje). Os do Yahoo levam 'fonte': 'yahoo' e são tratados como aproximados."""
    b3 = C.get("proventos", {}) or {}
    ini = min((p["com"] for ps in b3.values() for p in ps if p.get("com")), default="9999")
    hist = C.get("hist", {}) or {}
    calend = sorted({d for h in hist.values() for d, _ in h})
    b3_hist = C.get("prov_b3_hist", {}) or {}
    yahoo_div = C.get("prov_yahoo", {}) or {}
    yahoo_split = C.get("split_yahoo", {}) or {}
    out = {}
    for cod in set(b3) | set(yahoo_div) | set(b3_hist) | set(yahoo_split):
        lst = []
        for p in b3.get(cod, []):
            if p.get("em") and p.get("fator") is not None:
                # bonificação paga em ações de OUTRA classe (CYRE3 -> CYRE4): a quantidade teórica do papel não muda; vale como
                # provento em espécie = fator/100 x 1º fechamento da classe nova (referência do redutor), como um dividendo
                ex = next((d for d in calend if d > p["com"]), None)
                p_novo = next((v for d, v in hist.get(p["em"], []) if ex and d >= ex), None)
                if p_novo:
                    lst.append({"com": p["com"], "valor": p["fator"] / 100 * p_novo, "acao": f"{p['acao']} em {p['em']}", "fonte": "outra_classe"})
                continue
            lst.append(p)
        # antes da cobertura do suplemento (~13 meses): histórico completo da B3 (valor bruto + fechamento oficial na data-com)
        antigos = [p for p in b3_hist.get(cod, []) if p["com"] < ini]
        if antigos:
            lst += [{"com": p["com"], "valor": p["valor"], "pcum": p.get("pcum"), "acao": p.get("acao") or "DIVIDENDO", "fonte": "b3_hist"} for p in antigos]
        else:
            # reserva: Yahoo (data ex; valores na base de ações de HOJE -> desfaz os desdobramentos/bonificações posteriores)
            sp = yahoo_split.get(cod, [])
            for d, v in yahoo_div.get(cod, []):
                if d > ini:
                    continue
                f = 1.0
                for sd, num, den in sp:
                    if sd > d:
                        f *= num / den
                lst.append({"ex": d, "valor": v * f, "acao": "DIVIDENDO (Yahoo)", "fonte": "yahoo"})
        # desdobramentos, grupamentos e bonificações do Yahoo (events=split) fora da cobertura da B3: 110:100 = bonificação 10%
        com_fator = [(p.get("ex") or next((d for d in calend if d > p["com"]), None)) for p in lst if p.get("fator") is not None]
        for d, num, den in yahoo_split.get(cod, []):
            if any(x and abs((date.fromisoformat(d) - date.fromisoformat(x)).days) <= 3 for x in com_fator):
                continue
            if num > den:
                lst.append({"ex": d, "fator": (num / den - 1) * 100, "acao": "BONIFICACAO/DESDOBRAMENTO (Yahoo)", "fonte": "yahoo_split"})
            else:
                lst.append({"ex": d, "fator": den / num, "acao": "GRUPAMENTO (Yahoo)", "fonte": "yahoo_split"})
        out[cod] = lst
    for cod, evs in saltos_inferidos(C.get("hist", {}), out).items():
        out.setdefault(cod, []).extend(evs)
    # JCP entra LÍQUIDO de IR na reconstrução da quantidade teórica: é assim que a B3 ajusta o Ibovespa (retorno total com
    # proventos líquidos). Testado: com o bruto a réplica derivava +0,15%/quadrimestre; com 85% fecha em ±0,05%.
    jcp_liq = 1 - float(os.environ.get("JCP_IR", getattr(config, "JCP_IR", 0.15)))
    if jcp_liq != 1:
        for lst in out.values():
            for p in lst:
                if p.get("valor") and "JRS" in (p.get("acao") or "").upper() and not p.get("liq"):
                    p["valor"] *= jcp_liq; p["liq"] = True
    return out


def carrega_fotos() -> dict:
    """Fotografias da carteira (data -> {redutor, q, peso}), com códigos antigos traduzidos para os atuais (config.ALIAS_TICKER),
    para que um papel renomeado não apareça como 'saiu' e 'entrou'. Só fotografias com redutor."""
    fdir = config.DATA / "ibov_carteira"
    if not fdir.exists():
        return {}
    alias = getattr(config, "ALIAS_TICKER", {})
    inv = {v: k for k, v in alias.items()}          # antigo -> novo
    out = {}
    for f in sorted(fdir.glob("*.json")):
        obj = json.loads(f.read_text(encoding="utf-8"))
        if not obj.get("redutor") or obj.get("ok") is False:
            continue
        for chave in ("q", "peso"):
            if isinstance(obj.get(chave), dict):
                obj[chave] = {inv.get(k, k): v for k, v in obj[chave].items()}
        out[f.stem] = obj
    return out


def _foto_quadri(fotos: dict, t0: str, rebal: list[str]) -> tuple[str | None, dict | None]:
    """Fotografia mais próxima de t0 dentro do mesmo quadrimestre (nenhum rebalanceamento entre as duas datas)."""
    melhor = None
    for s, f in fotos.items():
        a, b = min(s, t0), max(s, t0)
        if any(a < r <= b for r in rebal):
            continue
        dist = abs(date.fromisoformat(s).toordinal() - date.fromisoformat(t0).toordinal())
        if melhor is None or dist < melhor[0]:
            melhor = (dist, s, f)
    return (melhor[1], melhor[2]) if melhor else (None, None)


def datas_rebalanceamento(calend: list[str]) -> list[str]:
    """Primeiro pregão de janeiro, maio e setembro de cada ano (início de cada carteira quadrimestral do Ibovespa)."""
    out, vistos = [], set()
    for d in calend:
        if d[5:7] in ("01", "05", "09") and d[:7] not in vistos:
            vistos.add(d[:7]); out.append(d)
    return out[1:] if out and calend and out[0][:7] == calend[0][:7] else out   # o mês inicial do calendário não é um rebalanceamento


def decomp_ibov(M: dict) -> dict | None:
    """Decomposição do Ibovespa por identidade: índice = Σ q_i·p_i / redutor. Contribuição_i = (q_i(t)p_i(t) − q_i(t0)p_i(t0)) / Σ q(t0)p(t0).
    q(t0) vem de fotografia diária da carteira (exato) ou é reconstruída dos proventos (exato p/ ON/PN, aprox. p/ units).
    Janelas que cruzam rebalanceamento sem fotografia (ano) usam peso atual × retorno e são marcadas como aproximadas."""
    p = config.DATA / "ibov_comp.json"
    if not p.exists():
        return None
    C = json.loads(p.read_text(encoding="utf-8"))
    hist, itens = C.get("hist", {}), C.get("itens", [])
    if not hist or not itens:
        return None
    for cod, q in (C.get("intraday") or {}).items():          # último ponto intraday (Yahoo) sobre o fechamento oficial (B3)
        h = hist.get(cod)
        if h and q.get("data") and q["data"] > h[-1][0]:
            hist[cod] = h + [[q["data"], q["preco"]]]
    prov = proventos_completos(C)
    fotos = carrega_fotos()
    calend = sorted({d for h in hist.values() for d, _ in h})
    if not calend:
        return None
    t = calend[-1]
    ib = M.get("hist", {}).get("Ibovespa", [])
    ibq = (M.get("mercado") or {}).get("Ibovespa") or {}
    ib_map = {d: v for d, v in ib}
    if ibq.get("preco") and ibq.get("hora") and ibq["hora"][:10] >= t:
        ib_map[t] = ibq["preco"]
    ano = date.today().year
    rebal = datas_rebalanceamento(calend)
    out = {"data": C.get("data"), "jan": {}, "fotos": len(fotos)}
    def preco_em(cod, d):
        h = hist.get(cod) or []
        return next((v for dd, v in reversed(h) if dd <= d), None)
    for chave, rotulo, n in JANELAS_IBOV:
        if n is not None:
            if len(calend) <= n:
                continue
            t0 = calend[-1 - n]
        else:
            t0 = next((d for d in reversed(calend) if d < f"{ano}-01-01"), None)
            if not t0:
                continue
        s0, foto0 = _foto_quadri(fotos, t0, rebal)          # fotografia do mesmo quadrimestre (exata no dia, ajustada por proventos se em outro dia)
        cruza = any(t0 < r <= t for r in rebal) and not foto0
        exato_janela = bool(foto0) or (not cruza and n is not None)   # com fotografia, exato mesmo cruzando rebalanceamento (redutor incluído)
        red_t = float(C.get("redutor") or 0) or 1.0
        red_0 = float((foto0 or {}).get("redutor") or 0) or red_t
        prov_ini = min((p["com"] for ps in prov.values() for p in ps if p.get("com")), default="9999")
        papeis, v0_tot, vt_tot, exato_all = [], 0.0, 0.0, exato_janela
        if foto0 and s0 != t0 and min(s0, t0) < prov_ini:
            exato_all = False                                  # entre a fotografia e t0 os proventos vêm do Yahoo (aproximados)
        for it in itens:
            cod, w, classe = it["cod"], it["peso"] / 100, it.get("classe", "ON")
            pt, p0 = preco_em(cod, t), preco_em(cod, t0)
            if not pt or (not p0 and not foto0):
                continue
            pt_r = pt * _fator_preco(prov.get(cod, []), calend, t0, t)   # preço final na base de ações de t0 (só para a variação exibida)
            qt = float(it.get("q") or 0)
            if exato_janela and qt:
                if foto0:
                    qs = foto0.get("q", {}).get(cod)
                    if qs is None or not p0:                    # entrou no índice depois de t0: valor inicial zero
                        q0, ex, p0 = 0.0, True, (p0 or 0.0)
                    elif s0 == t0:
                        q0, ex = qs, True
                    elif s0 > t0:
                        q0, ex = _q_inicio(cod, qs, prov.get(cod, []), calend, t0, s0, classe, preco_em)
                    else:
                        q0, ex = _q_avanca(cod, qs, prov.get(cod, []), calend, s0, t0, classe, preco_em)
                else:
                    q0, ex = _q_inicio(cod, qt, prov.get(cod, []), calend, t0, t, classe, preco_em)
                exato_all = exato_all and ex
                v0, vt = q0 * p0 / red_0, qt * pt / red_t
                v0_tot += v0; vt_tot += vt
                papeis.append([cod, it["setor"], it["peso"], (pt_r / p0 - 1) if p0 else 0.0, v0, vt])
            else:
                r = pt_r / p0 - 1
                papeis.append([cod, it["setor"], it["peso"], r, None, w * r * 100])
        if foto0:                                              # papéis que saíram do índice: valor inicial, valor final zero
            atuais = {it["cod"] for it in itens}
            for cod, qs in foto0.get("q", {}).items():
                if cod in atuais:
                    continue
                p0 = preco_em(cod, t0)
                if p0:
                    v0 = qs * p0 / red_0
                elif foto0.get("peso", {}).get(cod) is not None and s0 in ib_map:
                    v0 = foto0["peso"][cod] / 100 * ib_map[s0]; exato_all = False
                else:
                    continue
                v0_tot += v0
                papeis.append([cod, "Saíram do índice", 0.0, -1.0, v0, 0.0])
        if exato_janela and v0_tot > 0:
            papeis = [(c, s, w, r, (vt - v0) / v0_tot * 100) for c, s, w, r, v0, vt in papeis]
            replica = (vt_tot / v0_tot - 1) * 100
        else:
            papeis = [(c, s, w, r, x) for c, s, w, r, _, x in papeis]
            replica = None
        setores: dict[str, list] = {}
        for cod, setor, peso, r, c in papeis:
            s = setores.setdefault(setor, [0.0, 0.0]); s[0] += peso; s[1] += c
        lst = sorted([(k, v[0], v[1], (v[1] / v[0]) if v[0] else 0.0) for k, v in setores.items()], key=lambda x: -x[2])
        soma = sum(c for *_, c in papeis)
        indice = (ib_map[t] / ib_map[t0] - 1) * 100 if (t in ib_map and t0 in ib_map) else None
        out["jan"][chave] = {"rotulo": rotulo, "t0": t0, "t": t, "setores": lst, "papeis": sorted(papeis, key=lambda x: -x[4]),
                             "soma": soma, "indice": indice, "replica": replica,
                             "metodo": (("exato" if exato_all else "quase exato (units/proventos aprox.)") + (f" · fotografia {s0[8:]}/{s0[5:7]}/{s0[2:4]}" if foto0 else "")) if exato_janela else "aprox. (cruza rebalanceamento sem fotografia)",
                             "erro": (soma - indice) if indice is not None else None}
    return out


def svg_hbar(linhas: list[tuple[str, float]], W=560, RH=13, ML=170, MR=56, dec=2, suf=" p.p.", chaves: list[str] | None = None) -> str:
    """Barras horizontais divergentes (positivo azul, negativo vermelho) com rótulo do valor na ponta.
    `chaves` (uma por linha) vira data-setor na barra e no rótulo: o Painel usa para abrir os papéis do setor ao clicar."""
    if not linhas:
        return '<div class="empty small">sem dados</div>'
    H = 8 + RH * len(linhas) + 4
    vals = [v for _, v in linhas]
    lo, hi = min(vals + [0]), max(vals + [0])
    span = (hi - lo) or 1
    X = lambda v: ML + (v - lo) / span * (W - ML - MR)
    out = [f'<line class="axis" x1="{X(0):.1f}" x2="{X(0):.1f}" y1="4" y2="{H - 4}"/>']
    for i, (lab, v) in enumerate(linhas):
        y = 8 + RH * i
        x0, x1 = X(min(v, 0)), X(max(v, 0))
        cor = "var(--s1)" if v >= 0 else "var(--dn)"
        ds = f' data-setor="{chaves[i]}"' if chaves else ""
        cur = ";cursor:pointer" if chaves else ""
        out.append(f'<text class="tick"{ds} style="font-size:10.5px;fill:var(--ink){cur}" x="{ML - 6}" y="{y + RH - 4:.1f}" text-anchor="end">{lab}</text>')
        out.append(f'<rect class="bar"{ds} data-tip="{lab}: {num(v, dec)}{suf}{" · clique para ver os papéis" if chaves else ""}" x="{x0:.1f}" y="{y + 1.5:.1f}" width="{max(x1 - x0, 0.8):.1f}" height="{RH - 3}" rx="2" style="fill:{cor}{cur}"/>')
        esq = v < 0 and (x0 - ML) >= 34   # negativo curto: rótulo à direita do eixo, para não invadir os nomes
        out.append(f'<text class="cap" style="font-size:10px" x="{(x0 - 4) if esq else (x1 + 4):.1f}" y="{y + RH - 4:.1f}" text-anchor="{"end" if esq else "start"}">{num(v, dec, "+" if v > 0 else "")}</text>')
    return f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="Contribuição por setor">{"".join(out)}</svg>'


def bloco_ibov_painel(D: dict) -> str:
    """Bloco do painel: barras por setor + maiores/menores contribuições, com chips dia/5d/20d/ano (troca por JS)."""
    if not D:
        return '<div class="pbox"><h2>Ibovespa · decomposição</h2><div class="empty small">Rode <code>python coletar.py --janela diario</code>.</div></div>'
    chips = "".join(f'<button data-j="{k}"{" class=on" if k == "dia" else ""}>{r}</button>' for k, r, _ in JANELAS_IBOV)
    vs, vp = [], []
    for k, _, _ in JANELAS_IBOV:
        j = D["jan"].get(k)
        if not j:
            continue
        cab = (f'<div class="mut" style="font-size:11px;margin:0 0 4px">Ibovespa {num(j["indice"], 2, "+" if j["indice"] and j["indice"] > 0 else "", "%") if j["indice"] is not None else "—"} · soma {num(j["soma"], 2, "+" if j["soma"] > 0 else "", " p.p.")}'
               f' · erro {num(j["erro"], 2, "+" if j["erro"] and j["erro"] > 0 else "", " p.p.") if j["erro"] is not None else "—"} · {j["metodo"]}</div>')
        ABREV = {"Consumo não cíclico": "Cons. não cíclico", "Consumo cíclico": "Cons. cíclico", "Utilidade pública": "Utilidade públ.", "Materiais básicos": "Mat. básicos", "Bens industriais": "Bens indust."}
        vs.append(f'<div data-j="{k}"{"" if k == "dia" else " style=display:none"}>{cab}{svg_hbar([(ABREV.get(s, s), c) for s, _, c, _ in j["setores"]], W=430, RH=15, ML=104, MR=44)}</div>')
        top, bot = j["papeis"][:3], j["papeis"][-3:][::-1]
        tr = "".join(f'<tr><td class="tk">{c}</td><td class="{dlt_cls(r)}">{pct(r)}</td><td class="{dlt_cls(x)}">{num(x, 2, "+" if x > 0 else "")}</td></tr>' for c, _, _, r, x in top)
        tr2 = "".join(f'<tr><td class="tk">{c}</td><td class="{dlt_cls(r)}">{pct(r)}</td><td class="{dlt_cls(x)}">{num(x, 2, "+" if x > 0 else "")}</td></tr>' for c, _, _, r, x in bot)
        vp.append(f'<div data-j="{k}" style="display:{"grid" if k == "dia" else "none"};grid-template-columns:1fr 1fr;gap:8px"><table class="mini"><thead><tr><th>Puxaram</th><th>Var.</th><th>p.p.</th></tr></thead><tbody>{tr}</tbody></table>'
                  f'<table class="mini"><thead><tr><th>Seguraram</th><th>Var.</th><th>p.p.</th></tr></thead><tbody>{tr2}</tbody></table></div>')
    return (f'<div class="pbox dec"><div style="display:flex;justify-content:space-between;align-items:center;gap:6px"><h2 style="margin:0;white-space:nowrap">Ibovespa · decomposição</h2><div class="janela" style="margin:0">{chips}</div></div>'
            f'<div style="margin-top:4px">{"".join(vs)}</div><div style="margin-top:4px">{"".join(vp)}</div></div>')


def agrupa_empresa(papeis: list) -> list:
    """Soma as classes da mesma empresa (PETR3+PETR4, ELET3+ELET6...) pelo radical de 4 letras: [rótulo, setor, peso, retorno ponderado, contribuição]."""
    g: dict[str, list] = {}
    for cod, setor, peso, r, x in papeis:
        e = g.setdefault(cod[:4], [set(), setor, 0.0, 0.0, 0.0])
        e[0].add(cod[4:]); e[2] += peso; e[3] += peso * r; e[4] += x
    out = []
    for raiz, (cls, setor, peso, wr, x) in g.items():
        rot = raiz + ("+".join(sorted(cls)) if len(cls) > 1 else next(iter(cls)))
        out.append([rot, setor, peso, (wr / peso) if peso else 0.0, x])
    return sorted(out, key=lambda e: -e[4])


LIMIAR_NOME = 0.5      # p.p.: todo nome com contribuição ≥ 0,5 p.p. aparece, mesmo além do mínimo de linhas


def seleciona_nomes(emp: list, minimo: int = 4, maximo: int = 8) -> tuple[list, list, str]:
    """Quem puxou (positivos) e quem segurou (negativos): pelo menos `minimo` linhas, mais todo nome acima do limiar, até `maximo`.
    Devolve também a frase de cobertura: quanto do movimento os nomes mostrados explicam."""
    pos = [e for e in emp if e[4] > 0]
    neg = [e for e in emp if e[4] < 0][::-1]
    def corta(lst):
        n = max(minimo, sum(1 for e in lst if abs(e[4]) >= LIMIAR_NOME))
        return lst[:min(n, maximo)]
    top, bot = corta(pos), corta(neg)
    tp, tn = sum(e[4] for e in pos), sum(e[4] for e in neg)
    sp, sn = sum(e[4] for e in top), sum(e[4] for e in bot)
    cab = (f'Mostrados explicam {num(sp, 2, "+")} de {num(tp, 2, "+")} p.p. que puxaram e {num(sn, 2)} de {num(tn, 2)} p.p. que seguraram'
           f' ({num((abs(sp) + abs(sn)) / (abs(tp) + abs(tn)) * 100, 0) if (tp or tn) else "—"}% do movimento bruto). Classes da mesma empresa somadas.')
    return top, bot, cab


def linha_variaveis_painel(M: dict) -> str:
    """Linha de variáveis-chave do Painel: juro nominal de 10 anos (Tesouro, vértice constante interpolado), Treasury de
    10 anos, Brent à vista e a curva futura do Brent (hoje contra a fotografia mais antiga disponível)."""
    S1, S2, MUT = "var(--s1)", "var(--s2)", "var(--axis)"
    pc = config.DATA / "curva_tesouro.json"
    CT = json.loads(pc.read_text(encoding="utf-8")) if pc.exists() else {}
    prazos = CT.get("prazos_pre") or []
    g_br = ""
    if CT.get("pre") and 10 in prazos:
        i = prazos.index(10) + 1
        pts = [[r[0], r[i]] for r in CT["pre"] if r[i] is not None]
        g_br = svg_linhas("painel-pre10", [("Pré 10 anos", S1, pts)], 2, suf="%", W=400, H=170, ini=5, titulo="Juro nominal 10 anos (Tesouro, vértice constante)")
    else:
        g_br = '<div class="empty small">Curva do Tesouro ausente.</div>'
    tnx = M.get("hist", {}).get("Treasury 10a (%)") or []
    g_us = svg_linhas("painel-tnx", [("Treasury 10a", S1, [[d, v] for d, v in tnx])], 2, suf="%", W=400, H=170, ini=5, titulo="Treasury 10 anos (%)") if tnx else '<div class="empty small">Sem histórico do Treasury: rode a janela diária.</div>'
    br = M.get("hist", {}).get("Brent (US$)") or []
    g_brent = svg_linhas("painel-brent", [("Brent", S1, [[d, v] for d, v in br])], 1, pref="US$ ", W=400, H=170, ini=5, titulo="Brent à vista (US$/bbl)") if br else '<div class="empty small">Sem histórico do Brent.</div>'
    pb = config.DATA / "brent_curva.json"
    BC = json.loads(pb.read_text(encoding="utf-8")) if pb.exists() else {}
    fotos = BC.get("fotos") or {}
    if fotos:
        datas = sorted(fotos)
        hoje, ant = datas[-1], datas[0]
        def pts_de(f):   # [venc, preço(, contratos negociados)] -> ponto com o volume como extra do tooltip
            return [[f"{r[0]}-15", r[1], (r[2] if len(r) > 2 else None)] for r in f]
        series = [(f"curva {hoje[8:]}/{hoje[5:7]}", S1, pts_de(fotos[hoje]))]
        if ant != hoje:
            series.append((f"curva {ant[8:]}/{ant[5:7]}", MUT, pts_de(fotos[ant])))
        spot = br[-1][1] if br else None
        g_curva = svg_linhas("painel-brent-curva", series, 1, pref="US$ ", W=400, H=170, titulo="Curva futura do Brent (US$/bbl; contratos/dia no tooltip)",
                             refs=[("à vista", spot)] if spot else None, extras=["contratos"])
        g_curva = g_curva.replace('<div class="janela" data-for="painel-brent-curva">', '<div class="janela" data-for="painel-brent-curva" style="display:none">')
        g_curva = g_curva.replace('id="painel-brent-curva"', 'id="painel-brent-curva" data-nosel="1"')   # eixo x = vencimentos, não entra na seleção de datas
    else:
        g_curva = '<div class="empty small">Curva do Brent: rode a janela diária.</div>'
    return (f'<div class="pgrid" style="grid-template-columns:1fr 1fr 1fr 1fr;margin-top:10px">'
            + "".join(f'<div class="pbox" style="padding:8px 12px 4px">{g}</div>' for g in (g_br, g_us, g_brent, g_curva)) + '</div>')


def linha_ibov_painel(D: dict, M: dict) -> str:
    """Linha de baixo do Painel: gráfico do Ibovespa (clicável) + decomposição em dois blocos (setores; puxaram/seguraram).
    O clique num ponto do gráfico dispara, no navegador, a decomposição daquela data até hoje (dados embutidos em #ibov-dados)."""
    ib = M.get("hist", {}).get("Ibovespa", [])
    if not D:
        g = svg_linhas("painel-ibov", [("Ibovespa", "var(--s1)", [[d, v] for d, v in ib])], 0, W=800, H=232, ini=2, titulo="Ibovespa") if ib else '<div class="empty small">Sem histórico do Ibovespa.</div>'
        return (f'<div class="pgrid dec" style="grid-template-columns:1.6fr 1fr;margin-top:10px"><div class="pbox" style="padding:8px 12px 4px">{g}</div>'
                f'<div class="pbox"><h2>Ibovespa · decomposição</h2><div class="empty small">Rode <code>python coletar.py --janela diario</code>.</div></div></div>')
    ABREV = {"Consumo não cíclico": "Cons. não cíclico", "Consumo cíclico": "Cons. cíclico", "Utilidade pública": "Utilidade públ.", "Materiais básicos": "Mat. básicos", "Bens industriais": "Bens indust."}
    # dados para o clique: carteira, histórico (com o último ponto intraday), proventos, fotografias, índice, calendário, rebalanceamentos
    C = json.loads((config.DATA / "ibov_comp.json").read_text(encoding="utf-8"))
    hist = C.get("hist", {})
    for cod, q in (C.get("intraday") or {}).items():
        h = hist.get(cod)
        if h and q.get("data") and q["data"] > h[-1][0]:
            hist[cod] = h + [[q["data"], q["preco"]]]
    calend = sorted({d for h in hist.values() for d, _ in h})
    fotos = carrega_fotos()
    rebal = datas_rebalanceamento(calend)
    ini_h = calend[0] if calend else "9999"
    prov_ini = min((p["com"] for ps in C.get("proventos", {}).values() for p in ps if p.get("com")), default="9999")
    dados = {"itens": [{"cod": i["cod"], "setor": i["setor"], "peso": i["peso"], "q": i.get("q"), "classe": i.get("classe", "ON")} for i in C.get("itens", [])],
             "hist": hist, "prov": proventos_completos(C), "prov_ini": prov_ini, "red": C.get("redutor"), "fotos": fotos, "cal": calend, "rebal": rebal,
             "ib": [[d, v] for d, v in ib if d >= ini_h], "unit": {k: 1 for k in UNIT_COMP}, "abrev": ABREV, "setor_ex": getattr(config, "SETOR_EX", {})}
    dados_js = json.dumps(dados, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    # a janela do gráfico (1 d … máx) é a janela da decomposição; a área sem fechamentos dos papéis (antes de ini_h) fica sombreada
    g = svg_linhas("painel-ibov", [("Ibovespa", "var(--s1)", [[d, v] for d, v in ib])], 0, W=800, H=232, ini=2, curtas=True, sombra_desde=ini_h,
                   titulo="Ibovespa · a janela escolhida (ou o ponto clicado) define a decomposição ao lado")
    return (f'<div class="pgrid dec" style="grid-template-columns:2fr 1fr 1fr">'
            f'<div class="pbox" style="padding:8px 12px 4px">{g}</div>'
            f'<div class="pbox" data-slot="setores"><h2 style="margin:0 0 4px">Decomposição por setor</h2><div class="empty small">calculando…</div></div>'
            f'<div class="pbox" data-slot="papeis"><h2>Quem puxou, quem segurou</h2><div class="empty small">calculando…</div></div>'
            f'<script type="application/json" id="ibov-dados">{dados_js}</script></div>')


def slide_ibov_decomp(D: dict) -> tuple[str, str] | None:
    if not D:
        return None
    chips = "".join(f'<button data-j="{k}"{" class=on" if k == "21d" else ""}>{r}</button>' for k, r, _ in JANELAS_IBOV)
    blocos = []
    for k, _, _ in JANELAS_IBOV:
        j = D["jan"].get(k)
        if not j:
            continue
        trs = "".join(f'<tr><td class="tk">{s}</td><td>{num(w, 1, suf="%")}</td><td class="{dlt_cls(r)}">{pct(r)}</td><td class="{dlt_cls(c)}">{num(c, 2, "+" if c > 0 else "")}</td></tr>' for s, w, c, r in j["setores"])
        tab_s = f'<table class="compact"><thead><tr><th>Setor</th><th>Peso</th><th>Retorno</th><th>Contrib. p.p.</th></tr></thead><tbody>{trs}</tbody></table>'
        top, bot = j["papeis"][:12], j["papeis"][-12:][::-1]
        def tab(lst, tit):
            tr = "".join(f'<tr><td class="tk">{c}<small>{s[:14]}</small></td><td>{num(w, 2, suf="%")}</td><td class="{dlt_cls(r)}">{pct(r)}</td><td class="{dlt_cls(x)}">{num(x, 2, "+" if x > 0 else "")}</td></tr>' for c, s, w, r, x in lst)
            return f'<table class="compact"><thead><tr><th>{tit}</th><th>Peso</th><th>Var.</th><th>p.p.</th></tr></thead><tbody>{tr}</tbody></table>'
        cab = (f'<p class="note" style="margin:0 0 8px">Ibovespa {num(j["indice"], 2, "+" if j["indice"] and j["indice"] > 0 else "", "%") if j["indice"] is not None else "—"} de {j["t0"][8:]}/{j["t0"][5:7]} a {j["t"][8:]}/{j["t"][5:7]}; soma das contribuições {num(j["soma"], 2, "+" if j["soma"] > 0 else "", " p.p.")}; '
               f'erro {num(j["erro"], 2, "+" if j["erro"] and j["erro"] > 0 else "", " p.p.") if j["erro"] is not None else "—"}. Método: {j["metodo"]}.</p>')
        blocos.append(f'<div data-j="{k}"{"" if k == "21d" else " style=display:none"}>{cab}<div class="grid3"><div>{tab_s}</div><div>{tab(top, "Maiores contribuições")}</div><div>{tab(bot, "Menores contribuições")}</div></div></div>')
    corpo = f'<div class="dec"><div class="janela" style="justify-content:flex-start;margin-bottom:8px">{chips}</div>{"".join(blocos)}</div>'
    return slide("Macro", "ibov-decomp", "Ibovespa · decomposição",
                 corpo, "Quem puxou e quem segurou o índice. Identidade do índice: Σ quantidade teórica × preço ÷ redutor; a quantidade já reinveste o dividendo, então a soma bate com o índice.",
                 f"B3: carteira teórica de {D.get('data', '—')} ({sum(1 for _ in D['jan'].get('dia', {}).get('papeis', []))} papéis, {D.get('fotos', 0)} fotografias diárias), fechamentos oficiais (COTAHIST) e proventos (canal de listadas); último ponto intraday do Yahoo. Quantidade na data inicial: fotografia do dia ou reconstruída dos proventos (units aproximadas). Janela que cruza rebalanceamento sem fotografia: peso atual × retorno.")


def dy_ibov() -> dict:
    """Lê data/ibov_dy.json (série exata diária, papéis de hoje, histórico mensal aproximado)."""
    p = config.DATA / "ibov_dy.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def analise_volume(M: dict) -> dict | None:
    """Volume financeiro dos papéis do Ibovespa (COTAHIST): total diário e média de 21 pregões, e por papel a razão
    volume do dia ÷ média de 21 pregões (excluindo o dia). Base do slide de volume e do sinal no Painel."""
    from fontes import b3 as _b3
    p = config.DATA / "ibov_comp.json"
    if not p.exists():
        return None
    C = json.loads(p.read_text(encoding="utf-8"))
    itens = C.get("itens", [])
    if not itens:
        return None
    S = _b3.series_todas(("fechamento", "quantidade", "volume"), desde="2019-01-01")
    cods = [i["cod"] for i in itens]
    setor = {i["cod"]: i["setor"] for i in itens}
    # total diário dos papéis da carteira atual (proxy do mercado: ~85% do volume de ações da B3)
    tot: dict[str, float] = {}
    for c in cods:
        for d, _, _, v in S.get(c, []):
            tot[d] = tot.get(d, 0.0) + v
    dias = sorted(tot)
    if len(dias) < 30:
        return None
    serie_tot = [[d, tot[d] / 1e9] for d in dias]                     # R$ bi
    mm21 = []
    for i in range(len(serie_tot)):
        jan = [v for _, v in serie_tot[max(0, i - 20):i + 1]]
        mm21.append([serie_tot[i][0], sum(jan) / len(jan)])
    hoje = dias[-1]
    med21 = sum(tot[d] for d in dias[-22:-1]) / 21 / 1e9
    med63 = sum(tot[d] for d in dias[-64:-1]) / 63 / 1e9
    # por papel
    papeis = []
    for c in cods:
        s = S.get(c, [])
        if len(s) < 25 or s[-1][0] != hoje:
            continue
        v_hoje = s[-1][3]
        base = [x[3] for x in s[-22:-1]]
        med = sum(base) / len(base) if base else 0
        var = (s[-1][1] / s[-2][1] - 1) if s[-2][1] else None
        papeis.append({"cod": c, "setor": setor.get(c, ""), "vol": v_hoje / 1e6, "med21": med / 1e6, "razao": (v_hoje / med) if med else None, "var": var})
    papeis.sort(key=lambda r: -(r["razao"] or 0))
    top5 = sorted(papeis, key=lambda r: -r["vol"])[:5]
    conc = sum(r["vol"] for r in top5) / (tot[hoje] / 1e6) if tot.get(hoje) else None
    return {"data": hoje, "hoje": tot[hoje] / 1e9, "med21": med21, "med63": med63, "razao": (tot[hoje] / 1e9 / med21) if med21 else None,
            "serie": serie_tot, "mm21": mm21, "papeis": papeis, "top5": top5, "conc5": conc,
            "anormais": [r for r in papeis if (r["razao"] or 0) >= 1.5], "fracos": [r for r in papeis if r["razao"] is not None and r["razao"] <= 0.5]}


def slide_volume(M: dict) -> tuple[str, str] | None:
    """Volume: total do Ibovespa contra a média, papéis com volume anormal, concentração."""
    V = analise_volume(M)
    if not V:
        return None
    S1, S2, MUT = "var(--s1)", "var(--s2)", "var(--axis)"
    tiles = "".join(f'<div class="tile"><div class="l">{l}</div><div class="v">{v}</div><div class="d">{s}</div></div>' for l, v, s in (
        (f"Volume hoje ({V['data'][8:]}/{V['data'][5:7]})", f"R$ {num(V['hoje'], 1)} bi", f"papéis do Ibovespa, {len(V['papeis'])} negociados"),
        ("× média 21 pregões", num(V["razao"], 2, suf="x"), f"média R$ {num(V['med21'], 1)} bi · 63 pregões R$ {num(V['med63'], 1)} bi"),
        ("Papéis com volume anormal", str(len(V["anormais"])), "≥ 1,5× a própria média de 21 pregões"),
        ("Concentração", num((V["conc5"] or 0) * 100, 0, suf="%"), "5 papéis mais negociados no volume do dia")))
    g = svg_linhas("volume-ibov", [("Volume diário", MUT, [[d, v] for d, v in V["serie"]]), ("Média 21 pregões", S1, [[d, v] for d, v in V["mm21"]])],
                   1, pref="R$ ", suf=" bi", W=1080, H=165, ini=2, titulo="Volume financeiro diário dos papéis do Ibovespa (R$ bi) e média móvel de 21 pregões")
    def tab(lst, tit):
        tr = "".join(f'<tr><td class="tk">{r["cod"]}<small>{r["setor"][:14]}</small></td><td>{num(r["vol"], 0)}</td><td>{num(r["razao"], 1, suf="x")}</td><td class="{dlt_cls(r["var"])}">{pct(r["var"])}</td></tr>' for r in lst)
        return f'<table class="mini" style="width:100%"><thead><tr><th>{tit}</th><th>R$ mi</th><th>× méd. 21</th><th>Dia</th></tr></thead><tbody>{tr}</tbody></table>'
    corpo = (f'<div class="tiles strip" style="grid-template-columns:repeat(4,1fr);margin-bottom:10px">{tiles}</div>'
             f'<div class="panel" style="padding:10px 14px 4px"><div class="legend"><span><i style="background:{MUT}"></i>volume diário</span><span><i style="background:{S1}"></i>média 21 pregões</span></div>{g}</div>'
             f'<div class="grid3" style="margin-top:10px;align-items:start"><div><h2 style="font-size:13px;margin:0 0 4px">Volume mais forte que o normal</h2>{tab(V["papeis"][:7], "Papel")}</div>'
             f'<div><h2 style="font-size:13px;margin:0 0 4px">Volume mais fraco que o normal</h2>{tab(V["papeis"][-7:][::-1], "Papel")}</div>'
             f'<div><h2 style="font-size:13px;margin:0 0 4px">Mais negociados hoje</h2>{tab(V["top5"] + sorted(V["papeis"], key=lambda r: -r["vol"])[5:7], "Papel")}</div></div>')
    return slide("Macro", "volume", "Volume · quem está sendo negociado", corpo,
                 "O mercado está mais líquido ou mais parado que o normal, e em quais papéis o volume destoa da média própria.",
                 "B3 COTAHIST (volume financeiro do mercado à vista, tipo 010) dos papéis da carteira atual do Ibovespa, desde 2019. A média de 21 pregões exclui o dia. "
                 "O total é dos constituintes, não de toda a B3; a B3 divulga o total do mercado no Boletim Diário, sem histórico aberto.")


def fluxo_tiles(M: dict) -> tuple[str, str] | None:
    """Tiles do fluxo por tipo de investidor (saldo 1 d, 5 d, 21 d, mês, ano, 12 m), usados no Painel e no slide próprio.
    Devolve (html, data da última referência) ou None se a série do BDI não existe."""
    from fontes import b3_bdi
    if not b3_bdi.ARQ.exists():
        return None
    O = json.loads(b3_bdi.ARQ.read_text(encoding="utf-8"))
    serie = b3_bdi.serie_diaria(O)
    if not serie:
        return None
    S1, S2, S3, S4 = "var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)"
    tipos = [("estrangeiro", "Estrangeiro", S1), ("institucional", "Institucional", S2), ("pessoa física", "Pessoa física", S3), ("inst. financeira", "Inst. financeira", S4)]
    ult = serie[-1]["data"]
    acum = O["diario"][ult]
    ano = ult[:4]
    def saldo_mes(t):
        return (acum[t][0] - acum[t][1]) / 1000.0 if t in acum else None
    def soma(t, desde):
        return sum(r.get(t) or 0 for r in serie if r["data"] >= desde)
    def soma_n(t, n):
        return sum(r.get(t) or 0 for r in serie[-n:])
    def sfmt(v, dec=0, suf=" mi"):
        return num(v, dec, "+" if v > 0 else "", suf)
    def it(rot, v):
        return f'<span class="it">{rot} {v}</span>'                   # rótulo + valor nunca se separam na quebra de linha
    tiles = "".join(f'<div class="tile"><div class="l">{lab} · 1 d / 5 d / 21 d</div><div class="v {dlt_cls(soma_n(t, 1))}">{sfmt(soma_n(t, 1))}</div>'
                    f'<div class="d">{it("5 d", sfmt(soma_n(t, 5)))} · {it("21 d", sfmt(soma_n(t, 21)))} · {it("mês", sfmt(saldo_mes(t) or 0))} · '
                    f'{it("ano", sfmt(soma(t, ano + "-01-01") / 1000, 1, " bi"))} · {it("12 m", sfmt(soma(t, serie[0]["data"]) / 1000, 1, " bi"))}</div></div>'
                    for t, lab, _ in tipos)
    return tiles, ult


def slide_fluxo_investidores(M: dict) -> tuple[str, str] | None:
    """Fluxo por tipo de investidor no mercado de ações (B3/BDI): saldo diário, acumulado no mês e participação no volume."""
    from fontes import b3_bdi
    if not b3_bdi.ARQ.exists():
        return None
    O = json.loads(b3_bdi.ARQ.read_text(encoding="utf-8"))
    serie = b3_bdi.serie_diaria(O)
    if not serie:
        return None
    S1, S2, S3, S4 = "var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)"
    tipos = [("estrangeiro", "Estrangeiro", S1), ("institucional", "Institucional", S2), ("pessoa física", "Pessoa física", S3), ("inst. financeira", "Inst. financeira", S4)]
    ult = serie[-1]["data"]
    mes = ult[:7]
    acum = O["diario"][ult]                                   # acumulado do mês na última data de referência
    tot_c = sum(v[0] for v in acum.values()); tot_v = sum(v[1] for v in acum.values())
    def saldo_mes(t):
        return (acum[t][0] - acum[t][1]) / 1000.0 if t in acum else None
    ano = ult[:4]
    def soma(t, desde):
        return sum(r.get(t) or 0 for r in serie if r["data"] >= desde)
    def soma_n(t, n):
        return sum(r.get(t) or 0 for r in serie[-n:])
    tiles, _ = fluxo_tiles(M)
    # saldo diário do estrangeiro (colunas, últimos 40 pregões) e acumulado por tipo desde o início da série (linhas)
    dias = serie[-40:]
    g_dia = svg_colunas(f"Estrangeiro · saldo diário no mercado de ações (R$ mi), últimos 40 pregões", [r["data"][8:] + "/" + r["data"][5:7] if i % 4 == 0 else "" for i, r in enumerate(dias)],
                        [r.get("estrangeiro") for r in dias], [S1 if (r.get("estrangeiro") or 0) >= 0 else "var(--dn)" for r in dias], 0, "", W=560, H=175, rotulos=False, passo_rotulo_x=1)
    series = []
    for t, lab, cor in tipos:
        acc, pts = 0.0, []
        for r in serie:
            acc += r.get(t) or 0
            pts.append([r["data"], acc / 1000])
        if pts:
            series.append((lab, cor, pts))
    g_acum = svg_linhas("fluxo-acum", series, 1, suf=" bi", W=520, H=175, titulo=f"Acumulado por tipo desde {serie[0]['data'][8:]}/{serie[0]['data'][5:7]}/{serie[0]['data'][2:4]} (R$ bi)")
    leg = "".join(f'<span><i style="background:{c}"></i>{l}</span>' for _, l, c in tipos)
    tr = "".join(f'<tr><td class="tk">{lab}</td><td>{num(acum[t][0] / 1000, 0)}</td><td>{num(acum[t][1] / 1000, 0)}</td><td class="{dlt_cls(saldo_mes(t))}">{num(saldo_mes(t), 0, "+" if (saldo_mes(t) or 0) > 0 else "")}</td>'
                 f'<td>{num((acum[t][0] + acum[t][1]) / (tot_c + tot_v) * 100, 1, suf="%") if (tot_c + tot_v) else "—"}</td></tr>' for t, lab, _ in tipos + [("outros", "Outros", "")] if t in acum)
    tab = f'<table class="mini" style="width:100%"><thead><tr><th>Investidor</th><th>Compras R$ mi</th><th>Vendas R$ mi</th><th>Saldo</th><th>% do volume</th></tr></thead><tbody>{tr}</tbody></table>'
    # participação mensal por segmento (último mês fechado)
    men = O.get("mensal") or {}
    tab_m = ""
    if men:
        km = sorted(men)[-1]; m = men[km]
        segs = ["À vista", "A termo", "Opções", "Exercícios de opções", "Blocos", "Total geral"]
        segs = [s for s in segs if any(s in v for v in m.values())]
        trm = "".join(f'<tr><td class="tk">{lab}</td>' + "".join(f'<td>{num(m[t][s][1], 0, suf="%") if s in m.get(t, {}) else "—"}</td>' for s in segs) + "</tr>" for t, lab, _ in tipos + [("outros", "Outros", "")] if t in m)
        tab_m = (f'<h2 style="font-size:13px;margin:10px 0 4px">Participação no volume por segmento, {km[5:]}/{km[:4]} (compras + vendas, %)</h2>'
                 f'<table class="mini" style="width:100%"><thead><tr><th>Investidor</th>{"".join(f"<th>{s}</th>" for s in segs)}</tr></thead><tbody>{trm}</tbody></table>')
    corpo = (f'<div class="tiles strip" style="grid-template-columns:repeat(4,1fr);margin-bottom:10px">{tiles}</div>'
             f'<div class="grid2" style="grid-template-columns:1.1fr 1fr;align-items:start"><div>{g_dia}</div><div><div class="legend" style="margin-bottom:4px">{leg}</div>{g_acum}</div></div>'
             f'<div class="grid2" style="grid-template-columns:1fr 1.2fr;align-items:start;margin-top:10px"><div><h2 style="font-size:13px;margin:0 0 4px">Acumulado do mês até {ult[8:]}/{ult[5:7]} (R$ mi)</h2>{tab}</div><div>{tab_m}</div></div>')
    return slide("Macro", "fluxo-investidores", "Fluxo por tipo de investidor", corpo,
                 "Quem está comprando e quem está vendendo ações na B3: saldo diário, acumulado do mês e fatia de cada investidor no volume. Só o mercado de ações; futuros de índice não entram.",
                 f"B3, Boletim Diário (tabela 'Participação dos investidores'): compras e vendas por tipo, acumuladas no mês até D-2; o saldo diário é a diferença entre dois acumulados. "
                 f"A B3 só serve os últimos ~20 dias: a série é acumulada aqui desde {sorted(O['diario'])[0][8:]}/{sorted(O['diario'])[0][5:7]}/{sorted(O['diario'])[0][:4]}; antes disso, histórico compilado pelo Dados de Mercado a partir da mesma tabela (bate ao centavo na sobreposição). Mensal por segmento: tabela 'Participação dos investidores mensal'.")


def slide_ibov_dy(M: dict) -> tuple[str, str] | None:
    """Dividend yield 12 m do Ibovespa: exato hoje (B3), histórico aproximado (Yahoo) e comparação com o juro real."""
    D = dy_ibov()
    ex, apx = D.get("exato") or [], D.get("aprox_mensal") or []
    if not ex and not apx:
        return None
    S1, S2, MUT = "var(--s1)", "var(--s2)", "var(--axis)"
    dy = ex[-1][1] if ex else None
    n35 = M.get("ntnb_2035", [])
    ntnb = n35[-1][1] if n35 else None
    apx_v = [v for _, v, *_ in apx]
    med = sum(apx_v) / len(apx_v) if apx_v else None
    tiles = "".join(f'<div class="tile"><div class="l">{l}</div><div class="v">{v}</div><div class="d">{s}</div></div>' for l, v, s in (
        ("DY 12 m hoje (exato)", num(dy, 2, suf="%"), f'B3, data-com até {D.get("data", "—")}'),
        ("Média desde " + (apx[0][0][:4] if apx else "—"), num(med, 2, suf="%"), f'mín {num(min(apx_v), 2, suf="%") if apx_v else "—"} · máx {num(max(apx_v), 2, suf="%") if apx_v else "—"} (aprox.)'),
        ("NTN-B 2035 (real)", num(ntnb, 2, suf="%"), "Tesouro Direto"),
        ("DY − juro real", num(dy - ntnb, 2, "+" if dy and ntnb and dy > ntnb else "", " p.p.") if (dy is not None and ntnb is not None) else "—", "caixa da bolsa acima da NTN-B")))
    series = [("DY 12 m aprox. (Yahoo, carteira atual)", S2, [[d, v] for d, v, *_ in apx])]
    if len(ex) > 1:
        series.append(("DY 12 m exato (B3)", S1, [[d, v] for d, v in ex]))
    if n35:
        d0 = apx[0][0] if apx else ex[0][0]
        series.append(("NTN-B 2035", MUT, [[d, v] for d, v in n35 if d >= d0]))
    g = svg_linhas("ibov-dy", series, 2, suf="%", W=1080, H=190, titulo="Dividend yield 12 m do Ibovespa × juro real")
    leg = f'<div class="legend"><span><i style="background:{S2}"></i>DY aprox. mensal</span><span><i style="background:{S1}"></i>DY exato diário (acumula a partir de {ex[0][0][8:] + "/" + ex[0][0][5:7] + "/" + ex[0][0][:4] if ex else "—"})</span><span><i style="background:{MUT}"></i>NTN-B 2035</span></div>'
    pap = D.get("papeis") or []
    tr = "".join(f'<tr><td class="tk">{c}<small>{s[:14]}</small></td><td>{num(w, 2, suf="%")}</td><td>{num(y, 2, suf="%")}</td><td>{num(x, 2)}</td></tr>' for c, s, w, y, x in pap[:7])
    tab = f'<table class="mini" style="width:100%"><thead><tr><th>Papel</th><th>Peso</th><th>DY 12 m</th><th>p.p. do índice</th></tr></thead><tbody>{tr}</tbody></table>'
    set_: dict[str, float] = {}
    for c, s, w, y, x in pap:
        set_[s] = set_.get(s, 0.0) + (x or 0)
    g_set = svg_hbar(sorted(set_.items(), key=lambda kv: -kv[1]), W=470, RH=12, ML=120, MR=50, dec=2, suf=" p.p.")
    corpo = (f'<div class="tiles strip" style="grid-template-columns:repeat(4,1fr);margin-bottom:10px">{tiles}</div>'
             f'<div class="panel" style="padding:12px 16px 6px">{leg}{g}</div>'
             f'<div class="grid2" style="grid-template-columns:1fr 1.1fr;align-items:start;margin-top:10px">'
             f'<div><h2 style="font-size:13px;margin:0 0 4px">Quem paga o DY, por setor (p.p. do índice)</h2>{g_set}</div><div><h2 style="font-size:13px;margin:0 0 4px">Maiores contribuições ao DY</h2>{tab}</div></div>')
    return slide("Macro", "ibov-dy", "Ibovespa · dividend yield", corpo,
                 "Quanto o índice paga em caixa em 12 meses, quem paga, e como se compara com o juro real.",
                 "Exato: B3, carteira teórica (quantidade × proventos com data-com em 12 meses ÷ quantidade × preço), canal de listadas, dividendos e JCP brutos. "
                 "Aproximado: Yahoo (dividendos e fechamentos de cada papel), carteira e quantidades de hoje aplicadas ao passado; JCP e dividendos misturados; papéis sem 12 meses de história ficam de fora do mês. Sem recompras.")


def _tri_data(t: str) -> str:
    """'4T26' -> '2026-12-31' (fim do trimestre)."""
    q, yy = int(t[0]), int(t[2:])
    m = q * 3
    return f"{2000 + yy}-{m:02d}-{31 if m in (3, 12) else 30}"


def bloco_rpm_painel() -> str:
    """Bloco do Painel: cenário de referência do Copom, RPM a RPM (macro_bcb/rpm_hist.json) — trajetória do IPCA projetada
    em cada relatório e a tabela de como os condicionantes e os horizontes mudaram de um RPM para o outro."""
    p = config.RAIZ / "macro_bcb" / "rpm_hist.json"
    if not p.exists():
        return ""
    todos = json.loads(p.read_text(encoding="utf-8")).get("rpms", [])
    rpms = todos[-4:]                                    # o Painel mostra os 4 últimos; o histórico completo fica no slide rpm-historico
    if not rpms:
        return ""
    cores = ["var(--axis)", "var(--s3)", "var(--s2)", "var(--s1)"][-len(rpms):]
    series = [(r["data"], c, [[_tri_data(t), v] for t, v in r["ipca"] if v is not None]) for r, c in zip(rpms, cores)]
    g = svg_linhas("painel-rpm", series, 1, suf="%", W=440, H=190, refs=[("meta 3%", 3.0), ("teto 4,5%", 4.5)], gap=120,
                   titulo="IPCA acumulado em 4 trimestres projetado em cada RPM (%)")
    g = g.replace('<div class="janela" data-for="painel-rpm">', '<div class="janela" data-for="painel-rpm" style="display:none">').replace('id="painel-rpm"', 'id="painel-rpm" data-nosel="1"')
    leg = "".join(f'<span><i style="background:{c}"></i>{r["data"]}</span>' for r, c in zip(rpms, cores))
    ult, ant = rpms[-1], rpms[-2] if len(rpms) > 1 else None
    def v_ipca(r, t):
        return next((v for k, v in r["ipca"] if k == t), None)
    linhas = []
    def linha(rot, vals, dec=1, suf="%", pp=True):
        cels = "".join(f"<td>{num(v, dec, suf=suf) if v is not None else '—'}</td>" for v in vals)
        d = (vals[-1] - vals[-2]) if len(vals) > 1 and vals[-1] is not None and vals[-2] is not None else None
        dtxt = num(d, dec, "+" if d and d > 0 else "", " p.p." if pp else "") if d is not None else "—"
        linhas.append(f'<tr><td class="tk">{rot}</td>{cels}<td class="{dlt_cls(d)}">{dtxt}</td></tr>')
    horiz = [t for t in ("4T26", "4T27", "1T28", "4T28") if v_ipca(ult, t) is not None]
    for t in horiz:
        linha(f"IPCA {t}" + (' <small style="font-size:10.5px">(horizonte relevante)</small>' if t == "1T28" else ""), [v_ipca(r, t) for r in rpms])
    linha("Prob. IPCA acima do teto em 4T26", [(r.get("prob_teto") or {}).get("2026", r.get("prob_teto_4T26")) for r in rpms], 0, "%", pp=True)
    for ano in ("2026", "2027"):
        linha(f"Selic fim-{ano[2:]} usada (Focus)", [(r.get("selic_fim") or {}).get(ano) for r in rpms], 2, "%")
    linha("Câmbio de partida (R$/US$)", [r.get("cambio_partida") for r in rpms], 2, "", pp=False)
    linha("PIB 2026", [(r.get("pib") or {}).get("2026") for r in rpms], 1, "%")
    cab = "".join(f"<th>{r['data']}</th>" for r in rpms)
    tab = f'<table class="mini" style="width:100%"><thead><tr><th>Cenário de referência</th>{cab}<th>Δ último</th></tr></thead><tbody>{"".join(linhas)}</tbody></table>'
    nota = f'<div class="mut" style="font-size:10.5px;margin-top:4px">{ult.get("nota", "")} Histórico desde {todos[0]["data"]} em <a href="#rpm-historico">BCB · cenário RPM a RPM</a>.</div>'
    return (f'<div class="pbox"><h2>BCB · cenário de referência, RPM a RPM (até {ult["data"]})</h2>'
            f'<div class="legend" style="margin-bottom:2px">{leg}</div>{g}{tab}{nota}</div>')


def slide_rpm_historico(M: dict, sgs: dict) -> tuple[str, str] | None:
    """Cenário de referência do Copom desde jun/22: como a projeção para o 4T de cada ano andou a cada RPM, contra o realizado;
    probabilidade de estourar o teto por ano; e o erro por antecedência (o que se aprende sobre o viés do BCB)."""
    p = config.RAIZ / "macro_bcb" / "rpm_hist.json"
    if not p.exists():
        return None
    rpms = json.loads(p.read_text(encoding="utf-8")).get("rpms", [])
    if len(rpms) < 4:
        return None
    S1, S2, S3, S4, MUT = "var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)", "var(--axis)"
    def v(r, t):
        return next((x for k, x in r["ipca"] if k == t), None)
    # realizado: IPCA dez/dez por ano (SGS 433 mensal)
    ipca_m = (sgs.get("433") or {}).get("serie") or []
    real = {}
    for ano in range(2022, date.today().year + 1):
        ms = [x for d, x in ipca_m if d.startswith(str(ano))]
        if len(ms) == 12:
            acc = 1.0
            for x in ms:
                acc *= 1 + x / 100
            real[str(ano)] = (acc - 1) * 100
    # gráfico A: projeção para o 4T de cada ano, RPM a RPM
    anos = [a for a in range(2022, 2030) if sum(1 for r in rpms if v(r, f"4T{str(a)[2:]}") is not None) >= 3]
    pal = [S1, S2, S3, S4, MUT, "var(--mut)", "var(--ink)"]
    def serie_ano(a, cor):
        return (f"4T{str(a)[2:]}" + (f" (real. {num(real[str(a)], 1)}%)" if str(a) in real else ""), cor, [[r["corte"], v(r, f"4T{str(a)[2:]}")] for r in rpms if v(r, f"4T{str(a)[2:]}") is not None])
    fechados = [a for a in anos if str(a) in real]; abertos = [a for a in anos if str(a) not in real]
    def legenda(series):
        return '<div class="legend" style="margin:0 0 2px">' + "".join(f'<span><i style="background:{c}"></i>{n}</span>' for n, c, _ in series) + "</div>"
    sA = [serie_ano(a, pal[i % len(pal)]) for i, a in enumerate(fechados)]
    sB = [serie_ano(a, pal[i % len(pal)]) for i, a in enumerate(abertos)]
    gA = (legenda(sA) + svg_linhas("rpm-hist-fechados", sA, 1, suf="%", W=620, H=280, gap=130, refs=[("teto 4,5%", 4.5), ("meta 3%", 3.0)],
                                   titulo="Anos já fechados: projeção do BCB para o 4T a cada relatório (%)")) if sA else ""
    gB = (legenda(sB) + svg_linhas("rpm-hist-abertos", sB, 1, suf="%", W=620, H=280, gap=130, refs=[("teto 4,5%", 4.5), ("meta 3%", 3.0)],
                                   titulo="Anos em aberto: projeção para o 4T a cada relatório (%)")) if sB else ""
    # gráfico C: probabilidade de o IPCA do ano superar o teto, por ano, RPM a RPM
    anos_p = sorted({a for r in rpms for a in (r.get("prob_teto") or {})})
    sp = [(f"IPCA {a}", pal[i % len(pal)], [[r["corte"], r["prob_teto"][a]] for r in rpms if (r.get("prob_teto") or {}).get(a) is not None]) for i, a in enumerate(anos_p)]
    sp = [s for s in sp if len(s[2]) >= 2]
    gC = (legenda(sp) + svg_linhas("rpm-hist-prob", sp, 0, suf="%", W=620, H=250, gap=130, titulo="Probabilidade estimada pelo BCB de o IPCA do ano superar o teto (%)")) if sp else ""
    # tabela: erro por antecedência (projeção feita N trimestres antes do fim do ano vs realizado)
    idx = {r["data"]: r for r in rpms}
    def proj(rot, a):
        r = idx.get(rot)
        return v(r, f"4T{str(a)[2:]}") if r else None
    tr, E = [], []
    for a in [a for a in anos if str(a) in real] + abertos:
        s = str(a)[2:]
        cols = [proj(f"dez/{int(s) - 2:02d}", a), proj(f"dez/{int(s) - 1:02d}", a), proj(f"jun/{s}", a), proj(f"set/{s}", a), proj(f"dez/{s}", a)]
        rv = real.get(str(a))
        err = (cols[1] - rv) if (cols[1] is not None and rv is not None) else None
        if err is not None:
            E.append(err)
        tr.append(f'<tr><td class="tk">{a}</td>' + "".join(f"<td>{num(c, 1, suf='%') if c is not None else '—'}</td>" for c in cols)
                  + f'<td style="font-weight:600">{num(rv, 1, suf="%") if rv is not None else "—"}</td><td class="{dlt_cls(err)}">{num(err, 1, "+" if err and err > 0 else "", " p.p.") if err is not None else "—"}</td></tr>')
    tab = ('<table class="mini"><thead><tr><th>Ano</th><th>8 tri antes</th><th>4 tri antes</th><th>2 tri</th><th>1 tri</th><th>no fim</th><th>Realizado</th><th>Erro a 4 tri</th></tr></thead>'
           f'<tbody>{"".join(tr)}</tbody></table>')
    resumo = ""
    if E:
        vies = sum(E) / len(E); mae = sum(abs(e) for e in E) / len(E)
        resumo = f'<p class="note" style="margin:6px 0 0">Com 4 trimestres de antecedência, o cenário de referência errou em média {num(mae, 1)} p.p. (viés {num(vies, 1, "+" if vies > 0 else "")} p.p.: {"subestimou" if vies < 0 else "superestimou"} a inflação) nos anos fechados. Compare com o Focus a 12 meses no slide de assertividade.</p>'
    corpo = (f'<div class="grid2">{"<div>" + gA + "</div>" if gA else ""}{"<div>" + gB + "</div>" if gB else ""}</div>'
             f'<div class="grid2" style="margin-top:10px;align-items:start"><div>{gC}</div><div><h2 style="font-size:13px;margin:0 0 4px">Projeção para o 4T de cada ano por antecedência (RPM de dezembro, junho e setembro)</h2>{tab}{resumo}</div></div>')
    return slide("Macro", "rpm-historico", "BCB · cenário RPM a RPM",
                 corpo, f"Desde {rpms[0]['data']}: como a projeção do Copom para o fim de cada ano foi mudando de relatório em relatório, e quanto errou. Referência para ler o relatório novo.",
                 f"BCB, Relatórios de Inflação/Política Monetária ({rpms[0]['data']} a {rpms[-1]['data']}), Tabela 2.2.1 (cenário com Selic do Focus e câmbio PPC, IPCA acumulado em 4 trimestres) e probabilidades do capítulo 2. Realizado: IPCA dez/dez (SGS 433). Arquivo manual macro_bcb/rpm_hist.json.")


def slide_painel(M: dict, sgs: dict, mercado_micro: dict, minhas: list[dict], cons: list[dict], est: dict) -> tuple[str, str]:
    """Painel de uma tela: mercado, macro (realizado × Focus × BCB), sinais, opinião, cobertura, movimentos, sparklines."""
    from fontes import b3 as _b3
    S1, S2 = "var(--s1)", "var(--s2)"
    a0, a1 = M.get("anos", [date.today().year, date.today().year + 1])
    focus, mk = M.get("focus", {}), M.get("mercado", {})
    hoje = date.today().isoformat()
    def foc(ind, ano):
        return focus.get(ind, {}).get(str(ano)) or []
    def delta(s, dias):
        if not s:
            return None
        alvo = date.fromisoformat(s[-1][0]).toordinal() - dias
        base = next((r[1] for r in reversed(s) if date.fromisoformat(r[0]).toordinal() <= alvo), None)
        return s[-1][1] - base if base is not None else None
    def ult(cod):
        s = sgs.get(str(cod), {}).get("serie") or []
        return s[-1] if s else None
    ipca_m = sgs.get("433", {}).get("serie") or []
    ipca12 = None
    if len(ipca_m) >= 12:
        acc = 1.0
        for _, v in ipca_m[-12:]:
            acc *= 1 + v / 100
        ipca12 = (acc - 1) * 100
    selic, cambio, pib = ult(432), ult(1), ult(7326)
    n35 = M.get("ntnb_2035", []); ntnb35 = n35[-1][1] if n35 else None
    prpm = config.RAIZ / "macro_bcb" / "rpm.json"
    R = json.loads(prpm.read_text(encoding="utf-8")) if prpm.exists() else {}
    bcb = {(c["indicador"], c["horizonte"][:4]): c for c in R.get("comparaveis", [])}
    def bcb_v(ind, ano):
        for (i, h), c in bcb.items():
            if i.startswith(ind) and h.startswith(str(ano)):
                return c["bcb"]
        return None

    # --- tabela de mercado: último valor e variações 1 d / 5 d / MTD / YTD / 12 m (preços em %, juros em p.p.)
    dyx = (dy_ibov().get("exato") or [])
    hist = M.get("hist", {})
    pc = config.DATA / "curva_tesouro.json"
    CT = json.loads(pc.read_text(encoding="utf-8")) if pc.exists() else {}
    pre10 = [[r[0], r[CT["prazos_pre"].index(10) + 1]] for r in CT.get("pre", []) if 10 in CT.get("prazos_pre", []) and r[CT["prazos_pre"].index(10) + 1] is not None]
    def variacoes(serie, ultimo=None, taxa=False):
        """[1 d, 5 d, MTD, YTD, 12 m] contra fechamentos anteriores: % (preço) ou p.p. (taxa)."""
        s = [[d, v] for d, v in serie if v is not None]
        if not s:
            return [None] * 5, None
        if ultimo and ultimo[0] > s[-1][0]:
            s = s + [list(ultimo)]
        d, v = s[-1]
        def em(pred):
            return next((x[1] for x in reversed(s[:-1]) if pred(x[0])), None)
        ano, mes = d[:4], d[:7]
        bases = [s[-2][1] if len(s) > 1 else None, s[-6][1] if len(s) > 5 else None, em(lambda x: x[:7] < mes), em(lambda x: x[:4] < ano),
                 em(lambda x: x <= (date.fromisoformat(d) - timedelta(days=365)).isoformat())]
        out = []
        for b in bases:
            if b is None:
                out.append(None)
            else:
                out.append((v - b) if taxa else (v / b - 1) * 100)
        return out, v
    linhas_m = []
    def linha(nome, serie, ultimo=None, taxa=False, dec=2, suf="", obs="", var1d=None, svg=""):
        """Uma linha da tabela de variáveis. `svg` = id do gráfico de linha (no detalhe) que já embute a série completa:
        a coluna "Janela" é calculada no navegador a partir dele, com a janela escolhida no gráfico do Ibovespa do Painel."""
        var, v = variacoes(serie, ultimo, taxa)
        if var1d is not None:
            var[0] = var1d                                    # 1 d pela cotação intraday (fechamento anterior do mesmo contrato/instrumento)
        cels = "".join(f'<td class="{dlt_cls(x)}">{num(x, 2, "+" if x and x > 0 else "", " p.p." if taxa else "%") if x is not None else "—"}</td>' for x in var)
        jan = f'<td class="varjan" data-svg="{svg}" data-v="{v if v is not None else ""}" data-taxa="{1 if taxa else 0}">—</td>'
        linhas_m.append(f'<tr><td class="tk">{nome}</td><td style="font-weight:600">{num(v, dec, suf=suf)}</td>{cels}{jan}<td class="mut" style="text-align:left;white-space:nowrap">{obs}</td></tr>')
    for nome, rot, dec in (("Ibovespa", "Ibovespa", 0), ("S&P 500", "S&P 500", 0), ("USD/BRL", "USD/BRL", 2), ("Brent (US$)", "Brent (US$/bbl)", 2),
                           ("VIX", "VIX (CBOE: vol. implícita do S&P 500, 30 d)", 2)):
        q = mk.get(nome) or {}
        ult = [q["hora"][:10], q["preco"]] if q.get("preco") and q.get("hora") else None
        linha(rot, hist.get(nome, []), ult, dec=dec, obs=(f'DY 12 m {num(dyx[-1][1], 1, suf="%")}' if nome == "Ibovespa" and dyx else ""),
              var1d=(q["var_dia"] * 100) if q.get("var_dia") is not None else None, svg="h-" + nome.replace(" ", "").replace("/", ""))
    qt = mk.get("Treasury 10a (%)") or {}
    linha("Treasury 10 anos", hist.get("Treasury 10a (%)", []), [qt["hora"][:10], qt["preco"]] if qt.get("preco") and qt.get("hora") else None, taxa=True, suf="%", svg="painel-tnx")
    linha("Pré 10 anos (Tesouro)", pre10, taxa=True, suf="%", svg="painel-pre10")
    med35 = (sum(v for _, v in n35) / len(n35)) if n35 else None
    linha("NTN-B 2035 (real)", n35, taxa=True, suf="%", obs=f"média hist. {num(med35, 2, suf='%')}", svg="ntnb35")
    cab_m = ('<thead><tr><th>Variável</th><th>Último</th><th>1 d</th><th>5 d</th><th>MTD</th><th>YTD</th><th>12 m</th>'
             '<th class="varjan-h" title="Variação na janela escolhida nos chips do gráfico do Ibovespa (abaixo)">Janela</th><th style="text-align:left"></th></tr></thead>')
    tab_var = f'<div class="pbox" style="padding:6px 10px"><table class="mini" style="width:100%">{cab_m}<tbody>{"".join(linhas_m)}</tbody></table></div>'
    fl = fluxo_tiles(M)
    if fl:
        tiles_fl, ult_fl = fl
        box_fl = (f'<div class="pbox" style="padding:6px 10px"><h2 style="margin:0 0 4px">Fluxo por tipo de investidor · ações B3, saldo até {ult_fl[8:]}/{ult_fl[5:7]} (R$ mi)</h2>'
                  f'<div class="tiles fltiles">{tiles_fl}</div></div>')
        strip = f'{tab_var}<div style="margin-top:10px">{box_fl}</div>'
    else:
        strip = tab_var

    # --- macro: realizado × Focus × BCB
    linhas = []
    for ind, real, dec, suf, chave in (("IPCA", ipca12, 2, "%", "IPCA"), ("Selic", selic and selic[1], 2, "%", "Selic"),
                                        ("PIB", pib and pib[1], 1, "%", "PIB Total"), ("Câmbio", cambio and cambio[1], 2, "", "Câmbio")):
        for ano in (a0, a1):
            s = foc(chave, ano)
            d4 = delta(s, 28)
            b = bcb_v(ind, ano)
            linhas.append(f'<tr><td class="tk">{ind} <small>{ano}</small></td><td class="mut">{num(real, dec, suf=suf) if ano == a0 else ""}</td>'
                          f'<td style="font-weight:600">{num(s[-1][1], dec, suf=suf) if s else "—"}</td><td class="{dlt_cls(d4)}">{num(d4, 2, "+" if d4 and d4 > 0 else "") if d4 is not None else "—"}</td>'
                          f'<td>{num(b, dec, suf=suf) if b is not None else "—"}</td></tr>')
    tab_macro = f'<table class="mini"><thead><tr><th>Indicador</th><th>Realizado</th><th>Focus</th><th>Δ 4 sem</th><th>BCB</th></tr></thead><tbody>{"".join(linhas)}</tbody></table>'
    fs = M.get("focus_selic", [])
    selic_path = " · ".join(f'{r["reuniao"].split("/")[0]}/{r["reuniao"][-2:]} {num(r["mediana"], 2)}' for r in fs[:6])

    # --- assertividade em uma linha (recalcula só EAM 12 m e viés a partir do cache)
    assert_txt = ""
    preal = config.DATA / "realizado.json"; dirp = config.DATA / "focus_longo"
    if preal.exists() and dirp.exists():
        real = json.loads(preal.read_text(encoding="utf-8")); partes = []
        for ind, (cod, chave) in config.FOCUS_ASSERT_INDICADORES.items():
            E = []
            for ano in range(config.FOCUS_ASSERT_ANO_INI, date.today().year):
                r = real.get(chave, {}).get(str(ano)); f = dirp / f"{ind.replace(' ', '_')}_{ano}.json"
                if r is None or not f.exists():
                    continue
                srs = json.loads(f.read_text(encoding="utf-8")); alvo = date(ano, 12, 31).toordinal() - 365
                obs = None
                for row in srs:
                    if date.fromisoformat(row[0]).toordinal() <= alvo:
                        obs = row
                    else:
                        break
                if obs and date.fromisoformat(obs[0]).toordinal() >= alvo - 45:
                    E.append(obs[1] - r)
            if E:
                mae = sum(abs(e) for e in E) / len(E); vies = sum(E) / len(E)
                partes.append(f"{ind.replace(' Total', '')} {num(mae, 1)}{'' if ind == 'Câmbio' else ' p.p.'} ({'viés ' + num(vies, 1, '+' if vies > 0 else '')})")
        assert_txt = "Erro médio do Focus a 12 meses, 2002–2025: " + "; ".join(partes) + "."

    # --- cobertura + movimentos
    linhas_cob, sinais_cob = [], []
    for tk, v in config.UNIVERSO.items():
        m = mercado_micro.get(tk, {}); intr = m.get("intraday") or {}; acoes = m.get("acoes")
        serie = _b3.serie(tk); preco = intr.get("preco") or (serie[-1][1] if serie else None)
        fa = intr.get("fech_anterior"); var_dia = (preco / fa - 1) if (preco and fa) else None
        c = ultima(cons, tk, a0, prefer=("bloomberg", "yahoo")); mm = ultima(minhas, tk, a0)
        def pl(r):
            if not r or not preco or not acoes:
                return None
            eps = r["eps"] or (r["lucro"] * 1e6 / acoes if r["lucro"] else None)
            return preco / eps if eps and eps > 0 else None
        tgt_c = c["target"] if c else None; up_c = (tgt_c / preco - 1) if (tgt_c and preco) else None
        tgt_m = mm["target"] if mm else None; up_m = (tgt_m / preco - 1) if (tgt_m and preco) else None
        # revisão do consenso: 1ª vs última coleta do lucro do ano corrente
        hist_c = sorted([r for r in cons if r["ticker"] == tk and r["ano"] == a0 and r["lucro"]], key=lambda r: r["data"])
        rev = (hist_c[-1]["lucro"] / hist_c[0]["lucro"] - 1) if len(hist_c) >= 2 and hist_c[0]["lucro"] else None
        h = [[d, p] for d, p in serie]
        r20 = _ret(h, preco, 20)
        sv = _b3.serie(tk, ("fechamento", "quantidade", "volume"))          # volume financeiro do dia vs média de 20 pregões
        vol_rel = (sv[-1][3] / (sum(x[3] for x in sv[-21:-1]) / 20)) if len(sv) > 21 and sum(x[3] for x in sv[-21:-1]) > 0 else None
        vol_txt = f'{num(sv[-1][3] / 1e6, 0)} mi <span class="mut">({num(vol_rel, 1, suf="x")})</span>' if sv and vol_rel else "—"
        linhas_cob.append(f'<tr><td class="tk">{tk}</td><td>{num(preco, 2)}</td><td class="{dlt_cls(var_dia)}">{pct(var_dia)}</td><td class="{dlt_cls(r20)}">{pct(r20)}</td><td>{vol_txt}</td>'
                          f'<td>{num(pl(mm), 1, suf="x")}<span class="mut"> / </span>{num(pl(c), 1, suf="x")}</td><td class="{dlt_cls(up_c)}">{pct(up_m)}<span class="mut"> / </span>{pct(up_c)}</td></tr>')
        if c:
            sinais_cob.append(f"<b>{tk}</b> a {num(pl(c), 1, suf='x')} P/L {a0} do consenso ({c.get('n_analistas') or '—'} analistas), upside {pct(up_c)} para R$ {num(tgt_c, 2)}"
                              + (f"; consenso de lucro {pct(rev)} desde {hist_c[0]['data'][5:].replace('-', '/')}" if rev is not None and abs(rev) > 0.002 else "") + ".")
    tab_cob = f'<table class="mini"><thead><tr><th>Papel</th><th>Preço</th><th>Dia</th><th>20 d</th><th>Vol. R$ (× méd. 20 d)</th><th>P/L {a0} meu / cons.</th><th>Upside meu / cons.</th></tr></thead><tbody>{"".join(linhas_cob)}</tbody></table>'
    mov = []
    for tk, q in M.get("movimentos", {}).items():
        h = M.get("mov_hist", {}).get(tk, [])
        mov.append((tk, q["preco"], q["var_dia"], _ret(h, q["preco"], 5), _ret(h, q["preco"], 20)))
    tab_mov = f'<table class="mini"><thead><tr><th>Papel</th><th>Preço</th><th>Dia</th><th>5 d</th><th>20 d</th></tr></thead><tbody>' + "".join(
        f'<tr><td class="tk">{t}</td><td>{num(p, 2)}</td><td class="{dlt_cls(d1)}">{pct(d1)}</td><td class="{dlt_cls(d5)}">{pct(d5)}</td><td class="{dlt_cls(d20)}">{pct(d20)}</td></tr>' for t, p, d1, d5, d20 in mov) + "</tbody></table>"

    # --- sinais automáticos (regras simples sobre os dados)
    sinais = []
    s26, s27 = foc("IPCA", a0), foc("IPCA", a1)
    d4 = delta(s26, 28)
    if s26 and d4 is not None:
        sinais.append(f"<b>Inflação:</b> Focus {a0} em {num(s26[-1][1], 2, suf='%')} ({num(d4, 2, '+' if d4 > 0 else '')} em 4 sem); {a1} em {num(s27[-1][1], 2, suf='%') if s27 else '—'}, acima da meta de 3%.")
    b27, f27 = bcb_v("IPCA", a1), (s27[-1][1] if s27 else None)
    if b27 is not None and f27 is not None:
        sinais.append(f"<b>BCB vs mercado:</b> Copom projeta IPCA {a1} em {num(b27, 1, suf='%')} contra {num(f27, 2, suf='%')} do Focus: {'aposta em devolução dos choques que o mercado não compra' if b27 < f27 else 'BCB mais pessimista que o mercado'}.")
    sl0 = foc("Selic", a0); sl1 = foc("Selic", a1)
    if selic and sl0 and sl1:
        sinais.append(f"<b>Juros:</b> Selic {num(selic[1], 2, suf='%')}; Focus vê {num(sl0[-1][1], 2, suf='%')} no fim de {a0} e {num(sl1[-1][1], 2, suf='%')} em {a1}. Trajetória: {selic_path}.")
    if ntnb35 and med35:
        sinais.append(f"<b>Juro real:</b> NTN-B 2035 a {num(ntnb35, 2, suf='%')}, {num(ntnb35 - med35, 2, '+' if ntnb35 > med35 else '')} p.p. contra a média desde {n35[0][0][:4]}.")
    ib = M.get("hist", {}).get("Ibovespa", [])
    if ib and mk.get("Ibovespa"):
        px = mk["Ibovespa"]["preco"]; mx = max(v for _, v in ib); r12 = _ret(ib, px, 252); r1 = _ret(ib, px, 21)
        sinais.append(f"<b>Bolsa:</b> Ibovespa {pct(r1)} em 1 mês e {pct(r12)} em 12 meses, {pct(px / mx - 1)} do máximo histórico.")
    cf = foc("Câmbio", a0)
    if cambio and cf:
        sinais.append(f"<b>Câmbio:</b> PTAX {num(cambio[1], 2)} contra {num(cf[-1][1], 2)} esperado para o fim de {a0} pelo Focus.")
    V = analise_volume(M)
    if V and V.get("razao"):
        an = ", ".join(f'{r["cod"]} ({num(r["razao"], 1)}x)' for r in V["anormais"][:5])
        sinais.append(f"<b>Volume:</b> R$ {num(V['hoje'], 1)} bi nos papéis do Ibovespa, {num(V['razao'], 2)}x a média de 21 pregões"
                      + (f"; anormal em {an}" if an else "; nenhum papel acima de 1,5x a própria média") + ".")
    sinais += sinais_cob                                     # assertividade do Focus fica no slide próprio (Painel precisa caber numa tela)

    # --- opinião do usuário
    pop = config.RAIZ / "opiniao.json"
    O = json.loads(pop.read_text(encoding="utf-8")) if pop.exists() else {}
    campos = [("macro", "Macro"), ("juros", "Juros"), ("cambio", "Câmbio"), ("bolsa", "Bolsa")] + [(tk, tk) for tk in config.UNIVERSO]
    op = "".join(f'<p><b>{lab}:</b> {O[k]}</p>' for k, lab in campos if O.get(k))
    if not op:
        op = '<p class="vazio">Escreva em <code>opiniao.json</code> (macro, juros, câmbio, bolsa e um campo por papel). Aparece aqui, datado.</p>'

    # --- sparklines de 1 ano
    def ult_ano(pts):
        if not pts:
            return []
        corte = date.fromisoformat(pts[-1][0]).toordinal() - 365
        return [p for p in pts if date.fromisoformat(p[0]).toordinal() >= corte]
    sparks = [svg_spark("Ibovespa, 1 ano", ult_ano(ib), S1, 0), svg_spark("USD/BRL, 1 ano", ult_ano(M.get("hist", {}).get("USD/BRL", [])), S1, 2),
              svg_spark("NTN-B 2035, 1 ano (% real)", ult_ano(n35), S1, 2, suf="%", W=480, H=90),
              svg_spark(f"Focus IPCA {a0}, 1 ano (mediana)", ult_ano([[r[0], r[1]] for r in s26]), S2, 2, suf="%")]

    # --- carimbos compactos
    def car(nome, lim):
        e = est.get(nome)
        if not e:
            return ""
        em = e.get("em") or ""
        try:
            idade = (datetime.now() - datetime.strptime(em, "%Y-%m-%d %H:%M")).days
        except ValueError:
            idade = 999
        cor = "var(--dn)" if (not e.get("ok", True) or idade > lim) else "var(--up)"
        return f'<i style="background:{cor}"></i>{nome} {em[5:16].replace("-", "/")}'
    carimbos = '<span class="carimbo">Atualizações:' + "".join(car(n, l) for n, l in (("cotações universo", 1), ("B3 COTAHIST", 4), ("Focus", 9), ("SGS mensais", 40), ("Tesouro NTN-B", 4))) + f'<i style="background:var(--up)"></i>RPM {R.get("data", "—")}</span>'

    D = decomp_ibov(M)
    M["_ibov_decomp"] = D
    corpo = f'''{strip}
{linha_ibov_painel(D, M)}
{linha_variaveis_painel(M)}
<div class="pgrid" style="margin-top:10px">
  <div><div class="pbox"><h2>Macro · realizado × Focus × BCB ({a0} e {a1})</h2>{tab_macro}</div>
       {bloco_rpm_painel()}
       <div class="pbox"><h2>Cobertura</h2>{tab_cob}</div></div>
  <div><div class="pbox"><h2>Sinais</h2><ul class="sinais">{"".join(f"<li>{x}</li>" for x in sinais)}</ul></div></div>
  <div><div class="pbox"><h2>Opinião · {O.get("data", "—")}</h2><div class="opiniao">{op}</div></div>
       <div class="pbox"><h2>Movimentos recentes</h2>{tab_mov}</div>
       <div class="pbox" style="padding:6px 10px 2px">{sparks[2]}</div></div>
</div>'''
    return slide("Painel", "painel", "Painel", corpo, f"Uma tela para formar opinião. O detalhe de cada bloco está nos slides abaixo. Cotações às {(mk.get('Ibovespa') or {}).get('hora', '—')[11:]}.", carimbos, cls="painel")

def slides_juros(M: dict, sgs: dict) -> list[tuple[str, str]]:
    """Juros nominais: curva ANBIMA de hoje (pré, IPCA, implícita) e histórico de vencimento constante do Tesouro desde 2004."""
    from fontes import anbima
    S1, S2, MUT = "var(--s1)", "var(--s2)", "var(--axis)"
    out = []
    fotos = anbima.fotos()
    pc = config.DATA / "curva_tesouro.json"
    CT = json.loads(pc.read_text(encoding="utf-8")) if pc.exists() else None
    a0 = M.get("anos", [date.today().year])[0]
    focus = M.get("focus", {})
    selic = (sgs.get("432", {}).get("serie") or [])
    selic_v = selic[-1][1] if selic else None

    # --- slide 1: curva de hoje (ANBIMA) + inflação implícita vs Focus
    if fotos:
        datas = sorted(fotos)
        hoje_f = fotos[datas[-1]]
        ant = fotos[datas[0]] if len(datas) > 1 else None
        vert = hoje_f["vertices"]
        anos_v = [v[0] / 252 for v in vert]
        # curva pré hoje vs foto mais antiga disponível; curva IPCA; implícita
        def serie_v(f, idx):
            return [[str(round(v[0] / 252, 3)), v[idx]] for v in f["vertices"] if v[idx] is not None]
        W, H, ML, MR, MT, MB = 470, 250, 56, 16, 22, 30
        vert = [v for v in vert if v[0] / 252 <= 10.05]
        def g_curva(titulo, series, dec, suf, refs=None):
            xs = [x for _, _, pts in series for x, _ in pts]
            ys = [y for _, _, pts in series for _, y in pts] + [r[1] for r in (refs or [])]
            lo, hi = min(ys), max(ys); pad = (hi - lo) * 0.1 or 0.5; lo -= pad; hi += pad
            X = lambda x: ML + (x - 0) / 10.5 * (W - ML - MR); Y = lambda v: MT + (hi - v) / (hi - lo) * (H - MT - MB)
            h = [f'<text class="sub" x="{ML}" y="12">{titulo}</text>']
            for t in ticks(lo, hi, 4):
                if lo <= t <= hi:
                    h.append(f'<line class="grid" x1="{ML}" x2="{W - MR}" y1="{Y(t):.1f}" y2="{Y(t):.1f}"/><text class="tick" x="{ML - 6}" y="{Y(t) + 4:.1f}" text-anchor="end">{num(t, dec)}</text>')
            for a in range(1, 11):
                h.append(f'<text class="tick" x="{X(a):.1f}" y="{H - 10}" text-anchor="middle">{a}a</text>')
            for lab, v, cor in (refs or []):
                h.append(f'<line class="ref" style="stroke:{cor}" x1="{ML}" x2="{W - MR}" y1="{Y(v):.1f}" y2="{Y(v):.1f}"/><text class="reflab" x="{W - MR}" y="{Y(v) - 4:.1f}" text-anchor="end">{lab} {num(v, dec)}{suf}</text>')
            for nome, cor, pts in series:
                h.append('<path class="line" style="stroke:%s" d="%s"/>' % (cor, " ".join(f"{'M' if i == 0 else 'L'}{X(x):.1f},{Y(y):.1f}" for i, (x, y) in enumerate(pts))))
                xl, yl = X(pts[-1][0]), Y(pts[-1][1])
                h.append(f'<circle class="dot" cx="{xl:.1f}" cy="{yl:.1f}" r="4" style="fill:{cor}"/><text class="endlab" x="{xl - 7:.1f}" y="{yl - 8:.1f}" text-anchor="end">{num(pts[-1][1], dec)}{suf}</text>')
                for x, y in pts:
                    h.append(f'<circle data-tip="{nome} {num(x, 1)}a: {num(y, 2)}{suf}" cx="{X(x):.1f}" cy="{Y(y):.1f}" r="7" fill="transparent"/>')
            return f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="{titulo}">{"".join(h)}</svg>'
        pre_h = [(v[0] / 252, v[2]) for v in vert if v[2] is not None]
        ipca_h = [(v[0] / 252, v[1]) for v in vert if v[1] is not None]
        imp_h = [(v[0] / 252, v[3]) for v in vert if v[3] is not None]
        ser_pre = [(f"pré {datas[-1][5:]}", S1, pre_h)]
        if ant:
            ser_pre.append((f"pré {datas[0][5:]}", MUT, [(v[0] / 252, v[2]) for v in ant["vertices"] if v[2] is not None and v[0] / 252 <= 10.05]))
        refs_pre = [("Selic meta", selic_v, "var(--mut)")] if selic_v else []
        g1 = g_curva("Curva prefixada (ANBIMA), % a.a. por prazo em anos", ser_pre, 2, "%", refs_pre)
        f26 = focus.get("IPCA", {}).get(str(a0)) or []; f27 = focus.get("IPCA", {}).get(str(a0 + 1)) or []
        refs_imp = [(f"Focus IPCA {a0 + 1}", f27[-1][1], "var(--mut)")] if f27 else []
        g2 = g_curva("Inflação implícita (pré − IPCA+), % a.a.", [("implícita", S2, imp_h), ("IPCA+ (real)", S1, ipca_h)], 2, "%", refs_imp)
        leg = f'<div class="legend"><span><i style="background:{S1}"></i>hoje</span><span><i style="background:{MUT}"></i>{datas[0][8:]}/{datas[0][5:7]}</span><span><i style="background:{S2}"></i>implícita</span></div>'
        # tabela de vértices-chave
        def at(anos, idx):
            v = min(vert, key=lambda v: abs(v[0] / 252 - anos)); return v[idx]
        tr = "".join(f'<tr><td class="tk">{a} ano{"s" if a > 1 else ""}</td><td>{num(at(a, 2), 2, suf="%")}</td><td>{num(at(a, 1), 2, suf="%")}</td><td>{num(at(a, 3), 2, suf="%")}</td></tr>' for a in (1, 2, 3, 5, 10))
        incl = (at(5, 2) or 0) - (at(1, 2) or 0)
        real_ex = None
        f12 = M.get("focus_horizonte", {}).get("12m", {}).get("IPCA", [])
        if f12 and at(1, 2):
            real_ex = ((1 + at(1, 2) / 100) / (1 + f12[-1][1] / 100) - 1) * 100
        tab = (f'<table class="compact"><thead><tr><th>Prazo</th><th>Pré</th><th>IPCA+</th><th>Implícita</th></tr></thead><tbody>{tr}</tbody></table>'
               f'<p class="note" style="margin-top:8px">Inclinação 5a − 1a: <b>{num(incl, 2, "+" if incl > 0 else "", " p.p.")}</b>. Selic real ex-ante (pré 1a deflacionado pelo Focus 12 m): <b>{num(real_ex, 2, suf="%")}</b>. Focus IPCA {a0}: {num(f26[-1][1], 2, suf="%") if f26 else "—"}.</p>')
        out.append(slide("Macro", "juros-curva", "Juros nominais · curva de hoje",
                         f'{leg}<div class="grid2" style="grid-template-columns:1fr 1fr">{"<div>" + g1 + "</div>"}{"<div>" + g2 + "</div>"}</div><div class="panel" style="margin-top:12px">{tab}</div>',
                         "Estrutura a termo prefixada e IPCA da ANBIMA, e a inflação que o mercado embute entre as duas.",
                         f"ANBIMA, ETTJ de {datas[-1]} (Svensson, vértices em dias úteis ÷ 252). Fotografias diárias desde {datas[0]}."))

    # --- slide 2: histórico de vencimento constante (Tesouro Direto, desde 2004)
    if CT and CT.get("pre"):
        pz = CT["prazos_pre"]
        def col(i):
            return [[r[0], r[1 + i]] for r in CT["pre"] if r[1 + i] is not None]
        cores = [S1, S2, "var(--s3, #1baf7a)", "var(--s4, #eda100)"]
        series = [(f"{pz[i]}a", cores[k], col(i)) for k, i in enumerate([0, 1, 3, 5]) if col(i)]
        g3 = svg_linhas("ct-pre", series, 2, suf="%", W=960, H=400, titulo="Prefixado de vencimento constante (Tesouro Direto), % a.a.", refs=[("Selic meta", selic_v)] if selic_v else None)
        pr = CT["prazos_real"]
        def colr(i):
            return [[r[0], r[1 + i]] for r in CT["real"] if r[1 + i] is not None]
        g4 = svg_linhas("ct-real", [("IPCA+ 5a", S1, colr(1)), ("IPCA+ 10a", S2, colr(2))], 2, suf="%", W=470, H=340, titulo="Juro real de vencimento constante (NTN-B), % a.a.")
        # inclinação 5a-1a e implícita 5a (pré 5a vs IPCA+ 5a)
        m_pre = {r[0]: r for r in CT["pre"]}; m_real = {r[0]: r for r in CT["real"]}
        incl_s = [[d, r[1 + 3] - r[1]] for d, r in m_pre.items() if r[1] is not None and r[1 + 3] is not None]
        imp5 = [[d, ((1 + m_pre[d][1 + 3] / 100) / (1 + m_real[d][1 + 1] / 100) - 1) * 100] for d in m_pre if d in m_real and m_pre[d][1 + 3] is not None and m_real[d][1 + 1] is not None]
        g5 = svg_linhas("ct-incl", [("inclinação 5a − 1a", S1, incl_s)], 2, suf=" p.p.", W=470, H=340, titulo="Inclinação: pré 5a − pré 1a (p.p.)", refs=[("zero", 0.0)])
        g6 = svg_linhas("ct-imp", [("implícita 5a", S2, imp5)], 2, suf="%", W=470, H=340, titulo="Inflação implícita 5 anos (pré 5a vs IPCA+ 5a), %", refs=[("meta 3%", 3.0)])
        leg3 = '<div class="legend">' + "".join(f'<span><i style="background:{c}"></i>{n}</span>' for n, c, _ in series) + "</div>"
        out.append(slide("Macro", "juros-historico", "Juros nominais · histórico da curva",
                         f'<div class="panel">{leg3}{g3}</div>',
                         "Vencimento constante interpolado entre os títulos prefixados de cada dia. O seletor de janela vale aqui.",
                         "Tesouro Transparente, taxa de compra do Tesouro Direto (LTN e NTN-F), desde 2004; sem extrapolação, prazo sem título que o cerque fica vazio."))
        out.append(slide("Macro", "juros-real-implicita", "Juro real, inclinação e implícita",
                         f'<div class="grid3"><div>{g4}</div><div>{g5}</div><div>{g6}</div></div>',
                         "As três leituras que resumem o prêmio da curva: quanto paga o real longo, quanto a curva cobra do prazo, e quanta inflação está embutida.",
                         "Tesouro Direto (NTN-B, LTN, NTN-F), vencimento constante."))
    return out



def _fwd(r1, t1, r2, t2):
    """Taxa a termo entre t1 e t2 (anos), taxas % a.a. capitalizadas 252: ((1+r2)^t2/(1+r1)^t1)^(1/(t2-t1)) - 1."""
    if r1 is None or r2 is None or t2 <= t1:
        return None
    return (((1 + r2 / 100) ** t2 / (1 + r1 / 100) ** t1) ** (1 / (t2 - t1)) - 1) * 100


def _svensson(params: list[float], t: float) -> float:
    """Taxa spot (% a.a.) pela ETTJ da ANBIMA (Svensson, λ·t): β1 + β2·A + β3·(A − e^{−λ1 t}) + β4·(B − e^{−λ2 t}), A=(1−e^{−λ1 t})/(λ1 t), B idem com λ2. t em anos (du/252)."""
    b1, b2, b3, b4, l1, l2 = params
    x1, x2 = t * l1, t * l2
    A = (1 - math.exp(-x1)) / x1
    B = (1 - math.exp(-x2)) / x2
    return (b1 + b2 * A + b3 * (A - math.exp(-x1)) + b4 * (B - math.exp(-x2))) * 100


def _selic_implicita(params: list[float], meses: int = 30) -> list[tuple[float, float]]:
    """Forward de 1 mês ao longo dos próximos `meses` (Selic implícita na curva): [(anos, taxa % a.a.)]."""
    out = []
    for m in range(0, meses):
        t1, t2 = m / 12, (m + 1) / 12
        r2 = _svensson(params, t2)
        f = r2 if m == 0 else _fwd(_svensson(params, t1), t1, r2, t2)
        if f is not None:
            out.append(((t1 + t2) / 2, f))
    return out


def _fra_1a(vert: list) -> list[tuple[float, float, float]]:
    """FRAs de 1 ano ano a ano a partir dos vértices ANBIMA [du, ipca, pre, impl]: [(inicio, fim, taxa)], 0→1 é o próprio pré 1a."""
    pre = {round(v[0] / 252, 2): v[2] for v in vert if v[2] is not None}
    def r(t):
        if t == 0:
            return None
        k = min(pre, key=lambda x: abs(x - t))
        return pre[k] if abs(k - t) < 0.1 else None
    out = []
    for a in range(0, 10):
        if a == 0:
            if r(1) is not None:
                out.append((0, 1, r(1)))
        else:
            f = _fwd(r(a), a, r(a + 1), a + 1)
            if f is not None:
                out.append((a, a + 1, f))
    return out


COPOM_MES = {1: (1, 28), 2: (3, 18), 3: (5, 6), 4: (6, 17), 5: (7, 29), 6: (9, 16), 7: (11, 4), 8: (12, 9)}   # datas típicas das 8 reuniões


def slides_fra(M: dict, sgs: dict) -> list[tuple[str, str]]:
    """FRAs (taxas a termo) da curva de hoje vs Selic esperada pelo Focus, e histórico dos FRAs clássicos desde 2004."""
    from fontes import anbima
    S1, S2, MUT = "var(--s1)", "var(--s2)", "var(--axis)"
    out = []
    fotos = anbima.fotos()
    selic = (sgs.get("432", {}).get("serie") or [])
    selic_v = selic[-1][1] if selic else None
    hoje = date.today()
    if fotos:
        datas = sorted(fotos)
        fra_h = _fra_1a([v for v in fotos[datas[-1]]["vertices"]])
        fra_a = _fra_1a([v for v in fotos[datas[0]]["vertices"]]) if len(datas) > 1 else []
        # Focus por reunião -> (anos à frente, mediana)
        pts_focus = []
        for r in M.get("focus_selic", []):
            try:
                n, ano = r["reuniao"][1:].split("/"); m, d = COPOM_MES[int(n)]
                dt = date(int(ano), m, d)
                pts_focus.append(((dt - hoje).days / 365.25, r["mediana"], r["reuniao"]))
            except (KeyError, ValueError):
                pass
        W, H, ML, MR, MT, MB = 960, 205, 56, 16, 22, 30
        ys = [f for *_, f in fra_h + fra_a] + ([selic_v] if selic_v else [])
        lo, hi = min(ys), max(ys); pad = (hi - lo) * 0.15 or 0.5; lo -= pad; hi += pad
        X = lambda t: ML + t / 10.2 * (W - ML - MR); Y = lambda v: MT + (hi - v) / (hi - lo) * (H - MT - MB)
        h = [f'<text class="sub" x="{ML}" y="12">FRAs de 1 ano, ano a ano (% a.a.): o que a curva cobra para cada ano à frente</text>']
        for t in ticks(lo, hi, 4):
            if lo <= t <= hi:
                h.append(f'<line class="grid" x1="{ML}" x2="{W - MR}" y1="{Y(t):.1f}" y2="{Y(t):.1f}"/><text class="tick" x="{ML - 6}" y="{Y(t) + 4:.1f}" text-anchor="end">{num(t, 2)}</text>')
        for a in range(0, 11):
            h.append(f'<text class="tick" x="{X(a):.1f}" y="{H - 10}" text-anchor="middle">{a}a</text>')
        if selic_v:
            h.append(f'<line class="ref" style="stroke:var(--mut)" x1="{ML}" x2="{W - MR}" y1="{Y(selic_v):.1f}" y2="{Y(selic_v):.1f}"/><text class="reflab" x="{W - MR}" y="{Y(selic_v) + 12:.1f}" text-anchor="end">Selic meta {num(selic_v, 2)}%</text>')
        for fra, cor, lab in ((fra_a, MUT, datas[0][5:] if len(datas) > 1 else ""), (fra_h, S1, "hoje")):
            for a, b, f in fra:
                h.append(f'<line data-tip="FRA {a}a→{b}a ({lab}): {num(f, 2)}%" x1="{X(a) + 3:.1f}" x2="{X(b) - 3:.1f}" y1="{Y(f):.1f}" y2="{Y(f):.1f}" style="stroke:{cor};stroke-width:{3 if cor == S1 else 2};stroke-linecap:round"/>')
                if cor == S1:
                    h.append(f'<text class="cap" x="{(X(a) + X(b)) / 2:.1f}" y="{Y(f) - 7:.1f}" text-anchor="middle">{num(f, 2)}</text>')
        g1 = f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="FRAs">{"".join(h)}</svg>'
        # Selic implícita (forward mensal pela Svensson) vs Focus por reunião, próximos 2,5 anos
        sv = fotos[datas[-1]]["svensson"].get("PREFIXADOS")
        sv_a = fotos[datas[0]]["svensson"].get("PREFIXADOS") if len(datas) > 1 else None
        g2 = ""
        if sv:
            imp = _selic_implicita(sv, 30); imp_a = _selic_implicita(sv_a, 30) if sv_a else []
            W2, H2 = 600, 272
            ys2 = [v for _, v in imp + imp_a] + [v for tt, v, _rn in pts_focus if 0 <= tt <= 2.6] + ([selic_v] if selic_v else [])
            lo2, hi2 = min(ys2), max(ys2); pad2 = (hi2 - lo2) * 0.12 or 0.5; lo2 -= pad2; hi2 += pad2
            X2 = lambda t: ML + t / 2.6 * (W2 - ML - MR); Y2 = lambda v: MT + (hi2 - v) / (hi2 - lo2) * (H2 - MT - MB)
            h2 = [f'<text class="sub" x="{ML}" y="12">Selic implícita na curva (forward de 1 mês) vs Focus por reunião, % a.a.</text>']
            for t in ticks(lo2, hi2, 5):
                if lo2 <= t <= hi2:
                    h2.append(f'<line class="grid" x1="{ML}" x2="{W2 - MR}" y1="{Y2(t):.1f}" y2="{Y2(t):.1f}"/><text class="tick" x="{ML - 6}" y="{Y2(t) + 4:.1f}" text-anchor="end">{num(t, 2)}</text>')
            for m in range(0, 31, 6):
                h2.append(f'<text class="tick" x="{X2(m / 12):.1f}" y="{H2 - 10}" text-anchor="middle">{m}m</text>')
            if selic_v:
                h2.append(f'<line class="ref" style="stroke:var(--mut)" x1="{ML}" x2="{W2 - MR}" y1="{Y2(selic_v):.1f}" y2="{Y2(selic_v):.1f}"/><text class="reflab" x="{W2 - MR}" y="{Y2(selic_v) - 4:.1f}" text-anchor="end">Selic meta</text>')
            if imp_a:
                h2.append('<path class="line" style="stroke:%s;stroke-width:1.5" d="%s"/>' % (MUT, " ".join(f"{'M' if i == 0 else 'L'}{X2(t):.1f},{Y2(v):.1f}" for i, (t, v) in enumerate(imp_a))))
            h2.append('<path class="line" style="stroke:%s" d="%s"/>' % (S1, " ".join(f"{'M' if i == 0 else 'L'}{X2(t):.1f},{Y2(v):.1f}" for i, (t, v) in enumerate(imp))))
            for t, v in imp[::6]:
                h2.append(f'<circle data-tip="curva {num(t * 12, 0)}m: {num(v, 2)}%" cx="{X2(t):.1f}" cy="{Y2(v):.1f}" r="7" fill="transparent"/>')
            for t, v, rn in pts_focus:
                if 0 <= t <= 2.6:
                    h2.append(f'<circle class="dot" data-tip="Focus Selic {rn}: {num(v, 2)}%" cx="{X2(t):.1f}" cy="{Y2(v):.1f}" r="4.5" style="fill:{S2}"/>')
            xl, yl = X2(imp[-1][0]), Y2(imp[-1][1])
            h2.append(f'<text class="endlab" x="{xl - 6:.1f}" y="{yl - 8:.1f}" text-anchor="end">{num(imp[-1][1], 2)}%</text>')
            g2 = f'<svg class="chart" viewBox="0 0 {W2} {H2}" role="img" aria-label="Selic implícita">{"".join(h2)}</svg>'
        leg = (f'<div class="legend"><span><i style="background:{S1}"></i>curva hoje ({datas[-1][8:]}/{datas[-1][5:7]})</span>'
               + (f'<span><i style="background:{MUT}"></i>curva {datas[0][8:]}/{datas[0][5:7]}</span>' if len(datas) > 1 else "")
               + f'<span><i style="background:{S2};border-radius:50%"></i>Selic esperada pelo Focus, por reunião</span></div>')
        # tabela dos FRAs clássicos de hoje + variação vs foto anterior
        def fra(vert, t1, t2):
            pre = {round(v[0] / 252, 2): v[2] for v in vert if v[2] is not None}
            def r(t):
                k = min(pre, key=lambda x: abs(x - t)); return pre[k] if abs(k - t) < 0.1 else None
            return _fwd(r(t1), t1, r(t2), t2)
        vh = fotos[datas[-1]]["vertices"]; va = fotos[datas[0]]["vertices"] if len(datas) > 1 else None
        tr = []
        for nome, t1, t2 in (("1a1a", 1, 2), ("2a1a", 2, 3), ("3a2a", 3, 5), ("5a5a", 5, 10), ("2a3a", 2, 5)):
            fh = fra(vh, t1, t2); fa = fra(va, t1, t2) if va else None
            d = (fh - fa) if (fh is not None and fa is not None) else None
            tr.append(f'<tr><td class="tk">{nome}<small>{t1}a → {t2}a</small></td><td>{num(fh, 2, suf="%")}</td><td class="{dlt_cls(d)}">{num(d, 2, "+" if d and d > 0 else "", " p.p.") if d is not None else "—"}</td></tr>')
        f1a1a = fra(vh, 1, 2)
        fs27 = next((r["mediana"] for r in M.get("focus_selic", []) if r["reuniao"].endswith("/2027") and r["reuniao"].startswith("R8")), None)
        nota = f'FRA 1a1a de {num(f1a1a, 2, suf="%")} contra Selic de {num(fs27, 2, suf="%")} esperada pelo Focus para o fim de 2027: a curva cobra {num(f1a1a - fs27, 2, "+" if f1a1a - fs27 > 0 else "", " p.p.")} acima do consenso.' if (f1a1a and fs27) else ""
        tab = f'<table class="compact"><thead><tr><th>FRA</th><th>Hoje</th><th>Δ vs {datas[0][8:]}/{datas[0][5:7]}</th></tr></thead><tbody>{"".join(tr)}</tbody></table><p class="note" style="margin-top:8px">{nota}</p>'
        out.append(slide("Macro", "juros-fra", "Juros nominais · FRAs",
                         f'{leg}<div class="panel" style="padding:10px 14px 4px">{g1}</div><div class="grid2" style="grid-template-columns:1.5fr 1fr;margin-top:12px"><div>{g2}</div><div>{tab}</div></div>',
                         "Taxas a termo de 1 ano ano a ano, e a Selic mês a mês que a curva embute contra a que o Focus espera. A distância é o prêmio.",
                         f"ANBIMA ETTJ de {datas[-1]}; FRA(t1,t2) = ((1+r2)^t2 / (1+r1)^t1)^(1/(t2−t1)) − 1, base 252. Datas do Copom aproximadas pelo calendário típico."))
    # histórico dos FRAs clássicos (Tesouro Direto, vencimento constante)
    pc = config.DATA / "curva_tesouro.json"
    if pc.exists():
        CT = json.loads(pc.read_text(encoding="utf-8"))
        pz = CT["prazos_pre"]; idx = {p: 1 + i for i, p in enumerate(pz)}
        def serie(t1, t2):
            return [[r[0], _fwd(r[idx[t1]], t1, r[idx[t2]], t2)] for r in CT["pre"] if r[idx[t1]] is not None and r[idx[t2]] is not None]
        ser = [("1a1a", S1, serie(1, 2)), ("2a1a", S2, serie(2, 3)), ("3a2a", "var(--s3)", serie(3, 5)), ("5a5a", "var(--s4)", serie(5, 10))]
        ser = [x for x in ser if x[2]]
        g2 = svg_linhas("fra-hist", ser, 2, suf="%", W=960, H=400, titulo="FRAs de vencimento constante (Tesouro Direto), % a.a.", refs=[("Selic meta", selic_v)] if selic_v else None)
        leg2 = '<div class="legend">' + "".join(f'<span><i style="background:{c}"></i>{n}</span>' for n, c, _ in ser) + "</div>"
        out.append(slide("Macro", "juros-fra-historico", "FRAs · histórico",
                         f'<div class="panel">{leg2}{g2}</div>',
                         "Os mesmos FRAs calculados dia a dia sobre a curva de vencimento constante. O 5a5a só existe quando há título de 10 anos.",
                         "Tesouro Direto (LTN e NTN-F) desde 2004. Linhas interrompidas onde falta título no prazo."))
    return out


def slide_linha_tempo(C: dict, stem: str, M: dict, sgs: dict) -> tuple[str, str] | None:
    """Linha do tempo da visão do gestor: tabela por data + chamada de Selic fim-de-ano vs Focus vs Selic realizada."""
    lt = C.get("linha_do_tempo") or []
    if not lt:
        return None
    S1, S2 = "var(--s1)", "var(--s2)"
    nome = C.get("fundo", stem)
    a0 = M.get("anos", [date.today().year])[0]
    tr = []
    for e in lt:
        tipo = e.get("tipo", "")
        cor = "var(--up)" if tipo.startswith("prim") else "var(--mut)"
        cel = "".join(f'<td style="text-align:left;white-space:normal;font-size:11.6px;line-height:1.28;vertical-align:top;padding:3px 6px">{(e.get(k) or "—")}</td>' for k in ("juros", "inflacao", "posicoes"))
        tr.append(f'<tr><td class="tk" style="vertical-align:top;white-space:nowrap;padding:3px 6px">{e["data"][8:]}/{e["data"][5:7]}/{e["data"][2:4]}<br><small style="color:{cor}">{e.get("fonte", "")}</small></td>{cel}</tr>')
    tab = f'<table class="compact"><thead><tr><th>Data · fonte</th><th style="text-align:left">Juros</th><th style="text-align:left">Inflação</th><th style="text-align:left">Posições</th></tr></thead><tbody>{"".join(tr)}</tbody></table>'
    # gráfico: chamada do gestor para Selic fim-2026 (pontos) vs Focus fim-2026 (linha) vs Selic meta (linha)
    pts_g = [[e["data"], e["selic_fim_2026"]] for e in lt if e.get("selic_fim_2026") is not None]
    focus = (M.get("focus", {}).get("Selic", {}).get(str(a0)) or [])
    d0 = lt[0]["data"]
    f_ser = [[r[0], r[1]] for r in focus if r[0] >= d0]
    selic = [[d, v] for d, v in (sgs.get("432", {}).get("serie") or []) if d >= d0]
    g = svg_linhas(f"lt-{stem}", [(f"Focus Selic fim {a0}", S2, f_ser), ("Selic meta", "var(--axis)", selic)] + ([("chamada do gestor", S1, pts_g)] if pts_g else []),
                   2, suf="%", W=400, H=250, titulo=f"Selic no fim de {a0}: gestor × Focus × realizado")
    leg = (f'<div class="legend"><span><i style="background:{S1}"></i>gestor</span><span><i style="background:{S2}"></i>Focus</span><span><i style="background:var(--axis)"></i>Selic meta</span>'
           f'<span style="color:var(--up)">■ fonte primária</span><span class="mut">■ imprensa</span></div>')
    ipca = [(e["data"], e["ipca_2026"]) for e in lt if e.get("ipca_2026") is not None]
    f_ipca = (M.get("focus", {}).get("IPCA", {}).get(str(a0)) or [])
    nota_ipca = f'Última chamada de IPCA {a0}: {num(ipca[-1][1], 1, suf="%")} ({ipca[-1][0][8:]}/{ipca[-1][0][5:7]}) contra {num(f_ipca[-1][1], 2, suf="%") if f_ipca else "—"} do Focus hoje.' if ipca else ""
    corpo = (f'<div class="grid2" style="grid-template-columns:2.3fr 1fr;align-items:start"><div>{tab}</div>'
             f'<div>{leg}{g}<p class="note" style="margin-top:8px">{nota_ipca}</p></div></div>')
    return slide("Opinião qualificada", f"carta-{stem}-tempo", f"{nome} · evolução da visão", corpo,
                 "O que o gestor disse ao longo do tempo sobre juros, inflação e posições, e como a chamada de Selic caminhou contra o mercado.",
                 "Fontes primárias (cartas, relatórios, evento da gestora) em verde; entrevistas à imprensa em cinza. Ver lista de links no slide anterior.")


def slides_carteiras_cvm(M: dict) -> list[tuple[str, str]]:
    """Carteiras de fundos pela CVM (CDA), com ~6 meses de defasagem: maiores posições e variação contra o mês anterior."""
    from fontes import cvm_cda
    out = []
    for nome, cnpj in getattr(config, "FUNDOS_CVM", {}).items():
        meses = cvm_cda.meses_disponiveis(6)
        carts = [c for c in (cvm_cda.carteira(cnpj, m) for m in meses) if c and c["acoes"]]
        if not carts:
            continue
        atual, ant = carts[-1], (carts[-2] if len(carts) > 1 else None)
        tot = atual["total_acoes"] or 1
        pesos_ant = {a[0]: a[2] / (ant["total_acoes"] or 1) for a in ant["acoes"]} if ant else {}
        tr = []
        for cod, nm, v, q in atual["acoes"][:14]:
            w = v / tot
            d = (w - pesos_ant[cod]) if cod in pesos_ant else None
            tr.append(f'<tr><td class="tk">{cod}<small>{nm[:18]}</small></td><td>{num(v / 1e6, 0)}</td><td>{num(w * 100, 1, suf="%")}</td><td class="{dlt_cls(d)}">{num(d * 100, 1, "+" if d and d > 0 else "", " p.p.") if d is not None else "nova"}</td></tr>')
        saiu = [c for c in pesos_ant if c not in {a[0] for a in atual["acoes"]}] if ant else []
        # barras dos 12 maiores pesos
        g = svg_hbar([(a[0], a[2] / tot * 100) for a in atual["acoes"][:12]], W=470, RH=15, ML=80, MR=60, dec=1, suf="%")
        mes = atual["mes"]
        cab = f'{atual["nome"].title()} · PL R$ {num((atual["pl"] or 0) / 1e9, 1, suf=" bi")} · {len(atual["acoes"])} papéis · ações R$ {num(tot / 1e9, 1, suf=" bi")}'
        out.append(slide("Opinião qualificada", f"cvm-{nome.lower().replace(' ', '-')}", f"{nome} · carteira pela CVM",
                         f'<div class="grid2" style="grid-template-columns:1fr 1.2fr;align-items:start"><div><h2 style="font-size:13px;margin:0 0 6px">Peso das 12 maiores (% da carteira de ações)</h2>{g}'
                         f'<p class="note">{("Saíram desde " + ant["mes"][4:] + "/" + ant["mes"][:4] + ": " + ", ".join(saiu[:8])) if saiu else ""}</p></div>'
                         f'<div><table class="compact"><thead><tr><th>Papel</th><th>R$ mi</th><th>Peso</th><th>Δ vs mês ant.</th></tr></thead><tbody>{"".join(tr)}</tbody></table></div></div>',
                         f'{cab}. Competência {mes[4:]}/{mes[:4]}: a CVM abre as posições quando vence o sigilo de 180 dias.',
                         f"CVM, dados abertos, CDA (cda_fi_{mes}.zip, BLC_4). Defasagem de ~6 meses por sigilo; meses mais recentes só mostram o agregado por tipo de ativo."))
    return out


def slides_opiniao(M: dict | None = None, sgs: dict | None = None) -> list[tuple[str, str]]:
    """Opinião qualificada: cartas de gestão de bons fundos (cartas/*.json) + carteiras pela CVM (config.FUNDOS_CVM)."""
    out = []
    M, sgs = M or {}, sgs or {}
    dirp = config.RAIZ / "cartas"
    if not dirp.exists():
        return out
    for f in sorted(dirp.glob("*.json")):
        C = json.loads(f.read_text(encoding="utf-8"))
        nome = C.get("fundo", f.stem)
        d = C.get("desempenho", {})
        def li(xs):
            return "".join(f"<li>{x}</li>" for x in xs)
        cen = C.get("cenario", {})
        pos = C.get("posicoes", {})
        # slide 1: cenário e posições
        corpo1 = (f'<div class="grid2"><div><h2 style="font-size:14px;margin:0 0 6px">Cenário global</h2><ul class="note" style="font-size:13.5px;padding-left:18px">{li(cen.get("global", []))}</ul>'
                  f'<h2 style="font-size:14px;margin:10px 0 6px">Brasil</h2><ul class="note" style="font-size:13.5px;padding-left:18px">{li(cen.get("brasil", []))}</ul></div>'
                  f'<div><h2 style="font-size:14px;margin:0 0 6px">Posições</h2><table class="compact"><tbody>' +
                  "".join(f'<tr><td class="tk" style="white-space:nowrap">{k.replace("_", " ").capitalize()}</td><td style="text-align:left;white-space:normal;font-size:13px">{v}</td></tr>' for k, v in pos.items()) +
                  f'</tbody></table></div></div>')
        out.append(slide("Opinião qualificada", f"carta-{f.stem}", f"{nome} · cenário e posições", corpo1,
                         f'{C.get("gestora", "")} · {C.get("tipo", "")}. Última leitura: {C.get("data", "—")}.',
                         " · ".join(f'<a href="{s["url"]}" style="color:inherit">{s["nome"]}</a>' for s in C.get("fontes", []))))
        # slide 2: tese longa, leitura e desempenho
        anos = d.get("anos", {})
        tr = "".join(f'<tr><td class="tk">{a}</td><td>{num(v["fundo"], 2, suf="%")}</td><td>{num(v["cdi"], 2, suf="%")}</td><td class="{dlt_cls(v["fundo"] - v["cdi"])}">{num(v["fundo"] - v["cdi"], 2, "+" if v["fundo"] > v["cdi"] else "", " p.p.")}</td></tr>' for a, v in anos.items())
        atr = d.get("atribuicao_ano", [])
        g_atr = svg_hbar([(k, v) for k, v in atr if k != "CDI"], W=470, RH=16, ML=150, MR=56, dec=2, suf=" p.p.") if atr else ""
        m26 = d.get("mensal_2026", [])
        g_m = svg_colunas(f"Retorno mensal {date.today().year} (%)", [m for m, _ in m26], [v for _, v in m26], "var(--s1)", 2, "%", W=470, H=170) if m26 else ""
        tiles = "".join(f'<div class="tile"><div class="l">{l}</div><div class="v">{v}</div><div class="d">{s}</div></div>' for l, v, s in (
            ("No ano", num(d.get("ano", {}).get("fundo"), 2, suf="%"), f'{num(d.get("ano", {}).get("pct_cdi"), 0, suf="% do CDI")}'),
            ("12 meses", num(d.get("12m", {}).get("fundo"), 2, suf="%"), f'{num(d.get("12m", {}).get("pct_cdi"), 0, suf="% do CDI")}'),
            ("Desde o início", num(d.get("inicio", {}).get("fundo"), 2, suf="%"), f'{num(d.get("inicio", {}).get("pct_cdi"), 0, suf="% do CDI")} · CDI + {num(d.get("inicio", {}).get("excesso_aa"), 2, suf=" p.p. a.a.")}'),
            ("Sharpe · vol", f'{num(d.get("sharpe"), 2)} · {num(d.get("vol"), 1, suf="%")}', f'PL médio 12 m R$ {num(d.get("pl_medio_12m_mi"), 0, suf=" mi")}')))
        corpo2 = (f'<div class="tiles" style="grid-template-columns:repeat(4,1fr)">{tiles}</div>'
                  f'<div class="grid3"><div><h2 style="font-size:13px;margin:0 0 4px">Atribuição no ano (p.p.), ex-CDI</h2>{g_atr}</div><div>{g_m}<table class="compact" style="margin-top:6px"><thead><tr><th>Ano</th><th>Fundo</th><th>CDI</th><th>Δ</th></tr></thead><tbody>{tr}</tbody></table></div>'
                  f'<div><h2 style="font-size:13px;margin:0 0 4px">Minha leitura</h2><ul class="note" style="font-size:11.8px;line-height:1.35;padding-left:16px">{li(C.get("leitura", []))}</ul></div></div>')
        out.append(slide("Opinião qualificada", f"carta-{f.stem}-desempenho", f"{nome} · desempenho e leitura", corpo2,
                         "O que o fundo entregou, de onde veio o resultado, e o que a carta diz de estrutural.",
                         f"Relatório mensal e carta do gestor, {C.get('gestora', '')}. Atribuição e retornos como publicados; 'Minha leitura' é interpretação nossa."))
        pf = slide_performance_cartas(C, f.stem, sgs)
        if pf:
            out.append(pf)
        ct = slide_cartas_trimestrais(C, f.stem)
        if ct:
            out.append(ct)
        lt = slide_linha_tempo(C, f.stem, M, sgs)
        if lt:
            out.append(lt)
    out += slides_carteiras_cvm(M)
    return out


def svg_perf_periodos(cid: str, fundo: list[list], cdi: list[list], periodos: list[dict], W=1080, H=300) -> str:
    """Retorno acumulado diário do fundo (linha colorida pelo período/carta) e do CDI (cinza), faixas de fundo por período.
    fundo = [[data, cota]], cdi = [[data, % a.d.]] (SGS 12). Escreve dentro de cada faixa o retorno do fundo e do CDI no período."""
    ML, MR, MT, MB = 50, 16, 40, 26
    if len(fundo) < 2:
        return '<div class="empty small">Cota diária do fundo ausente: rode <code>python coletar.py diario</code>.</div>'
    c0 = fundo[0][1]
    f_pts = [[d, (v / c0 - 1) * 100] for d, v in fundo]
    acc, c_pts = 1.0, []
    cdi_d = {d: v for d, v in cdi}
    for d, _ in fundo:
        acc *= 1 + cdi_d.get(d, 0) / 100
        c_pts.append([d, (acc - 1) * 100])
    c_idx = {d: v for d, v in c_pts}
    f_idx = {d: v for d, v in f_pts}
    d0, d1 = datetime.fromisoformat(f_pts[0][0]).toordinal(), datetime.fromisoformat(f_pts[-1][0]).toordinal()
    span = max(d1 - d0, 30)
    ys = [p[1] for p in f_pts] + [p[1] for p in c_pts]
    lo, hi = min(ys + [0]), max(ys)
    pad = (hi - lo) * 0.06
    lo, hi = lo - pad, hi + pad
    X = lambda o: ML + (o - d0) / span * (W - ML - MR)
    Y = lambda v: MT + (hi - v) / (hi - lo) * (H - MT - MB)
    out = []
    # faixas por período, com rótulo e retorno do fundo × CDI no período
    for p in periodos:
        a = max(datetime.fromisoformat(p["ini"]).toordinal(), d0)
        b = min(datetime.fromisoformat(p["fim"]).toordinal(), d1)
        if b <= a:
            continue
        cor = p.get("cor", "var(--mut)")
        out.append(f'<rect x="{X(a):.1f}" y="{MT}" width="{X(b) - X(a):.1f}" height="{H - MT - MB}" style="fill:{cor};opacity:.10"/>')
        out.append(f'<line x1="{X(a):.1f}" x2="{X(a):.1f}" y1="{MT}" y2="{H - MB}" style="stroke:{cor};stroke-width:1;opacity:.5"/>')
        # retorno no período: cota do último dia anterior ao início (ou primeiro dia) até o fim
        dias = [q[0] for q in f_pts if p["ini"] <= q[0] <= p["fim"]]
        if dias:
            ant = [q[0] for q in f_pts if q[0] < p["ini"]]
            base = ant[-1] if ant else dias[0]
            rf = (1 + f_idx[dias[-1]] / 100) / (1 + f_idx[base] / 100) - 1
            rc = (1 + c_idx[dias[-1]] / 100) / (1 + c_idx[base] / 100) - 1
            xm = (X(a) + X(b)) / 2
            out.append(f'<text x="{xm:.1f}" y="{MT - 24}" text-anchor="middle" style="fill:{cor};font-size:11.5px;font-weight:600">{p["rotulo"]}</text>'
                       f'<text class="tick" x="{xm:.1f}" y="{MT - 10}" text-anchor="middle">{num(rf * 100, 1, "+" if rf > 0 else "", "%")} · CDI {num(rc * 100, 1, suf="%")}</text>')
    for t in ticks(lo, hi, 4):
        if lo <= t <= hi:
            out.append(f'<line class="grid" x1="{ML}" x2="{W - MR}" y1="{Y(t):.1f}" y2="{Y(t):.1f}"/>'
                       f'<text class="tick" x="{ML - 6}" y="{Y(t) + 4:.1f}" text-anchor="end">{num(t, 0, suf="%")}</text>')
    out += _xticks(d0, d1, X, H - 8, W - ML - MR)
    # CDI: linha cinza única
    out.append('<path class="line" style="stroke:var(--axis);stroke-width:1.8" d="%s"/>' % " ".join(f"{'M' if i == 0 else 'L'}{X(datetime.fromisoformat(d).toordinal()):.1f},{Y(v):.1f}" for i, (d, v) in enumerate(c_pts)))
    # fundo: um segmento por período, na cor do período (o ponto anterior ao início liga os segmentos)
    for p in periodos:
        seg = [q for q in f_pts if p["ini"] <= q[0] <= p["fim"]]
        if not seg:
            continue
        ant = [q for q in f_pts if q[0] < p["ini"]]
        if ant:
            seg = [ant[-1]] + seg
        out.append('<path class="line" style="stroke:%s;stroke-width:2.2" d="%s"/>' % (p.get("cor", "var(--s1)"), " ".join(f"{'M' if i == 0 else 'L'}{X(datetime.fromisoformat(d).toordinal()):.1f},{Y(v):.1f}" for i, (d, v) in enumerate(seg))))
    xf, yf, yc = X(d1), Y(f_pts[-1][1]), Y(c_pts[-1][1])
    out.append(f'<circle class="dot" cx="{xf:.1f}" cy="{yf:.1f}" r="4" style="fill:var(--ink)"/><text class="endlab" x="{xf - 7:.1f}" y="{yf - 8:.1f}" text-anchor="end">fundo {num(f_pts[-1][1], 1, "+", "%")}</text>'
               f'<text class="endlab" x="{xf - 7:.1f}" y="{yc + 15:.1f}" text-anchor="end">CDI {num(c_pts[-1][1], 1, "+", "%")}</text>')
    return f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="Retorno acumulado do fundo e do CDI por período de carta">{"".join(out)}</svg>'


def slide_performance_cartas(C: dict, stem: str, sgs: dict) -> tuple[str, str] | None:
    """Performance diária do fundo (cota CVM) × CDI, pintada pela carta que explica cada período + tabela cenário/resultado."""
    per = C.get("periodos_cartas") or []
    if not per or not C.get("cnpj"):
        return None
    from fontes import cvm_inf_diario
    f = cvm_inf_diario.CACHE / f"{cvm_inf_diario._limpo(C['cnpj'])}.json"
    fundo = [[d, v] for d, v, *_ in json.loads(f.read_text(encoding="utf-8")).get("serie", [])] if f.exists() else []
    cdi = sgs.get("12", {}).get("serie") or []
    nome = C.get("fundo", stem)
    hoje = date.today().isoformat()
    per = [p for p in per if p["ini"] <= hoje]
    g = svg_perf_periodos(f"perf-{stem}", fundo, cdi, per, H=222)
    tr = []
    for p in per:
        cor = p.get("cor", "var(--mut)")
        a, b = p["ini"], p["fim"]
        fonte_cor = "var(--up)" if p.get("tipo") == "primária" else "var(--mut)"
        tr.append(f'<tr><td style="vertical-align:top;white-space:nowrap;padding:3px 6px;font-size:12px;line-height:1.25"><span style="display:inline-block;width:10px;height:10px;border-radius:3px;background:{cor};vertical-align:-1px;margin-right:6px"></span><b>{p["rotulo"]}</b><br>'
                  f'<small style="color:{fonte_cor};font-size:10.5px">{p.get("carta", "")}</small></td>'
                  f'<td style="text-align:left;white-space:normal;font-size:11.2px;line-height:1.25;vertical-align:top;padding:3px 6px">{p.get("cenario", "")}</td>'
                  f'<td style="text-align:left;white-space:normal;font-size:11.2px;line-height:1.25;vertical-align:top;padding:3px 6px">{p.get("resultado", "")}</td></tr>')
    tab = (f'<table class="compact" style="margin-top:6px"><thead><tr><th style="text-align:left;padding:3px 6px">Período · carta</th><th style="text-align:left;padding:3px 6px">Cenário que a carta descreve</th>'
           f'<th style="text-align:left;padding:3px 6px">O que explicou o resultado</th></tr></thead><tbody>{"".join(tr)}</tbody></table>')
    corpo = f'<div class="panel" style="padding:10px 14px 4px">{g}</div>{tab}'
    return slide("Opinião qualificada", f"carta-{stem}-performance", f"{nome} · performance explicada pelas cartas", corpo,
                 "Retorno acumulado desde o início; fundo colorido pela carta de cada trimestre, CDI em cinza.",
                 "Cota diária: CVM, informe diário (dados abertos), CNPJ do FICFI. CDI: BCB/SGS 12, capitalizado dia a dia. Cartas: LinkedIn do gestor; retrospectiva do primeiro ano vem da carta de 1 ano; períodos em cinza não têm carta.")


def slide_cartas_trimestrais(C: dict, stem: str) -> tuple[str, str] | None:
    """Cartas trimestrais do gestor (lidas na íntegra): uma coluna por carta, da mais recente para a mais antiga."""
    cartas = C.get("cartas_trimestrais") or []
    if not cartas:
        return None
    nome = C.get("fundo", stem)
    cols = []
    for c in cartas[:3]:
        d = c.get("data", "")
        cab = f'<div class="kicker" style="margin-bottom:2px">{c.get("ref", "")} · {d[8:]}/{d[5:7]}/{d[2:4]} · publicada {c.get("publicada", "")}</div>'
        tit = f'<h2 style="font-size:13.5px;margin:0 0 6px;line-height:1.3">{c.get("titulo", "")}</h2>'
        pts = "".join(f"<li>{p}</li>" for p in c.get("pontos", []))
        cols.append(f'<div style="border-left:2px solid var(--s1);padding-left:12px">{cab}{tit}<ul class="note" style="font-size:12.2px;line-height:1.38;padding-left:16px;margin:0">{pts}</ul></div>')
    tese = C.get("cenario", {}).get("tese_longa") or []
    rodape = (f'<div style="margin-top:14px;padding-top:10px;border-top:1px solid var(--line)"><h2 style="font-size:13px;margin:0 0 4px">O que atravessa as cartas (tese de longo prazo)</h2>'
              f'<ul class="note" style="font-size:12px;line-height:1.35;padding-left:16px;margin:0;columns:2;column-gap:28px">{"".join(f"<li>{t}</li>" for t in tese)}</ul></div>') if tese else ""
    corpo = f'<div class="grid3" style="align-items:start">{"".join(cols)}</div>{rodape}'
    return slide("Opinião qualificada", f"carta-{stem}-trimestrais", f"{nome} · cartas trimestrais", corpo,
                 "As cartas de gestão, uma por trimestre, como o gestor as publica no LinkedIn. O número de Selic e IPCA muda de carta para carta; a tese, não.",
                 f"Cartas do gestor publicadas como documento no LinkedIn de Bruno Serra Fernandes; texto integral lido e resumido. {C.get('nota_fontes', '')}")


def slides_focus(M: dict, sgs: dict) -> list[tuple[str, str]]:
    """Slides com o Focus completo: painéis por grupo, dispersão, mensal, trimestral, 12/24m, Top 5."""
    S1, S2, MUT = "var(--s1)", "var(--s2)", "var(--axis)"
    T = M.get("focus_todos", {})
    if not T:
        return []
    anos = [str(a) for a in range(M["anos"][0], M["anos"][0] + 5)]
    data_focus = max((s[-1][0] for i in T.values() for s in i.values() if s), default="—")
    out = []

    def delta(serie, dias):
        if not serie:
            return None
        alvo = date.fromisoformat(serie[-1][0]).toordinal() - dias
        base = next((r[1] for r in reversed(serie) if date.fromisoformat(r[0]).toordinal() <= alvo), None)
        return serie[-1][1] - base if base is not None else None

    def dec_de(ind):
        return 2 if ind in ("Câmbio", "Selic") or ind.startswith("IPCA") or ind == "IGP-M" else 1

    # --- 3 painéis por grupo
    for grupo, inds in config.FOCUS_GRUPOS.items():
        tr = []
        for ind in inds:
            d = T.get(ind, {})
            s26 = d.get(anos[0], [])
            dc = dec_de(ind)
            un = config.FOCUS_UNIDADES.get(ind, "%")
            d4, d13 = delta(s26, 28), delta(s26, 91)
            cel = [f'<td class="tk">{ind.replace(" · ", " — ")}<small>{un}</small></td>']
            for j, a in enumerate(anos):
                s = d.get(a, [])
                cel.append(f'<td{" style=font-weight:600" if j == 0 else ""}>{num(s[-1][1], dc) if s else "—"}</td>')
                if j == 0:
                    cel.append(f'<td class="{dlt_cls(d4)}">{num(d4, 2, "+" if d4 and d4 > 0 else "") if d4 is not None else "—"}</td>'
                               f'<td class="{dlt_cls(d13)}">{num(d13, 2, "+" if d13 and d13 > 0 else "") if d13 is not None else "—"}</td>')
            cel.append(f'<td class="mut">{s26[-1][6] if s26 else "—"}</td>')
            tr.append("<tr>" + "".join(cel) + "</tr>")
        cab = f'<th>Indicador</th><th>{anos[0]}</th><th>Δ 4 sem</th><th>Δ 3 m</th>' + "".join(f"<th>{a}</th>" for a in anos[1:]) + "<th>n</th>"
        out.append(slide("Macro", "focus-" + grupo.split(",")[0].split(" ")[0].lower(), f"Focus · {grupo}",
                         f'<div class="panel"><table><thead><tr>{cab}</tr></thead><tbody>{"".join(tr)}</tbody></table></div>',
                         f"Mediana do mercado por ano de referência. Δ = variação da mediana de {anos[0]} em pontos.",
                         f"BCB Focus de {data_focus}, todos os respondentes. n = respondentes para {anos[0]}. Fiscais têm horizonte até {int(anos[0]) + 9}, aqui até {anos[-1]}."))

    # --- dispersão do consenso
    linhas = []
    for ind, dc, suf in (("IPCA", 2, "%"), ("Selic", 2, "%"), ("PIB Total", 1, "%"), ("Câmbio", 2, ""), ("Taxa de desocupação", 1, "%")):
        for a in anos[:2]:
            s = T.get(ind, {}).get(a, [])
            if s:
                r = s[-1]
                linhas.append((f"{ind} {a}", r[4], r[1], r[2], r[5], r[6], dc, suf))
    if linhas:
        leg = f'<div class="legend"><span><i style="background:{S1};border-radius:50%"></i>Mediana</span><span><i style="background:{S2};width:3px"></i>Média</span><span><i style="background:var(--grid)"></i>Mínimo–máximo dos respondentes</span></div>'
        out.append(slide("Macro", "focus-dispersao", "Focus · dispersão do consenso",
                         f'<div class="panel">{leg}{svg_dispersao(linhas)}</div>',
                         "Quanto os respondentes discordam: faixa entre a projeção mais baixa e a mais alta, com mediana e média.",
                         f"BCB Focus de {data_focus}. Cada linha tem escala própria."))

    # --- trajetória mensal: IPCA realizado (12m) + esperado (24m); câmbio e desocupação mensais
    fm = M.get("focus_mensais", {})
    lin = fm.get("linhas", [])
    def mensal(ind):
        rows = sorted([r for r in lin if r["Indicador"] == ind], key=lambda r: (r["DataReferencia"][3:], r["DataReferencia"][:2]))
        return [(r["DataReferencia"], r["Mediana"]) for r in rows]
    ipca_real = (sgs.get("433", {}).get("serie") or [])[-12:]
    cats = [f"{d[5:7]}/{d[2:4]}" for d, _ in ipca_real] + [f"{r[:2]}/{r[-2:]}" for r, _ in mensal("IPCA")]
    vals = [v for _, v in ipca_real] + [v for _, v in mensal("IPCA")]
    cores = [MUT] * len(ipca_real) + [S1] * len(mensal("IPCA"))
    g_ipca = svg_colunas("IPCA mensal: realizado (cinza, IBGE) e esperado (azul, Focus), % ao mês", cats, vals, cores, 2, "%", H=270, passo_rotulo_x=2)
    def linha_mensal(ind, dec, suf, tit):
        pts = [[f"20{r[-2:]}-{r[:2]}-01", v] for r, v in mensal(ind)]
        return svg_linhas("fm-" + ind[:6].replace(" ", ""), [(ind, S1, pts)], dec, suf=suf, titulo=tit, H=230)
    out.append(slide("Macro", "focus-mensal", "Focus · trajetória mensal",
                     f'<div class="panel">{g_ipca}</div><div class="grid2" style="margin-top:14px"><div>{linha_mensal("Câmbio", 2, "", "Câmbio esperado por mês (R$/US$)")}</div><div>{linha_mensal("Taxa de desocupação", 1, "%", "Taxa de desocupação esperada por mês (%)")}</div></div>',
                     "Os próximos 24 meses como o mercado os vê, mês a mês.",
                     f"BCB Focus de {fm.get('data', '—')} (ExpectativaMercadoMensais) e SGS 433 para o realizado. Aberturas mensais do IPCA e IGP-M ficam em data/macro.json."))

    # --- trajetória trimestral
    ft = M.get("focus_trimestrais", {})
    lt = ft.get("linhas", [])
    def trim(ind):
        rows = sorted([r for r in lt if r["Indicador"] == ind], key=lambda r: (r["DataReferencia"][2:], r["DataReferencia"][0]))
        return [f"{r['DataReferencia'][0]}T{r['DataReferencia'][-2:]}" for r in rows], [r["Mediana"] for r in rows]
    g_tri = []
    for ind, dc, suf, tit in (("PIB Total", 1, "%", "PIB: variação esperada contra o mesmo trimestre do ano anterior (%)"),
                              ("IPCA", 2, "%", "IPCA esperado no trimestre (%)"),
                              ("Taxa de desocupação", 1, "%", "Taxa de desocupação esperada (%)"),
                              ("Câmbio", 2, "", "Câmbio esperado no fim do trimestre (R$/US$)")):
        c, v = trim(ind)
        if c:
            g_tri.append(f"<div>{svg_colunas(tit, c, v, S1, dc, suf, W=470, H=230)}</div>")
    if g_tri:
        out.append(slide("Macro", "focus-trimestral", "Focus · trajetória trimestral",
                         f'<div class="grid2">{"".join(g_tri)}</div>', "Oito trimestres à frente.",
                         f"BCB Focus de {ft.get('data', '—')} (ExpectativasMercadoTrimestrais). Aberturas trimestrais do IPCA em data/macro.json."))

    # --- inflação 12 e 24 meses à frente
    fh = M.get("focus_horizonte", {})
    h12, h24 = fh.get("12m", {}), fh.get("24m", {})
    if h12.get("IPCA"):
        def pts_h(rows):
            return [[r[0], (r[3] if len(r) > 3 and r[3] is not None else r[1]), r[1], (r[4] if len(r) > 4 else None), (r[5] if len(r) > 5 else None), r[2]] for r in rows]
        def banda_h(rows):
            return [[r[0], r[4], r[5]] for r in rows if len(r) > 5]
        g = (svg_linhas("fh-ipca12", [("12 meses", S1, pts_h(h12["IPCA"]))], 2, suf="%", titulo="IPCA esperado nos próximos 12 meses: média e faixa mín–máx (suavizado, % a.a.)",
                        W=620, H=200, bandas={0: banda_h(h12["IPCA"])}, extras=["mediana", "mín", "máx", "n"])
             + svg_linhas("fh-ipca24", [("24 meses", S2, pts_h(h24.get("IPCA", [])))], 2, suf="%", titulo="IPCA esperado nos próximos 24 meses",
                          W=620, H=200, bandas={0: banda_h(h24.get("IPCA", []))}, extras=["mediana", "mín", "máx", "n"]))
        tr = []
        for ind in ("IPCA", "IPCA Livres", "IPCA Administrados", "IPCA Serviços", "IPCA Bens industrializados", "IPCA Alimentação no domicílio", "IGP-M"):
            a, b = h12.get(ind), h24.get(ind)
            tr.append(f'<tr><td class="tk">{ind}</td><td>{num(a[-1][1], 2, suf="%") if a else "—"}</td><td>{num(b[-1][1], 2, suf="%") if b else "—"}</td><td class="mut">{a[-1][2] if a else "—"}</td></tr>')
        leg = f'<div class="legend"><span><i style="background:{S1}"></i>12 meses à frente</span><span><i style="background:{S2}"></i>24 meses à frente</span></div>'
        out.append(slide("Macro", "focus-horizonte", "Focus · inflação 12 e 24 meses à frente",
                         f'<div class="grid2" style="grid-template-columns:2fr 1fr"><div>{leg}{g}</div><div><table><thead><tr><th>Abertura</th><th>12m</th><th>24m</th><th>n</th></tr></thead><tbody>{"".join(tr)}</tbody></table></div></div>',
                         "A âncora de curto e médio prazo: o que o mercado espera de inflação acumulada nos próximos 12 e 24 meses.",
                         f"BCB Focus, séries suavizadas desde {config.FOCUS_DESDE[:7]}. Tabela: última leitura por abertura."))

    # --- Focus 12/24 m vs inflação implícita de 2 anos (Tesouro Direto: pré 2a contra IPCA+ 2a, vencimento constante)
    pc = config.DATA / "curva_tesouro.json"
    CT = json.loads(pc.read_text(encoding="utf-8")) if pc.exists() else {}
    if h12.get("IPCA") and CT.get("pre") and 2 in CT.get("prazos_pre", []) and 2 in CT.get("prazos_real", []):
        ip, ir = CT["prazos_pre"].index(2) + 1, CT["prazos_real"].index(2) + 1
        real2 = {r[0]: r[ir] for r in CT.get("real", []) if r[ir] is not None}
        imp2 = [[r[0], ((1 + r[ip] / 100) / (1 + real2[r[0]] / 100) - 1) * 100] for r in CT["pre"]
                if r[ip] is not None and r[0] in real2 and r[0] >= config.FOCUS_DESDE]
        f12 = [[r[0], (r[3] if len(r) > 3 and r[3] is not None else r[1])] for r in h12["IPCA"]]
        f24 = [[r[0], (r[3] if len(r) > 3 and r[3] is not None else r[1])] for r in h24.get("IPCA", [])]
        g = svg_linhas("fh-implicita", [("Focus 12 m", S1, f12), ("Focus 24 m", S2, f24), ("implícita 2 a (Tesouro)", "var(--s3)", imp2)], 2, suf="%",
                       W=900, H=320, refs=[("meta 3%", 3.0)], titulo="IPCA esperado: Focus 12 e 24 meses (média) contra a inflação implícita de 2 anos na curva (% a.a.)")
        # spread implícita − Focus 24 m (prêmio de inflação / risco): série diária nas datas comuns e resumo
        d24 = {d: v for d, v in f24}
        spread = [[d, v - d24[d]] for d, v in imp2 if d in d24]
        g2 = svg_linhas("fh-implicita-spread", [("implícita 2 a − Focus 24 m", "var(--s3)", spread)], 2, suf=" p.p.", W=400, H=150,   # W = largura da coluna: fontes reais ≥ 11 px
                        refs=[("zero", 0.0)], titulo="Prêmio: implícita 2 anos menos Focus 24 meses (p.p.)")
        def med(s, dias):
            if not s:
                return None
            corte = date.fromisoformat(s[-1][0]).toordinal() - dias
            v = [x[1] for x in s if date.fromisoformat(x[0]).toordinal() >= corte]
            return sum(v) / len(v) if v else None
        tr = "".join(f'<tr><td class="tk">{rot}</td><td>{num(s[-1][1], 2, suf=suf) if s else "—"}</td><td>{num(med(s, 365), 2, suf=suf) if s else "—"}</td><td>{num(min(x[1] for x in s), 2, suf=suf) if s else "—"}</td><td>{num(max(x[1] for x in s), 2, suf=suf) if s else "—"}</td></tr>'
                     for rot, s, suf in (("Focus 12 m", f12, "%"), ("Focus 24 m", f24, "%"), ("Implícita 2 a", imp2, "%"), ("Prêmio (impl. − Focus 24 m)", spread, " p.p.")))
        tab = f'<table class="mini"><thead><tr><th>Série</th><th>Último</th><th>Média 12 m</th><th>Mín. desde {config.FOCUS_DESDE[:4]}</th><th>Máx.</th></tr></thead><tbody>{tr}</tbody></table>'
        leg = f'<div class="legend"><span><i style="background:{S1}"></i>Focus 12 m</span><span><i style="background:{S2}"></i>Focus 24 m</span><span><i style="background:var(--s3)"></i>implícita 2 anos (pré 2a vs IPCA+ 2a)</span></div>'
        out.append(slide("Macro", "focus-vs-implicita", "Focus vs inflação implícita",
                         f'{leg}{g}<div class="grid2" style="align-items:start;margin-top:8px"><div>{g2}</div><div>{tab}</div></div>',
                         "O que o economista responde ao Focus contra o que o mercado paga na curva: a implícita de 2 anos carrega prêmio de risco e sazonalidade, por isso costuma correr acima do Focus 24 meses; o prêmio abrindo ou fechando é o sinal.",
                         "BCB Focus (12 e 24 meses à frente, suavizado, média dos respondentes). Implícita: Tesouro Direto, vencimento constante de 2 anos, (1 + pré)/(1 + IPCA+) − 1 (fontes/curva.py). Séries desde " + config.FOCUS_DESDE[:4] + "."))

    # --- Focus fiscal: dívida bruta e líquida por ano de referência (série completa) contra o realizado (SGS)
    dirp = config.DATA / "focus_longo"
    blocos_div, tabs_div = [], []
    a0 = M["anos"][0]
    for ind, cod in (("Dívida bruta do governo geral", "13762"), ("Dívida líquida do setor público", "4513")):
        anos_ref = list(range(a0 - 1, a0 + getattr(config, "FOCUS_FISCAL_ANOS_FRENTE", 4) + 1))
        srs = {}
        for ano in anos_ref:
            f = dirp / f"{ind.replace(' ', '_')}_{ano}.json"
            if f.exists():
                srs[ano] = [[r[0], r[1]] for r in json.loads(f.read_text(encoding="utf-8")) if r[1] is not None]
        if not srs:
            continue
        pal = ["var(--axis)", "var(--s3)", "var(--s2)", "var(--s1)", "var(--s4)", "var(--mut)"]
        series = [(f"Focus {ano}", pal[i % len(pal)], s) for i, (ano, s) in enumerate(sorted(srs.items()))]
        real = (sgs.get(cod) or {}).get("serie") or []
        if real:
            series.append(("realizado (mensal)", "var(--ink)", [[d, v] for d, v in real]))
        blocos_div.append(svg_linhas(f"fd-{cod}", series, 1, suf="%", W=420, H=230, ini=3, titulo=f"{ind} (% do PIB)"))   # W = largura da coluna: fontes reais ≥ 11 px
        def cel_d(s, dias):
            d = delta(s, dias)
            if d is None:
                return "<td>—</td>"
            d = round(d, 1) or 0.0                                   # evita "-0,0"
            return f'<td class="{dlt_cls(d)}">{num(d, 1, "+" if d > 0 else "", " p.p.")}</td>'
        tr = "".join(f'<tr><td class="tk"><i style="display:inline-block;width:9px;height:9px;border-radius:3px;background:{cor};margin-right:5px"></i>{ano}</td><td>{num(s[-1][1], 1, suf="%")}</td>{cel_d(s, 28)}{cel_d(s, 91)}{cel_d(s, 365)}</tr>'
                     for (ano, s), (_, cor, _) in zip(sorted(srs.items()), series) if s)
        tabs_div.append(f'<div><h2 style="font-size:13px;margin:0 0 4px">{ind}{f" · realizado {real[-1][0][5:7]}/{real[-1][0][2:4]}: {num(real[-1][1], 1, suf=chr(37))}" if real else ""}</h2>'
                        f'<table class="mini"><thead><tr><th>Ano ref.</th><th>Focus</th><th>Δ 4 sem</th><th>Δ 3 m</th><th>Δ 12 m</th></tr></thead><tbody>{tr}</tbody></table></div>')
    if blocos_div:
        leg = ('<div class="legend"><span><i style="background:var(--ink)"></i>realizado (mensal)</span>'
               + "".join(f'<span><i style="background:{c}"></i>Focus {n[6:]}</span>' for n, c, _ in series if n.startswith("Focus")) + '</div>')
        out.append(slide("Macro", "focus-divida", "Focus · dívida bruta e líquida",
                         f'{leg}<div class="grid2">{"".join(blocos_div)}</div><div class="grid2" style="margin-top:10px;align-items:start">{"".join(tabs_div)}</div>',
                         "Como o mercado revisa a trajetória fiscal: a expectativa de dívida para cada ano de referência, ao longo do tempo, contra o realizado.",
                         "BCB Focus (ExpectativasMercadoAnuais, mediana, todos os respondentes; fiscais têm horizonte até 2035) e SGS 13762 (DBGG) / 4513 (DLSP), % do PIB, mensal."))

    # --- Top 5 vs mercado
    t5 = M.get("focus_top5", {}).get("linhas", [])
    t5s = M.get("focus_top5_selic", {}).get("linhas", [])
    if t5:
        nomes = {"C": "curto", "M": "médio", "L": "longo"}
        tr = []
        for ind, dc in (("IPCA", 2), ("Selic", 2), ("PIB Total", 1), ("Câmbio", 2), ("Taxa de desocupação", 1)):
            for a in anos[:2]:
                geral = T.get(ind, {}).get(a, [])
                g = geral[-1][1] if geral else None
                cel = [f'<td class="tk">{ind}<small>{a}</small></td><td style="font-weight:600">{num(g, dc)}</td>']
                for tc in ("C", "M", "L"):
                    r = next((x for x in t5 if x["Indicador"] == ind and x["DataReferencia"] == a and x["tipoCalculo"] == tc), None)
                    v = r["Mediana"] if r else None
                    dif = (v - g) if (v is not None and g is not None) else None
                    cel.append(f'<td>{num(v, dc)} <small class="{dlt_cls(dif)}">{num(dif, 2, "+" if dif and dif > 0 else "") if dif is not None else ""}</small></td>')
                tr.append("<tr>" + "".join(cel) + "</tr>")
        tab5 = f'<table><thead><tr><th>Indicador</th><th>Mercado</th><th>Top 5 {nomes["C"]}</th><th>Top 5 {nomes["M"]}</th><th>Top 5 {nomes["L"]}</th></tr></thead><tbody>{"".join(tr)}</tbody></table>'
        sel = ""
        if t5s:
            fs = {r["reuniao"]: r["mediana"] for r in M.get("focus_selic", [])}
            reun = sorted({r["reuniao"] for r in t5s}, key=lambda x: (int(x.split("/")[1]), int(x[1:].split("/")[0])))[:8]
            tr = []
            for rn in reun:
                c = next((x["mediana"] for x in t5s if x["reuniao"] == rn and x["tipoCalculo"] == "C"), None)
                l = next((x["mediana"] for x in t5s if x["reuniao"] == rn and x["tipoCalculo"] == "L"), None)
                tr.append(f'<tr><td class="tk">{rn}</td><td>{num(fs.get(rn), 2)}</td><td>{num(c, 2)}</td><td>{num(l, 2)}</td></tr>')
            sel = f'<div><table><thead><tr><th>Reunião</th><th>Mercado</th><th>Top 5 curto</th><th>Top 5 longo</th></tr></thead><tbody>{"".join(tr)}</tbody></table></div>'
        out.append(slide("Macro", "focus-top5", "Focus · Top 5 contra o mercado",
                         f'<div class="grid2" style="grid-template-columns:3fr 2fr"><div>{tab5}</div>{sel}</div>',
                         "As cinco instituições que mais acertaram, por prazo, contra a mediana de todos. O número pequeno é a diferença.",
                         f"BCB Focus de {M.get('focus_top5', {}).get('data', '—')} (Top5Anuais e Top5Selic). Ranking do próprio BCB por acurácia de curto, médio e longo prazo."))
    # --- BCB (Relatório de Política Monetária) vs Focus
    prpm = config.RAIZ / "macro_bcb" / "rpm.json"
    if prpm.exists():
        R = json.loads(prpm.read_text(encoding="utf-8"))
        S1, S2 = "var(--s1)", "var(--s2)"
        tr = []
        for item in R.get("comparaveis", []):
            ind, hor, bcb, dc, un = item["indicador"], item["horizonte"], item.get("bcb"), item.get("dec", 1), item.get("unidade", "%")
            foco = None
            if item.get("focus_ind") and item.get("focus_ref"):
                s = T.get(item["focus_ind"], {}).get(str(item["focus_ref"]), [])
                foco = s[-1][1] if s else None
            dif = (bcb - foco) if (bcb is not None and foco is not None) else None
            tr.append(f'<tr><td class="tk">{ind}<small>{hor}</small></td><td style="font-weight:600">{num(bcb, dc)}{un if un != "%" else "%"}</td>'
                      f'<td>{num(foco, dc)}{"%" if foco is not None and un == "%" else ""}</td><td class="{dlt_cls(dif)}">{num(dif, 2, "+" if dif and dif > 0 else "") if dif is not None else "—"}</td>'
                      f'<td class="mut" style="white-space:normal;text-align:left;font-size:12.5px">{item.get("nota", "")}</td></tr>')
        tab = f'<table class="compact"><thead><tr><th>Indicador</th><th>BCB</th><th>Focus</th><th>BCB − Focus</th><th>Premissa / observação</th></tr></thead><tbody>{"".join(tr)}</tbody></table>'
        pontos = "".join(f'<li>{p}</li>' for p in R.get("pontos", [])[:6])
        traj = R.get("trajetoria_ipca", [])
        g_traj = ""
        if traj:
            ef = R.get("trajetoria_ipca_efetivo_ate")
            cores = []
            passou = False
            for c, _ in traj:
                cores.append("var(--axis)" if not passou else S1)
                if c == ef:
                    passou = True
            g_traj = f'<div class="panel" style="margin-bottom:14px;padding:14px 18px 6px">{svg_colunas("IPCA acumulado em 4 trimestres, cenário de referência do Copom (cinza = efetivo, azul = projeção), %", [c for c, _ in traj], [v for _, v in traj], cores, 1, "%", H=190)}</div>'
        out.append(slide("Macro", "bcb-cenario", "BCB · cenário de referência",
                         f'{g_traj}<div class="panel"><h2 style="font-size:14px;margin:0 0 8px">O que o Copom diz</h2><ul class="note" style="font-size:14px;padding-left:18px;columns:2;column-gap:36px">{pontos}</ul></div>',
                         "A trajetória de inflação que o Copom projeta e as premissas por trás dela.",
                         f'{R.get("fonte_nome", "BCB, Relatório de Política Monetária")}.'))
        out.append(slide("Macro", "bcb-vs-focus", "BCB vs Focus",
                         f'<div class="panel">{tab}</div>',
                         R.get("lede", "Projeções do cenário de referência do Copom contra a mediana do mercado."),
                         f'{R.get("fonte_nome", "BCB, Relatório de Política Monetária")} · {R.get("data", "")}. Focus de {data_focus}.'))
        if R.get("credito") or R.get("riscos_alta"):
            cab = "".join(f"<th>{c}</th>" for c in R.get("credito_cab", []))
            tr = "".join(f'<tr><td class="tk">{r[0]}</td>' + "".join(f"<td>{num(v, 1, suf='%')}</td>" for v in r[1:]) + "</tr>" for r in R.get("credito", []))
            tcred = f'<div><h2 style="font-size:14px;margin:0 0 8px">Crédito: variação do saldo em 12 meses (%)</h2><table><thead><tr><th>Carteira</th>{cab}</tr></thead><tbody>{tr}</tbody></table></div>'
            ra = "".join(f"<li>{p}</li>" for p in R.get("riscos_alta", []))
            rb = "".join(f"<li>{p}</li>" for p in R.get("riscos_baixa", []))
            risc = (f'<div><h2 style="font-size:14px;margin:0 0 8px">Balanço de riscos: assimetria altista</h2>'
                    f'<div class="legend"><span><i style="background:var(--dn)"></i>Alta</span></div><ul class="note" style="padding-left:18px;font-size:13.5px">{ra}</ul>'
                    f'<div class="legend"><span><i style="background:var(--up)"></i>Baixa</span></div><ul class="note" style="padding-left:18px;font-size:13.5px">{rb}</ul></div>')
            out.append(slide("Macro", "bcb-credito-riscos", "BCB · crédito e balanço de riscos",
                             f'<div class="grid2">{tcred}{risc}</div>',
                             R.get("sinalizacao", ""),
                             f'{R.get("fonte_nome", "BCB, Relatório de Política Monetária")}: boxe de crédito e seção 2.3.'))
    return out


def svg_erros(cid: str, curvas: list[tuple[int, list[tuple[float, float]]]], banda: list[tuple[int, float, float]],
              unidade: str, dec: int, destaque: int | None, titulo: str, W=960, H=360) -> str:
    """Erro do Focus (mediana − realizado) por meses até o fim do ano de referência.
    Linhas finas = um ano cada; sombra = ±erro absoluto médio; linha grossa = erro médio (viés). Destaque = ano mais recente."""
    ML, MR, MT, MB = 56, 16, 24, 34
    if not curvas:
        return '<div class="empty small">Sem séries longas. Rode <code>python coletar.py --so-focus-longo</code>.</div>'
    ys = [e for _, c in curvas for _, e in c] + [x for _, me, mae in banda for x in (me - mae, me + mae)]
    lo, hi = min(ys + [0]), max(ys + [0])
    pad = (hi - lo) * 0.06 or 1
    lo, hi = lo - pad, hi + pad
    yt = ticks(lo, hi, 5)
    X = lambda m: ML + (m + 36) / 36 * (W - ML - MR)
    Y = lambda v: MT + (hi - v) / (hi - lo) * (H - MT - MB)
    out = [f'<text class="sub" x="{ML}" y="12">{titulo}</text>']
    for t in yt:
        if lo <= t <= hi:
            out.append(f'<line class="grid" x1="{ML}" x2="{W - MR}" y1="{Y(t):.1f}" y2="{Y(t):.1f}"/><text class="tick" x="{ML - 6}" y="{Y(t) + 4:.1f}" text-anchor="end">{num(t, dec)}</text>')
    for m in range(-36, 1, 6):
        out.append(f'<text class="tick" x="{X(m):.1f}" y="{H - 12}" text-anchor="middle">{"fim do ano" if m == 0 else f"{-m} m antes"}</text>')
    if banda:
        ida = " ".join(f"{'M' if i == 0 else 'L'}{X(m):.1f},{Y(me + mae):.1f}" for i, (m, me, mae) in enumerate(banda))
        volta = " ".join(f"L{X(m):.1f},{Y(me - mae):.1f}" for m, me, mae in reversed(banda))
        out.append(f'<path d="{ida} {volta} Z" style="fill:var(--s1);opacity:.14;stroke:none"/>')
    out.append(f'<line class="axis" x1="{ML}" x2="{W - MR}" y1="{Y(0):.1f}" y2="{Y(0):.1f}" style="stroke:var(--ink2)"/>')
    for ano, c in curvas:
        if len(c) < 2:
            continue
        d = " ".join(f"{'M' if i == 0 else 'L'}{X(m):.1f},{Y(e):.1f}" for i, (m, e) in enumerate(c))
        if ano == destaque:
            continue
        out.append(f'<path class="line" data-tip="{ano}: erro no fim do ano {num(c[-1][1], dec, "+" if c[-1][1] > 0 else "")} {unidade}" d="{d}" style="stroke:var(--s1);opacity:.28;stroke-width:1.3"/>')
    if banda:
        out.append('<path class="line" d="%s" style="stroke:var(--s1);stroke-width:2.5"/>' % " ".join(f"{'M' if i == 0 else 'L'}{X(m):.1f},{Y(me):.1f}" for i, (m, me, _) in enumerate(banda)))
    for ano, c in curvas:
        if ano == destaque and len(c) >= 2:
            d = " ".join(f"{'M' if i == 0 else 'L'}{X(m):.1f},{Y(e):.1f}" for i, (m, e) in enumerate(c))
            out.append(f'<path class="line" data-tip="{ano}: erro no fim do ano {num(c[-1][1], dec, "+" if c[-1][1] > 0 else "")} {unidade}" d="{d}" style="stroke:var(--s2);stroke-width:2.5"/>')
            out.append(f'<text class="endlab" x="{X(c[-1][0]) - 6:.1f}" y="{Y(c[-1][1]) - 8:.1f}" text-anchor="end">{ano}</text>')
    return f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="{titulo}">{"".join(out)}</svg>'


def slides_assertividade() -> list[tuple[str, str]]:
    """Assertividade do Focus: erro por horizonte, viés e acerto dentro da faixa mín–máx, por indicador, desde 2002."""
    preal = config.DATA / "realizado.json"
    dirp = config.DATA / "focus_longo"
    if not preal.exists() or not dirp.exists():
        return []
    real = json.loads(preal.read_text(encoding="utf-8"))
    hoje = date.today()
    H = config.FOCUS_ASSERT_HORIZONTES
    out, resumo = [], []
    for ind, (cod, chave) in config.FOCUS_ASSERT_INDICADORES.items():
        dec = 2 if ind in ("IPCA", "Selic", "Câmbio") else 1
        unid = "R$" if ind == "Câmbio" else "p.p."
        linhas, curvas = [], []
        erros = {h: [] for h in H}
        dentro = {h: [] for h in H}
        for ano in range(config.FOCUS_ASSERT_ANO_INI, hoje.year):
            r = real.get(chave, {}).get(str(ano))
            f = dirp / f"{ind.replace(' ', '_')}_{ano}.json"
            if r is None or not f.exists():
                continue
            s = json.loads(f.read_text(encoding="utf-8"))
            fim = date(ano, 12, 31).toordinal()
            por_mes = {}
            for row in s:
                por_mes[row[0][:7]] = row              # última pesquisa de cada mês
            curva = []
            for ym, row in sorted(por_mes.items()):
                m = -(fim - date.fromisoformat(row[0]).toordinal()) / 30.4375
                if -36 <= m <= 0.5:
                    curva.append((round(min(m, 0), 2), row[1] - r))
            curvas.append((ano, curva))
            linha = {"ano": ano, "real": r}
            for h in H:
                alvo = fim - h * 30.4375
                obs = None
                for row in s:
                    if date.fromisoformat(row[0]).toordinal() <= alvo:
                        obs = row
                    else:
                        break
                if obs is None or date.fromisoformat(obs[0]).toordinal() < alvo - 45:
                    linha[h] = None
                    continue
                e = obs[1] - r
                linha[h] = e
                erros[h].append(e)
                if obs[3] is not None and obs[4] is not None:
                    dentro[h].append(obs[3] <= r <= obs[4])
            linhas.append(linha)
        if not linhas:
            continue
        met = {}
        for h in H:
            E = erros[h]
            if E:
                met[h] = (sum(abs(e) for e in E) / len(E), sum(E) / len(E),
                          (100 * sum(dentro[h]) / len(dentro[h])) if dentro[h] else None, len(E))
        resumo.append((ind, unid, dec, met))
        # banda ±EAM e viés por mês antes do fim
        buckets: dict[int, list[float]] = {m: [] for m in range(-36, 1)}
        for _, c in curvas:
            for m, e in c:
                mb = max(-36, min(0, round(m)))
                buckets[mb].append(e)
        banda = [(m, sum(E) / len(E), sum(abs(e) for e in E) / len(E)) for m, E in sorted(buckets.items()) if len(E) >= 3]
        destaque = max(a for a, _ in curvas)
        g = (f'<div class="legend" style="gap:14px"><span><i style="background:var(--s1);opacity:.3"></i>um ano por linha</span><span><i style="background:var(--s1);opacity:.14"></i>±erro abs. médio</span>'
             f'<span><i style="background:var(--s1)"></i>viés</span><span><i style="background:var(--s2)"></i>{destaque}</span></div>'
             + svg_erros(f"as-{ind[:4]}", curvas, banda, unid, dec, destaque, f"{ind}: mediana do Focus − realizado ({unid}), por mês antes do fim do ano", W=600, H=660))
        # tabela: últimos 12 anos + métricas de toda a amostra
        hs = [24, 12, 6, 3, 0]
        tr = []
        for l in linhas[-12:]:
            cel = [f'<td class="tk">{l["ano"]}</td><td>{num(l["real"], dec)}</td>']
            for h in hs:
                e = l.get(h)
                cel.append(f'<td class="{dlt_cls(e)}">{num(e, dec, "+" if e and e > 0 else "") if e is not None else "—"}</td>')
            tr.append("<tr>" + "".join(cel) + "</tr>")
        def mrow(nome, idx, fmt):
            return "<tr>" + f'<td class="tk">{nome}</td><td class="mut">{linhas[0]["ano"]}–{linhas[-1]["ano"]}</td>' + "".join(f"<td>{fmt(met[h][idx]) if h in met and met[h][idx] is not None else '—'}</td>" for h in hs) + "</tr>"
        tr.append(mrow("Erro abs. médio", 0, lambda v: num(v, dec)))
        tr.append(mrow("Viés (média do erro)", 1, lambda v: num(v, dec, "+" if v > 0 else "")))
        tr.append(mrow("Realizado dentro da faixa mín–máx", 2, lambda v: num(v, 0, suf="%")))
        cab = "".join(f"<th>{'fim' if h == 0 else f'{h} m'}</th>" for h in hs)
        tab = f'<table class="compact"><thead><tr><th>Ano</th><th>Realizado</th>{cab}</tr></thead><tbody>{"".join(tr)}</tbody></table>'
        out.append(slide("Macro", f"assert-{ind[:4].lower().replace(' ', '')}", f"Assertividade do Focus · {ind}",
                         f'<div class="grid2" style="grid-template-columns:3fr 2fr"><div>{g}</div><div>{tab}</div></div>',
                         f"Erro = mediana do Focus − realizado, em {unid}. Quanto antes do fim do ano, maior o erro; a sombra mostra o tamanho típico. Amostra {linhas[0]['ano']}–{linhas[-1]['ano']}.",
                         f"BCB Focus (Olinda, série diária por ano de referência) e SGS {cod} para o realizado. Tabela: últimos 12 anos; métricas da amostra completa."))
    if resumo:
        cab = "".join(f"<th>{'fim' if h == 0 else f'{h} m'}</th>" for h in H)
        tr = []
        for ind, unid, dec, met in resumo:
            for nome, idx, fmt in (("erro abs. médio", 0, lambda v, d=dec: num(v, d)), ("viés", 1, lambda v, d=dec: num(v, d, "+" if v > 0 else "")), ("dentro da faixa", 2, lambda v, d=dec: num(v, 0, suf="%"))):
                tr.append(f'<tr><td class="tk">{ind}<small>{nome} ({unid if idx < 2 else "% dos anos"})</small></td>' + "".join(f"<td>{fmt(met[h][idx]) if h in met and met[h][idx] is not None else '—'}</td>" for h in H) + "</tr>")
        out.insert(0, slide("Macro", "assert-resumo", "Assertividade do Focus · resumo",
                            f'<div class="panel"><table class="compact"><thead><tr><th>Indicador</th>{cab}</tr></thead><tbody>{"".join(tr)}</tbody></table></div>',
                            f"Quanto o Focus erra por horizonte, para onde erra, e com que frequência o realizado cai dentro da faixa de todos os respondentes. Anos {config.FOCUS_ASSERT_ANO_INI}–{hoje.year - 1}.",
                            "BCB Focus e SGS. Erro = mediana − realizado; viés positivo = Focus projetou acima do que ocorreu. IPCA e PIB em p.p., Selic em p.p., câmbio em R$."))
    return out


def svg_barras(titulo: str, grupos: list[tuple[str, float | None, float | None]], unidade: str) -> str:
    """Barras lado a lado (nunca empilhadas): série Minha vs Consenso por grupo. Um gráfico por métrica (eixo próprio)."""
    W, H, ML, MR, MT, MB = 470, 220, 64, 16, 26, 34
    vals = [v for _, a, b in grupos for v in (a, b) if v is not None]
    if not vals:
        return f'<div class="empty small">{titulo}: sem estimativas.</div>'
    hi = max(vals + [0]); lo = min(vals + [0])
    yt = ticks(lo, hi, 4, from_zero=True)
    lo, hi = min(lo, yt[0]), max(hi, yt[-1]) * 1.10
    Y = lambda v: MT + (hi - v) / (hi - lo) * (H - MT - MB)
    n = len(grupos)
    slot = (W - ML - MR) / n
    bw = min(24, slot * 0.22)
    out = [f'<text class="sub" x="{ML}" y="12">{titulo} ({unidade})</text>']
    for t in yt:
        out.append(f'<line class="grid" x1="{ML}" x2="{W - MR}" y1="{Y(t):.1f}" y2="{Y(t):.1f}"/>'
                   f'<text class="tick" x="{ML - 8}" y="{Y(t) + 4:.1f}" text-anchor="end">{num(t, 0)}</text>')
    for i, (lab, a, b) in enumerate(grupos):
        cx = ML + slot * (i + 0.5)
        for k, v, off in (("minha", a, -bw / 2 - 9), ("cons", b, bw / 2 + 9)):
            x = cx + off - bw / 2
            if v is None:
                out.append(f'<text class="tick" x="{cx + off:.1f}" y="{Y(0) - 6:.1f}" text-anchor="middle">—</text>')
                continue
            y, y0 = Y(max(v, 0)), Y(min(v, 0))
            h = max(y0 - y, 1)
            out.append(f'<rect class="bar" data-tip="{"Minha" if k == "minha" else "Consenso"} · {lab}: {num(v, 1)} {unidade}" x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{h:.1f}" rx="4" style="fill:{COR[k]}"/>'
                       f'<rect x="{x:.1f}" y="{y0 - 4 if v >= 0 else y:.1f}" width="{bw:.1f}" height="4" style="fill:{COR[k]}"/>'
                       f'<text class="cap" x="{x + bw / 2:.1f}" y="{y - 6 if v >= 0 else y0 + 14:.1f}" text-anchor="middle">{num(v, 0 if abs(v) >= 100 else 1)}</text>')
        out.append(f'<text class="tick" x="{cx:.1f}" y="{H - 12}" text-anchor="middle">{lab}</text>')
    out.append(f'<line class="axis" x1="{ML}" x2="{W - MR}" y1="{Y(0):.1f}" y2="{Y(0):.1f}"/>')
    return f'<svg class="chart bars" viewBox="0 0 {W} {H}" role="img" aria-label="Minha estimativa vs consenso">{"".join(out)}</svg>'


def svg_revisoes(ano: int, cons: list[dict], minhas: list[dict], campo="lucro") -> str:
    """Pequeno múltiplo: evolução do consenso (linha) e das minhas versões (pontos) para um ano fiscal."""
    W, H, ML, MR, MT, MB = 470, 200, 56, 16, 18, 30
    c = sorted([(r["data"], r[campo]) for r in cons if r["ano"] == ano and r[campo] is not None])
    m = sorted([(r["data"], r[campo]) for r in minhas if r["ano"] == ano and r[campo] is not None])
    if not c and not m:
        return f'<div class="empty small">{ano}: sem histórico ainda.</div>'
    dts = [d for d, _ in c + m]
    d0 = datetime.fromisoformat(min(dts)).toordinal() - 7
    d1 = max(datetime.fromisoformat(max(dts)).toordinal(), date.today().toordinal()) + 7
    span = max(d1 - d0, 30)
    ys = [v for _, v in c + m]
    lo, hi = min(ys), max(ys)
    pad = (hi - lo) * 0.15 or abs(hi) * 0.05 or 1
    lo, hi = lo - pad, hi + pad
    yt = ticks(lo, hi, 3)
    X = lambda d: ML + (datetime.fromisoformat(d).toordinal() - d0) / span * (W - ML - MR)
    Y = lambda v: MT + (hi - v) / (hi - lo) * (H - MT - MB)
    out = [f'<text class="sub" x="{ML}" y="12">Lucro {ano} (R$ mi)</text>']
    for t in yt:
        if lo <= t <= hi:
            out.append(f'<line class="grid" x1="{ML}" x2="{W - MR}" y1="{Y(t):.1f}" y2="{Y(t):.1f}"/>'
                       f'<text class="tick" x="{ML - 6}" y="{Y(t) + 4:.1f}" text-anchor="end">{num(t, 0)}</text>')
    datas = sorted(set(dts))
    if len(datas) <= 6:   # poucos pontos: rotula cada data, pulando as que colidiriam
        ux = -1e9
        for d in datas:
            if X(d) - ux < 60:
                continue
            ux = X(d)
            out.append(f'<text class="tick" x="{X(d):.1f}" y="{H - 10}" text-anchor="middle">{d[8:10]}/{d[5:7]}/{d[2:4]}</text>')
    else:                 # muitos: rotula meses dentro da janela
        for d in sorted({x[:7] for x in dts}):
            o = datetime.fromisoformat(d + "-01")
            if d0 <= o.toordinal() <= d1:
                out.append(f'<text class="tick" x="{X(d + "-01"):.1f}" y="{H - 10}" text-anchor="middle">{o.strftime("%m/%y")}</text>')
    if c:
        out.append('<path class="line" style="stroke:%s" d="%s"/>' % (COR["cons"], " ".join(f"{'M' if i == 0 else 'L'}{X(d):.1f},{Y(v):.1f}" for i, (d, v) in enumerate(c))))
        for d, v in c:
            out.append(f'<circle class="dot" data-tip="Consenso {d}: {num(v, 0)}" cx="{X(d):.1f}" cy="{Y(v):.1f}" r="4" style="fill:{COR["cons"]}"/>')
        out.append(f'<text class="endlab" x="{X(c[-1][0]) - 8:.1f}" y="{Y(c[-1][1]) - 8:.1f}" text-anchor="end">{num(c[-1][1], 0)}</text>')
    if m:
        out.append('<path class="line" style="stroke:%s" d="%s"/>' % (COR["minha"], " ".join(f"{'M' if i == 0 else 'L'}{X(d):.1f},{Y(v):.1f}" for i, (d, v) in enumerate(m))))
        for d, v in m:
            out.append(f'<circle class="dot" data-tip="Minha {d}: {num(v, 0)}" cx="{X(d):.1f}" cy="{Y(v):.1f}" r="4" style="fill:{COR["minha"]}"/>')
    return f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="Revisões {ano}">{"".join(out)}</svg>'


# ----------------------------------------------------------------------------- page
CSS = r"""
:root{color-scheme:light;--f-titulo:/*FT*/;--f-corpo:/*FC*/;--bg:#f4f1ea;--sf:#fbfaf6;--ink:#15171b;--ink2:#4b4e54;--mut:#8a8d92;--grid:#e6e2d8;--axis:#cfcac0;--ring:rgba(21,23,27,.09);
--s1:#2457c5;--s2:#e0662b;--s3:#1baf7a;--s4:#c98500;--up:#1e7a3c;--dn:#bf3a2f;--chip:#ebe7dd;--rail:#eeeae1;--acc:#2457c5}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--bg:#0e1013;--sf:#161920;--ink:#f1eee6;--ink2:#c2bfb6;--mut:#858892;--grid:#262a32;--axis:#3a3f49;--ring:rgba(255,255,255,.09);--s1:#5b8cff;--s2:#ff8a4c;--s3:#199e70;--s4:#eda100;--up:#4cc06a;--dn:#ff6b5e;--chip:#1e222b;--rail:#111419;--acc:#5b8cff}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#0e1013;--sf:#161920;--ink:#f1eee6;--ink2:#c2bfb6;--mut:#858892;--grid:#262a32;--axis:#3a3f49;--ring:rgba(255,255,255,.09);--s1:#5b8cff;--s2:#ff8a4c;--s3:#199e70;--s4:#eda100;--up:#4cc06a;--dn:#ff6b5e;--chip:#1e222b;--rail:#111419;--acc:#5b8cff}
*{box-sizing:border-box}html{scroll-behavior:smooth}/*ITALICO*/
body{margin:0;background:var(--bg);color:var(--ink);font:15.5px/1.5 var(--f-corpo);-webkit-font-smoothing:antialiased}
.serif{font-family:var(--f-titulo)}
/* trilho de navegação */
.rail{position:fixed;left:0;top:0;bottom:0;width:232px;background:var(--rail);border-right:1px solid var(--ring);padding:28px 20px;overflow:auto;z-index:5}
.rail .brand{font-size:18px;font-weight:600;letter-spacing:-.01em;margin:0 0 2px}.rail .brand small{display:block;font-size:12px;color:var(--mut);font-weight:400;margin-top:3px}
.rail .chip{margin:10px 0 22px}
.rail .sec{font-size:10.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--mut);margin:16px 0 6px}
.rail a{display:block;font-size:13px;color:var(--ink2);text-decoration:none;padding:5px 10px;border-radius:7px;line-height:1.3}
.rail a:hover{background:var(--chip)}.rail a.on{background:var(--ink);color:var(--bg)}
.janela,.janela-global{display:flex;flex-wrap:wrap;gap:4px;align-items:center;margin:0 0 6px}.janela{justify-content:flex-end}
.dec .janela{gap:3px}.dec .janela button{font-size:10.5px;padding:1px 6px}
.dec table.mini td,.dec table.mini th{padding:2px 5px;white-space:nowrap}.dec .mut{font-size:10.5px}
.janela button,.janela-global button{font:inherit;font-size:11px;padding:1px 8px;border-radius:999px;border:1px solid var(--ring);background:var(--sf);color:var(--ink2);cursor:pointer;line-height:1.5}
.janela button.on,.janela-global button.on{background:var(--ink);color:var(--bg);border-color:var(--ink)}.janela button:disabled{opacity:.35;cursor:default}
.janela-global{margin:8px 0 10px}.janela-global span{font-size:10.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--mut);width:100%;margin-bottom:2px}

/* painel de uma tela */
.slide.painel{padding:20px 40px 16px}.slide.painel header{margin-bottom:6px}.slide.painel h1{font-size:26px}.slide.painel .lede{font-size:13.5px;margin-top:2px}
.strip8{display:grid;grid-template-columns:repeat(8,1fr);gap:8px;margin-bottom:12px}.strip8 .tile{padding:8px 10px;border-radius:10px}.strip8 .tile .l{font-size:11px}.strip8 .tile .v{font-size:19px;margin-top:1px}.strip8 .tile .d{font-size:11px;margin-top:1px}
.pgrid{display:grid;grid-template-columns:1.15fr 1fr 1.05fr;gap:12px;align-items:start}
.pbox{background:var(--sf);border:1px solid var(--ring);border-radius:12px;padding:10px 12px}.pbox h2{font-size:12.5px;margin:0 0 6px;letter-spacing:.02em}.pbox+.pbox{margin-top:10px}
table.mini{font-size:12.5px}table.mini td,table.mini th{padding:3px 6px}table.mini th{font-size:10.5px}
.sinais{margin:0;padding-left:16px;font-size:12.1px;line-height:1.33}.sinais li{margin:0 0 3px}.sinais b{font-weight:600}
.opiniao{font-size:12.8px;line-height:1.45}.opiniao p{margin:0 0 5px}.opiniao .vazio{color:var(--mut)}
.sparks{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:12px}.sparks>div{background:var(--sf);border:1px solid var(--ring);border-radius:12px;padding:8px 10px 4px}
.carimbo{font-size:11px;color:var(--mut)}.carimbo i{display:inline-block;width:7px;height:7px;border-radius:50%;margin:0 4px 0 10px;vertical-align:1px}
.chip{display:inline-block;font-size:11.5px;padding:2px 9px;border-radius:999px;background:var(--chip);color:var(--ink2);border:1px solid var(--ring)}
/* slides */
main{margin-left:232px}
.slide{min-height:100vh;padding:56px 64px 40px;max-width:1760px;display:flex;flex-direction:column;border-bottom:1px solid var(--ring);scroll-margin-top:0}
@media(min-width:1700px){body{font-size:17px}.slide h1{font-size:44px}.slide .lede{font-size:19px;max-width:960px}.slide.painel h1{font-size:30px}.slide.painel .lede{font-size:15px}
 table.mini{font-size:14px}table.mini td,table.mini th{padding:4px 7px}table.mini th{font-size:11.5px}table.compact td{font-size:15px}.sinais{font-size:14px;line-height:1.4}.opiniao{font-size:14.2px}
 .pbox h2{font-size:14px}.strip8 .tile .v{font-size:23px}.strip8 .tile .l,.strip8 .tile .d{font-size:12px}.tile .v{font-size:34px}.tile .l,.tile .d{font-size:13.5px}.note,.legend{font-size:14px}.carimbo{font-size:12.5px}
 .janela button,.janela-global button{font-size:12.5px}.mut{font-size:inherit}}
.slide header{margin-bottom:22px}
.kicker{font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:var(--acc);font-weight:600;margin-bottom:8px}
.slide h1{font-family:var(--f-titulo);font-weight:500;font-size:38px;line-height:1.1;letter-spacing:-.01em;margin:0}
.slide .lede{font-family:var(--f-titulo);font-size:17px;color:var(--ink2);margin:10px 0 0;max-width:760px}
.body{flex:1;display:flex;flex-direction:column;justify-content:center}
.slide footer{margin-top:18px;display:flex;justify-content:space-between;align-items:baseline;gap:16px;font-size:11.5px;color:var(--mut)}
.slide footer .n{font-variant-numeric:tabular-nums}
/* capa */
.cover{justify-content:center}.cover h1{font-size:64px;max-width:760px}.cover .lede{font-size:21px}
.cover .heroes{display:grid;grid-template-columns:repeat(2,1fr);gap:14px;margin-top:8px}
.cover .duo{display:grid;grid-template-columns:1fr 560px;gap:28px;align-items:start}
.cover .duo table.compact td,.cover .duo table.compact th{padding:4px 8px;white-space:nowrap;font-size:12.5px}.cover .duo .panel h2{margin-bottom:6px}
@media(max-width:980px){.cover .duo{grid-template-columns:1fr}}
/* números */
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-bottom:22px}
.tile{background:var(--sf);border:1px solid var(--ring);border-radius:14px;padding:16px 18px}.tile .l{font-size:12px;color:var(--mut)}.tile .v{font-size:30px;font-weight:600;letter-spacing:-.02em;margin-top:4px;line-height:1.1}.tile .d{font-size:12.5px;color:var(--ink2);margin-top:4px}
.hero .v{font-size:46px;white-space:nowrap}
.tiles.strip{grid-template-columns:repeat(8,1fr);gap:8px}.tiles.strip .tile{padding:12px 12px}.tiles.strip .v{font-size:20px}
.tiles.fltiles{grid-template-columns:repeat(4,1fr);gap:8px;margin:0}.fltiles .tile{padding:5px 10px;border-radius:10px}.fltiles .tile .l{font-size:10.5px}.fltiles .tile .v{font-size:17px;margin-top:0}.fltiles .tile .d{font-size:10.5px;margin-top:1px;line-height:1.3}.fltiles .tile .d .it{white-space:nowrap}
td.varjan{font-weight:600}th.varjan-h{white-space:nowrap}
/* painéis e grids */
.panel{background:var(--sf);border:1px solid var(--ring);border-radius:16px;padding:20px 22px}
.panel h2{font-size:14px;margin:0 0 2px;font-weight:600}.panel .sub{color:var(--mut);font-size:12.5px;margin-bottom:10px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}.grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px}
.grid2>div,.grid3>div{background:var(--sf);border:1px solid var(--ring);border-radius:16px;padding:16px 18px 10px}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:14.5px}th,td{padding:11px 10px;text-align:right;border-bottom:1px solid var(--grid);white-space:nowrap}
th{color:var(--mut);font-weight:500;font-size:11.5px;letter-spacing:.06em;text-transform:uppercase}td:first-child,th:first-child{text-align:left}tr:last-child td{border-bottom:0}
table.big td{font-size:17px;padding:14px 10px}table.compact td{font-size:13.5px;padding:7px 10px}
.tk{font-weight:600}.tk small{color:var(--mut);font-weight:400;margin-left:6px}
.up{color:var(--up)}.dn{color:var(--dn)}.mut{color:var(--mut)}
.legend{display:flex;gap:18px;font-size:12.5px;color:var(--ink2);margin:0 0 10px}.legend i{display:inline-block;width:12px;height:12px;border-radius:3px;vertical-align:-1px;margin-right:6px}
svg.chart{width:100%;height:auto;display:block;overflow:visible}svg .grid{stroke:var(--grid);stroke-width:1}svg .axis{stroke:var(--axis);stroke-width:1}
svg .tick{fill:var(--mut);font-size:11px}svg .sub{fill:var(--ink2);font-size:12px;font-weight:600}svg .line{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
svg .dot{stroke:var(--sf);stroke-width:2}svg .cap,svg .endlab{fill:var(--ink2);font-size:11.5px;paint-order:stroke;stroke:var(--sf);stroke-width:3.5px;stroke-linejoin:round}svg .ref{stroke-width:1.5;opacity:.9}svg .reflab{fill:var(--ink2);font-size:11px;paint-order:stroke;stroke:var(--sf);stroke-width:3px}
svg .xh{stroke:var(--axis);stroke-width:1}svg .hdot{stroke:var(--sf);stroke-width:2}svg .hit{cursor:crosshair}svg .bar{cursor:default}
.tip{position:absolute;pointer-events:none;background:var(--ink);color:var(--bg);font-size:12px;padding:5px 8px;border-radius:6px;display:none;white-space:nowrap;z-index:9;line-height:1.35}
.empty{color:var(--mut);padding:24px;text-align:center;border:1px dashed var(--grid);border-radius:8px}.empty.small{padding:12px;font-size:13px}
.novo{font-size:10px;padding:1px 6px;border-radius:999px;background:var(--acc);color:#fff;margin-left:6px;vertical-align:middle}
code{background:var(--chip);padding:1px 5px;border-radius:4px;font-size:12.5px}
.note{font-size:12.5px;color:var(--ink2)}.note li{margin:3px 0}
.formula{font-family:var(--f-titulo);font-size:30px;color:var(--ink);margin:6px 0 18px}
.progress{position:fixed;top:0;left:232px;right:0;height:3px;background:transparent;z-index:6}.progress i{display:block;height:100%;background:var(--acc);width:0}
@media(max-width:980px){.rail{position:static;width:auto;border-right:0;border-bottom:1px solid var(--ring);padding:16px;white-space:nowrap;overflow-x:auto}
 .rail .sec{display:inline-block;margin:0 6px 0 12px}.rail a{display:inline-block}.rail .chip{margin:6px 0}main{margin-left:0}.progress{left:0}
 .slide{padding:32px 16px 28px;min-height:auto}.slide h1{font-size:28px}.cover h1{font-size:40px}.cover .heroes,.tiles.strip{grid-template-columns:1fr 1fr}.grid2,.grid3{grid-template-columns:1fr}.hero .v{font-size:32px}.tile .v{font-size:24px}}
"""

JS = r"""
(function(){
  var ptBR=function(v,d){return v.toLocaleString('pt-BR',{minimumFractionDigits:d,maximumFractionDigits:d});};
  // navegação de slides: trilho ativo por IntersectionObserver, teclado ↑↓/PgUp/PgDn, barra de progresso
  var slides=[].slice.call(document.querySelectorAll('.slide')),links=document.querySelectorAll('.rail a[data-s]'),bar=document.querySelector('.progress i');
  function ativa(id){links.forEach(function(a){a.classList.toggle('on',a.dataset.s===id);});
    var i=slides.findIndex(function(s){return s.id===id;});if(bar)bar.style.width=((i+1)/slides.length*100)+'%';
    var a=document.querySelector('.rail a.on');if(a&&a.scrollIntoView)a.scrollIntoView({block:'nearest'});}
  if('IntersectionObserver' in window){var io=new IntersectionObserver(function(es){es.forEach(function(e){if(e.isIntersecting)ativa(e.target.id);});},{rootMargin:'-45% 0px -45% 0px'});slides.forEach(function(s){io.observe(s);});}
  document.addEventListener('keydown',function(e){if(e.target.tagName==='INPUT')return;var cur=document.querySelector('.rail a.on');var i=cur?slides.findIndex(function(s){return s.id===cur.dataset.s;}):0;
    if(e.key==='ArrowDown'||e.key==='PageDown'||e.key===' '){e.preventDefault();if(slides[i+1])slides[i+1].scrollIntoView();}
    if(e.key==='ArrowUp'||e.key==='PageUp'){e.preventDefault();if(slides[i-1])slides[i-1].scrollIntoView();}});
  if(location.hash){var t=document.getElementById(location.hash.slice(1));if(t)setTimeout(function(){t.scrollIntoView();},50);}else ativa(slides[0]&&slides[0].id);
  // gráficos de linha: renderizador no navegador (seletor de janela) + crosshair
  var MESES=['jan','fev','mar','abr','mai','jun','jul','ago','set','out','nov','dez'];
  function ord(s){var d=s.split('-');return Math.round(Date.UTC(+d[0],+d[1]-1,+d[2])/86400000)+719163;}
  function ordDate(a,m){return Math.round(Date.UTC(a,m-1,1)/86400000)+719163;}
  function ticks(lo,hi,n){if(hi<=lo)hi=lo+1;var raw=(hi-lo)/n,mag=Math.pow(10,Math.floor(Math.log10(raw)));var step=[1,2,2.5,5,10].map(function(s){return s*mag;}).filter(function(s){return s>=raw;})[0];var out=[];for(var t=Math.ceil(lo/step-1e-9)*step;t<=hi+1e-9;t+=step)out.push(Math.round(t*1e6)/1e6);return out;}
  function num(v,d){return v.toLocaleString('pt-BR',{minimumFractionDigits:d,maximumFractionDigits:d});}
  function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;');}
  function setupLinhas(svg){
    var data=JSON.parse(svg.querySelector('script.data').textContent);
    var W=data.W,H=data.H,ML=data.ML,MR=data.MR,MT=data.MT,MB=data.MB;
    var corpo=svg.querySelector('.corpo'),hov=svg.querySelector('.hover'),xh=svg.querySelector('.xh'),hds=svg.querySelectorAll('.hdot'),hit=svg.querySelector('.hit');
    var tip=document.getElementById(svg.id+'-tip');var G=null;
    data.series.forEach(function(s){s.o=s.pts.map(function(p){return ord(p[0]);});});
    function render(anos){
      var dmax=Math.max.apply(null,data.series.map(function(s){return s.o[s.o.length-1];}));
      var corte=anos>0?dmax-Math.round(anos*365.25):-Infinity;
      var S=data.series.map(function(s){var pts=[],o=[];for(var i=0;i<s.pts.length;i++){if(s.o[i]>=corte){pts.push(s.pts[i]);o.push(s.o[i]);}}return {n:s.n,cor:s.cor,pts:pts,o:o};}).filter(function(s){return s.pts.length;});
      if(!S.length)return;
      var B={};Object.keys(data.bandas||{}).forEach(function(k){B[k]=(data.bandas[k]||[]).filter(function(b){return ord(b[0])>=corte;});});
      var d0=Math.min.apply(null,S.map(function(s){return s.o[0];})),d1=Math.max.apply(null,S.map(function(s){return s.o[s.o.length-1];})),span=Math.max(d1-d0,30);
      var ys=[];S.forEach(function(s){s.pts.forEach(function(p){ys.push(p[1]);});});(data.refs||[]).forEach(function(r){ys.push(r[1]);});Object.keys(B).forEach(function(k){B[k].forEach(function(b){ys.push(b[1],b[2]);});});
      var lo=Math.min.apply(null,ys),hi=Math.max.apply(null,ys);var pad=(hi-lo)*0.08||Math.abs(hi)*0.05||1;lo-=pad;hi+=pad;
      var X=function(o){return ML+(o-d0)/span*(W-ML-MR);},Y=function(v){return MT+(hi-v)/(hi-lo)*(H-MT-MB);};
      var h=[];if(data.titulo)h.push('<text class="sub" x="'+ML+'" y="12">'+esc(data.titulo)+'</text>');
      ticks(lo,hi,4).forEach(function(t){if(t>=lo&&t<=hi)h.push('<line class="grid" x1="'+ML+'" x2="'+(W-MR)+'" y1="'+Y(t).toFixed(1)+'" y2="'+Y(t).toFixed(1)+'"/><text class="tick" x="'+(ML-6)+'" y="'+(Y(t)+4).toFixed(1)+'" text-anchor="end">'+num(t,Math.abs(t)>=1000?0:data.dec)+'</text>');});
      var D0=new Date((d0-719163)*86400000),D1=new Date((d1-719163)*86400000),a0=D0.getUTCFullYear(),a1=D1.getUTCFullYear();
      if(a1-a0>=2){var passo=Math.max(1,Math.ceil(38*(a1-a0)/(W-ML-MR)));for(var a=a0;a<=a1;a++){var o=ordDate(a,1);if(o>=d0&&o<=d1&&(a-a0)%passo===0)h.push('<text class="tick" x="'+X(o).toFixed(1)+'" y="'+(H-10)+'" text-anchor="middle">'+a+'</text>');}}
      else if(d1-d0<=45){var passoD=Math.max(1,Math.ceil((d1-d0)/8));for(var o=d0;o<=d1;o+=passoD){var dt=new Date((o-719163)*86400000);h.push('<text class="tick" x="'+X(o).toFixed(1)+'" y="'+(H-10)+'" text-anchor="middle">'+String(dt.getUTCDate()).padStart(2,'0')+'/'+String(dt.getUTCMonth()+1).padStart(2,'0')+'</text>');}}
      else{for(var a=a0;a<=a1;a++)for(var m=1;m<=12;m++){var o=ordDate(a,m);if(o>=d0&&o<=d1)h.push('<text class="tick" x="'+X(o).toFixed(1)+'" y="'+(H-10)+'" text-anchor="middle">'+MESES[m-1]+(m===1?'/'+String(a).slice(2):'')+'</text>');}}
      var refs=(data.refs||[]).slice().sort(function(p,q){return p[1]-q[1];});
      refs.forEach(function(r,i){var abaixo=i===0&&refs.length>1&&Math.abs(Y(refs[1][1])-Y(r[1]))<16;var cor=r[2]||'var(--mut)';
        var esq=cor==='var(--mut)';
        h.push('<line class="ref" style="stroke:'+cor+'" x1="'+ML+'" x2="'+(W-MR)+'" y1="'+Y(r[1]).toFixed(1)+'" y2="'+Y(r[1]).toFixed(1)+'"/><text class="reflab" x="'+(esq?ML+4:W-MR)+'" y="'+(Y(r[1])+(abaixo?12:-4)).toFixed(1)+'" text-anchor="'+(esq?'start':'end')+'">'+esc(r[0])+(esq?'':' '+(data.pref||'')+num(r[1],data.dec)+(data.suf||''))+'</text>');});
      Object.keys(B).forEach(function(k){var b=B[k],s=S[+k];if(!s||!b.length)return;var ida=b.map(function(p,i){return (i?'L':'M')+X(ord(p[0])).toFixed(1)+','+Y(p[2]).toFixed(1);}).join(' ');var volta=b.slice().reverse().map(function(p){return 'L'+X(ord(p[0])).toFixed(1)+','+Y(p[1]).toFixed(1);}).join(' ');
        h.push('<path d="'+ida+' '+volta+' Z" style="fill:'+s.cor+';opacity:.13;stroke:none"/>');});
      // área sem decomposição (antes dos fechamentos dos papéis) sombreada, e marcador do início da decomposição
      if(svg.dataset.desde){var od=ord(svg.dataset.desde);if(od>d0){var xf=X(Math.min(od,d1));h.push('<rect x="'+ML+'" y="'+MT+'" width="'+(xf-ML).toFixed(1)+'" height="'+(H-MT-MB)+'" style="fill:var(--mut);opacity:.09"/><text class="tick" x="'+(ML+6)+'" y="'+(MT+14)+'" style="fill:var(--mut)">sem decomposição antes de '+svg.dataset.desde.slice(8)+'/'+svg.dataset.desde.slice(5,7)+'/'+svg.dataset.desde.slice(2,4)+'</text>');}}
      // seleção global de datas (1º clique = início, 2º = fim): as mesmas datas em todos os gráficos, com a variação da 1ª série
      var sel=svg.dataset.nosel?{}:(window.__sel||{});var s1=S[0];function idxAte(o){var i=-1;for(var k=0;k<s1.o.length;k++)if(s1.o[k]<=o)i=k;return i;}
      var i0=sel.t0?idxAte(ord(sel.t0)):-1,i1=sel.t1?idxAte(ord(sel.t1)):(sel.t0?s1.o.length-1:-1);
      if(i0>=0&&i1>i0){var xa=X(s1.o[i0]),xb=X(s1.o[i1]);h.push('<rect x="'+xa.toFixed(1)+'" y="'+MT+'" width="'+(xb-xa).toFixed(1)+'" height="'+(H-MT-MB)+'" style="fill:var(--s2);opacity:.07"/>');
        var v0=s1.pts[i0][1],v1=s1.pts[i1][1],dv=data.suf==='%'?(v1-v0):(v0?(v1/v0-1)*100:0);var txt=(dv>0?'+':'')+num(dv,data.suf==='%'?2:1)+(data.suf==='%'?' p.p.':'%');
        h.push('<text class="tick" x="'+((xa+xb)/2).toFixed(1)+'" y="'+(MT+12)+'" text-anchor="middle" style="fill:var(--s2);font-weight:600">'+txt+'</text>');}
      [[sel.t0,'início',i0],[sel.t1,'fim',sel.t1?i1:-1]].forEach(function(m){if(!m[0]||m[2]<0)return;var ot=s1.o[m[2]];if(ot<d0||ot>d1)return;var xm=X(ot);
        // início embaixo, fim uma linha acima: não se sobrepõem quando o intervalo é curto
        h.push('<line x1="'+xm.toFixed(1)+'" x2="'+xm.toFixed(1)+'" y1="'+MT+'" y2="'+(H-MB)+'" style="stroke:var(--s2);stroke-width:1.2;stroke-dasharray:4 3"/><text class="tick" x="'+(xm+(m[1]==='fim'?-4:4)).toFixed(1)+'" y="'+(H-MB-(m[1]==='fim'?18:6))+'" text-anchor="'+(m[1]==='fim'?'end':'start')+'" style="fill:var(--s2)">'+m[1]+' '+m[0].slice(8)+'/'+m[0].slice(5,7)+'/'+m[0].slice(2,4)+'</text>');});
      var labels=[];
      var gap=data.gap||45;
      S.forEach(function(s){h.push('<path class="line" style="stroke:'+s.cor+'" d="'+s.pts.map(function(p,i){return ((i&&s.o[i]-s.o[i-1]<=gap)?'L':'M')+X(s.o[i]).toFixed(1)+','+Y(p[1]).toFixed(1);}).join(' ')+'"/>');
        var last=s.pts[s.pts.length-1],xl=X(s.o[s.o.length-1]),yl=Y(last[1]);h.push('<circle class="dot" cx="'+xl.toFixed(1)+'" cy="'+yl.toFixed(1)+'" r="4" style="fill:'+s.cor+'"/>');
        var rec=s.pts.slice(-Math.max(3,Math.floor(s.pts.length/12))).map(function(p){return p[1];});var acima=!(Math.max.apply(null,rec)>last[1]+(hi-lo)*0.04);
        labels.push([xl,yl,(data.pref||'')+num(last[1],data.dec)+(data.suf||''),acima]);});
      labels.sort(function(p,q){return p[1]-q[1];});var ysl=[];
      labels.forEach(function(l){var y=l[3]?l[1]-8:l[1]+15;if(ysl.length&&y<ysl[ysl.length-1]+17)y=ysl[ysl.length-1]+17;ysl.push(y);});
      if(ysl.length&&ysl[ysl.length-1]>H-MB-2){var dyl=ysl[ysl.length-1]-(H-MB-2);ysl=ysl.map(function(y){return y-dyl;});}   // pilha acima dos ticks do eixo x
      labels.forEach(function(l,i){h.push('<text class="endlab" x="'+(l[0]-7).toFixed(1)+'" y="'+ysl[i].toFixed(1)+'" text-anchor="end">'+esc(l[2])+'</text>');});
      corpo.innerHTML=h.join('');
      G={S:S,d0:d0,span:span,X:X,Y:Y};
      svg._anos=anos;svg._d0=d0;
      svg.dispatchEvent(new CustomEvent('janela',{bubbles:true,detail:{anos:anos,d0:d0,d1:d1}}));
    }
    hit.addEventListener('mousemove',function(e){if(!G)return;
      var r=svg.getBoundingClientRect();var mx=(e.clientX-r.left)*W/r.width;var o=G.d0+(mx-ML)/(W-ML-MR)*G.span;
      var txt=[],ox=null;
      G.S.forEach(function(s,si){var i=0,best=1e18;for(var k=0;k<s.o.length;k++){var dd=Math.abs(s.o[k]-o);if(dd<best){best=dd;i=k;}}
        if(!s.pts.length||best>G.span*0.02){if(hds[si])hds[si].style.display='none';return;}
        var p=s.pts[i];if(ox===null)ox=s.o[i];
        if(hds[si]){hds[si].style.display='';hds[si].setAttribute('cx',G.X(s.o[i]));hds[si].setAttribute('cy',G.Y(p[1]));}
        txt.push((G.S.length>1?s.n+' ':'')+p[0].split('-').reverse().join('/')+' · '+(data.pref||'')+num(p[1],data.dec)+(data.suf||''));
        (data.extras||[]).forEach(function(nm,k){var ex=p[2+k];if(ex!=null)txt.push('<span style="opacity:.75">'+nm+' '+(typeof ex==='number'?num(ex,nm==='n'?0:data.dec)+(nm==='n'?'':(data.suf||'')):ex)+'</span>');});});
      if(ox===null)return;hov.style.display='';xh.setAttribute('x1',G.X(ox));xh.setAttribute('x2',G.X(ox));
      tip.style.display='block';tip.innerHTML=txt.join('<br>');
      var pr=svg.parentNode.getBoundingClientRect();tip.style.left=(e.clientX-pr.left+12)+'px';tip.style.top=(e.clientY-pr.top-30)+'px';});
    hit.addEventListener('mouseleave',function(){hov.style.display='none';tip.style.display='none';});
    // clique num ponto: avisa quem quiser (o Painel usa para explicar o movimento do Ibovespa daquela data até hoje)
    hit.addEventListener('click',function(e){if(!G||!G.S.length||svg.dataset.nosel)return;   // eixo x que não é o calendário (curva do Brent): não entra na seleção
      var r=svg.getBoundingClientRect();var mx=(e.clientX-r.left)*W/r.width;var o=G.d0+(mx-ML)/(W-ML-MR)*G.span;
      var s=G.S[0],i=0,best=1e18;for(var k=0;k<s.o.length;k++){var dd=Math.abs(s.o[k]-o);if(dd<best){best=dd;i=k;}}
      svg.dispatchEvent(new CustomEvent('pontoclique',{bubbles:true,detail:{data:s.pts[i][0],valor:s.pts[i][1]}}));});
    svg._render=render;
    // chips: data-a = anos (0 = máx); data-d = pregões (1, 5, 21); data-ytd = desde o 1º dia do ano
    function anosDoChip(b){var s=data.series[0],o1=s.o[s.o.length-1],last=new Date((o1-719163)*86400000);
      if(b.dataset.d){var n=+b.dataset.d,o0=s.o[Math.max(0,s.o.length-1-n)];return (o1-o0+1)/365.25;}
      if(b.dataset.ytd){var o0y=ordDate(last.getUTCFullYear(),1)-1;return (o1-o0y+1)/365.25;}          // desde o fechamento do ano anterior
      if(b.dataset.mtd){var o0m=ordDate(last.getUTCFullYear(),last.getUTCMonth()+1)-1;return (o1-o0m+1)/365.25;}   // desde o fechamento do mês anterior
      if(b.dataset.m)return (+b.dataset.m)/12+1/365.25;
      return +b.dataset.a;}
    var chips=svg.parentNode.querySelector('.janela');
    if(chips)chips.querySelectorAll('button').forEach(function(b){b.addEventListener('click',function(){chips.querySelectorAll('button').forEach(function(x){x.classList.remove('on');});b.classList.add('on');render(anosDoChip(b));});});
    document.addEventListener('selecao',function(){render(svg._anos||0);});
    render(+(svg.dataset.ini||0));
  }
  // coluna "Janela" da tabela de variáveis do Painel: variação de cada variável na janela escolhida nos chips do gráfico
  // do Ibovespa (evento 'janela' do #painel-ibov). Base = último ponto da série completa (embutida no gráfico de detalhe
  // indicado em data-svg) até o início da janela; valor final = o "Último" da tabela.
  (function(){var tds=document.querySelectorAll('td.varjan');if(!tds.length)return;
    function serie(id){var g=document.getElementById(id);if(!g)return null;var sc=g.querySelector('script.data');if(!sc)return null;
      try{var s=JSON.parse(sc.textContent).series[0].pts;return s.map(function(p){return [ord(p[0]),p[1]];});}catch(e){return null;}}
    var cache={};
    function atualiza(e){var d0=e.detail.d0;var ch=document.querySelector('.janela[data-for="painel-ibov"] button.on');var rot=ch?ch.textContent:'';
      document.querySelectorAll('th.varjan-h').forEach(function(th){th.textContent='Janela'+(rot?' · '+rot:'');});
      // a janela começa no fechamento anterior do Ibovespa (d0); para cada série, a base é o último ponto ANTES do 1º pregão
      // do Ibovespa dentro da janela (assim S&P/Treasury em YTD partem de 31/12, dia em que a B3 não abre — igual à coluna YTD)
      var ib=cache['painel-ibov']||(cache['painel-ibov']=serie('painel-ibov'));var lim=d0+1;
      if(ib){for(var k=0;k<ib.length;k++)if(ib[k][0]>d0){lim=ib[k][0];break;}}
      // MTD/YTD são de calendário: base = último ponto antes do 1º dia do mês/ano corrente (câmbio cota em feriados)
      if(ch&&(ch.dataset.ytd||ch.dataset.mtd)&&e.detail.d1){var D1=new Date((e.detail.d1-719163)*86400000);lim=ch.dataset.ytd?ordDate(D1.getUTCFullYear(),1):ordDate(D1.getUTCFullYear(),D1.getUTCMonth()+1);}
      tds.forEach(function(td){var v=parseFloat(td.dataset.v);var taxa=td.dataset.taxa==='1';
        var s=cache[td.dataset.svg]||(cache[td.dataset.svg]=serie(td.dataset.svg));
        if(!s||isNaN(v)){td.textContent='—';td.className='varjan';return;}
        var base=null,bd=null;for(var i=0;i<s.length;i++){if(s[i][0]<lim&&s[i][1]!=null){base=s[i][1];bd=s[i][0];}else if(s[i][0]>=lim)break;}
        if(base===null){base=s[0][1];bd=s[0][0];}
        var x=taxa?(v-base):(base?(v/base-1)*100:null);if(x===null||!isFinite(x)){td.textContent='—';td.className='varjan';return;}
        var dt=new Date((bd-719163)*86400000);td.title='desde '+String(dt.getUTCDate()).padStart(2,'0')+'/'+String(dt.getUTCMonth()+1).padStart(2,'0')+'/'+dt.getUTCFullYear();
        td.textContent=(x>0?'+':'')+num(x,2)+(taxa?' p.p.':'%');td.className='varjan '+(x>0?'up':x<0?'dn':'');});}
    document.addEventListener('janela',function(e){if(e.target&&e.target.id==='painel-ibov')atualiza(e);});
    var g=document.getElementById('painel-ibov');if(g&&g._d0!==undefined)atualiza({detail:{d0:g._d0}});
  })();
  // seleção global de datas: 1º clique em qualquer gráfico de linha = início, 2º = fim (invertidos se vier antes), 3º recomeça
  window.__sel={t0:null,t1:null};
  window.__selecionar=function(d){var s=window.__sel;
    if(!d){s.t0=null;s.t1=null;}
    else if(!s.t0||s.t1){s.t0=d;s.t1=null;}
    else if(d===s.t0)return;
    else if(d<s.t0){s.t1=s.t0;s.t0=d;}
    else s.t1=d;
    document.dispatchEvent(new CustomEvent('selecao'));};
  document.addEventListener('pontoclique',function(e){window.__selecionar(e.detail.data);});
  document.querySelectorAll('svg.chart[data-x0]').forEach(setupLinhas);
  // seletor global (trilho): aplica a janela a todos os gráficos de linha
  document.querySelectorAll('.janela-global button').forEach(function(b){b.addEventListener('click',function(){
    document.querySelectorAll('.janela-global button').forEach(function(x){x.classList.remove('on');});b.classList.add('on');
    var a=+b.dataset.a;document.querySelectorAll('.lin').forEach(function(w){var svg=w.querySelector('svg.chart');var ch=w.querySelector('.janela');if(!svg||!svg._render)return;
      var alvo=null;ch.querySelectorAll('button').forEach(function(x){x.classList.remove('on');if(+x.dataset.a===a&&!x.disabled)alvo=x;});
      if(!alvo)alvo=ch.querySelector('button[data-a="0"]');alvo.classList.add('on');svg._render(+alvo.dataset.a);});});});
  // decomposição do Ibovespa: chips trocam a janela (blocos data-j) dentro do container .dec
  function mostraJ(box,key){box.querySelectorAll('.janela button[data-j]').forEach(function(x){x.classList.toggle('on',x.dataset.j===key);});
    box.querySelectorAll('[data-j]:not(button)').forEach(function(el){el.style.display=el.dataset.j===key?(el.getAttribute('style')||'').indexOf('grid-template')>=0?'grid':'':'none';});}
  document.querySelectorAll('.dec').forEach(function(box){box.querySelectorAll('.janela button[data-j]').forEach(function(b){b.addEventListener('click',function(){mostraJ(box,b.dataset.j);});});});
  // clique no gráfico do Ibovespa (Painel): decomposição daquela data até hoje, calculada aqui com a identidade do índice
  (function(){
    var el=document.getElementById('ibov-dados');if(!el)return;var D=JSON.parse(el.textContent);
    var box=document.querySelector('.dec');var svg=document.getElementById('painel-ibov');if(!box||!svg)return;
    var ABREV=D.abrev||{};
    function precoEm(cod,d){var h=D.hist[cod]||[];for(var i=h.length-1;i>=0;i--)if(h[i][0]<=d)return h[i][1];return null;}
    function comEx(p){var com=p.com||null,exd=p.ex||null;   // B3: data-com -> ex = pregão seguinte; Yahoo: data ex -> com = pregão anterior
      if(!exd){if(!com)return null;for(var i=0;i<D.cal.length;i++)if(D.cal[i]>com){exd=D.cal[i];break;}}
      else if(!com){for(var i=D.cal.length-1;i>=0;i--)if(D.cal[i]<exd){com=D.cal[i];break;}}
      return exd?[com,exd]:null;}
    function qInicio(it,qt,t0,t){var q=qt,ex=true;(D.prov[it.cod]||[]).forEach(function(p){var ce=comEx(p);if(!ce)return;var com=ce[0],exd=ce[1];
        if(!(t0<exd&&exd<=t))return;
        if(p.fator!=null){var a=(p.acao||'').toUpperCase();if(a.indexOf('BONIFIC')>=0||a.indexOf('DESDOBR')>=0)q=q/(1+p.fator/100);else if(a.indexOf('GRUPAM')>=0&&p.fator)q=q*(p.fator>=1?p.fator:1/p.fator);return;}
        var Dv=p.valor||0,pc=p.pcum||(com?precoEm(it.cod,com):null);if(pc&&pc>Dv&&Dv>0)q=q/(pc/(pc-Dv));else if(Dv>0)ex=false;});
      if(it.classe==='UNT'&&!D.unit[it.cod])ex=false;return [q,ex];}
    function hbar(linhas,W,RH,ML,MR,chaves){var H=8+RH*linhas.length+4,vals=linhas.map(function(l){return l[1];});var lo=Math.min.apply(null,vals.concat([0])),hi=Math.max.apply(null,vals.concat([0])),sp=(hi-lo)||1;
      var X=function(v){return ML+(v-lo)/sp*(W-ML-MR);};var h=['<line class="axis" x1="'+X(0).toFixed(1)+'" x2="'+X(0).toFixed(1)+'" y1="4" y2="'+(H-4)+'"/>'];
      linhas.forEach(function(l,i){var y=8+RH*i,v=l[1],x0=X(Math.min(v,0)),x1=X(Math.max(v,0)),cor=v>=0?'var(--s1)':'var(--dn)';var ds=chaves?' data-setor="'+esc(chaves[i])+'"':'';
        h.push('<text class="tick"'+ds+' style="font-size:10.5px;fill:var(--ink);cursor:pointer" x="'+(ML-6)+'" y="'+(y+RH-4)+'" text-anchor="end">'+esc(l[0])+'</text><rect class="bar"'+ds+' x="'+x0.toFixed(1)+'" y="'+(y+1.5)+'" width="'+Math.max(x1-x0,0.8).toFixed(1)+'" height="'+(RH-3)+'" rx="2" style="fill:'+cor+';cursor:pointer"/>');
        var esq=v<0&&(x0-ML)>=34;h.push('<text class="cap" style="font-size:10px" x="'+((esq?x0-4:x1+4)).toFixed(1)+'" y="'+(y+RH-4)+'" text-anchor="'+(esq?'end':'start')+'">'+(v>0?'+':'')+num(v,2)+'</text>');});
      return '<svg class="chart" viewBox="0 0 '+W+' '+H+'">'+h.join('')+'</svg>';}
    function sinal(v,d,suf){return (v>0?'+':'')+num(v,d)+(suf||'');}
    function cls(v){return v>0?'up':(v<0?'dn':'');}
    function qAvanca(it,qs,s,t0){var q=qs,ex=true;(D.prov[it.cod]||[]).forEach(function(p){var ce=comEx(p);if(!ce)return;var com=ce[0],exd=ce[1];
        if(!(s<exd&&exd<=t0))return;
        if(p.fator!=null){var a=(p.acao||'').toUpperCase();if(a.indexOf('BONIFIC')>=0||a.indexOf('DESDOBR')>=0)q=q*(1+p.fator/100);else if(a.indexOf('GRUPAM')>=0&&p.fator)q=q/(p.fator>=1?p.fator:1/p.fator);return;}
        var Dv=p.valor||0,pc=p.pcum||(com?precoEm(it.cod,com):null);if(pc&&pc>Dv&&Dv>0)q=q*(pc/(pc-Dv));else if(Dv>0)ex=false;});
      if(it.classe==='UNT'&&!D.unit[it.cod])ex=false;return [q,ex];}
    function fotoQuadri(t0){var melhor=null;Object.keys(D.fotos).forEach(function(s){var a=s<t0?s:t0,b=s<t0?t0:s;if(D.rebal.some(function(r){return a<r&&r<=b;}))return;
        var dist=Math.abs(ord(s)-ord(t0));if(melhor===null||dist<melhor[0])melhor=[dist,s];});return melhor?[melhor[1],D.fotos[melhor[1]]]:[null,null];}
    function ibEm(d){for(var i=D.ib.length-1;i>=0;i--)if(D.ib[i][0]<=d)return D.ib[i][1];return null;}
    function precoDesde(cod,d){var h=D.hist[cod]||[];for(var i=0;i<h.length;i++)if(h[i][0]>=d)return h[i][1];return null;}
    // fator que leva o preço em b para a base de ações de a (desdobramento/bonificação multiplica, grupamento n:1 divide):
    // a variação exibida por papel não pode mostrar um desdobramento 1:5 como queda de 80%
    function fatorPreco(cod,a,b){var f=1;(D.prov[cod]||[]).forEach(function(p){if(p.fator==null)return;var ce=comEx(p);if(!ce)return;var exd=ce[1];if(!(a<exd&&exd<=b))return;
        var ac=(p.acao||'').toUpperCase();if(ac.indexOf('BONIFIC')>=0||ac.indexOf('DESDOBR')>=0)f*=1+p.fator/100;else if(ac.indexOf('GRUPAM')>=0&&p.fator)f/=(p.fator>=1?p.fator:1/p.fator);});return f;}
    function varPreco(cod,a,b){var pa=precoEm(cod,a),pb=precoEm(cod,b);return (pa&&pb)?pb*fatorPreco(cod,a,b)/pa-1:0;}
    // ---- decomposição ENCADEADA por quadrimestre: cada papel contribui só enquanto esteve no índice, com a composição
    // vigente em cada trecho; os trechos se encontram no fechamento do dia anterior a cada rebalanceamento (o redutor novo é
    // calibrado para o índice ser contínuo ali), então a soma dos Δ em pontos fecha com o índice mesmo cruzando rebalanceamentos.
    function itemDe(cod){for(var i=0;i<D.itens.length;i++)if(D.itens[i].cod===cod)return D.itens[i];
      var irmao=null;for(var j=0;j<D.itens.length;j++)if(D.itens[j].cod.slice(0,4)===cod.slice(0,4)){irmao=D.itens[j];break;}   // outra classe da mesma empresa (CYRE4 -> CYRE3)
      var cl=cod.slice(4)==='11'?'UNT':(cod.slice(4)==='3'?'ON':'PN');
      return {cod:cod,classe:cl,setor:(D.setor_ex&&D.setor_ex[cod])||(irmao&&irmao.setor)||'Ex-constituintes',peso:0};}
    function ultimoPreco(cod,b){var h=D.hist[cod]||[];for(var i=h.length-1;i>=0;i--)if(h[i][0]<=b)return h[i][0];return null;}
    function diaAntes(d){for(var i=D.cal.length-1;i>=0;i--)if(D.cal[i]<d)return D.cal[i];return null;}
    function decompEncadeada(t0,t){var rs=D.rebal.filter(function(r){return r>t0&&r<=t;});   // sem rebalanceamento: um trecho só
      var inicios=[t0].concat(rs.map(diaAntes)),fins=rs.map(diaAntes).concat([t]);var delta={},exAll=true,faltam=[],v0t=null,n=0,dbg=[],foto1=null;
      for(var k=0;k<inicios.length;k++){var a=inicios[k],b=fins[k];if(!a||!b||a>=b)continue;
        var ref=k===0?a:rs[k-1];var fq=fotoQuadri(ref);if(!fq[0])return null;   // trecho sem fotografia do quadrimestre: não dá para encadear
        var s0=fq[0],foto=fq[1],red=foto.redutor||D.red;var soma0=0;n++;if(!foto1)foto1=s0;
        // saídas no meio do trecho (OPA, incorporação, conversão de classe): o papel contribui até o último fechamento e,
        // dali em diante, o índice redistribui o valor dele nos demais (fator S = valor total / valor dos que ficam)
        var cods=Object.keys(foto.q),ult={},cortes=[];
        cods.forEach(function(cod){var L=ultimoPreco(cod,b);ult[cod]=L;if(L&&L>a&&L<b&&cortes.indexOf(L)<0)cortes.push(L);});
        cortes.sort();var pontos=[a].concat(cortes,[b]),S=1;
        function qEm(it,qs,x){if(s0===x)return [qs,true];if(s0>x)return qInicio(it,qs,x,s0);return qAvanca(it,qs,s0,x);}
        // papéis que saíram ENTRE a fotografia e o início do trecho (fotografia de maio, trecho começando em junho, AXIA6
        // convertida no meio): o índice já redistribuiu o valor deles; o mesmo fator S entra desde o início
        if(s0<a){var antes=[];cods.forEach(function(cod){var L=ult[cod];if(L&&L>s0&&L<a&&antes.indexOf(L)<0)antes.push(L);});antes.sort();
          antes.forEach(function(L){var vt=0,vf=0;cods.forEach(function(cod){if(!ult[cod]||ult[cod]<L)return;var p=precoEm(cod,L);if(!p)return;
            var v=qEm(itemDe(cod),foto.q[cod],L)[0]*p/red;vt+=v;if(ult[cod]>L)vf+=v;});if(vf>0)S*=vt/vf;});}
        var dbgT=[a,b,s0,cortes.slice()];
        for(var j=0;j<pontos.length-1;j++){var x=pontos[j],y=pontos[j+1],vy=0,vyFica=0;
          cods.forEach(function(cod){if(!ult[cod]||ult[cod]<x||(j>0&&ult[cod]===x))return;   // já saiu antes deste subtrecho
            var it=itemDe(cod),qs=foto.q[cod],px=precoEm(cod,x),py=precoEm(cod,y);
            if(!px&&j===0){px=precoDesde(cod,a);if(px)exAll=false;}   // papel novo (classe criada no rebalanceamento): 1º fechamento disponível
            if(!px||!py){if(faltam.indexOf(cod)<0)faltam.push(cod);return;}
            var r1=qEm(it,qs,x),qx=r1[0],ex=r1[1];var r3=qAvanca(it,qx,x,y),qy=r3[0];ex=ex&&r3[1];exAll=exAll&&ex;
            var vx=qx*px/red,vyi=qy*py/red;if(j===0)soma0+=S*vx;delta[cod]=(delta[cod]||0)+S*(vyi-vx);
            vy+=vyi;if(ult[cod]>y||y===b)vyFica+=vyi;});
          if(j===0)dbgT.push(soma0/ibEm(a)-1);if(y<b&&vyFica>0)S*=vy/vyFica;else dbgT.push(S*vy/ibEm(b)-1);}
        dbg.push(dbgT);if(k===0)v0t=soma0;}
      window.__ibovEncDbg=dbg;
      if(!v0t)return null;
      var pap=Object.keys(delta).map(function(cod){var it=itemDe(cod);return [cod,it.setor,it.peso||0,varPreco(cod,t0,t),delta[cod]/v0t*100];});
      return {pap:pap,exAll:exAll,faltam:faltam,n:n,foto:foto1};}
    function decomp(t0c,t1c){var cal=D.cal,t=cal[cal.length-1],t0=null;for(var i=cal.length-1;i>=0;i--)if(cal[i]<=t0c){t0=cal[i];break;}
      if(t1c){for(var i2=cal.length-1;i2>=0;i2--)if(cal[i2]<=t1c){t=cal[i2];break;}}
      if(!t0||t0>=t)return null;
      var enc=decompEncadeada(t0,t);
      if(enc){var pap=enc.pap;var set={};pap.forEach(function(p){var s=set[p[1]]||(set[p[1]]=[0,0]);s[0]+=p[2];s[1]+=p[4];});
        var lst=Object.keys(set).map(function(k){return [k,set[k][0],set[k][1]];}).sort(function(a,b){return b[2]-a[2];});
        var soma=pap.reduce(function(a,p){return a+p[4];},0);var ib0=null,ibt=null;for(var i=D.ib.length-1;i>=0;i--){if(ibt===null&&D.ib[i][0]<=t)ibt=D.ib[i][1];if(D.ib[i][0]<=t0){ib0=D.ib[i][1];break;}}
        var indice=(ib0&&ibt)?(ibt/ib0-1)*100:null;pap.sort(function(a,b){return b[4]-a[4];});
        return {t0:t0,t:t,setores:lst,papeis:pap,soma:soma,indice:indice,erro:indice===null?null:soma-indice,faltam:enc.faltam,
          metodo:(enc.exAll?'exato':'quase exato (units/proventos aprox.)')+(enc.n>1?' · encadeado em '+enc.n+' trechos de carteira':' · fotografia '+br(enc.foto))};}
      if(t!==cal[cal.length-1])return null;   // sem fotografia e fim no passado: a carteira atual não serve
      var fq=fotoQuadri(t0),s0=fq[0],foto0=fq[1];var cruza=D.rebal.some(function(r){return t0<r&&r<=t;})&&!foto0;var exato=!!foto0||!cruza;
      var redT=D.red||1,red0=(foto0&&foto0.redutor)||redT,pap=[],v0t=0,vtt=0,exAll=exato,faltam=[];
      if(foto0&&s0!==t0&&(s0<t0?s0:t0)<D.prov_ini)exAll=false;
      var atuais={};D.itens.forEach(function(it){atuais[it.cod]=1;});
      D.itens.forEach(function(it){var pt=precoEm(it.cod,t),p0=precoEm(it.cod,t0);if(!pt||(!p0&&!foto0)){faltam.push(it.cod);return;}var w=it.peso/100,qt=it.q||0;
        if(exato&&qt){var q0,ex=true;
          if(foto0){var qs=foto0.q[it.cod];if(qs==null||!p0){q0=0;p0=p0||0;}else if(s0===t0)q0=qs;else if(s0>t0){var r1=qInicio(it,qs,t0,s0);q0=r1[0];ex=r1[1];}else{var r2=qAvanca(it,qs,s0,t0);q0=r2[0];ex=r2[1];}}
          else{var r=qInicio(it,qt,t0,t);q0=r[0];ex=r[1];}
          exAll=exAll&&ex;var v0=q0*p0/red0,vt=qt*pt/redT;v0t+=v0;vtt+=vt;pap.push([it.cod,it.setor,it.peso,p0?varPreco(it.cod,t0,t):0,v0,vt]);}
        else{var rr=varPreco(it.cod,t0,t);pap.push([it.cod,it.setor,it.peso,rr,null,w*rr*100]);}});
      if(foto0){Object.keys(foto0.q).forEach(function(cod){if(atuais[cod])return;var p0=precoEm(cod,t0),v0=null;
        if(p0)v0=foto0.q[cod]*p0/red0;else if(foto0.peso&&foto0.peso[cod]!=null&&ibEm(s0)){v0=foto0.peso[cod]/100*ibEm(s0);exAll=false;}
        if(v0===null)return;v0t+=v0;pap.push([cod,'Saíram do índice',0,-1,v0,0]);});}
      var replica=null;if(exato&&v0t>0){pap=pap.map(function(p){return [p[0],p[1],p[2],p[3],(p[5]-p[4])/v0t*100];});replica=(vtt/v0t-1)*100;}else pap=pap.map(function(p){return [p[0],p[1],p[2],p[3],p[5]];});
      var set={};pap.forEach(function(p){var s=set[p[1]]||(set[p[1]]=[0,0]);s[0]+=p[2];s[1]+=p[4];});
      var lst=Object.keys(set).map(function(k){return [k,set[k][0],set[k][1]];}).sort(function(a,b){return b[2]-a[2];});
      var soma=pap.reduce(function(a,p){return a+p[4];},0);var ib0=null,ibt=null;for(var i=D.ib.length-1;i>=0;i--){if(ibt===null&&D.ib[i][0]<=t)ibt=D.ib[i][1];if(D.ib[i][0]<=t0){ib0=D.ib[i][1];break;}}
      var indice=(ib0&&ibt)?(ibt/ib0-1)*100:null;pap.sort(function(a,b){return b[4]-a[4];});
      return {t0:t0,t:t,setores:lst,papeis:pap,soma:soma,indice:indice,erro:indice===null?null:soma-indice,faltam:faltam,
        metodo:exato?((exAll?'exato':'quase exato (units/proventos aprox.)')+(foto0?' · fotografia '+br(s0):'')):'aprox. (cruza rebalanceamento sem fotografia)'};}
    function br(d){return d.slice(8)+'/'+d.slice(5,7)+'/'+d.slice(2,4);}
    function render(j){var sel=window.__sel||{};
      var dica=sel.t0?' · <a href="#" data-limpar="1" style="color:var(--acc)">limpar datas</a>':' · <span style="opacity:.8">clique no gráfico: 1º = início, 2º = fim</span>';
      var cab='<div class="mut" style="font-size:11px;margin:0 0 4px">De '+br(j.t0)+' a '+br(j.t)+': Ibovespa '+(j.indice===null?'—':sinal(j.indice,2,'%'))+' · soma '+sinal(j.soma,2,' p.p.')+' · erro '+(j.erro===null?'—':sinal(j.erro,2,' p.p.'))+' · '+j.metodo+(j.faltam.length?' · sem preço na data: '+j.faltam.join(', '):'')+(j.nota?' · '+j.nota:'')+dica+'</div>';
      var rhS=11;
      var vs=cab+hbar(j.setores.map(function(s){return [ABREV[s[0]]||s[0],s[2]];}),430,rhS,104,44,j.setores.map(function(s){return s[0];}));
      window.__ibovClique=j;
      // agrupa classes da mesma empresa e escolhe os nomes: mínimo 4, todo nome ≥ 0,5 p.p., máximo 8
      var g={};j.papeis.forEach(function(p){var k=p[0].slice(0,4);var e=g[k]||(g[k]={cls:[],setor:p[1],peso:0,wr:0,x:0});e.cls.push(p[0].slice(4));e.peso+=p[2];e.wr+=p[2]*p[3];e.x+=p[4];});
      var emp=Object.keys(g).map(function(k){var e=g[k];return [k+(e.cls.length>1?e.cls.sort().join('+'):e.cls[0]),e.setor,e.peso,e.peso?e.wr/e.peso:0,e.x];}).sort(function(a,b){return b[4]-a[4];});
      var pos=emp.filter(function(e){return e[4]>0;}),neg=emp.filter(function(e){return e[4]<0;}).reverse();
      function corta(l){var n=Math.max(4,l.filter(function(e){return Math.abs(e[4])>=0.5;}).length);return l.slice(0,Math.min(n,5));}
      var top=corta(pos),bot=corta(neg);var sum=function(l){return l.reduce(function(a,e){return a+e[4];},0);};
      var tp=sum(pos),tn=sum(neg),sp=sum(top),sn=sum(bot);
      var cabE='<div class="mut" style="font-size:10.5px;margin:0 0 4px">Mostrados: '+sinal(sp,2)+' de '+sinal(tp,2)+' p.p. (puxaram) e '+num(sn,2)+' de '+num(tn,2)+' (seguraram) = '+((tp||tn)?num((Math.abs(sp)+Math.abs(sn))/(Math.abs(tp)+Math.abs(tn))*100,0):'—')+'% do movimento bruto. Classes da mesma empresa somadas.</div>';
      function tab(l,tit){return '<table class="mini"><thead><tr><th>'+tit+'</th><th>Var.</th><th>p.p.</th></tr></thead><tbody>'+l.map(function(p){var v=p[1]==='Saíram do índice'?'<span class="mut">saiu</span>':'<span class="'+cls(p[3])+'">'+sinal(p[3]*100,1,'%')+'</span>';return '<tr><td class="tk">'+p[0]+'</td><td>'+v+'</td><td class="'+cls(p[4])+'">'+sinal(p[4],2)+'</td></tr>';}).join('')+'</tbody></table>';}
      var vp=cabE+'<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">'+tab(top,'Puxaram')+tab(bot,'Seguraram')+'</div>';
      conteudo('setores',vs);conteudo('papeis',vp);}
    function conteudo(slot,html){var sl=box.querySelector('[data-slot="'+slot+'"]');var h2=sl.querySelector('h2');sl.innerHTML='';if(h2)sl.appendChild(h2);var d=document.createElement('div');d.innerHTML=html;sl.appendChild(d);}
    function aviso(txt){conteudo('setores','<div class="mut" style="font-size:11.5px">'+txt+'</div>');conteudo('papeis','');}
    // a decomposição segue a janela do gráfico: começa no 1º pregão dentro da janela (ou no ponto clicado, se houver)
    function decompJanela(){var cal=D.cal,t=cal[cal.length-1];var d0=svg._d0;var ini=null,sel=window.__sel||{},nota=null;
      // início = data selecionada (1º clique) se estiver dentro da janela; senão o último pregão até o começo da janela
      // (MTD parte do fechamento do mês anterior, YTD do ano anterior); antes dos fechamentos dos papéis, o 1º disponível.
      // fim = 2º clique (ou o último pregão)
      if(sel.t0&&ord(sel.t0)>=d0)ini=sel.t0;else{for(var i=cal.length-1;i>=0;i--)if(ord(cal[i])<=d0){ini=cal[i];break;}if(!ini)ini=cal[0];}
      if(ini<cal[0]){nota='sem fechamentos dos papéis antes de '+br(cal[0])+'; decomposição desde essa data';ini=cal[0];}
      var fim=sel.t1&&sel.t1<t?sel.t1:t;
      if(!ini||ini>=fim){aviso('Escolha um fim depois do início ('+br(ini)+').');return;}
      var j=decomp(ini,fim);if(!j){aviso('Sem decomposição para este intervalo (sem fotografia da carteira).');return;}
      if(ord(cal[0])>d0&&!sel.t0)nota='a janela começa antes dos fechamentos dos papéis; decomposição desde '+br(cal[0]);
      if(nota)j.nota=nota;
      render(j);}
    svg.addEventListener('janela',function(){decompJanela();});
    box.addEventListener('click',function(e){var el=e.target.closest&&e.target.closest('[data-limpar]');if(!el)return;e.preventDefault();window.__selecionar(null);});
    // clique num setor (barra ou rótulo): desce do setor para as empresas, na janela ativa
    box.addEventListener('click',function(e){var el=e.target.closest&&e.target.closest('[data-setor]');if(!el)return;var setor=el.dataset.setor;
      var j=window.__ibovClique;if(!j)return;
      // classes da mesma empresa somadas (BBDC3+4), como na tabela principal: peso somado, variação ponderada pelo peso
      var gs={};j.papeis.filter(function(p){return p[1]===setor;}).forEach(function(p){var k=p[0].slice(0,4);var e=gs[k]||(gs[k]={cls:[],peso:0,wr:0,x:0});e.cls.push(p[0].slice(4));e.peso+=p[2];e.wr+=p[2]*p[3];e.x+=p[4];});
      var pap=Object.keys(gs).map(function(k){var e=gs[k];return [k+(e.cls.length>1?e.cls.sort().join('+'):e.cls[0]),setor,e.peso,e.peso?e.wr/e.peso:0,e.x];}).sort(function(a,b){return b[4]-a[4];});var soma=pap.reduce(function(a,p){return a+p[4];},0);
      // desce do setor para as empresas: o gráfico de setores dá lugar às barras dos papéis do setor, na mesma caixa
      var cabS='<div class="mut" style="font-size:11px;margin:0 0 4px"><a href="#" data-volta="1" style="color:var(--acc)">&larr; setores</a> · <b style="color:var(--ink)">'+esc(setor)+'</b> · '+pap.length+' papéis · '+sinal(soma,2,' p.p.')+' de '+br(j.t0)+' a '+br(j.t)+'</div>';
      var rh=pap.length>14?11:13;var gS=hbar(pap.map(function(p){return [p[0],p[4]];}),430,rh,104,44);
      conteudo('setores',cabS+gS);
      // e a caixa "quem puxou" lista os mesmos papéis com peso e variação
      var rows=pap.map(function(p){return '<tr><td class="tk">'+p[0]+'</td><td>'+num(p[2],2)+'%</td><td class="'+cls(p[3])+'">'+sinal(p[3]*100,1,'%')+'</td><td class="'+cls(p[4])+'">'+sinal(p[4],2)+'</td></tr>';});
      var metade=Math.ceil(rows.length/2);function tb(r){return '<table class="mini"><thead><tr><th>Papel</th><th>Peso</th><th>Var.</th><th>p.p.</th></tr></thead><tbody>'+r.join('')+'</tbody></table>';}
      conteudo('papeis','<div class="mut" style="font-size:11px;margin:0 0 4px">'+esc(setor)+' · cada papel do setor</div><div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">'+tb(rows.slice(0,metade))+(rows.length>metade?tb(rows.slice(metade)):'')+'</div>');
      box.querySelector('[data-volta]').addEventListener('click',function(ev){ev.preventDefault();render(j);});
      e.preventDefault();});
    // o clique num ponto (deste ou de qualquer gráfico) entra na seleção global; cada gráfico se redesenha e o do Ibovespa
    // dispara 'janela', que refaz a decomposição para o intervalo selecionado
    if(svg._d0!==undefined)decompJanela();   // o gráfico já foi desenhado antes deste bloco existir
  })();
  // tooltip simples em barras/pontos
  var t=document.createElement('div');t.className='tip';document.body.appendChild(t);
  document.querySelectorAll('[data-tip]').forEach(function(el){
    el.addEventListener('mousemove',function(e){t.style.display='block';t.textContent=el.dataset.tip;t.style.left=(e.pageX+12)+'px';t.style.top=(e.pageY-30)+'px';});
    el.addEventListener('mouseleave',function(){t.style.display='none';});});
})();
"""

PAGE = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Monitor de Cobertura</title>
<style>/*FONTS*/</style>
<style>/*CSS*/</style></head><body>
<nav class="rail" aria-label="Seções"><p class="brand">Monitor de Cobertura<small>Ferreira Lavourinha · book FL</small></p><span class="chip">v/*VERSAO*/ · /*DATA*/</span>
<div class="janela-global"><span>Janela dos gráficos</span><button data-a="1">1a</button><button data-a="2">2a</button><button data-a="3">3a</button><button data-a="5">5a</button><button data-a="10">10a</button><button data-a="0" class="on">máx</button></div>
/*RAIL*/
</nav><div class="progress"><i></i></div>
<main>
/*SLIDES*/
</main><script>/*JS*/</script></body></html>"""

_N = {"n": 0}


def slide(sec: str, sid: str, titulo: str, corpo: str, lede: str = "", nota: str = "", cls: str = "") -> tuple[str, str]:
    """Um slide da apresentação. Devolve (html, item do trilho)."""
    _N["n"] += 1
    n = _N["n"]
    html = f'''<section class="slide {cls}" id="{sid}" data-sec="{sec}">
<header><div class="kicker">{sec} · {n:02d}</div><h1>{titulo}</h1>{f'<p class="lede">{lede}</p>' if lede else ''}</header>
<div class="body">{corpo}</div>
<footer><span>{nota}</span><span class="n">{n:02d}</span></footer></section>'''
    return html, f'<a href="#{sid}" data-s="{sid}">{titulo}</a>'


def _ret(hist: list[list], preco: float | None, n: int) -> float | None:
    """Retorno do preço atual contra o fechamento n pregões atrás."""
    if not hist or not preco or len(hist) <= n:
        return None
    base = hist[-1 - n][1]
    return preco / base - 1 if base else None


def secao_macro(mercado_micro: dict) -> list[tuple[str, str]]:
    p = config.DATA / "macro.json"
    if not p.exists():
        return [slide("Macro", "capa", "Monitor de Cobertura", '<div class="empty">Sem dados macro. Rode <code>python coletar.py --so-macro</code>.</div>', cls="cover")]
    M = json.loads(p.read_text(encoding="utf-8"))
    a0, a1 = M["anos"]
    focus, sgs = M.get("focus", {}), M.get("sgs", {})
    S1, S2 = "var(--s1)", "var(--s2)"

    # --- mercado (tiles)
    tiles = []
    for nome, q in M.get("mercado", {}).items():
        dec = 2 if q["preco"] < 100 else 0
        tiles.append(f'<div class="tile"><div class="l">{nome}</div><div class="v">{num(q["preco"], dec)}</div><div class="d {dlt_cls(q["var_dia"])}">{pct(q["var_dia"])} no dia</div></div>')
    hora = max((q["hora"] for q in M.get("mercado", {}).values() if q.get("hora")), default="—")

    # --- realizado
    def ult(cod):
        s = sgs.get(str(cod), {}).get("serie") or []
        return s[-1] if s else None
    ipca_m = sgs.get("433", {}).get("serie") or []
    ipca12 = None
    if len(ipca_m) >= 12:
        acc = 1.0
        for _, v in ipca_m[-12:]:
            acc *= 1 + v / 100
        ipca12 = (acc - 1) * 100
    selic = ult(432)
    cambio = ult(1)
    pib = ult(7326)

    def foc(ind, ano):
        s = focus.get(ind, {}).get(str(ano)) or []
        return s
    def foc_delta(s, dias):
        if not s:
            return None
        alvo = (date.fromisoformat(s[-1][0]).toordinal() - dias)
        base = next((r[1] for r in reversed(s) if date.fromisoformat(r[0]).toordinal() <= alvo), None)
        return (s[-1][1] - base) if base is not None else None
    linhas = []
    for ind, real, real_lab, dec, suf in (("IPCA", ipca12, f"12m até {ipca_m[-1][0][:7] if ipca_m else '—'}", 2, "%"),
                                          ("Selic", selic and selic[1], f"meta em {selic[0] if selic else '—'}", 2, "%"),
                                          ("PIB Total", pib and pib[1], f"ano {pib[0][:4] if pib else '—'}", 1, "%"),
                                          ("Câmbio", cambio and cambio[1], f"PTAX {cambio[0] if cambio else '—'}", 2, "")):
        cel = [f'<td class="tk">{ind}</td><td>{num(real, dec, suf=suf)}<br><small class="mut">{real_lab}</small></td>']
        for ano in (a0, a1):
            s = foc(ind, ano)
            d4, d13 = foc_delta(s, 28), foc_delta(s, 91)
            cel.append(f'<td>{num(s[-1][1], dec, suf=suf) if s else "—"}</td>'
                       f'<td class="{dlt_cls(d4)}">{num(d4, 2, "+" if d4 and d4 > 0 else "") if d4 is not None else "—"}</td>'
                       f'<td class="{dlt_cls(d13)}">{num(d13, 2, "+" if d13 and d13 > 0 else "") if d13 is not None else "—"}</td>')
        linhas.append("<tr>" + "".join(cel) + "</tr>")
    data_focus = max((s[-1][0] for i in focus.values() for s in i.values() if s), default="—")
    tabela_focus = f'''<table><thead><tr><th>Indicador</th><th>Realizado</th><th>Focus {a0}</th><th>Δ 4 sem</th><th>Δ 3 m</th><th>Focus {a1}</th><th>Δ 4 sem</th><th>Δ 3 m</th></tr></thead><tbody>{"".join(linhas)}</tbody></table>
<p class="note">Mediana de todos os respondentes (Focus de {data_focus}). Δ = variação da mediana em pontos, sinal verde = subiu (não é juízo de valor).</p>'''

    # --- gráficos Focus (histórico completo desde config.FOCUS_DESDE)
    # formato mín–máx + média: um gráfico por indicador e ano (a banda não se sobrepõe)
    def g_banda(ind, ano, dec, suf, cor, cid, H=212):
        s = foc(ind, ano)
        pts = [[r[0], (r[3] if len(r) > 3 and r[3] is not None else r[1]), r[1], (r[4] if len(r) > 4 else None), (r[5] if len(r) > 5 else None), r[2]] for r in s]
        banda = [[r[0], r[4], r[5]] for r in s if len(r) > 5]
        return svg_linhas(cid, [(f"{ind} {ano}", cor, pts)], dec, suf=suf, titulo=f"{ind} {ano}: média (linha) e mínimo–máximo (sombra)",
                          bandas={0: banda}, extras=["mediana", "mín", "máx", "n"], H=H)
    g_focus_a = [f'<div>{g_banda(ind, ano, dec, suf, cor, f"fc-{ind[:4]}-{ano}")}</div>'
                 for ind, dec, suf in (("IPCA", 2, "%"), ("Selic", 2, "%")) for ano, cor in ((a0, S1), (a1, S2))]
    g_focus_b = [f'<div>{g_banda(ind, ano, dec, suf, cor, f"fc-{ind[:4]}-{ano}")}</div>'
                 for ind, dec, suf in (("PIB Total", 1, "%"), ("Câmbio", 2, "")) for ano, cor in ((a0, S1), (a1, S2))]
    legenda_anos = f'<div class="legend"><span><i style="background:{S1}"></i>{a0}</span><span><i style="background:{S2}"></i>{a1}</span><span><i style="background:{S1};opacity:.25"></i>faixa mínimo–máximo dos respondentes</span></div>'

    # --- Selic por reunião
    fs = M.get("focus_selic", [])
    W, H, ML, MR, MT, MB = 960, 220, 56, 16, 22, 34
    g_selic = ""
    if fs:
        vals = [r["mediana"] for r in fs] + ([selic[1]] if selic else [])
        lo, hi = min(vals) - 0.5, max(vals) + 0.5
        n = len(fs)
        X = lambda i: ML + (i + 0.5) / n * (W - ML - MR)
        Y = lambda v: MT + (hi - v) / (hi - lo) * (H - MT - MB)
        out = []
        for t in ticks(lo, hi, 4):
            out.append(f'<line class="grid" x1="{ML}" x2="{W - MR}" y1="{Y(t):.1f}" y2="{Y(t):.1f}"/><text class="tick" x="{ML - 6}" y="{Y(t) + 4:.1f}" text-anchor="end">{num(t, 2)}</text>')
        if selic:
            out.append(f'<line class="ref" style="stroke:var(--mut)" x1="{ML}" x2="{W - MR}" y1="{Y(selic[1]):.1f}" y2="{Y(selic[1]):.1f}"/><text class="reflab" x="{W - MR}" y="{Y(selic[1]) - 4:.1f}" text-anchor="end">Selic meta hoje {num(selic[1], 2)}%</text>')
        # degraus: cada reunião vale até a próxima
        d = ""
        for i, r in enumerate(fs):
            x0, x1 = X(i) - (W - ML - MR) / n / 2, X(i) + (W - ML - MR) / n / 2
            d += f"{'M' if i == 0 else 'L'}{x0:.1f},{Y(r['mediana']):.1f} L{x1:.1f},{Y(r['mediana']):.1f} "
        out.append(f'<path class="line" style="stroke:{S1}" d="{d}"/>')
        for i, r in enumerate(fs):
            out.append(f'<circle class="dot" data-tip="{r["reuniao"]}: {num(r["mediana"], 2)}% ({r["n"]} respondentes)" cx="{X(i):.1f}" cy="{Y(r["mediana"]):.1f}" r="4" style="fill:{S1}"/>'
                       f'<text class="tick" x="{X(i):.1f}" y="{H - 12}" text-anchor="middle">{r["reuniao"]}</text>')
            if i == 0 or i == n - 1 or r["mediana"] != fs[i - 1]["mediana"]:
                out.append(f'<text class="cap" x="{X(i):.1f}" y="{Y(r["mediana"]) - 9:.1f}" text-anchor="middle">{num(r["mediana"], 2)}</text>')
        g_selic = f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="Selic por reunião">{"".join(out)}</svg>'

    # --- juro real
    ntnb = M.get("ntnb", {})
    curva = [(v, t) for v, t in ntnb.get("curva", [])]
    n35 = M.get("ntnb_2035", [])
    ntnb35 = n35[-1][1] if n35 else None

    # --- Ibov = lucro / (Ke - g)
    pb = config.DATA / "bloomberg_macro.json"
    bbg = json.loads(pb.read_text(encoding="utf-8")) if pb.exists() else {}
    pe = config.IBOV_PE_FWD or bbg.get("ibov_pe_fwd")
    ey = (1 / pe) if pe else None
    premio = (ey * 100 - ntnb35) if (ey and ntnb35) else None
    ibov_txt = (f'<div class="tiles"><div class="tile"><div class="l">P/L Ibov 12m à frente</div><div class="v">{num(pe, 1, suf="x")}</div><div class="d">{("Bloomberg " + bbg.get("data", "")) if bbg.get("ibov_pe_fwd") and not config.IBOV_PE_FWD else ("config.IBOV_PE_FWD (manual)" if pe else "aguardando Bloomberg (IBOV Index BEST_PE_RATIO)")}</div></div>'
                f'<div class="tile"><div class="l">Earnings yield (1/P/L)</div><div class="v">{pct(ey, 1, sinal=False)}</div><div class="d">lucro ÷ preço, nominal</div></div>'
                f'<div class="tile"><div class="l">NTN-B 2035 (juro real)</div><div class="v">{num(ntnb35, 2, suf="%")}</div><div class="d">Tesouro Direto {ntnb.get("data", "—")}</div></div>'
                f'<div class="tile"><div class="l">EY − NTN-B</div><div class="v">{num(premio, 1, suf=" pp")}</div><div class="d">prêmio implícito aproximado (EY nominal vs juro real)</div></div></div>'
                f'<p class="note">Modelo do esboço: Ibov = Lucro ÷ (Ke − g). Com P/L à frente e juro real observados, o que sobra é (Ke − g) implícito = earnings yield. '
                f'Quando o P/L vier da ponte Bloomberg, o painel passa a acompanhar a série do prêmio ao longo do tempo.</p>')

    # --- atividade e crédito
    def serie(cod):
        return sgs.get(str(cod), {}).get("serie") or []
    g_ativ = [
        f'<div>{svg_linhas("ibc", [("IBC-Br", S1, serie(24364))], 1, titulo="IBC-Br (índice dessazonalizado)")}</div>',
        f'<div>{svg_linhas("cred", [("Crédito", S1, [[d, v / 1000] for d, v in serie(20539)])], 0, pref="R$ ", suf=" bi", titulo="Saldo de crédito total (R$ bi)")}</div>',
        f'<div>{svg_linhas("caged", [("CAGED", S1, [[d, v / 1e6] for d, v in serie(28763)])], 2, suf=" mi", titulo="CAGED: estoque de empregos formais (mi)")}</div>',
        f'<div>{svg_linhas("ipca", [("IPCA 12m", S1, _acum12(serie(433)))], 2, suf="%", titulo="IPCA acumulado 12 meses (%)")}</div>',
    ]
    g_merc = [f'<div>{svg_linhas("h-" + k.replace(" ", "").replace("/", ""), [(k, S1, pts)], 2 if k in ("USD/BRL", "VIX") else 0, titulo=k + " (série completa)", H=300 if i < 2 else 380)}</div>'
              for i, (k, pts) in enumerate(M.get("hist", {}).items())]
    g_juro = [f'<div>{svg_curva(curva, f"Curva NTN-B em {ntnb.get('data', '—')} (taxa real, % a.a.)", H=320)}</div>',
              f'<div>{svg_linhas("ntnb35", [("NTN-B 2035", S1, n35)], 2, suf="%", titulo="NTN-B 2035: juro real, histórico completo", H=320)}</div>']

    # --- movimentos recentes (avulsos + cobertura)
    mov = []
    for tk, q in M.get("movimentos", {}).items():
        h = M.get("mov_hist", {}).get(tk, [])
        mov.append((tk, q["preco"], q["var_dia"], _ret(h, q["preco"], 5), _ret(h, q["preco"], 20), q["hora"]))
    from fontes import b3
    for tk, mk in mercado_micro.items():
        intr = mk.get("intraday") or {}
        h = [[d, v] for d, v in b3.serie(tk)]
        if intr.get("preco"):
            fa = intr.get("fech_anterior")
            mov.append((tk + " (cobertura)", intr["preco"], (intr["preco"] / fa - 1) if fa else None, _ret(h, intr["preco"], 5), _ret(h, intr["preco"], 20), intr.get("hora")))
    linhas_mov = "".join(f'<tr><td class="tk">{t}</td><td>{num(p, 2)}</td><td class="{dlt_cls(d1)}">{pct(d1)}</td><td class="{dlt_cls(d5)}">{pct(d5)}</td><td class="{dlt_cls(d20)}">{pct(d20)}</td><td class="mut">{(h or "—")[11:]}</td></tr>'
                         for t, p, d1, d5, d20, h in mov)

    # --- capa: quatro números-herói
    mk = M.get("mercado", {})
    def hero(l, v, d=""):
        return f'<div class="tile hero"><div class="l">{l}</div><div class="v">{v}</div><div class="d">{d}</div></div>'
    ibv = mk.get("Ibovespa", {})
    heroes = (hero("Ibovespa", num(ibv.get("preco"), 0), f'<span class="{dlt_cls(ibv.get("var_dia"))}">{pct(ibv.get("var_dia"))}</span> no dia')
              + hero("Selic meta", num(selic[1] if selic else None, 2, suf="%"), f"Focus fim {a0}: {num(foc('Selic', a0)[-1][1] if foc('Selic', a0) else None, 2, suf='%')}")
              + hero("NTN-B 2035", num(ntnb35, 2, suf="%"), "juro real, " + str(ntnb.get("data", "—")))
              + hero("USD/BRL", num(mk.get("USD/BRL", {}).get("preco"), 2), f"Focus fim {a0}: {num(foc('Câmbio', a0)[-1][1] if foc('Câmbio', a0) else None, 2)}"))
    # painel de atualizações (direita da capa): fonte · periodicidade · última coleta · data do dado
    pest = config.DATA / "estado.json"
    est = json.loads(pest.read_text(encoding="utf-8")) if pest.exists() else {}
    from fontes import b3 as _b3
    serie_ref = max((s[-1][0] for tk in config.UNIVERSO for s in [_b3.serie(tk)] if s), default=None)
    prpm = config.RAIZ / "macro_bcb" / "rpm.json"
    rpm_data = json.loads(prpm.read_text(encoding="utf-8")).get("data") if prpm.exists() else None
    FONTES = [  # (nome no estado, rótulo, periodicidade, limite em dias, data do dado)
        ("cotações universo", "Cotações (Yahoo)", "a cada hora", 1, hora[5:10].replace("-", "/") if hora and hora != "—" else None),
        ("B3 COTAHIST", "Fechamento B3", "diário, 19h30", 4, serie_ref[5:].replace("-", "/") if serie_ref else None),
        ("B3 ações", "Nº de ações (B3)", "diário", 4, None),
        ("Tesouro NTN-B", "NTN-B (Tesouro)", "diário", 4, ntnb.get("data", "")[5:].replace("-", "/") or None),
        ("SGS diários", "Selic e PTAX (SGS)", "diário", 4, (selic[0][5:].replace("-", "/") if selic else None)),
        ("consenso Yahoo", "Consenso (Yahoo)", "diário", 4, None),
        ("consenso Bloomberg", "Consenso (Bloomberg)", "sob demanda", 30, None),
        ("minhas estimativas", "Minhas estimativas", "sob demanda", 999, None),
        ("Focus", "Focus (BCB)", "semanal, segunda", 9, data_focus[5:].replace("-", "/") if data_focus != "—" else None),
        ("Focus longo", "Focus, séries longas", "semanal", 9, None),
        ("SGS mensais", "SGS mensais", "mensal", 40, ipca_m[-1][0][:7] if ipca_m else None),
        ("realizado anual", "Realizado anual (SGS)", "mensal", 40, None),
        ("RPM", "RPM (BCB)", "trimestral", 120, rpm_data),
    ]
    agora = datetime.now()
    tr = []
    for chave, rotulo, per, lim, ref in FONTES:
        e = est.get(chave)
        if chave == "RPM":
            e = {"em": None, "ok": bool(rpm_data)}
        if not e:
            tr.append(f'<tr><td class="tk" style="opacity:.55">{rotulo}</td><td class="mut" style="text-align:left">{per}</td><td class="mut">nunca</td><td class="mut">—</td></tr>')
            continue
        em = e.get("em")
        try:
            idade = (agora - datetime.strptime(em, "%Y-%m-%d %H:%M")).days if em else 0
        except ValueError:
            idade = 999
        ok = e.get("ok", True)
        velho = idade > lim
        cor = "var(--dn)" if (not ok or velho) else "var(--up)"
        st = "falhou" if not ok else ("velho" if velho else "ok")
        col = (em[5:16].replace("-", "/") if em else "—")
        tr.append(f'<tr title="{e.get("msg", "")}"><td class="tk"><i style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{cor};margin-right:8px;vertical-align:1px" aria-label="{st}"></i>{rotulo}</td>'
                  f'<td class="mut" style="text-align:left">{per}</td><td style="color:{cor if st != "ok" else "inherit"}">{col}</td><td class="mut">{ref or "—"}</td></tr>')
    painel_atual = (f'<div class="panel" style="padding:16px 18px"><h2 style="margin-bottom:10px">Atualizações</h2>'
                    f'<table class="compact"><thead><tr><th>Fonte</th><th style="text-align:left">Periodicidade</th><th>Última coleta</th><th>Dado de</th></tr></thead><tbody>{"".join(tr)}</tbody></table>'
                    f'<p class="note" style="font-size:11.5px;margin:10px 0 0">Ponto verde = dentro do prazo; vermelho = falhou ou passou do limite da janela. Colunas: quando o coletor rodou e a data de referência do dado.</p></div>')
    S = []
    S.append(slide_painel(M, sgs, mercado_micro, le_csv(MINHAS), le_csv(CONS), est))
    S += slides_opiniao(M, sgs)
    capa_fontes = slide("Fontes", "capa", "Fontes e atualizações",
                   f'<div class="duo"><div><div class="heroes">{heroes}</div></div>{painel_atual}</div>',
                   f"Macro, setores e empresas cobertas. Preço, minha estimativa e consenso. Atualizado em {M.get('gerado_em', '—')}, cotações às {hora[11:]}.",
                   "Fontes: B3, BCB (Focus e SGS), Tesouro Transparente, Yahoo Finance, Bloomberg (quando disponível).", cls="cover")
    M["_capa_fontes"] = capa_fontes
    globals()['_M_CAPA'] = capa_fontes
    S.append(slide("Macro", "mercado", "Mercado agora",
                   f'<div class="tiles strip">{"".join(tiles)}</div><div class="grid2" style="margin-top:8px">{"".join(g_merc[:2])}</div>',
                   "Índices, câmbio, petróleo, volatilidade e juro americano, com atraso de ~15 minutos. Use o seletor de janela em cada gráfico ou o global no trilho.",
                   f"Yahoo Finance, última cotação {hora}. Ibovespa desde 1993 e S&P 500 desde 1970, fechamentos diários."))
    sd = slide_ibov_decomp(M.get("_ibov_decomp"))
    if sd:
        S.append(sd)
    sdy = slide_ibov_dy(M)
    if sdy:
        S.append(sdy)
    sfl = slide_fluxo_investidores(M)
    if sfl:
        S.append(sfl)
    svl = slide_volume(M)
    if svl:
        S.append(svl)
    srh = slide_rpm_historico(M, sgs)
    if srh:
        S.append(srh)
    S += slides_juros(M, sgs)
    S += slides_fra(M, sgs)
    S.append(slide("Macro", "focus", "Focus vs realizado",
                   f'<div class="panel"><table class="big">{tabela_focus.split("<table>")[1].split("</table>")[0]}</table></div>',
                   f"Mediana do mercado para {a0} e {a1} contra o último dado observado.",
                   f"BCB, Focus de {data_focus}, todos os respondentes. Δ = variação da mediana em pontos; verde = subiu, sem juízo de valor."))
    S.append(slide("Macro", "revisoes", "Revisões do Focus · inflação e juros",
                   f'{legenda_anos}<div class="grid2">{"".join(g_focus_a)}</div>',
                   "Linha = média dos respondentes; sombra = do mais baixo ao mais alto. Quando a sombra estreita, o mercado converge. Diário desde " + config.FOCUS_DESDE[:7] + ".",
                   "BCB Olinda, ExpectativasMercadoAnuais, baseCalculo 0. Tooltip traz mediana, mínimo, máximo e nº de respondentes."))
    S.append(slide("Macro", "revisoes-b", "Revisões do Focus · atividade e câmbio",
                   f'{legenda_anos}<div class="grid2">{"".join(g_focus_b)}</div>',
                   "Mesmo formato: média e faixa mínimo–máximo, PIB e câmbio para " + f"{a0} e {a1}.",
                   "BCB Olinda, ExpectativasMercadoAnuais, baseCalculo 0."))
    S += slides_focus(M, sgs)
    S += slides_assertividade()
    S.append(slide("Macro", "selic", "Trajetória esperada da Selic",
                   f'<div class="panel">{g_selic}</div>',
                   "Mediana do Focus por reunião do Copom. Cada degrau é uma decisão esperada.",
                   "BCB Olinda, ExpectativasMercadoSelic. Linha cinza: Selic meta vigente."))
    S.append(slide("Macro", "juro-real", "Juro real",
                   f'<div class="grid2">{"".join(g_juro)}</div>',
                   "Curva das NTN-B hoje e o histórico completo do vencimento 2035.",
                   "Tesouro Transparente, taxa de compra da manhã do Tesouro Direto."))
    S.append(slide("Macro", "ibov-modelo", "Ibov = Lucro ÷ (Ke − g)",
                   f'<div class="formula">Ibov = Lucro ÷ (K<sub>e</sub> − g)</div>{ibov_txt.replace(chr(10), "")}',
                   "Com o P/L à frente e o juro real observados, o que sobra é o (Ke − g) implícito.",
                   "P/L do Ibov: ponte Bloomberg (IBOV Index BEST_PE_RATIO) ou config.IBOV_PE_FWD."))
    S.append(slide("Macro", "mercado-5a", "Câmbio, petróleo e volatilidade",
                   f'<div class="grid3">{"".join(g_merc[2:])}</div>',
                   "USD/BRL, Brent e VIX, série completa. O seletor estreita a janela.", "Yahoo Finance, fechamentos diários."))
    S.append(slide("Macro", "atividade", "Atividade, inflação e crédito",
                   f'<div class="grid2">{"".join(g_ativ)}</div>',
                   "Séries completas do SGS para a janela configurada.", "BCB SGS: 24364, 20539, 28763, 433."))
    S.append(slide("Macro", "movimentos", "Movimentos recentes",
                   f'<div class="panel"><table class="big"><thead><tr><th>Papel</th><th>Preço</th><th>Dia</th><th>5 dias</th><th>20 dias</th><th>Hora</th></tr></thead><tbody>{linhas_mov}</tbody></table></div>',
                   "Papéis avulsos e cobertura. 5 e 20 dias contra o fechamento de 5 e 20 pregões atrás.",
                   "Sem fonte automática ainda: CDS Brasil (ponte Bloomberg), minério de ferro (TIO=F inconsistente), carteiras recomendadas e podcasts (curadoria manual)."))
    return S


def _acum12(mensal: list[list]) -> list[list]:
    out = []
    for i in range(11, len(mensal)):
        acc = 1.0
        for _, v in mensal[i - 11:i + 1]:
            acc *= 1 + v / 100
        out.append([mensal[i][0], (acc - 1) * 100])
    return out


def build() -> None:
    mercado = json.loads((config.DATA / "mercado.json").read_text(encoding="utf-8")) if (config.DATA / "mercado.json").exists() else {"tickers": {}}
    minhas, cons = le_csv(MINHAS), le_csv(CONS)
    from fontes import b3
    hoje = date.today().isoformat()
    _N["n"] = 0
    linhas_resumo, slides_emp = [], []
    horas = []

    for tk, v in config.UNIVERSO.items():
        mk = mercado["tickers"].get(tk, {})
        intr = mk.get("intraday") or {}
        acoes = mk.get("acoes")
        serie = b3.serie(tk)
        preco = intr.get("preco") or (serie[-1][1] if serie else None)
        fech_ant = intr.get("fech_anterior") or (serie[-2][1] if len(serie) > 1 else None)
        var_dia = (preco / fech_ant - 1) if (preco and fech_ant) else None
        if intr.get("hora"):
            horas.append(intr["hora"])
        # variação no ano e 12m a partir do COTAHIST
        def _var(dias_ou_data):
            if not serie or not preco:
                return None
            base = next((p for d, p in reversed(serie) if d <= dias_ou_data), None)
            return (preco / base - 1) if base else None
        var_ano = _var(f"{hoje[:4]}-01-01") if serie and serie[0][0] < f"{hoje[:4]}-01-01" else None
        var_12m = _var(f"{int(hoje[:4]) - 1}{hoje[4:]}") if serie and serie[0][0] < f"{int(hoje[:4]) - 1}{hoje[4:]}" else None
        lab_ano, lab_12m = "No ano", "em 12m"
        if var_ano is None and serie and preco:   # série mais curta que o ano: variação desde o 1º pregão
            var_ano = preco / serie[0][1] - 1
            lab_ano = f"Desde {serie[0][0][8:10]}/{serie[0][0][5:7]}/{serie[0][0][2:4]}"
        if var_12m is None:
            lab_12m = "em 12m (série curta)"

        est = {}
        for ano in config.ANOS_FISCAIS:
            m = ultima(minhas, tk, ano)
            c = ultima(cons, tk, ano, prefer=("bloomberg", "yahoo"))
            def pl(r):
                if not r or not preco or not acoes:
                    return None
                eps = r["eps"] or (r["lucro"] * 1e6 / acoes if r["lucro"] else None)
                return preco / eps if eps and eps > 0 else None
            est[ano] = {"m": m, "c": c, "pl_m": pl(m), "pl_c": pl(c),
                        "delta": (m["lucro"] / c["lucro"] - 1) if (m and c and m["lucro"] and c["lucro"]) else None}
        a0, a1 = config.ANOS_FISCAIS
        tgt_m = next((est[a]["m"]["target"] for a in config.ANOS_FISCAIS if est[a]["m"] and est[a]["m"]["target"]), None)
        tgt_c = next((est[a]["c"]["target"] for a in config.ANOS_FISCAIS if est[a]["c"] and est[a]["c"]["target"]), None)
        up_m = (tgt_m / preco - 1) if (tgt_m and preco) else None
        up_c = (tgt_c / preco - 1) if (tgt_c and preco) else None
        fonte_c = (est[a0]["c"] or {}).get("fonte", "—")
        n_an = (est[a0]["c"] or {}).get("n_analistas")
        novo_m = any(est[a]["m"] and est[a]["m"]["data"] >= hoje[:8] for a in config.ANOS_FISCAIS)

        linhas_resumo.append(f'''<tr><td class="tk"><a href="#{tk}-preco" style="color:inherit;text-decoration:none">{tk}</a><small>{v["nome"]}</small></td>
<td>{num(preco, 2, "R$ ")}</td><td class="{dlt_cls(var_dia)}">{pct(var_dia)}</td><td class="{dlt_cls(var_ano)}">{pct(var_ano)}</td>
<td>{num(est[a0]["pl_m"], 1, suf="x")}</td><td>{num(est[a0]["pl_c"], 1, suf="x")}</td><td>{num(est[a1]["pl_m"], 1, suf="x")}</td><td>{num(est[a1]["pl_c"], 1, suf="x")}</td>
<td class="{dlt_cls(est[a0]["delta"])}">{pct(est[a0]["delta"])}</td><td class="{dlt_cls(up_m)}">{pct(up_m)}</td><td class="{dlt_cls(up_c)}">{pct(up_c)}</td></tr>''')

        novo = "<span class=novo>novo</span>" if novo_m else ""

        # --- slides da empresa
        tiles = f'''<div class="tiles">
<div class="tile hero"><div class="l">{tk} · preço {"(" + intr["hora"][11:] + ", atraso ~15 min)" if intr.get("hora") else "(fechamento)"}</div><div class="v">{num(preco, 2, "R$ ")}</div><div class="d {dlt_cls(var_dia)}">{pct(var_dia)} no dia · {num(intr.get("min_dia"), 2)}–{num(intr.get("max_dia"), 2)}</div></div>
<div class="tile"><div class="l">{lab_ano} / 12 meses</div><div class="v {dlt_cls(var_ano)}">{pct(var_ano)}</div><div class="d {dlt_cls(var_12m)}">{pct(var_12m)} {lab_12m} · 52s {num(intr.get("min_52s"), 2)}–{num(intr.get("max_52s"), 2)}</div></div>
<div class="tile"><div class="l">P/L {a0} · minha / consenso</div><div class="v">{num(est[a0]["pl_m"], 1, suf="x")} <span class="mut">/</span> {num(est[a0]["pl_c"], 1, suf="x")}</div><div class="d">{a1}: {num(est[a1]["pl_m"], 1, suf="x")} / {num(est[a1]["pl_c"], 1, suf="x")}</div></div>
<div class="tile"><div class="l">Alvo · minha / consenso</div><div class="v">{num(tgt_m, 2)} <span class="mut">/</span> {num(tgt_c, 2)}</div><div class="d"><span class="{dlt_cls(up_m)}">{pct(up_m)}</span> / <span class="{dlt_cls(up_c)}">{pct(up_c)}</span> de upside</div></div>
<div class="tile"><div class="l">Consenso</div><div class="v">{n_an if n_an else "—"}</div><div class="d">analistas · fonte {fonte_c} · {(est[a0]["c"] or {}).get("data", "—")}</div></div>
</div>'''
        grafico_preco = svg_preco(f"px-{tk}", serie, intr, {"minha": tgt_m, "cons": tgt_c})
        barras = []
        for campo, lab in (("lucro", "Lucro líquido"), ("ebitda", "EBITDA"), ("receita", "Receita líquida")):
            grupos = []
            for a in config.ANOS_FISCAIS:
                m, c = est[a]["m"], est[a]["c"]
                if (m and m[campo] is not None) or (c and c[campo] is not None):
                    grupos.append((str(a), m[campo] if m else None, c[campo] if c else None))
            if grupos:
                barras.append(f"<div>{svg_barras(lab, grupos, 'R$ mi')}</div>")
        legenda = f'<div class="legend"><span><i style="background:{COR["minha"]}"></i>Minha estimativa</span><span><i style="background:{COR["cons"]}"></i>Consenso</span></div>'
        aviso_m = "" if any(est[a]["m"] for a in config.ANOS_FISCAIS) else f'<p class="note">Sem estimativa minha para {tk}: preencha <code>estimativas/minhas.csv</code> (ou aponte o modelo Excel em <code>config.UNIVERSO["{tk}"]["modelo"]</code>).</p>'
        rev = "".join(f'<div>{svg_revisoes(a, [r for r in cons if r["ticker"] == tk], [r for r in minhas if r["ticker"] == tk])}</div>' for a in config.ANOS_FISCAIS)
        tabela = "".join(
            f'<tr><td>{r["ano"]}</td><td>{"Minha" if src == "m" else "Consenso"}</td><td>{num(r["receita"], 0)}</td><td>{num(r["ebitda"], 0)}</td><td>{num(r["lucro"], 0)}</td><td>{num(r["eps"], 2)}</td><td>{num(r["target"], 2)}</td><td>{r["data"]}</td><td class="mut">{r["fonte"]}</td></tr>'
            for a in config.ANOS_FISCAIS for src, r in (("m", est[a]["m"]), ("c", est[a]["c"])) if r)
        slides_emp.append((tk, [
            (f"{tk} · preço{novo}", f"{tk}-preco",
             f'{tiles}<div class="panel">{grafico_preco}</div>',
             f'{v["razao"]} · CVM {v["cod_cvm"]} · {num(acoes / 1e6 if acoes else None, 1)} mi ações. Fechamento B3 desde {serie[0][0][:7] if serie else "—"}, último ponto intraday.',
             "Série não ajustada por proventos. Linhas horizontais: preço-alvo meu e do consenso."),
            (f"{tk} · eu vs consenso", f"{tk}-consenso",
             f'{legenda}<div class="grid2">{"".join(barras)}</div>{aviso_m}<h2 style="font-size:14px;margin:22px 0 6px">Revisões ao longo do tempo</h2><div class="grid2">{rev}</div>',
             f"Um gráfico por métrica, barras lado a lado por ano fiscal (R$ milhões). Abaixo, cada coleta acrescenta um ponto ao consenso e cada versão do modelo, um ponto meu.",
             "Consenso Yahoo não traz EBITDA; Bloomberg traz. Prioridade Bloomberg > Yahoo."),
            (f"{tk} · estimativas", f"{tk}-tabela",
             f'<div class="panel"><table class="big"><thead><tr><th>Ano</th><th>Série</th><th>Receita</th><th>EBITDA</th><th>Lucro</th><th>EPS</th><th>Alvo</th><th>Data</th><th>Fonte</th></tr></thead><tbody>{tabela}</tbody></table></div>',
             "Últimas estimativas por ano fiscal, R$ milhões e R$ por ação.", ""),
        ]))

    # --- montagem: Macro -> Setorial -> Cobertura -> empresas
    S = secao_macro(mercado["tickers"])
    S.append(slide("Setorial", "setorial", "Setorial × Macro · Wrap-up × Consenso",
                   '<div class="panel"><p class="note" style="font-size:15px;max-width:720px">Camada ainda não construída. Desenho do esboço: para cada setor coberto, como o cenário macro bate nos drivers e onde a casa diverge do consenso.</p></div>',
                   "Entra depois da camada Micro."))
    S.append(slide("Cobertura", "universo", "Universo coberto",
                   f'''<div class="panel"><table class="big"><thead><tr><th>Empresa</th><th>Preço</th><th>Dia</th><th>Ano*</th><th>P/L {a0} meu</th><th>cons.</th><th>P/L {a1} meu</th><th>cons.</th><th>Δ lucro {a0}</th><th>Upside meu</th><th>cons.</th></tr></thead><tbody>{"".join(linhas_resumo)}</tbody></table></div>''',
                   f"Preço intraday sobre histórico B3. P/L = preço ÷ lucro por ação estimado. Δ = minha estimativa de lucro {a0} contra o consenso.",
                   "* Ano = desde o 1º pregão do ano; série iniciada este ano (SAUD3) = desde a listagem. Minha estimativa: estimativas/minhas.csv ou aba E-A do modelo. Consenso: Bloomberg (ponte) > Yahoo."))
    for tk, lst in slides_emp:
        for titulo, sid, corpo, lede, nota in lst:
            S.append(slide(tk, sid, titulo, corpo, lede, nota))
    if M_capa := globals().get("_M_CAPA"):
        S.append(M_capa)

    rail, sec_atual = [], None
    for html_s, item in S:
        sec = html_s.split('data-sec="')[1].split('"')[0]
        if sec != sec_atual:
            rail.append(f'<div class="sec">{sec}</div>')
            sec_atual = sec
        rail.append(item)
    fonts_css = (config.RAIZ / "assets" / "fonts.css").read_text(encoding="utf-8") if (config.RAIZ / "assets" / "fonts.css").exists() else ""
    css = CSS.replace("/*ITALICO*/", "").replace("/*FT*/", config.FONTE_TITULO).replace("/*FC*/", config.FONTE_CORPO) + ("\nbody,body *,svg text{font-style:italic}" if config.ESTILO_ITALICO else "")
    html = (PAGE.replace("/*FONTS*/", fonts_css).replace("/*CSS*/", css).replace("/*JS*/", JS).replace("/*VERSAO*/", config.VERSAO)
            .replace("/*DATA*/", hoje).replace("/*RAIL*/", "".join(rail)).replace("/*SLIDES*/", "".join(h for h, _ in S)))
    SAIDA.write_text(html, encoding="utf-8")
    print(f"ok -> {SAIDA} ({len(html) // 1024} KB)")


if __name__ == "__main__":
    build()
