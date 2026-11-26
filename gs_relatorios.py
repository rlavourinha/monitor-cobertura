"""Coletor de research do Goldman Sachs (Marquee). Sessão: `python probe_gringas.py --login goldman` (entre, abra uma
empresa e feche a janela). O portal informa ~90 dias de validade (v1/token/status: expiresOn).

O Marquee derruba o Chrome headless (ERR_HTTP2_PROTOCOL_ERROR): o coletor abre o Chrome com janela FORA DA TELA.

Para cada empresa do cobertura.json (código de research = GUID, em btg-research/globais/gs_ids.json):
  - relatórios recentes (advanced-search com o token de /research/session-exchange) -> PDF COMPLETO (downloadPath)
    -> texto em btg-research/globais/txt + indice.json (casa "Goldman Sachs");  PDF em Claude/research-pdfs/gs/
  - modelos (/research/services/equity/models): cada versão nova em Claude/research-pdfs/gs/modelos/ (histórico)

    python gs_relatorios.py                # últimos 3 dias
    python gs_relatorios.py --dias 10
    python gs_relatorios.py --modelos-base # último modelo de cada empresa (carga inicial)
    python gs_relatorios.py --resolver     # refaz o mapa ticker -> GUID varrendo 12 meses de relatórios (lento)
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import sys
import urllib.parse
from datetime import date, datetime, timedelta, timezone

import config
from fontes import gringas
from jpm_relatorios import ESPELHO, INDICE, TXT, SessaoVencida, _carrega, _grava, _texto_pdf

M = "https://marquee.gs.com"
AQUECE = M + "/content/companies/7e32f54e-de00-11d5-9be0-f05e055bbe70.html"     # página da Verizon
IDS, ESTADO = ESPELHO / "gs_ids.json", ESPELHO / "gs_estado.json"
PDFS = config.RAIZ.parent / "research-pdfs" / "gs"
MODELOS = PDFS / "modelos"
ALIAS = {"ITUB4": ["ITUB"], "BBDC4": ["BBD"], "VIVT3": ["VIV"], "TIMS3": ["TIMB"], "SANB11": ["BSBR"],
         "TIT": ["TLIT.MI", "TIT.MI"], "TEF": ["TEF.MC", "TEF"]}
RATING = re.compile(r"\b(Buy|Neutral|Sell|Not Rated)\b")


def _token(p) -> dict:
    r = p.ctx.request.get(M + "/research/session-exchange", timeout=60000)
    try:
        return {"authorization": "Bearer " + r.json()["access_token"], "accept": "application/json, text/plain, */*"}
    except Exception:
        raise SessaoVencida(f"session-exchange {r.status}")


def _busca(p, H, inicio: date, fim: date, guid: str | None = None, page: int = 1, size: int = 50) -> list[dict]:
    q = {"language": '["en"]', "filter": f"(publicationDateTime IN [{inicio:%Y-%m-%d}T00:00:00,{fim:%Y-%m-%d}T00:00:00])",
         "page": str(page), "size": str(size), "sort": "time"}
    if guid:
        q["facetIds"] = f'(id_companies_primary CTS_ALL ["{guid}"])'
    r = p.ctx.request.get(M + "/research/search/reports/advanced-search?" + urllib.parse.urlencode(q), headers=H, timeout=120000)
    try:
        return r.json().get("documents") or []
    except Exception:
        raise SessaoVencida(f"advanced-search {r.status}")


def _abre(p):
    p.page.goto(AQUECE, timeout=90000, wait_until="domcontentloaded")
    p.page.wait_for_timeout(5000)
    if "login" in p.page.url or "idfs" in p.page.url:
        raise SessaoVencida("Marquee redirecionou para o login")


def resolver(p, H) -> dict:
    """ticker -> GUID: varre 12 meses de relatórios e casa primaryCompanies com primaryCompanyTickers."""
    emp = json.loads((config.RAIZ / "cobertura.json").read_text(encoding="utf-8"))["empresas"]
    mapa = collections.defaultdict(collections.Counter)
    hoje = date.today() + timedelta(days=1)
    for k in range(3):
        a, b = hoje - timedelta(days=122 * (k + 1)), hoje - timedelta(days=122 * k)
        for pg in range(1, 10):
            docs = _busca(p, H, a, b, page=pg, size=999)
            for d in docs:
                pc, pt = d.get("primaryCompanies") or [], d.get("primaryCompanyTickers") or []
                pares = zip(pc, pt) if len(pc) == len(pt) else ([(pc[0], t) for t in pt] if len(pc) == 1 else [])
                for g, t in pares:
                    mapa[t][g] += 1
            if len(docs) < 999:
                break
    ids = {}
    for tk in emp:
        cands = [tk, tk + ".SA"] + ALIAS.get(tk, [])
        ids[tk] = next((mapa[c].most_common(1)[0][0] for c in cands if c in mapa), None)
        print(f"  {tk:7s} -> {ids[tk] or 'não achado no GS'}")
    _grava(IDS, ids)
    return ids


def _baixa_modelo(p, link: str) -> tuple[bytes, str] | None:
    r = p.ctx.request.get(M + link + "?ar=true", timeout=180000)
    b = r.body()
    if b[:2] == b"PK" or b[:4] == b"\xd0\xcf\x11\xe0":
        nome = re.search(r'filename="?([^";]+)', r.headers.get("content-disposition", ""))
        return b, (nome.group(1) if nome else "model.xlsx")
    try:                                               # o portal às vezes só entrega pelo fluxo de download do navegador
        with p.page.expect_download(timeout=120000) as dl:
            try:
                p.page.goto(M + link)
            except Exception:
                pass
        d = dl.value
        return open(d.path(), "rb").read(), d.suggested_filename
    except Exception:
        return None


def _modelos(p, tk: str, guid: str, vistos: set, so_ultimo: bool, desde: date | None = None) -> list[str]:
    out = []
    try:
        ms = p.ctx.request.get(f"{M}/research/services/equity/models?companyguid={guid}", timeout=60000).json().get("models") or []
    except Exception:
        return out
    for m in (ms[:1] if so_ultimo else ms):
        if m["id"] in vistos:
            continue
        dm = re.search(r"/models/(\d{4})/(\d{2})/(\d{2})/", m["link"])
        if desde and dm and date(*map(int, dm.groups())) < desde:      # coleta diária: só modelo novo, não o histórico
            continue
        res = _baixa_modelo(p, m["link"])
        if not res:
            print(f"  modelo {tk} {m.get('formattedDate')}: sem arquivo", file=sys.stderr)
            continue
        b, nome = res
        data = re.search(r"/models/(\d{4})/(\d{2})/(\d{2})/", m["link"])
        dt = "-".join(data.groups()) if data else date.today().isoformat()
        arq = MODELOS / f"{tk}_{dt}_{m['id'][:8]}_{nome}"
        arq.write_bytes(b)
        vistos.add(m["id"])
        out.append(arq.name)
        print(f"  modelo {dt} {arq.name} ({len(b) // 1024} KB)")
    return out


def coletar(dias: int, so_modelos_base: bool = False, refazer_ids: bool = False) -> tuple[list[dict], list[str]]:
    for d in (TXT, PDFS, MODELOS):
        d.mkdir(parents=True, exist_ok=True)
    indice, est = _carrega(INDICE, {}), _carrega(ESTADO, {})
    vistos = set(est.get("modelos", []))
    novos, mods = [], []
    with gringas.Portal("goldman", headless="fora") as p:
        _abre(p)
        H = _token(p)
        ids = resolver(p, H) if (refazer_ids or not IDS.exists()) else _carrega(IDS, {})
        if so_modelos_base:
            for tk, g in ids.items():
                if g:
                    mods += _modelos(p, tk, g, vistos, so_ultimo=True)
        else:
            fim, ini = date.today() + timedelta(days=1), date.today() - timedelta(days=dias)
            docs, quem = {}, {}
            for tk, g in ids.items():
                if not g:
                    continue
                for d in _busca(p, H, ini, fim, g):
                    docs[d["id"]] = d
                    quem.setdefault(d["id"], []).append(tk)
                mods += _modelos(p, tk, g, vistos, so_ultimo=False, desde=ini)
            for did, d in sorted(docs.items(), key=lambda x: x[1].get("publicationDateTime") or 0):
                if did in indice or not str(d.get("downloadPath", "")).endswith(".pdf"):
                    continue
                r = p.ctx.request.get(M + d["downloadPath"], timeout=180000)
                dados = r.body()
                if dados[:4] != b"%PDF":
                    print(f"  {did}: sem PDF ({r.status}) — {(d.get('distributionHeadline') or '')[:60]}", file=sys.stderr)
                    continue
                texto, npag = _texto_pdf(dados)
                (PDFS / f"{did}.pdf").write_bytes(dados)
                (TXT / f"{did}.txt").write_text("FONTE: PDF completo\n\n" + texto, encoding="utf-8")
                ts = d.get("publicationDateTime")
                data_pub = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date().isoformat() if ts else ""
                mr = RATING.search(texto[:2500])
                reg = {"id": did, "casa": "Goldman Sachs", "titulo": (d.get("distributionHeadline") or d.get("title") or "")[:200],
                       "empresa": ", ".join(d.get("primaryCompanyTickers") or [])[:80],
                       "analista": ", ".join((d.get("authors") or [])[:2]), "rating": mr.group(1) if mr else "",
                       "data": data_pub, "paginas": npag, "chars": len(texto),
                       "coletado": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "arquivo": f"{did}.pdf",
                       "sha1": hashlib.sha1(dados).hexdigest()[:16], "cobertura_gs": quem.get(did, []),
                       "sinopse": re.sub(r"<[^>]+>|\s+", " ", d.get("synopsis") or "")[:400], "fonte_url": M + (d.get("path") or "")}
                indice[did] = reg
                _grava(INDICE, indice)
                novos.append(reg)
                print(f"  + {data_pub} [{', '.join(reg['cobertura_gs'])}] {reg['titulo'][:80]} · {npag}p")
    est["modelos"] = sorted(vistos)
    _grava(ESTADO, est)
    return novos, mods


def aviso_sessao(motivo: str) -> None:
    est = _carrega(ESTADO, {})
    hoje = date.today().isoformat()
    print(f"sessão do Goldman vencida ({motivo}): rode `python probe_gringas.py --login goldman`", file=sys.stderr)
    try:
        from fontes import telegram
        if est.get("aviso_sessao") != hoje and telegram.disponivel():
            telegram.enviar("<b>Goldman Sachs Research</b> · a sessão do Marquee venceu. No laptop: "
                            "<code>python probe_gringas.py --login goldman</code> (entre, abra uma empresa e feche a janela)")
            est["aviso_sessao"] = hoje
            _grava(ESTADO, est)
    except Exception as e:
        print(f"  telegram: {e}", file=sys.stderr)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Coletor de research do Goldman Sachs Marquee.")
    ap.add_argument("--dias", type=int, default=3)
    ap.add_argument("--modelos-base", action="store_true")
    ap.add_argument("--resolver", action="store_true")
    a = ap.parse_args()
    try:
        novos, mods = coletar(a.dias, a.modelos_base, a.resolver)
    except SessaoVencida as e:
        aviso_sessao(str(e))
        return 3
    print(f"Goldman Sachs: {len(novos)} relatório(s) e {len(mods)} modelo(s) novo(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
