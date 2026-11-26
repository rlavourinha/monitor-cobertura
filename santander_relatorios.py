"""Coletor de research do Santander (Santander Insights, plataforma BlueMatrix). Sessão: `python probe_gringas.py
--login santander` (entre em santandercibinsights.com, abra um relatório e feche).

O portal barra navegador automatizado sem janela ("automated browser access is not permitted"): roda com o Chrome
FORA DA TELA. Toda chamada à API exige o cabeçalho x-xsrf-token, capturado do 1º pedido da própria página.

Para cada empresa do cobertura.json: busca global por ticker (o portal devolve só os relatórios daquela empresa e o
símbolo, ex. "RDOR3 BZ"), baixa o PDF COMPLETO (pdfLink dos metadados do documento) e grava em btg-research/globais
(casa "Santander"). Sem modelos: os relatórios de empresa não trazem planilha anexada (checado em 26/11/2026).

    python santander_relatorios.py            # últimos 3 dias
    python santander_relatorios.py --dias 10
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime, timedelta

import config
from fontes import gringas
from jpm_relatorios import ESPELHO, INDICE, TXT, SessaoVencida, _carrega, _grava, _texto_pdf

S = "https://santandercibinsights.com"
HOME = S + "/?contentType=FEEDS"
IDS, ESTADO = ESPELHO / "santander_ids.json", ESPELHO / "santander_estado.json"
PDFS = config.RAIZ.parent / "research-pdfs" / "santander"
RATING = re.compile(r"\b(Outperform|Underperform|Hold|Buy|Sell|Neutral)\b")


def _abre(p) -> dict:
    tok = {}
    p.page.on("request", lambda req: tok.setdefault("t", req.headers.get("x-xsrf-token")) if req.headers.get("x-xsrf-token") else None)
    p.page.goto(HOME, timeout=90000, wait_until="domcontentloaded")
    p.page.wait_for_timeout(10000)
    if "login" in p.page.url or "bluematrix" in p.page.url or not tok.get("t"):
        raise SessaoVencida(f"sem sessão ({p.page.url[:60]})")
    return {"content-type": "application/json", "accept": "application/json, text/plain, */*", "x-xsrf-token": tok["t"]}


def _busca(p, H, termo: str) -> tuple[str | None, list[dict]]:
    corpo = {"searchString": termo, "overrideTickerMatch": False, "useSemanticSearch": False, "micrositeId": None,
             "firmId": None, "sort": {"sortField": "PUBLISHED", "sortOrder": "DESC"}}
    r = p.ctx.request.post(S + "/api/global-search/v1", data=json.dumps(corpo), headers=H, timeout=90000)
    try:
        j = r.json()
    except Exception:
        raise SessaoVencida(f"global-search {r.status}")
    lst = ((j.get("documents") or {}).get("results") or {}).get("list") or []
    return j.get("documentFilterSymbol"), lst


def coletar(dias: int) -> list[dict]:
    TXT.mkdir(parents=True, exist_ok=True)
    PDFS.mkdir(parents=True, exist_ok=True)
    indice, ids = _carrega(INDICE, {}), _carrega(IDS, {})
    emp = json.loads((config.RAIZ / "cobertura.json").read_text(encoding="utf-8"))["empresas"]
    corte = (date.today() - timedelta(days=dias)).isoformat()
    novos = []
    with gringas.Portal("santander", headless="fora") as p:
        H = _abre(p)
        docs, quem = {}, {}
        for tk in emp:
            if tk in ids and not ids[tk]:
                continue                                    # já sabemos que o Santander não cobre
            simbolo, lst = _busca(p, H, tk)
            if tk not in ids:
                ids[tk] = simbolo if simbolo and simbolo.split()[0] in (tk, tk.split(".")[0]) else None
                print(f"  {tk:7s} -> {ids[tk] or 'sem cobertura Santander'}")
            if not ids[tk]:
                continue
            for d in lst:
                if (d.get("published") or "")[:10] >= corte:
                    docs[d["encrypt"]] = d
                    quem.setdefault(d["encrypt"], []).append(tk)
        _grava(IDS, ids)
        for enc, d in sorted(docs.items(), key=lambda x: x[1].get("published") or ""):
            if enc in indice:
                continue
            det = p.ctx.request.get(f"{S}/api/document/v1/document/{enc}", headers=H, timeout=60000).json()
            link = det.get("pdfLink")
            dados = p.ctx.request.get(S + link, timeout=180000).body() if link else b""
            if dados[:4] != b"%PDF":
                print(f"  {enc}: sem PDF — {(d.get('title') or '')[:60]}", file=sys.stderr)
                continue
            texto, npag = _texto_pdf(dados)
            (PDFS / f"{enc}.pdf").write_bytes(dados)
            (TXT / f"{enc}.txt").write_text("FONTE: PDF completo\n\n" + texto, encoding="utf-8")
            an = [a.get("name") or a.get("displayName") or "" for a in (det.get("analysts") or []) if isinstance(a, dict)]
            mr = RATING.search(texto[:2500])
            reg = {"id": enc, "casa": "Santander", "titulo": (d.get("title") or "")[:200], "empresa": ", ".join(quem.get(enc, [])),
                   "analista": ", ".join(x for x in an if x)[:80], "rating": mr.group(1) if mr else "",
                   "data": (d.get("published") or "")[:10], "paginas": npag, "chars": len(texto),
                   "coletado": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "arquivo": f"{enc}.pdf",
                   "sha1": hashlib.sha1(dados).hexdigest()[:16], "cobertura_santander": quem.get(enc, []),
                   "sinopse": re.sub(r"\s+", " ", d.get("synopsis") or "")[:400], "fonte_url": f"{S}/report/{enc}"}
            indice[enc] = reg
            _grava(INDICE, indice)
            novos.append(reg)
            print(f"  + {reg['data']} [{', '.join(reg['cobertura_santander'])}] {reg['titulo'][:80]} · {npag}p")
    return novos


def aviso_sessao(motivo: str) -> None:
    est = _carrega(ESTADO, {})
    hoje = date.today().isoformat()
    print(f"sessão do Santander vencida ({motivo}): rode `python probe_gringas.py --login santander`", file=sys.stderr)
    try:
        from fontes import telegram
        if est.get("aviso_sessao") != hoje and telegram.disponivel():
            telegram.enviar("<b>Santander Research</b> · a sessão do Santander Insights venceu. No laptop: "
                            "<code>python probe_gringas.py --login santander</code> (entre em santandercibinsights.com e feche)")
            est["aviso_sessao"] = hoje
            _grava(ESTADO, est)
    except Exception as e:
        print(f"  telegram: {e}", file=sys.stderr)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Coletor de research do Santander Insights.")
    ap.add_argument("--dias", type=int, default=3)
    a = ap.parse_args()
    try:
        novos = coletar(a.dias)
    except SessaoVencida as e:
        aviso_sessao(str(e))
        return 3
    print(f"Santander: {len(novos)} relatório(s) novo(s) em {a.dias} dia(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
