"""Fonte primária B3: preço de fechamento (COTAHIST) e número de ações (canal de listadas).

COTAHIST: arquivo anual, layout posicional 245 chars. PREULT = pos 109 (13 chars, 2 decimais).
Cache por ano em data/cotahist/{ano}.csv com TODOS os tickers do universo; anos passados
não são rebaixados, só o ano corrente.
"""
from __future__ import annotations

import base64
import csv
import io
import json
import sys
import urllib.request
import zipfile
from datetime import date

import config

URL_COTAHIST = "https://bvmf.bmfbovespa.com.br/InstDados/SerHist/COTAHIST_A{ano}.ZIP"
URL_LISTADAS = "https://sistemaswebb3-listados.b3.com.br/listedCompaniesProxy/CompanyCall/{ep}/{b64}"
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
TPMERC_VISTA = "010"
CACHE = config.DATA / "cotahist"


def _baixa_zip(ano: int) -> bytes | None:
    req = urllib.request.Request(URL_COTAHIST.format(ano=ano), headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            blob = r.read()
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            return zf.read(zf.namelist()[0])
    except Exception as e:  # rede/HTTP: segue sem esse ano
        print(f"  COTAHIST {ano}: ERRO ({e})", file=sys.stderr)
        return None


def _extrai(dados: bytes, tickers: set[str]) -> list[tuple]:
    """(data, ticker, fechamento, quantidade negociada, volume financeiro R$) do layout COTAHIST (mercado à vista, tipo 010):
    PREULT pos 109-121, QUATOT pos 153-170, VOLTOT pos 171-188 (2 decimais implícitos)."""
    out = []
    for raw in dados.splitlines():
        if len(raw) < 217:
            continue
        codneg = raw[12:24].decode("latin-1").strip()
        if codneg not in tickers or raw[24:27] != b"010":
            continue
        d = raw[2:10].decode()
        try:
            preult = int(raw[108:121]) / 100.0
            quatot = int(raw[152:170])
            voltot = int(raw[170:188]) / 100.0
        except ValueError:
            continue
        out.append((f"{d[:4]}-{d[4:6]}-{d[6:8]}", codneg, preult, quatot, voltot))
    return out


CABECALHO = ["data", "ticker", "fechamento", "quantidade", "volume"]


def tickers_alvo() -> set[str]:
    """Universo coberto + papéis da carteira do Ibovespa (do cache data/ibov_comp.json ou direto da B3)."""
    t = set(config.UNIVERSO)
    p = config.DATA / "ibov_comp.json"
    try:
        itens = json.loads(p.read_text(encoding="utf-8")).get("itens", []) if p.exists() else carteira_ibov()["itens"]
        t |= {i["cod"] for i in itens}
    except Exception as e:
        print(f"  carteira Ibovespa indisponível ({e}); COTAHIST só do universo", file=sys.stderr)
    t |= {v for k, v in getattr(config, "ALIAS_TICKER", {}).items() if k in t}   # códigos antigos dos papéis renomeados
    fdir = config.DATA / "ibov_carteira"                                          # papéis de fotografias antigas que já saíram do índice
    if fdir.exists():
        for f in fdir.glob("*.json"):
            try:
                t |= set(json.loads(f.read_text(encoding="utf-8")).get("q", {}))
            except Exception:
                pass
    return t


def _cache_tem(ano: int, tickers: set[str]) -> bool:
    """O CSV do ano já cobre todos os tickers pedidos? (se não, o anual é rebaixado uma vez)"""
    f = CACHE / f"{ano}.csv"
    if not f.exists():
        return False
    with f.open(encoding="utf-8") as fh:
        vistos = {row["ticker"] for row in csv.DictReader(fh)}
    if ano < date.today().year - 1:          # anos antigos: só o universo interessa
        return True
    faltam = {t for t in tickers - vistos if config.UNIVERSO.get(t, {}).get("inicio", 0) <= ano}
    return len(faltam) <= max(2, len(tickers) // 10)   # tolera papéis listados depois (ex.: SAUD3 em 2025)


def atualiza_cotahist(force_ano_corrente: bool = True) -> None:
    """Garante data/cotahist/{ano}.csv para cada ano do universo."""
    CACHE.mkdir(parents=True, exist_ok=True)
    tickers = tickers_alvo()
    ano_ini = min([v["inicio"] for v in config.UNIVERSO.values()] + [getattr(config, "COTAHIST_DESDE", 9999)])
    hoje = date.today().year
    for ano in range(ano_ini, hoje + 1):
        f = CACHE / f"{ano}.csv"
        if f.exists() and not (ano == hoje and force_ano_corrente) and _cache_tem(ano, tickers):
            continue
        print(f"  COTAHIST {ano}: baixando...")
        dados = _baixa_zip(ano)
        if dados is None:
            continue
        linhas = sorted(_extrai(dados, tickers))
        with f.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(CABECALHO)
            w.writerows(linhas)
        print(f"  COTAHIST {ano}: {len(linhas)} registros")


URL_COTAHIST_D = "https://bvmf.bmfbovespa.com.br/InstDados/SerHist/COTAHIST_D{ddmmaaaa}.ZIP"


def atualiza_cotahist_diario(max_dias: int = 40) -> int:
    """Janela diária barata: baixa só os arquivos COTAHIST_D (~0,5 MB) dos pregões que faltam no ano corrente.
    Se o cache do ano não existir ou a lacuna passar de `max_dias`, cai no anual. Devolve nº de pregões acrescentados."""
    from datetime import timedelta
    CACHE.mkdir(parents=True, exist_ok=True)
    hoje = date.today()
    f = CACHE / f"{hoje.year}.csv"
    tickers = tickers_alvo()
    if not _cache_tem(hoje.year, tickers) or not _cache_tem(hoje.year - 1, tickers):
        atualiza_cotahist(force_ano_corrente=True)
        return -1
    existentes: set[str] = set()
    linhas: list[tuple[str, str, float]] = []
    if f.exists():
        with f.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                existentes.add(row["data"])
                linhas.append((row["data"], row["ticker"], float(row["fechamento"]), int(float(row.get("quantidade") or 0)), float(row.get("volume") or 0)))
    ultima = max(existentes) if existentes else None
    if ultima is None or (hoje - date.fromisoformat(ultima)).days > max_dias:
        atualiza_cotahist(force_ano_corrente=True)
        return -1
    d = date.fromisoformat(ultima) + timedelta(days=1)
    novos = 0
    while d <= hoje:
        if d.weekday() < 5 and d.isoformat() not in existentes:
            req = urllib.request.Request(URL_COTAHIST_D.format(ddmmaaaa=d.strftime("%d%m%Y")), headers={"User-Agent": "Mozilla/5.0"})
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    blob = r.read()
                with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                    dados = zf.read(zf.namelist()[0])
                regs = _extrai(dados, tickers)
                if regs:
                    linhas += regs
                    novos += 1
            except Exception:
                pass  # feriado ou arquivo ainda não publicado (sai ~19h)
        d += timedelta(days=1)
    if novos:
        linhas = sorted(set(linhas))
        with f.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(CABECALHO)
            w.writerows(linhas)
    return novos


def serie(ticker: str, campos: tuple[str, ...] = ("fechamento",)) -> list[tuple]:
    """Série (data, fechamento[, quantidade, volume]) ordenada, lida do cache. Papel renomeado (config.ALIAS_TICKER):
    emenda a série do código antigo antes do primeiro pregão do código novo."""
    antigo = getattr(config, "ALIAS_TICKER", {}).get(ticker)
    alvo = {ticker, antigo} if antigo else {ticker}
    pts, pts_ant = [], []
    for f in sorted(CACHE.glob("*.csv")):
        with f.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row["ticker"] in alvo:
                    (pts if row["ticker"] == ticker else pts_ant).append((row["data"], *[float(row.get(c) or 0) for c in campos]))
    pts.sort(); pts_ant.sort()
    if pts_ant:
        # o código antigo prevalece enquanto negociou (alguns códigos novos reaproveitam um código que já existiu,
        # ex.: NATU3 antes de 2020 e de novo em 2025); o novo entra só depois do último pregão do antigo
        primeiro_ant, ultimo_ant = pts_ant[0][0], pts_ant[-1][0]
        pts = [p for p in pts if p[0] < primeiro_ant] + pts_ant + [p for p in pts if p[0] > ultimo_ant]
    return pts


def _listadas(ep: str, payload: dict):
    b64 = base64.b64encode(json.dumps(payload).encode()).decode()
    req = urllib.request.Request(URL_LISTADAS.format(ep=ep, b64=b64), headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        txt = r.read().decode("utf-8", "replace")
    obj = json.loads(txt)
    if isinstance(obj, str):  # a B3 às vezes devolve JSON dentro de string
        obj = json.loads(obj)
    return obj


URL_INDICE = "https://sistemaswebb3-listados.b3.com.br/indexProxy/indexCall/{ep}/{b64}"
SETOR_MACRO = {  # subsetor da B3 -> setor agregado do painel (prefixo do texto que a API devolve)
    "Petróleo": "Petróleo e gás", "Financ": "Financeiro", "Financeiro": "Financeiro", "Utilidade": "Utilidade pública",
    "Mats": "Materiais básicos", "Bens": "Bens industriais", "Cons N": "Consumo não cíclico", "Consumo Cíclico": "Consumo cíclico",
    "Saúde": "Saúde", "Telecom": "Telecom", "Tec": "Tecnologia",
    # 'Diversos' na B3 é 'Consumo Cíclico / Diversos' (aluguel de carros, educação); a API só devolve a última parte
    "Diversos": "Consumo cíclico",
}
# Exceções por trecho do subsetor (avaliadas antes do prefixo): a B3 põe shoppings em 'Financeiro e Outros / Exploração de
# Imóveis' e incorporadoras em 'Consumo Cíclico / Construção Civil'; para o painel os dois são Imobiliário.
SETOR_EXCECAO = {"Explor Im": "Imobiliário", "Constr Civil": "Imobiliário"}


def carteira_ibov() -> dict:
    """Carteira teórica do Ibovespa do dia (B3), com peso (%) e subsetor -> {'data', 'itens': [{cod, nome, peso, subsetor, setor}]}."""
    payload = {"language": "pt-br", "pageNumber": 1, "pageSize": 200, "index": "IBOV", "segment": "2"}
    b64 = base64.b64encode(json.dumps(payload).encode()).decode()
    req = urllib.request.Request(URL_INDICE.format(ep="GetPortfolioDay", b64=b64), headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        obj = json.load(r)
    itens = []
    for x in obj.get("results", []):
        sub = (x.get("segment") or "").strip()
        setor = next((v for k, v in SETOR_EXCECAO.items() if k in sub), None) or next((v for k, v in SETOR_MACRO.items() if sub.startswith(k)), sub or "Outros")
        itens.append({"cod": x["cod"], "nome": x.get("asset", "").strip(), "peso": float(str(x.get("part", "0")).replace(".", "").replace(",", ".")),
                      "subsetor": sub, "setor": setor, "classe": (x.get("type") or "ON").split()[0],
                      "q": float(str(x.get("theoricalQty", "0")).replace(".", "").replace(",", "."))})
    h = obj.get("header") or {}
    d = h.get("date", "")
    data = f"20{d[6:8]}-{d[3:5]}-{d[0:2]}" if len(d) == 8 else None
    redutor = float(str(h.get("reductor", "0")).replace(".", "").replace(",", ".")) or None
    return {"data": data, "itens": itens, "redutor": redutor}


UNIT_COMP = {"BPAC11": (1, 2), "ENGI11": (1, 4), "IGTI11": (1, 2), "KLBN11": (1, 4), "SANB11": (1, 1), "TAEE11": (1, 2)}  # (ON, PN) por unit


def _br(v) -> float:
    try:
        return float(str(v).replace(".", "").replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def _iso(d: str) -> str | None:
    return f"{d[6:]}-{d[3:5]}-{d[:2]}" if d and len(d) == 10 else None


def proventos_ticker(ticker: str, classe: str, desde_iso: str) -> list[dict]:
    """Proventos e eventos de capital do papel (canal de listadas, GetListedSupplementCompany por código da empresa),
    já convertidos para 'por unidade do ticker' (ON, PN ou unit). [{com, valor, acao}] e [{com, fator, acao}] misturados:
    valor = R$ por papel (dividendo/JCP); fator = % de bonificação/desdobramento (quantidade sobe 1+fator/100)."""
    cod = ticker[:4]
    for tentativa in range(2):
        try:
            r = _listadas("GetListedSupplementCompany", {"language": "pt-br", "issuingCompany": cod})
            r = r[0] if isinstance(r, list) and r else r
            if not isinstance(r, dict):
                continue
            por_com: dict[tuple, dict] = {}
            for x in r.get("cashDividends") or []:
                com = _iso(x.get("lastDatePrior") or "")
                if not com or com < desde_iso:
                    continue
                isin = x.get("assetIssued") or ""
                cl = "PN" if "ACNPR" in isin or "ACNPA" in isin or "ACNPB" in isin else "ON"
                k = (com, x.get("label"), _br(x.get("rate")), cl)
                por_com[k] = {"com": com, "valor": _br(x.get("rate")), "classe": cl, "acao": x.get("label")}
            out = []
            if classe == "UNT":
                a_on, a_pn = UNIT_COMP.get(ticker, (1, 0))
                # soma por (com, acao): ON×a + PN×b; se não houver linha PN, PN = ON
                grupos: dict[tuple, dict] = {}
                for v in por_com.values():
                    g = grupos.setdefault((v["com"], v["acao"]), {"ON": 0.0, "PN": None})
                    if v["classe"] == "PN":
                        g["PN"] = (g["PN"] or 0.0) + v["valor"]
                    else:
                        g["ON"] += v["valor"]
                for (com, acao), g in grupos.items():
                    pn = g["PN"] if g["PN"] is not None else g["ON"]
                    out.append({"com": com, "valor": g["ON"] * a_on + pn * a_pn, "acao": acao})
            else:
                alvo = "PN" if classe.startswith("PN") else "ON"
                tem_alvo = any(v["classe"] == alvo for v in por_com.values())
                for v in por_com.values():
                    if v["classe"] == alvo or (not tem_alvo and v["classe"] == "ON"):
                        out.append({"com": v["com"], "valor": v["valor"], "acao": v["acao"]})
            for x in r.get("stockDividends") or []:
                com = _iso(x.get("lastDatePrior") or "")
                if com and com >= desde_iso:
                    out.append({"com": com, "fator": _br(x.get("factor")), "acao": x.get("label")})
            return sorted(out, key=lambda v: v["com"])
        except Exception as e:
            if tentativa:
                print(f"  proventos {ticker}: {e}", file=sys.stderr)
    return []


def proventos_recentes(trading_name: str, desde_iso: str) -> list[dict]:
    """Proventos em dinheiro (canal de listadas) com data-com >= desde: [{com, valor, pcum, tipo, acao}]. Só a classe que a B3 lista (ON)."""
    out = []
    for tentativa in range(2):
        try:
            r = _listadas("GetListedCashDividends", {"language": "pt-br", "pageNumber": 1, "pageSize": 40, "tradingName": trading_name})
            rows = r.get("results", []) if isinstance(r, dict) else []
            if rows or tentativa:
                for x in rows:
                    com = x.get("lastDatePriorEx") or ""
                    if len(com) != 10:
                        continue
                    com_iso = f"{com[6:]}-{com[3:5]}-{com[:2]}"
                    if com_iso < desde_iso:
                        continue
                    try:
                        out.append({"com": com_iso, "valor": float(str(x.get("valueCash", "0")).replace(".", "").replace(",", ".")),
                                    "pcum": float(str(x.get("closingPricePriorExDate", "0")).replace(".", "").replace(",", ".")) or None,
                                    "tipo": x.get("typeStock"), "acao": x.get("corporateAction")})
                    except ValueError:
                        pass
                break
        except Exception as e:
            if tentativa:
                print(f"  proventos {trading_name}: {e}", file=sys.stderr)
    return out


def numero_acoes(cod_b3: str) -> int | None:
    """Total de ações (inclui tesouraria) — só o valor ATUAL, a B3 não dá histórico."""
    for _ in range(2):  # 1ª chamada às vezes volta vazia
        try:
            r = _listadas("GetListedSupplementCompany", {"language": "pt-br", "issuingCompany": cod_b3})
            r = r[0] if isinstance(r, list) and r else r
            if isinstance(r, dict) and r.get("totalNumberShares"):
                return int(r["totalNumberShares"].replace(".", ""))
        except Exception as e:
            print(f"  B3 ações {cod_b3}: {e}", file=sys.stderr)
    return None
