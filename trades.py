"""Trades executados (trades.json): marcação a mercado diária e intraday, por trade e do book.

Preço: vigia (data/alertas_tempo_real.json, MT5 no pregão) > fechamento COTAHIST. Resultado por trade = qtd × (preço − PM),
com sinal invertido no vendido; retorno = resultado ÷ |qtd × PM|. Book: soma dos resultados ÷ soma dos notionais de entrada
(bruto), com CDI e Ibovespa no mesmo intervalo como referência. Trade encerrado ("saida") congela no preço de saída.
"""
from __future__ import annotations

import json
from datetime import date

import config

ARQ = config.RAIZ / "trades.json"


def carrega() -> list[dict]:
    if not ARQ.exists():
        return []
    T = json.loads(ARQ.read_text(encoding="utf-8")).get("trades", [])
    for t in T:
        t["sinal"] = -1 if t.get("lado") == "vendido" else 1
        t["notional"] = abs(t["qtd"] * t["preco"])
    return T


def lotes(t: dict) -> list[dict]:
    """Lotes da posição (data, qtd, preço). Sem 'lotes' no trades.json, a entrada única (data, qtd, preco)."""
    return [dict(l) for l in t.get("lotes") or []] or [{"data": t["data"], "qtd": t["qtd"], "preco": t["preco"]}]


def posicao(t: dict, d: str) -> tuple[float, float | None]:
    """(quantidade, preço médio) no fechamento do dia d: só os lotes com data ≤ d (antes: EZTC3/B3SA3/CYRE3/XPBR31 eram
    marcados com a quantidade cheia desde o 1º lote, ao PM final)."""
    ls = [l for l in lotes(t) if l["data"] <= d]
    q = sum(l["qtd"] for l in ls)
    return q, (sum(l["qtd"] * l["preco"] for l in ls) / q if q else None)


def pnl_dia(t: dict, d: str, px: float, px_ant: float | None) -> tuple[float, float]:
    """(resultado do dia, base) em d: lote antigo anda do fechamento anterior; lote de hoje anda do preço de entrada.
    base = valor das posições no início do dia (para o retorno encadeado do book)."""
    pnl = base = 0.0
    for l in lotes(t):
        if l["data"] > d:
            continue
        p0 = l["preco"] if (l["data"] == d or px_ant is None) else px_ant
        pnl += t["sinal"] * l["qtd"] * (px - p0)
        base += l["qtd"] * p0
    return pnl, base


def tickers() -> set[str]:
    return {t["ticker"] for t in carrega()}


def _tempo_real(tk: str, hoje: str) -> dict | None:
    """Último preço do vigia (só se for de hoje): {preco, hora, fech_ant}."""
    p = config.DATA / "alertas_tempo_real.json"
    if not p.exists():
        return None
    q = (json.loads(p.read_text(encoding="utf-8")).get("papeis") or {}).get(tk) or {}
    if not q.get("preco") or (q.get("hora") or "")[:10] != hoje:
        return None
    r1 = q.get("r1")
    return {"preco": q["preco"], "hora": q["hora"], "fech_ant": q["preco"] / (1 + r1) if r1 is not None and r1 > -1 else None}


def _fx(hoje: str) -> float | None:
    """Dólar de tela: WDO$ do vigia (intraday_hoje.json, pontos/1000) ou o último USD/BRL do macro.json."""
    try:
        J = json.loads((config.DATA / "intraday_hoje.json").read_text(encoding="utf-8"))
        b = (J.get("series") or {}).get("WDO$") or []
        if b and J.get("data") == hoje:
            return float(b[-1][1]) / 1000
    except Exception:
        pass
    try:
        M = json.loads((config.DATA / "macro.json").read_text(encoding="utf-8"))
        return float(M["mercado"]["USD/BRL"]["preco"])
    except Exception:
        return None


