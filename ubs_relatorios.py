"""Coletor de research do UBS (UBS Neo; inclui o research do UBS BB), sem janela. Sessão: `python probe_gringas.py
--login ubs` (entre, abra uma empresa e feche). Cookies de sessão gravados no login e reinjetados pelo Portal.

Para cada empresa do cobertura.json com cobertura no UBS (RIC em btg-research/globais/ubs_ids.json; BB não é coberto
desde 2019 por causa da sociedade UBS BB, Telecom Italia parou em 2021):
  - relatórios recentes (POST /api/search/v2/research-stream-advanced, primaryIssuerRicCode) -> PDF COMPLETO
    (/api/super-grid-provider-research/v1/document/<id>.pdf) -> texto em btg-research/globais (casa "UBS")
  - modelos ("<RIC> Company Model <data>", ZIP com o Excel): cada versão nova em Claude/research-pdfs/ubs/modelos/

    python ubs_relatorios.py                # últimos 3 dias
    python ubs_relatorios.py --dias 10
    python ubs_relatorios.py --modelos-base # último modelo de cada empresa (carga inicial)
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import zipfile
from datetime import date, datetime, timedelta

import config
from fontes import gringas
from jpm_relatorios import ESPELHO, INDICE, TXT, SessaoVencida, _carrega, _grava, _texto_pdf

N = "https://neo.ubs.com"
BUSCA = N + "/api/search/v2/research-stream-advanced?q=*&limit={lim}&offset=0"
DOC = N + "/api/super-grid-provider-research/v1/document/{arq}"
AQUECE = N + "/feed/stream/company/VZ.N/latest/company-research"
H = {"content-type": "application/json", "accept": "application/json",
     "x-client-component-id": "FedPCC_pcc-client-content-feed", "x-original-client-component-id": "FedPCC_pcc-client-content-feed"}
IDS, ESTADO = ESPELHO / "ubs_ids.json", ESPELHO / "ubs_estado.json"
PDFS = config.RAIZ.parent / "research-pdfs" / "ubs"
MODELOS = PDFS / "modelos"
RATING = re.compile(r"\((Buy|Neutral|Sell|Not Rated)\)")


def _busca(p, ric: str, lim: int = 25, so_modelo: bool = False) -> list[dict]:
    filtros = {"focus": ["company"], "primaryIssuerRicCode": [ric]}
    if so_modelo:
        filtros["fileType"] = ["ZIP"]
    corpo = {"filters": {"andFilters": filtros}, "notFilters": {"researchTypeCode": ["PT.PACKAGE"]}}
    r = p.ctx.request.post(BUSCA.format(lim=lim), data=json.dumps(corpo), headers=H, timeout=90000)
    try:
        j = r.json()
    except Exception:
        raise SessaoVencida(f"busca UBS {r.status} não-JSON")
    if "categories" not in j:
        raise SessaoVencida(f"busca UBS sem resultados ({str(j)[:80]})")
    return (j["categories"].get("research") or {}).get("results") or []


def _arquivo(d: dict) -> str:
    ext = (d.get("neoUrlPath") or "").rsplit(".", 1)[-1].lower() or "pdf"
    return f"{d['wireId']}.{ext}"


def _modelo(p, ric: str, d: dict, vistos: set) -> str | None:
    if d["wireId"] in vistos:
        return None
    b = p.ctx.request.get(DOC.format(arq=_arquivo(d)), timeout=180000).body()
    if b[:2] != b"PK":
        print(f"  modelo {ric} {d.get('pubDate', '')[:10]}: sem arquivo", file=sys.stderr)
        return None
    dt = (d.get("pubDate") or "")[:10]
    try:                                               # o ZIP traz o Excel: guarda o Excel (abre direto)
        z = zipfile.ZipFile(io.BytesIO(b))
        nome = next(n for n in z.namelist() if n.lower().endswith((".xlsx", ".xlsm", ".xls")))
        arq = MODELOS / f"{ric}_{dt}_{d['wireId']}_{nome.rsplit('/', 1)[-1]}"
        arq.write_bytes(z.read(nome))
    except Exception:
        arq = MODELOS / f"{ric}_{dt}_{d['wireId']}.zip"
        arq.write_bytes(b)
    vistos.add(d["wireId"])
    print(f"  modelo {dt} {arq.name} ({arq.stat().st_size // 1024} KB)")
    return arq.name


def coletar(dias: int, modelos_base: bool = False) -> tuple[list[dict], list[str]]:
    for d in (TXT, PDFS, MODELOS):
        d.mkdir(parents=True, exist_ok=True)
    indice, est = _carrega(INDICE, {}), _carrega(ESTADO, {})
    vistos = set(est.get("modelos", []))
    ids = _carrega(IDS, {})
    corte = (date.today() - timedelta(days=dias)).isoformat()
    novos, mods = [], []
    with gringas.Portal("ubs", headless=True) as p:
        p.page.goto(AQUECE, timeout=90000, wait_until="domcontentloaded")
        p.page.wait_for_timeout(4000)
        if "login" in p.page.url or "idp" in p.page.url:
            raise SessaoVencida("UBS Neo redirecionou para o login")
        docs, quem = {}, {}
        for tk, ric in ids.items():
            if not ric:
                continue
            if modelos_base:
                ms = _busca(p, ric, 1, so_modelo=True)
                if ms and (ms[0].get("pubDate") or "") >= str(date.today().year - 1):
                    m = _modelo(p, ric, ms[0], vistos)
                    mods += [m] if m else []
                continue
            for d in _busca(p, ric):
                if (d.get("pubDate") or "")[:10] < corte:
                    continue
                if (d.get("fileType") or "").upper() == "ZIP" or "Company Model" in (d.get("title") or ""):
                    m = _modelo(p, ric, d, vistos)
                    mods += [m] if m else []
                    continue
                docs[d["wireId"]] = d
                quem.setdefault(d["wireId"], []).append(tk)
        for wid, d in sorted(docs.items(), key=lambda x: x[1].get("pubDate") or ""):
            if wid in indice or not _arquivo(d).endswith(".pdf"):
                continue
            dados = p.ctx.request.get(DOC.format(arq=_arquivo(d)), timeout=180000).body()
            if dados[:4] != b"%PDF":
                print(f"  {wid}: sem PDF — {(d.get('title') or '')[:60]}", file=sys.stderr)
                continue
            texto, npag = _texto_pdf(dados)
            (PDFS / f"{wid}.pdf").write_bytes(dados)
            (TXT / f"{wid}.txt").write_text("FONTE: PDF completo\n\n" + texto, encoding="utf-8")
            titulo = d.get("distributionTitle") or d.get("title") or ""
            mr = RATING.search(titulo) or RATING.search(texto[:2500])
            ric = d.get("ric")
            ric = ", ".join(ric) if isinstance(ric, list) else (ric or "")          # o UBS às vezes manda lista
            reg = {"id": wid, "casa": "UBS", "titulo": titulo[:200], "empresa": ric[:80],
                   "analista": d.get("primaryAnalystName") or d.get("author") or "", "rating": mr.group(1) if mr else "",
                   "data": (d.get("pubDate") or "")[:10], "paginas": npag, "chars": len(texto),
                   "coletado": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "arquivo": f"{wid}.pdf",
                   "sha1": hashlib.sha1(dados).hexdigest()[:16], "cobertura_ubs": quem.get(wid, []),
                   "fonte_url": f"{N}/feed/stream/company/{d.get('ric')}/latest/company-research/article/research/{wid}"}
            indice[wid] = reg
            _grava(INDICE, indice)
            novos.append(reg)
            print(f"  + {reg['data']} [{', '.join(reg['cobertura_ubs'])}] {titulo[:80]} · {npag}p")
    est["modelos"] = sorted(vistos)
    _grava(ESTADO, est)
    return novos, mods


def aviso_sessao(motivo: str) -> None:
    est = _carrega(ESTADO, {})
    hoje = date.today().isoformat()
    print(f"sessão do UBS vencida ({motivo}): rode `python probe_gringas.py --login ubs`", file=sys.stderr)
    try:
        from fontes import telegram
        if est.get("aviso_sessao") != hoje and telegram.disponivel():
            telegram.enviar("<b>UBS Research</b> · a sessão do UBS Neo venceu. No laptop: "
                            "<code>python probe_gringas.py --login ubs</code> (entre, abra uma empresa e feche a janela)")
            est["aviso_sessao"] = hoje
            _grava(ESTADO, est)
    except Exception as e:
        print(f"  telegram: {e}", file=sys.stderr)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Coletor de research do UBS Neo (headless).")
    ap.add_argument("--dias", type=int, default=3)
    ap.add_argument("--modelos-base", action="store_true")
    a = ap.parse_args()
    try:
        novos, mods = coletar(a.dias, a.modelos_base)
    except SessaoVencida as e:
        aviso_sessao(str(e))
        return 3
    print(f"UBS: {len(novos)} relatório(s) e {len(mods)} modelo(s) novo(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
