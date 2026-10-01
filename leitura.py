"""Leitura do dia — resumo analítico dos relatórios novos (BTG Research + Itaú BBA) com gráficos de dados PRIMÁRIOS.

Fluxo (cron da sessão, 19h em dias úteis):
  1. `python leitura.py --montar`            junta os relatórios coletados desde a última leitura (índices do repositório
                                             privado btg-research), classifica por tema/setor/cobertura e grava o material
                                             em output/leitura_material.md (+ .json) para o Claude ler.
  2. Claude escreve output/leitura_digest.json: {"data", "cabecalho", "blocos": [{"titulo", "texto", "grafico"}]}
     - texto: HTML simples do Telegram (<b>, <i>); grafico: nome ("fiscal", "inflacao", "juros", "cambio", "fluxo",
       "volume", "ibov", "credito", "atividade", "emprego", "commodities") ou dict {"tipo": "setor", "setor": "Financeiro"},
       {"tipo": "papel", "ticker": "RDOR3"}, {"tipo": "papeis", "tickers": [...]}, {"tipo": "png", "arquivo": "..."} ou null.
  3. `python leitura.py --enviar output/leitura_digest.json`  renderiza os PNGs (matplotlib), manda pelo Telegram (foto +
     legenda por bloco), grava leituras/AAAA-MM-DD.html no btg-research (commit + push) e avança o marcador.

Regras: sell-side é insumo; o gráfico vem do dado primário (BCB, IBGE, B3, Tesouro) com a série completa; nada de
texto de relatório no site público. `python leitura.py --grafico fiscal` testa um gráfico isolado.
"""
from __future__ import annotations

import base64
import csv
import io
import json
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import config
from fontes import telegram

RES = config.RAIZ.parent / "btg-research"
LEIT = RES / "leituras"
PNG = LEIT / "png"
ESTADO = LEIT / "estado.json"
MATERIAL_MD = config.OUTPUT / "leitura_material.md"
MATERIAL_JSON = config.OUTPUT / "leitura_material.json"