def multiplo(m: dict, preco: float, hoje: str, fx: float | None) -> dict | None:
    """Múltiplo 12 meses à frente com o preço de tela: lucro/EBITDA 2026 e 2027 interpolados por dias corridos até 31/12
    (07/10: 23% de 2026, 77% de 2027), EPS = lucro ÷ ações, BDR = EPS × câmbio ÷ razão. pb = preço ÷ (PL ÷ ações).
    Devolve {rotulo, valor, detalhe} ou None."""
    if not m or preco is None:
        return None
    ano = int(hoje[:4])
    w = max(0.0, min(1.0, ((date(ano, 12, 31) - date.fromisoformat(hoje)).days) / 365))
    conv = ((fx or 1.0) if m.get("moeda") == "USD" else 1.0) / (m.get("razao_bdr") or 1)
    if m["tipo"] == "pe":
        e26, e27 = m["lucro26"] / m["acoes"] * conv, m["lucro27"] / m["acoes"] * conv
        e12 = w * e26 + (1 - w) * e27
        return {"rotulo": "P/E 12m", "valor": preco / e12 if e12 else None, "detalhe": f"EPS 12m {e12:.2f} (26: {e26:.2f}, 27: {e27:.2f})"}
    if m["tipo"] == "ev_ebitda":
        eb = w * m["ebitda26"] + (1 - w) * m["ebitda27"]
        dl = w * m["div_liq26"] + (1 - w) * m["div_liq27"]
        ev = preco * m["acoes"] * conv + dl
        return {"rotulo": "EV/EBITDA 12m", "valor": ev / eb if eb else None, "detalhe": f"EV R$ {ev / 1000:.1f} bi (DL {dl / 1000:.1f}) ÷ EBITDA 12m {eb / 1000:.2f} bi"}
    if m["tipo"] == "pb":
        bv = m["pl"] / m["acoes"]
        return {"rotulo": "P/B", "valor": preco / bv if bv else None, "detalhe": f"PL/ação {bv:.2f}"}
    return None