TEMAS = [
    ("fiscal", r"fiscal|primary (deficit|result|balance|surplus)|public sector|d[ií]vida|\bdebt\b|tesouro|treasury|or[çc]ament"),
    ("inflacao", r"\bIPCA\b|inflation|infla[çc][ãa]o|\bCPI\b|\bPCE\b|\bIGP"),
    ("juros", r"\bSelic\b|\bCopom\b|monetary policy|rate (hike|cut|decision)|\bFOMC\b|\bFed\b|\bjuros\b|BanRep|\bhik(e|ing)\b|yield"),
    ("atividade", r"\bGDP\b|\bPIB\b|\bactivity\b|IBC-?Br|\bIMACEC\b|retail sales|industrial production|\bvarejo\b|ind[úu]stria|atividade"),
    ("emprego", r"unemployment|labor market|\bPNAD\b|\bCAGED\b|payroll|\bemprego\b|desemprego|\bjobs?\b"),
    ("cambio", r"\bFX\b|\bBRL\b|c[âa]mbio|exchange (rate|flow)|currenc"),
    ("fluxo", r"\bflows?\b|\bfluxo\b|Follow the Money|positioning|\bEPFR\b|foreign investor|estrangeiro"),
    ("credito", r"\bcredit\b|cr[ée]dito|\bNPL\b|delinquen|inadimpl|lending|\bloans?\b|consignado"),
    ("commodities", r"\boil\b|petr[óo]leo|diesel|gasoline|\bfuel\b|brent|iron ore|min[ée]rio|commodit|\bsoy|a[çc][úu]car|sugar|ethanol|etanol|\bpulp\b|celulose"),
]
SETORES = [
    ("Financeiro", r"\bbank|banc|nubank|financ|insur|segur|\bIRB\b|\bB3\b|\bXP\b|BTG Pactual|Ita[úu]sa|Bradesco|Santander|Banco do Brasil|reinsur"),
    ("Petróleo e gás", r"\boil\b|\bgas\b|petro|Petrobras|\bPRIO\b|Brava|Vibra|Ultrapar|Cosan|Ra[íi]zen|fuel"),
    ("Materiais básicos", r"\bVale\b|mining|miner|steel|siderur|\bCSN\b|Gerdau|Usiminas|Suzano|Klabin|pulp|celulose|Braskem|petrochem"),
    ("Utilidade pública", r"utilit|energy|el[ée]tric|Eletrobras|Axia|Equatorial|Energisa|Copel|Cemig|Eneva|Engie|Sabesp|sanea|water|Taesa|\bISA\b|CPFL|Auren"),
    ("Consumo cíclico", r"retail|varejo|consumer|Magazine|Magalu|Mercado Livre|Lojas Renner|\bAzzas\b|\bC&A\b|Natura|Vivara|Smart ?Fit|educa|Cogna|Yduqs|Localiza|Movida|\bVamos\b|Azul|Gol\b|Embraer|CVC"),
    ("Consumo não cíclico", r"\bfood\b|aliment|\bJBS\b|\bBRF\b|Marfrig|Minerva|Ambev|beverage|bebida|Carrefour|Assa[íi]|supermarket|\bagri|Brasil ?Agro|SLC|3tentos|protein"),
    ("Imobiliário", r"real estate|homebuild|constru|imobili|Cyrela|\bCury\b|\bMRV\b|Tenda|Direcional|\bEztec\b|Multiplan|Iguatemi|\bAllos\b|shopping|\bFII|LOG\b"),
    ("Saúde", r"health|sa[úu]de|hospital|Rede D.?Or|Hapvida|Bradsa[úu]de|Fleury|\bRaia|Drogasil|Hypera|pharma|farm[áa]c|Oncocl"),
    ("Bens industriais", r"capital goods|industrial|\bWEG\b|Iochpe|Randon|Marcopolo|transport|logist|\bRumo\b|\bCCR\b|Motiva|Ecorodovias|Santos Brasil"),
    ("Telecom", r"telecom|\bVivo\b|Telef[ôo]nica|\bTIM\b|Brisanet|Desktop|Unifique"),
    ("Tecnologia", r"\btech|software|Totvs|Locaweb|Positivo|Intelbras|Bemobi|\bAI\b"),
]
COBERTURA = {"RDOR3": r"Rede D.?Or|RDOR3", "SAUD3": r"Bradsa[úu]de|SAUD3|Bradesco Sa[úu]de|Odontoprev", "RENT3": r"Localiza|RENT3",
             "CYRE3": r"Cyrela|CYRE3", "CURY3": r"\bCury\b|CURY3"}
EXCLUI = r"LatAm ex-Brasil|Latam Talking Points|\bChile\b|Argentin|Colombia|\bPeru\b|\bMexico\b|M[ée]xico|Paraguay|Uruguay|Global Earlybird|Daily Roadmap|Daily Fuel|Daily Briefing|Week Ahead|Market Data Monitor|BanRep|IMACEC|Falabella|Alpek|Mallplaza|CABA\b|Buenos Aires"
GRAFICO_POR_TEMA = {"fiscal": "fiscal", "inflacao": "inflacao", "juros": "juros", "atividade": "atividade", "emprego": "emprego",
                    "cambio": "cambio", "fluxo": "fluxo", "credito": "credito", "commodities": "commodities"}


# ----------------------------------------------------------------------------------------------------- material
def _indices() -> list[dict]:
    out = []
    for fonte, arq, txt in (("BTG", RES / "indice.json", RES / "txt"), ("Itaú BBA", RES / "bba" / "indice.json", RES / "bba" / "txt")):
        if not arq.exists():
            continue
        I = json.loads(arq.read_text(encoding="utf-8"))
        for k, it in (I.items() if isinstance(I, dict) else ((x["id"], x) for x in I)):
            it = dict(it); it["fonte"] = fonte; it["_txt"] = txt / f"{it.get('id', k)}.txt"
            out.append(it)
    return out


def _classifica(it: dict, texto: str) -> dict:
    base = f"{it.get('titulo', '')} {it.get('empresa', '')} {it.get('tipo', '')}"
    amostra = base + " " + texto[:1500]
    setores = [s for s, rx in SETORES if re.search(rx, base, re.I)]
    macro = bool(re.search(r"Macroeconomic|Macro|Brazil •|Brasil •|Global •|Strategy|Estrat", it.get("empresa", "") + " " + it.get("tipo", ""), re.I))
    temas = [t for t, rx in TEMAS if re.search(rx, base, re.I)]
    if not temas and macro:
        temas = [t for t, rx in TEMAS if re.search(rx, amostra, re.I)][:2]
    cob = [tk for tk, rx in COBERTURA.items() if re.search(rx, amostra, re.I)]
    fora = bool(re.search(EXCLUI, base, re.I))
    if cob:
        temas = ["cobertura"] + temas
    elif setores and not temas:
        temas = ["setores"]
    if not temas:
        temas = ["outros"]
    return {"temas": temas, "setores": setores, "cobertura": cob, "fora": fora}


def montar(desde: str | None = None, ate: str | None = None) -> dict:
    est = json.loads(ESTADO.read_text(encoding="utf-8")) if ESTADO.exists() else {}
    desde = desde or est.get("ultima") or (date.today() - timedelta(days=1)).isoformat() + " 19:00"
    ate = ate or datetime.now().strftime("%Y-%m-%d %H:%M")
    itens = []
    for it in _indices():
        col = it.get("coletado") or ""
        if not (desde < col <= ate):
            continue
        texto = it["_txt"].read_text(encoding="utf-8", errors="replace") if it["_txt"].exists() else ""
        texto = re.sub(r"\n{3,}", "\n\n", texto).strip()
        it.update(_classifica(it, texto)); it["texto"] = texto; it.pop("_txt", None)
        itens.append(it)
    itens.sort(key=lambda i: (i.get("fora", False), not i.get("destaque"), i.get("fonte"), i.get("titulo", "")))
    # material em Markdown
    ordem = ["cobertura", "fiscal", "juros", "inflacao", "atividade", "emprego", "cambio", "fluxo", "credito", "commodities", "setores", "outros"]
    por_tema: dict[str, list] = {}
    for it in itens:
        if it["fora"]:
            continue
        por_tema.setdefault(it["temas"][0], []).append(it)
    n_btg = sum(1 for i in itens if i["fonte"] == "BTG"); n_bba = len(itens) - n_btg
    md = [f"# Leitura do dia — material coletado entre {desde} e {ate}", "",
          f"{len(itens)} relatórios novos (BTG {n_btg}, Itaú BBA {n_bba}); {sum(1 for i in itens if i['fora'])} fora do escopo (LatAm ex-Brasil, diários).", ""]
    for tema in ordem:
        lst = por_tema.get(tema)
        if not lst:
            continue
        md.append(f"## {tema}  (gráfico sugerido: {GRAFICO_POR_TEMA.get(tema, 'setor/papel conforme o caso')})"); md.append("")
        for it in lst:
            tag = " ★" if it.get("destaque") else ""
            extra = " · ".join(x for x in [it.get("analista") or "", it.get("rating") or "", ", ".join(it.get("cobertura", [])), ", ".join(it.get("setores", []))] if x)
            md.append(f"- [{it['fonte']}] **{it.get('empresa', '')}** — {it.get('titulo', '')}{tag}  ({extra})  id={it.get('id')}")
            if it["texto"]:
                lim = 4500 if it.get("destaque") else 1500
                md.append(""); md.append("  " + it["texto"][:lim].replace("\n", "\n  ")); md.append("")
        md.append("")
    fora = [it for it in itens if it["fora"]]
    if fora:
        md.append("## fora do escopo (só títulos)"); md.append("")
        md += [f"- [{it['fonte']}] {it.get('empresa', '')} — {it.get('titulo', '')}" for it in fora]
    config.OUTPUT.mkdir(parents=True, exist_ok=True)
    MATERIAL_MD.write_text("\n".join(md), encoding="utf-8")
    MATERIAL_JSON.write_text(json.dumps({"desde": desde, "ate": ate, "itens": [{k: v for k, v in i.items() if k != "texto"} for i in itens]},
                                        ensure_ascii=False, indent=1), encoding="utf-8")
    return {"desde": desde, "ate": ate, "n": len(itens), "btg": n_btg, "bba": n_bba, "fora": len(fora), "temas": {t: len(v) for t, v in por_tema.items()}}