def marca(hoje: str | None = None, ibov_hist: list | None = None) -> dict:
    """Marcação de cada trade + curvas do book. Devolve {"trades": [...], "book": {...}, "curva": {...}, "hora": str}."""
    from fontes import b3
    hoje = hoje or date.today().isoformat()
    T = carrega()
    out, horas = [], []
    series = {}
    fx = _fx(hoje)
    for t in T:
        s = [(d, v) for d, v in b3.serie(t["ticker"]) if d >= "2026-01-01"]
        series[t["ticker"]] = s
        q = _tempo_real(t["ticker"], hoje) if not t.get("saida") else None
        if t.get("saida"):
            preco, hora = t["saida"]["preco"], t["saida"]["data"]
        elif q:
            preco, hora = q["preco"], q["hora"]
            horas.append(q["hora"])
        else:
            preco, hora = (s[-1][1], s[-1][0]) if s else (None, "")
        fech_ant = next((v for d, v in reversed(s) if d < (hora[:10] or hoje)), None)
        if q and q.get("fech_ant"):
            fech_ant = q["fech_ant"]
        if preco is None:
            out.append({**t, "preco_atual": None})
            continue
        res = t["sinal"] * t["qtd"] * (preco - t["preco"])
        d_px = hora[:10] or hoje
        res_dia, base_d = pnl_dia(t, d_px, preco, fech_ant)        # por lote: lote de hoje anda do preço de entrada
        dia = res_dia / base_d if base_d else None
        if t.get("saida") and t["saida"]["data"] < hoje:
            dia, res_dia, base_d = None, 0.0, 0.0             # encerrado: não mexe mais
        out.append({**t, "preco_atual": preco, "hora": hora, "resultado": res, "ret": res / t["notional"], "dia": dia,
                    "res_dia": res_dia, "base_dia": base_d, "saldo": t["sinal"] * t["qtd"] * preco, "mult": multiplo(t.get("mult"), preco, hoje, fx)})
    ab = [x for x in out if x.get("preco_atual") is not None]
    # book em aberto (bruto, resultado, peso) só com as posições vivas; o que foi zerado vira "realizado"
    vivos = [x for x in ab if not x.get("saida")]
    enc = [x for x in ab if x.get("saida")]
    bruto = sum(x["notional"] for x in vivos)
    comprado = sum(x["notional"] for x in vivos if x["sinal"] > 0)
    vendido = sum(x["notional"] for x in vivos if x["sinal"] < 0)
    res_tot = sum(x["resultado"] for x in vivos)
    book = {"bruto": bruto, "comprado": comprado, "vendido": vendido, "liquido": comprado - vendido,
            "resultado": res_tot, "ret": res_tot / bruto if bruto else None,
            "res_dia": sum((x.get("res_dia") or 0) for x in ab), "base_dia": sum((x.get("base_dia") or 0) for x in ab), "fx": fx,
            "res_comprado": sum(x["resultado"] for x in vivos if x["sinal"] > 0),
            "res_vendido": sum(x["resultado"] for x in vivos if x["sinal"] < 0),
            "realizado": sum(x["resultado"] for x in enc), "notional_encerrado": sum(x["notional"] for x in enc),
            "ult_saida": max((x["saida"]["data"] for x in enc), default=""),
            "n": len(ab), "n_abertos": len(vivos), "n_encerrados": len(enc)}
    curva_out: dict = {}
    if ab:
        d0 = min(x["data"] for x in ab)
        datas = sorted({d for s in series.values() for d, _ in s if d >= d0})
        if hoje not in datas and any(x.get("hora", "")[:10] == hoje for x in ab):
            datas.append(hoje)
        # book encadeado (time-weighted): retorno do dia = P&L do dia ÷ valor das posições na véspera (PM no dia da entrada);
        # assim um trade novo entra em zero sem diluir o acumulado dos antigos
        curva, cur_tr = [], {x["ticker"]: [] for x in ab}
        acc, prev = 1.0, {}
        for d in datas:
            pnl, base = 0.0, 0.0
            for x in ab:
                if x["data"] > d or (x.get("saida") and x["saida"]["data"] < d):
                    continue
                if d == hoje and x.get("hora", "")[:10] == hoje:
                    px = x["preco_atual"]
                elif x.get("saida") and x["saida"]["data"] == d:
                    px = x["saida"]["preco"]
                else:
                    px = next((v for dd, v in reversed(series[x["ticker"]]) if dd <= d), None)
                if px is None:
                    continue
                p_d, b_d = pnl_dia(x, d, px, prev.get(x["ticker"]))
                pnl += p_d
                base += b_d
                prev[x["ticker"]] = px
                q, pm = posicao(x, d)
                if pm:
                    cur_tr[x["ticker"]].append([d, x["sinal"] * (px / pm - 1) * 100])
            if base:
                acc *= 1 + pnl / base
                curva.append([d, (acc - 1) * 100])
        # referências no mesmo intervalo: CDI acumulado (última taxa carregada até hoje) e Ibovespa (base = fechamento anterior a d0)
        cdi: list = []
        p = config.DATA / "cdi_diario.json"
        if p.exists():
            C = json.loads(p.read_text(encoding="utf-8"))
            acc = 1.0
            for d, v in C:
                if d >= d0:
                    acc *= 1 + v / 100
                    cdi.append([d, (acc - 1) * 100])
            ult = cdi[-1][0] if cdi else (C[-1][0] if C else None)
            if C and ult and ult < datas[-1]:
                for d in datas:
                    if d > ult:
                        acc *= 1 + C[-1][1] / 100
                        cdi.append([d, (acc - 1) * 100])
        ibov: list = []
        pm = config.DATA / "macro.json"
        if pm.exists():
            H = ibov_hist or json.loads(pm.read_text(encoding="utf-8")).get("hist", {}).get("Ibovespa", [])
            base = next((v for d, v in reversed(H) if d < d0), None)
            if base:
                ibov = [[d, (v / base - 1) * 100] for d, v in H if d >= d0]
        curva_out = {"book": curva, "cdi": cdi, "ibov": ibov, "trades": cur_tr, "d0": d0}
    R = {"trades": out, "book": book, "curva": curva_out, "hora": max(horas) if horas else ""}
    _registra(R, hoje)
    return R