# ----------------------------------------------------------------------------------------------------- gráficos
def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": ["Segoe UI", "DejaVu Sans"], "font.size": 11, "axes.titlesize": 14, "axes.titleweight": "bold",
                         "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "grid.alpha": 0.25,
                         "figure.facecolor": "white", "axes.facecolor": "white", "legend.frameon": False})
    return plt


COR = ["#1f4e79", "#c0504d", "#4f8a3f", "#8064a2", "#e08a1e", "#5a5a5a"]


def _macro() -> dict:
    return json.loads((config.DATA / "macro.json").read_text(encoding="utf-8"))


def _sgs(cod: int) -> list[list]:
    return (_macro().get("sgs", {}).get(str(cod)) or {}).get("serie") or []


def _dt(s: str):
    return datetime.strptime(s[:10], "%Y-%m-%d")


def _fig(titulo: str, fonte: str, h: float = 5.0):
    plt = _plt()
    fig, ax = plt.subplots(figsize=(10, h), dpi=150)
    ax.set_title(titulo, loc="left", pad=12)
    fig.text(0.01, 0.01, f"Fonte: {fonte}. Monitor de Cobertura, {date.today():%d/%m/%Y}.", fontsize=8.5, color="#666")
    return fig, ax


def _ultimo(ax, xs, ys, fmt: str, cor: str, dx: float = 0.0):
    ax.annotate(fmt.format(ys[-1]), (xs[-1], ys[-1]), textcoords="offset points", xytext=(6 + dx, 0), va="center", fontsize=10, color=cor, fontweight="bold")


def _salva(fig, nome: str) -> Path:
    PNG.mkdir(parents=True, exist_ok=True)
    p = PNG / f"{date.today():%Y-%m-%d}_{nome}.png"
    fig.tight_layout(rect=(0, 0.03, 1, 1)); fig.savefig(p); _plt().close(fig)
    return p


def g_fiscal() -> Path:
    b, l = _sgs(13762), _sgs(4513)
    fig, ax = _fig("Dívida pública (% do PIB): bruta do governo geral e líquida do setor público", "BCB, SGS 13762 e 4513 (mensal, desde 2010)")
    for s, nome, c in ((b, "Dívida bruta (DBGG)", COR[0]), (l, "Dívida líquida (DLSP)", COR[1])):
        xs = [_dt(d) for d, _ in s]; ys = [v for _, v in s]
        ax.plot(xs, ys, color=c, lw=1.8, label=nome); _ultimo(ax, xs, ys, "{:.1f}%", c)
    ax.set_ylabel("% do PIB"); ax.legend(loc="upper left")
    return _salva(fig, "fiscal")


def g_inflacao() -> Path:
    m = _sgs(433)
    acc = []
    for i in range(11, len(m)):
        f = 1.0
        for _, v in m[i - 11:i + 1]:
            f *= 1 + v / 100
        acc.append((m[i][0], (f - 1) * 100))
    fig, ax = _fig("IPCA acumulado em 12 meses (%) e meta", "IBGE via BCB SGS 433; meta e banda do CMN")
    xs = [_dt(d) for d, _ in acc]; ys = [v for _, v in acc]
    ax.plot(xs, ys, color=COR[0], lw=1.8, label="IPCA 12 m"); _ultimo(ax, xs, ys, "{:.2f}%", COR[0])
    ax.axhline(3.0, color=COR[2], lw=1, ls="--", label="meta 3%"); ax.axhspan(1.5, 4.5, color=COR[2], alpha=0.06)
    ax.legend(loc="upper left")
    return _salva(fig, "inflacao")


def g_juros() -> Path:
    from fontes import di
    selic = _sgs(432)
    fig, ax = _fig("Selic meta e curva DI (% a.a.): vértices de 1, 5 e 10 anos", "BCB SGS 432; DI1 B3 via MT5 (interpolação flat-forward, contratos líquidos)")
    xs = [_dt(d) for d, _ in selic]; ys = [v for _, v in selic]
    ax.step(xs, ys, where="post", color=COR[5], lw=1.6, label=f"Selic meta {ys[-1]:.2f}")
    for anos, c in ((1, COR[0]), (5, COR[1]), (10, COR[3])):
        try:
            s = di.serie_vertice(anos)
        except Exception:
            s = []
        if s:
            xs = [_dt(d) for d, _ in s]; ys = [v for _, v in s]
            ax.plot(xs, ys, color=c, lw=1.5, label=f"DI {anos} ano{'s' if anos > 1 else ''} {ys[-1]:.2f}")
    ax.set_xlim(left=datetime(2021, 1, 1)); ax.legend(loc="upper left", ncol=4)
    return _salva(fig, "juros")


def g_cambio() -> Path:
    s = [p for p in _sgs(1) if p[0] >= "2015-01-01"]
    fig, ax = _fig("Câmbio R$/US$ (PTAX venda)", "BCB SGS 1 (diária, desde 2015)")
    xs = [_dt(d) for d, _ in s]; ys = [v for _, v in s]
    ax.plot(xs, ys, color=COR[0], lw=1.4); _ultimo(ax, xs, ys, "{:.2f}", COR[0])
    return _salva(fig, "cambio")


def g_fluxo() -> Path:
    F = json.loads((config.DATA / "fluxo_investidores.json").read_text(encoding="utf-8"))
    h = F.get("historico", {}); ds = sorted(h)
    fig, ax = _fig("Fluxo na B3 por tipo de investidor: saldo acumulado (R$ bi) desde o início da série", F.get("historico_fonte", "B3"))
    for tipo, c in (("estrangeiro", COR[0]), ("institucional", COR[1]), ("pessoa física", COR[2]), ("inst. financeira", COR[3])):
        acc, ys = 0.0, []
        for d in ds:
            acc += (h[d].get(tipo) or 0) / 1e3; ys.append(acc)
        xs = [_dt(d) for d in ds]
        ax.plot(xs, ys, color=c, lw=1.6, label=tipo); _ultimo(ax, xs, ys, "{:+.1f}", c)
    ax.axhline(0, color="#999", lw=0.8); ax.legend(loc="upper left", ncol=4)
    return _salva(fig, "fluxo")


def g_volume() -> Path:
    from fontes import b3_volume
    B = b3_volume.serie()
    xs = [_dt(d) for d, _ in B]; ys = [r["total"] / 1e9 for _, r in B]
    mm = [sum(ys[max(0, i - 20):i + 1]) / len(ys[max(0, i - 20):i + 1]) for i in range(len(ys))]
    fig, ax = _fig("Volume financeiro diário de toda a B3 à vista (R$ bi) e média de 21 pregões", "B3 COTAHIST (todos os registros à vista), desde 2019")
    ax.plot(xs, ys, color="#bbb", lw=0.8, label="diário"); ax.plot(xs, mm, color=COR[0], lw=1.8, label="média 21 pregões")
    _ultimo(ax, xs, mm, "{:.1f}", COR[0]); ax.legend(loc="upper left")
    return _salva(fig, "volume")


def g_ibov() -> Path:
    h = _macro().get("hist", {})
    fig, ax = _fig("Ibovespa e S&P 500, base 100 em jan/2019", "Yahoo Finance (^BVSP, ^GSPC)")
    for nome, c in (("Ibovespa", COR[0]), ("S&P 500", COR[5])):
        s = [p for p in h.get(nome, []) if p[0] >= "2019-01-01" and p[1]]
        if not s:
            continue
        b = s[0][1]; xs = [_dt(d) for d, _ in s]; ys = [v / b * 100 for _, v in s]
        ax.plot(xs, ys, color=c, lw=1.4, label=nome); _ultimo(ax, xs, ys, "{:.0f}", c)
    ax.legend(loc="upper left")
    return _salva(fig, "ibov")