def historico_fechamentos(fx_de=None) -> dict:
    """Histórico diário do book só com FECHAMENTOS OFICIAIS (COTAHIST), um registro por pregão, lote a lote. Corrige o
    histórico antigo (até 22/10/2026), que gravava o último preço do vigia (~17:30, antes do leilão: diferenças de até 1,7%),
    criava registros em fim de semana/feriado pela data do relógio e marcava lotes futuros desde o 1º lote."""
    from fontes import b3
    T = carrega()
    if not T:
        return {}
    S = {t["ticker"]: dict(b3.serie(t["ticker"])) for t in T}
    d0 = min(l["data"] for t in T for l in lotes(t))
    dias = sorted({d for s in S.values() for d in s if d >= d0})
    H, prev = {}, {}
    for d in dias:
        linhas = {}
        for t in T:
            if min(l["data"] for l in lotes(t)) > d or (t.get("saida") and t["saida"]["data"] < d):
                continue
            px = t["saida"]["preco"] if (t.get("saida") and t["saida"]["data"] == d) else S[t["ticker"]].get(d)
            if px is None:
                continue
            q, pm = posicao(t, d)
            pd_, _ = pnl_dia(t, d, px, prev.get(t["ticker"]))
            prev[t["ticker"]] = px
            res = t["sinal"] * q * (px - pm)
            m = multiplo(t.get("mult"), px, d, fx_de(d) if fx_de else None)
            linhas[t["ticker"]] = {"lado": t["lado"], "qtd": q, "pm": round(pm, 4), "preco": px, "resultado": round(res, 2),
                                   "ret": round(res / (q * pm), 5), "res_dia": round(pd_, 2),
                                   "mult": ({"rotulo": m["rotulo"], "valor": round(m["valor"], 3)} if m and m.get("valor") else None)}
        if not linhas:
            continue
        com = sum(x["qtd"] * x["pm"] for x in linhas.values() if x["lado"] == "comprado")
        ven = sum(x["qtd"] * x["pm"] for x in linhas.values() if x["lado"] == "vendido")
        rt = sum(x["resultado"] for x in linhas.values())
        H[d] = {"hora": d, "fonte": "fechamento B3",
                "book": {"bruto": round(com + ven, 2), "comprado": round(com, 2), "vendido": round(ven, 2), "liquido": round(com - ven, 2),
                         "resultado": round(rt, 2), "ret": round(rt / (com + ven), 5) if com + ven else None,
                         "res_dia": round(sum(x["res_dia"] for x in linhas.values()), 2),
                         "res_comprado": round(sum(x["resultado"] for x in linhas.values() if x["lado"] == "comprado"), 2),
                         "res_vendido": round(sum(x["resultado"] for x in linhas.values() if x["lado"] == "vendido"), 2),
                         "n": len(linhas)},
                "trades": linhas}
    return H