def g_credito() -> Path:
    s = _sgs(20539)
    yoy = [(s[i][0], (s[i][1] / s[i - 12][1] - 1) * 100) for i in range(12, len(s)) if s[i - 12][1]]
    fig, ax = _fig("Crédito: saldo total do SFN, variação em 12 meses (% nominal)", "BCB SGS 20539 (mensal)")
    xs = [_dt(d) for d, _ in yoy]; ys = [v for _, v in yoy]
    ax.plot(xs, ys, color=COR[0], lw=1.8); _ultimo(ax, xs, ys, "{:.1f}%", COR[0]); ax.axhline(0, color="#999", lw=0.8)
    return _salva(fig, "credito")


def g_atividade() -> Path:
    s = _sgs(24364)
    fig, ax = _fig("IBC-Br dessazonalizado (índice) e variação em 12 meses", "BCB SGS 24364 (mensal)")
    xs = [_dt(d) for d, _ in s]; ys = [v for _, v in s]
    ax.plot(xs, ys, color=COR[0], lw=1.8, label="IBC-Br (esq.)"); _ultimo(ax, xs, ys, "{:.1f}", COR[0])
    ax2 = ax.twinx(); ax2.grid(False); ax2.spines["top"].set_visible(False)
    yoy = [(s[i][0], (s[i][1] / s[i - 12][1] - 1) * 100) for i in range(12, len(s))]
    ax2.bar([_dt(d) for d, _ in yoy], [v for _, v in yoy], width=20, color=COR[2], alpha=0.35, label="var. 12 m (dir., %)")
    ax.legend(loc="upper left"); ax2.legend(loc="upper right")
    return _salva(fig, "atividade")


def g_emprego() -> Path:
    s = _sgs(28763)
    dif = [(s[i][0], (s[i][1] - s[i - 1][1]) / 1e3) for i in range(1, len(s))]
    fig, ax = _fig("CAGED: saldo mensal de empregos formais (mil) e média de 3 meses", "Ministério do Trabalho via BCB SGS 28763")
    xs = [_dt(d) for d, _ in dif]; ys = [v for _, v in dif]
    ax.bar(xs, ys, width=20, color=[COR[2] if v >= 0 else COR[1] for v in ys]); _ultimo(ax, xs, ys, "{:+.0f} mil", COR[0])
    mm = [sum(ys[max(0, i - 2):i + 1]) / len(ys[max(0, i - 2):i + 1]) for i in range(len(ys))]
    ax.plot(xs, mm, color=COR[0], lw=1.6, label="média 3 m"); ax.legend(loc="upper left")
    return _salva(fig, "emprego")


def g_commodities() -> Path:
    h = _macro().get("hist", {})
    s = [p for p in h.get("Brent (US$)", []) if p[1]]
    fig, ax = _fig("Brent (US$/barril)", "Yahoo Finance (BZ=F)")
    xs = [_dt(d) for d, _ in s]; ys = [v for _, v in s]
    ax.plot(xs, ys, color=COR[0], lw=1.3); _ultimo(ax, xs, ys, "{:.0f}", COR[0])
    return _salva(fig, "commodities")


def _comp() -> list[dict]:
    return json.loads((config.DATA / "ibov_comp.json").read_text(encoding="utf-8")).get("itens", [])