def _registra(R: dict, hoje: str) -> None:
    """Histórico das marcações (data/trades_diario.json). Pregões com fechamento oficial: reconstruídos do COTAHIST
    (historico_fechamentos). Pregão em andamento (preço do vigia mais novo que o último fechamento): uma entrada intraday
    com a DATA DO PREÇO, que o fechamento oficial substitui quando sai. Nunca grava fim de semana/feriado."""
    try:
        from fontes import lucros_casas as _lc
        p = config.DATA / "trades_diario.json"
        H = historico_fechamentos(_lc.dolar)
        ult = max(H) if H else ""
        hs = [x.get("hora", "") for x in R["trades"] if x.get("preco_atual") is not None and not x.get("saida")]
        hora = max(hs) if hs else ""
        d = hora[:10]
        if not d or d <= ult or len(hora) < 16:          # sem preço intraday mais novo que o último fechamento oficial
            p.write_text(json.dumps(dict(sorted(H.items())), ensure_ascii=False, indent=1), encoding="utf-8")
            return
        H[d] = {"fonte": "intraday (vigia)", "hora": hora, "book": {k: round(v, 2) if isinstance(v, float) else v for k, v in R["book"].items()},
                "trades": {x["ticker"]: {"lado": x["lado"], "qtd": x["qtd"], "pm": x["preco"], "preco": x.get("preco_atual"),
                                         "resultado": round(x["resultado"], 2) if x.get("resultado") is not None else None,
                                         "ret": round(x["ret"], 5) if x.get("ret") is not None else None,
                                         "res_dia": round(x["res_dia"], 2) if x.get("res_dia") is not None else None,
                                         "mult": ({"rotulo": x["mult"]["rotulo"], "valor": round(x["mult"]["valor"], 3)} if x.get("mult") and x["mult"].get("valor") else None)}
                           for x in R["trades"] if x.get("preco_atual") is not None}}
        p.write_text(json.dumps(dict(sorted(H.items())), ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        print(f"trades_diario: {e}")


def slide_trades(M: dict | None = None) -> tuple[str, str] | None:
    import build as B
    R = marca(ibov_hist=((M or {}).get("hist") or {}).get("Ibovespa"))
    T = R["trades"]
    if not T:
        return None
    bk, cv = R["book"], R["curva"]
    S1, S2, MUT = "var(--s1)", "var(--s2)", "var(--axis)"

    def sinal(v, dec=0, suf=""):
        return B.num(v, dec, "+" if (v or 0) > 0 else "", suf)

    d0 = cv.get("d0", "")
    us = bk.get("ult_saida") or ""
    tiles = "".join(f'<div class="tile"><div class="l">{l}</div><div class="v {c}">{v}</div><div class="d">{d}</div></div>' for l, v, c, d in (
        ("Realizado · encerrados", f'R$ {sinal(bk["realizado"])}', B.dlt_cls(bk["realizado"]),
         f'{bk["n_encerrados"]} trades zerados{" (último " + us[8:10] + "/" + us[5:7] + ")" if us else ""} · {sinal(bk["realizado"] / bk["notional_encerrado"] * 100 if bk.get("notional_encerrado") else None, 1, "%")} do notional'),
        ("Resultado em aberto", f'R$ {sinal(bk["resultado"])}', B.dlt_cls(bk["resultado"]),
         f'{sinal(bk["ret"] * 100 if bk["ret"] is not None else None, 2, "%")} do bruto · {bk["n_abertos"]} trades abertos'),
        ("Resultado do dia", f'R$ {sinal(bk["res_dia"])}', B.dlt_cls(bk["res_dia"]),
         f'{sinal(bk["res_dia"] / bk["base_dia"] * 100 if bk.get("base_dia") else None, 2, "%")} das posições do dia (inclui as zeradas hoje) · contra o fechamento anterior'),
        ("Comprado", f'R$ {B.num(bk["comprado"] / 1000, 1)} mil', "", f'resultado R$ {sinal(bk["res_comprado"])}'),
        ("Vendido", f'R$ {B.num(bk["vendido"] / 1000, 1)} mil', "", f'resultado R$ {sinal(bk["res_vendido"])} · líquido R$ {sinal(bk["liquido"] / 1000, 1)} mil'),
        (f"Referência desde {d0[8:10]}/{d0[5:7]}" if d0 else "Referência",
         f'{sinal(cv["cdi"][-1][1], 2, "%") if cv.get("cdi") else "—"} CDI', "",
         f'Ibovespa {sinal(cv["ibov"][-1][1], 1, "%") if cv.get("ibov") else "—"}')))
    rows = []
    for x in sorted([x for x in T if not x.get("saida")], key=lambda x: -(x.get("resultado") or 0)):   # só o que está aberto
        if x.get("preco_atual") is None:
            rows.append(f'<tr><td class="tk">{x["ticker"]}<small>{x.get("nome", "")}</small></td><td colspan="11" class="mut">sem preço</td></tr>')
            continue
        lado = f'<span class="{"up" if x["sinal"] > 0 else "dn"}">{x["lado"]}</span>'
        dt = x["data"][8:10] + "/" + x["data"][5:7] + ("<small> est.</small>" if x.get("data_estimada") else "")
        enc = f'<small>encerrado {x["saida"]["data"][8:10]}/{x["saida"]["data"][5:7]}</small>' if x.get("saida") else ""
        rows.append(f'<tr><td class="tk">{x["ticker"]}<small>{x.get("nome", "")}</small></td><td>{lado}{enc}</td><td>{dt}</td><td>{B.num(x["qtd"], 0)}</td>'
                    f'<td>{B.num(x["preco"], 2)}</td><td>{B.num(x["preco_atual"], 2)}<small>{x.get("hora", "")[11:16] or (x.get("hora", "")[8:10] + "/" + x.get("hora", "")[5:7] + " fech." if x.get("hora") else "sem preço")}</small></td>'
                    f'<td class="{B.dlt_cls(x.get("dia"))}">{B.pct(x.get("dia"), 2)}<small>{sinal(x.get("res_dia")) if x.get("res_dia") is not None else ""}</small></td>'
                    f'<td class="{B.dlt_cls(x["ret"])}">{B.pct(x["ret"], 2)}</td>'
                    f'<td class="{B.dlt_cls(x["resultado"])}">{sinal(x["resultado"])}</td>'
                    f'<td>{B.num(x["notional"] / bk["bruto"] * 100 if bk["bruto"] else None, 1, suf="%")}</td>'
                    f'<td title="{(x.get("mult") or {}).get("detalhe", "")}">{B.num((x.get("mult") or {}).get("valor"), 2 if "P/B" in (x.get("mult") or {}).get("rotulo", "") else 1, suf="x") if x.get("mult") else "—"}<small>{(x.get("mult") or {}).get("rotulo", "")}</small></td></tr>')
    tab = (f'<table class="mini"><thead><tr><th>Papel</th><th>Lado</th><th>Entrada</th><th>Qtd</th><th>PM</th><th>Preço</th><th>Dia · R$</th>'
           f'<th>Desde a entrada</th><th>R$</th><th>Peso</th><th>Múltiplo 12m</th></tr></thead><tbody>{"".join(rows)}</tbody></table>')
    g1 = g2 = g3 = ""
    # ganho do dia por papel (R$), a mercado: trades abertos com resultado do dia, do mais forte ao mais fraco
    dia = sorted([x for x in T if not x.get("saida") and x.get("res_dia") is not None], key=lambda x: -(x.get("res_dia") or 0))
    if dia:
        g3 = B.svg_colunas("Ganho do dia por papel (R$) · a mercado, contra o fechamento anterior",
                           [x["ticker"] for x in dia], [round(x["res_dia"], 0) for x in dia],
                           ["var(--s1)" if (x["res_dia"] or 0) >= 0 else "var(--dn)" for x in dia],
                           dec=0, W=560, H=240)
    if cv.get("book"):
        g1 = B.svg_linhas("trades-book", [("Book", S1, cv["book"]), ("CDI", MUT, cv["cdi"]), ("Ibovespa", S2, cv["ibov"])], 2, suf="%",
                          W=560, H=240, refs=[("zero", 0.0)], titulo="Book acumulado, retornos diários encadeados (%)", livre=True)
        cores = [S1, S2, "var(--s3)", "var(--s4)", "var(--dn)", MUT]
        vivos = {x["ticker"] for x in T if not x.get("saida")}
        ser = [(tk, cores[i % len(cores)], pts) for i, (tk, pts) in enumerate((tk, p) for tk, p in cv["trades"].items() if tk in vivos) if pts]
        g2 = B.svg_linhas("trades-cada", ser, 1, suf="%", W=1140, H=240, refs=[("zero", 0.0)],
                          titulo="Cada trade desde a entrada (%, vendido com sinal invertido)", livre=True)
    corpo = (f'<div class="tiles strip" style="grid-template-columns:repeat(6,1fr);margin-bottom:10px">{tiles}</div>'
             f'<div class="grid2"><div>{g1}</div><div>{g3}</div></div>'
             f'<div style="margin-top:10px">{g2}</div><div class="panel" style="margin-top:10px">{tab}</div>')
    hora = R["hora"]
    return B.slide("Trades", "trades", "Trades executados · book", corpo,
                   f'Posições reais da corretora marcadas a mercado{" às " + hora[11:16] + " (vigia)" if hora else " no fechamento"}: '
                   "resultado por trade, book comprado × vendido, e o book contra CDI e Ibovespa no mesmo intervalo.",
                   "Arquivo manual trades.json (preço médio e quantidade da corretora). Preço: vigia (MT5) no pregão, COTAHIST no fechamento. "
                   "Retorno por trade = resultado ÷ notional de entrada; book = retornos diários encadeados sobre o valor das posições (trade novo entra em zero). Vendido com sinal invertido. 'est.' = data de entrada inferida pelo fechamento. "
                   "Múltiplos 12 meses: lucro/EBITDA 2026 e 2027 das casas (BTG Stock Guide) interpolados por dias até 31/12, com o preço de tela e o dólar do vigia; "
                   "P/B com o PL do último ITR. Sem custos, proventos ou aluguel.")