def g_setor(setor: str) -> Path:
    from fontes import b3
    cods = [i["cod"] for i in _comp() if i.get("setor") == setor]
    h = {d: v for d, v in _macro().get("hist", {}).get("Ibovespa", []) if v}
    series = {c: dict(b3.serie(c)) for c in cods}
    datas = sorted(d for d in h if d >= "2019-01-01")
    idx, base = [], None
    for d in datas:
        vals = [series[c][d] / series[c][min(series[c])] for c in cods if d in series[c] and series[c].get(min(series[c]))]
        if len(vals) >= max(2, len(cods) // 2):
            v = sum(vals) / len(vals)
            base = base or v; idx.append((d, v / base * 100))
    fig, ax = _fig(f"Setor {setor} (índice equiponderado dos papéis do Ibovespa) vs. Ibovespa, base 100", "B3 COTAHIST (fechamentos), Yahoo (^BVSP)")
    xs = [_dt(d) for d, _ in idx]; ys = [v for _, v in idx]
    ax.plot(xs, ys, color=COR[0], lw=1.7, label=f"{setor} ({len(cods)} papéis)"); _ultimo(ax, xs, ys, "{:.0f}", COR[0])
    b = h[idx[0][0]]; ys2 = [h[d] / b * 100 for d, _ in idx]
    ax.plot(xs, ys2, color=COR[5], lw=1.2, label="Ibovespa"); _ultimo(ax, xs, ys2, "{:.0f}", COR[5])
    ax.legend(loc="upper left")
    return _salva(fig, "setor_" + re.sub(r"\W+", "_", setor.lower()))


def _ultimo_csv(arq: Path, ticker: str, campo: str = "target") -> float | None:
    if not arq.exists():
        return None
    val = None
    with arq.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("ticker") == ticker and row.get(campo):
                try:
                    val = float(row[campo])
                except ValueError:
                    pass
    return val


def g_papel(ticker: str) -> Path:
    from fontes import b3
    s = [p for p in b3.serie(ticker) if p[0] >= "2019-01-01"]
    fig, ax = _fig(f"{ticker}: fechamento (R$), preço-alvo do consenso e minha estimativa", "B3 COTAHIST (fechamento sem ajuste por proventos/desdobramentos); consenso (Bloomberg/Yahoo); estimativas/minhas.csv")
    xs = [_dt(d) for d, _ in s]; ys = [v for _, v in s]
    ax.plot(xs, ys, color=COR[0], lw=1.4, label="fechamento"); _ultimo(ax, xs, ys, "R$ {:.2f}", COR[0])
    for arq, nome, c in ((config.CONSENSO / "consenso.csv", "alvo consenso", COR[1]), (config.ESTIMATIVAS / "minhas.csv", "meu alvo", COR[2])):
        t = _ultimo_csv(arq, ticker)
        if t:
            ax.axhline(t, color=c, lw=1.2, ls="--", label=f"{nome} R$ {t:.2f}")
    ax.legend(loc="upper left")
    return _salva(fig, "papel_" + ticker.lower())


def g_papeis(tickers: list[str]) -> Path:
    from fontes import b3
    fig, ax = _fig("Papéis, base 100 em jan/2019", "B3 COTAHIST (fechamento sem ajuste por proventos/desdobramentos: saltos = eventos societários)")
    for tk, c in zip(tickers[:6], COR):
        s = [p for p in b3.serie(tk) if p[0] >= "2019-01-01"]
        if not s:
            continue
        xs = [_dt(d) for d, _ in s]; ys = [v / s[0][1] * 100 for _, v in s]
        ax.plot(xs, ys, color=c, lw=1.4, label=tk); _ultimo(ax, xs, ys, "{:.0f}", c)
    ax.legend(loc="upper left", ncol=3)
    return _salva(fig, "papeis_" + "_".join(t.lower() for t in tickers[:6]))


def grafico(spec) -> Path | None:
    """Renderiza o PNG de um bloco; spec = nome ou dict (ver docstring do módulo)."""
    if not spec:
        return None
    if isinstance(spec, str):
        fn = globals().get("g_" + spec)
        return fn() if fn else None
    t = spec.get("tipo")
    if t == "setor":
        return g_setor(spec["setor"])
    if t == "papel":
        return g_papel(spec["ticker"])
    if t == "papeis":
        return g_papeis(spec["tickers"])
    if t == "png":
        return Path(spec["arquivo"])
    return None


# ----------------------------------------------------------------------------------------------------- envio
def _html(digest: dict, pngs: dict) -> str:
    def img(p):
        if not p or not Path(p).exists():
            return ""
        b64 = base64.b64encode(Path(p).read_bytes()).decode()
        return f'<img src="data:image/png;base64,{b64}" style="width:100%;max-width:1000px;display:block;margin:8px 0 14px">'
    blocos = "".join(f'<section><h2>{b["titulo"]}</h2><p>{b["texto"].replace(chr(10), "<br>")}</p>{img(pngs.get(i))}</section>'
                     for i, b in enumerate(digest["blocos"]))
    return (f'<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><title>Leitura do dia {digest["data"]}</title>'
            '<style>body{font-family:"Segoe UI",system-ui,sans-serif;max-width:1040px;margin:24px auto;padding:0 16px;color:#222;line-height:1.45}'
            'h1{font-size:22px}h2{font-size:17px;margin:26px 0 6px}p{white-space:normal}section{border-top:1px solid #e5e5e5;padding-top:8px}</style></head>'
            f'<body><h1>Leitura do dia · {digest["data"]}</h1><p>{digest.get("cabecalho", "").replace(chr(10), "<br>")}</p>{blocos}'
            '<p style="color:#777;font-size:12px">Uso privado. Resumos próprios a partir dos relatórios do BTG Research e do Itaú BBA; gráficos de dados primários.</p></body></html>')


def enviar(arq: str, sem_telegram: bool = False, sem_push: bool = False) -> dict:
    digest = json.loads(Path(arq).read_text(encoding="utf-8"))
    digest.setdefault("data", date.today().isoformat())
    digest.setdefault("edicao", "manhã" if datetime.now().hour < 13 else "noite")   # duas edições por dia, arquivos separados
    nome = f"{digest['data']}-{'manha' if digest['edicao'].startswith('manh') else 'noite'}"
    pngs, enviados, falhas = {}, 0, []
    cab = f"<b>Leitura da {digest['edicao']} · {digest['data'][8:]}/{digest['data'][5:7]}</b>\n{digest.get('cabecalho', '')}".strip()
    if not sem_telegram and not telegram.enviar(cab):
        falhas.append("cabeçalho")
    for i, b in enumerate(digest["blocos"]):
        try:
            p = grafico(b.get("grafico"))
        except Exception as e:
            p = None; falhas.append(f"gráfico {b.get('titulo')}: {str(e)[:80]}")
        pngs[i] = str(p) if p else None
        legenda = f"<b>{b['titulo']}</b>\n{b['texto']}"
        if sem_telegram:
            continue
        ok = telegram.enviar_foto(p, legenda, silencioso=True) if p else telegram.enviar(legenda, silencioso=True)
        enviados += bool(ok)
        if not ok:
            falhas.append(b["titulo"])
    LEIT.mkdir(parents=True, exist_ok=True)
    (LEIT / f"{nome}.html").write_text(_html(digest, pngs), encoding="utf-8")
    (LEIT / f"{nome}.json").write_text(json.dumps(digest, ensure_ascii=False, indent=1), encoding="utf-8")
    ESTADO.write_text(json.dumps({"ultima": datetime.now().strftime("%Y-%m-%d %H:%M"), "data": digest["data"]}), encoding="utf-8")
    push = "pulado"
    if not sem_push:
        def git(*a):
            return subprocess.run(["git", *a], cwd=str(RES), capture_output=True, text=True, timeout=180)
        git("add", "leituras")
        git("commit", "-q", "-m", f"leitura da {digest['edicao']} {digest['data']}")
        git("pull", "--rebase", "-q", "origin", "main")
        r = git("push", "-q", "origin", "main")
        push = "ok" if r.returncode == 0 else "falhou: " + r.stderr.strip()[:120]
    return {"blocos": len(digest["blocos"]), "enviados": enviados, "falhas": falhas, "html": str(LEIT / f"{nome}.html"), "push": push}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    a = sys.argv[1:]
    if "--montar" in a:
        i = a.index("--montar")
        desde = a[i + 1] if len(a) > i + 1 and not a[i + 1].startswith("--") else None
        r = montar(desde)
        print(json.dumps(r, ensure_ascii=False)); print("material:", MATERIAL_MD)
    elif "--enviar" in a:
        r = enviar(a[a.index("--enviar") + 1], sem_telegram="--sem-telegram" in a, sem_push="--sem-push" in a)
        print(json.dumps(r, ensure_ascii=False))
    elif "--grafico" in a:
        nome = a[a.index("--grafico") + 1]
        spec = json.loads(nome) if nome.startswith("{") else nome
        print(grafico(spec))
    else:
        print(__doc__)
