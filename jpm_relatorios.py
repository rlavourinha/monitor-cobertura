"""Coletor de research do J.P. Morgan Markets (casa global), sem janela, com a sessão salva por `probe_gringas.py --login jpm`.

Para cada empresa do cobertura.json: lista os relatórios recentes do JPM (API GraphQL do próprio portal), baixa o PDF
COMPLETO e grava o texto onde a leitura.py já lê as globais:

    btg-research/globais/txt/<id>.txt     "FONTE: PDF completo" + texto (pypdf, até 80 páginas)
    btg-research/globais/indice.json      {id: {casa: "JPMorgan", titulo, empresa, analista, rating, data, ...}}
    Claude/research-pdfs/jpm/<id>.pdf     o PDF (fora de repositório git)

    python jpm_relatorios.py               # relatórios dos últimos 3 dias das empresas cobertas
    python jpm_relatorios.py --dias 10     # janela maior (1ª carga)
    python jpm_relatorios.py --resolver    # (re)descobre o código JPM de cada ticker (cache em globais/jpm_ids.json)

Sessão vencida (o portal devolve login em vez de JSON): avisa no Telegram uma vez por dia e sai com código 3.
Para renovar: `python probe_gringas.py --login jpm` (abre a janela; entre e feche).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import config
from fontes import gringas

ESPELHO = config.RAIZ.parent / "btg-research" / "globais"
TXT, INDICE, IDS, ESTADO = ESPELHO / "txt", ESPELHO / "indice.json", ESPELHO / "jpm_ids.json", ESPELHO / "jpm_estado.json"
PDFS = config.RAIZ.parent / "research-pdfs" / "jpm"
BASE = "https://markets.jpmorgan.com"
GQL = BASE + "/research/controller/graphql/query-v2"
PDF_URL = BASE + "/contentviewer/api/articles/{id}/pdf"
AQUECE = BASE + "/jpmm/research.browse.company?companyId=MA.N"
QUERY = json.loads((Path(__file__).resolve().parent / "fontes" / "jpm_query_artigos.json").read_text(encoding="utf-8"))
MAX_PAGINAS = 80
RATING = re.compile(r"\b(Overweight|Underweight|Neutral|Not Rated|Restricted)\b")
MESES = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
FIXOS = {"TEF": ["TEF.MC"], "TIT": ["TLIT.MI", "TIT.MI"], "CEF": []}     # sem listagem / listagem fora dos EUA


class SessaoVencida(RuntimeError):
    pass


def _carrega(arq, padrao):
    return json.loads(arq.read_text(encoding="utf-8")) if arq.exists() else padrao


def _grava(arq, obj):
    arq.parent.mkdir(parents=True, exist_ok=True)
    arq.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def _data(s: str) -> str:
    """'Sun Nov 15 21:57:41 UTC 2026' -> '2026-11-15'."""
    m = re.match(r"\w{3} (\w{3}) (\d{1,2}) .* (\d{4})$", s or "")
    return f"{m.group(3)}-{MESES[m.group(1)]:02d}-{int(m.group(2)):02d}" if m else ""


def _artigos(p: gringas.Portal, cid: str, start: int = 1) -> tuple[int, list[dict]] | None:
    """(total, relatórios da página) da empresa `cid` no JPM; None se o código não existe lá."""
    b = dict(QUERY)
    b["variables"] = dict(QUERY["variables"], companyId=[cid], start=start)
    r = p.ctx.request.post(GQL, data=json.dumps(b), headers={"content-type": "application/json"}, timeout=90000)
    try:
        j = r.json()
    except Exception:
        raise SessaoVencida(f"GraphQL devolveu {r.status} não-JSON")
    if r.status in (401, 403) or "data" not in j:
        raise SessaoVencida(f"GraphQL {r.status}: {str(j)[:120]}")
    cs = ((j["data"].get("companyService") or {}).get("companies") or {}).get("results") or []
    if not cs:
        return None
    res = cs[0]["research"]
    return res["count"], res["results"]


def resolver(p: gringas.Portal, forcar: bool = False) -> dict:
    """{ticker da cobertura: código JPM (RIC)} — testa candidatos na API e guarda em jpm_ids.json."""
    ids = {} if forcar else _carrega(IDS, {})
    emp = json.loads((config.RAIZ / "cobertura.json").read_text(encoding="utf-8"))["empresas"]
    for tk, v in emp.items():
        if tk in ids:
            continue
        amb = v.get("ambiente") or []
        if tk in FIXOS:
            cands = FIXOS[tk]
        elif "SEC" in amb:
            cands = [f"{tk}.N", f"{tk}.O", f"{tk}.OQ"]
        else:
            cands = [f"{tk}.SA"]
        ids[tk] = None
        for c in cands:
            if _artigos(p, c):
                ids[tk] = c
                break
        print(f"  {tk:7s} -> {ids[tk] or 'não achado no JPM'}")
    _grava(IDS, ids)
    return ids


def _texto_pdf(dados: bytes) -> tuple[str, int]:
    from pypdf import PdfReader
    rd = PdfReader(io.BytesIO(dados))
    partes = []
    for i, pg in enumerate(rd.pages[:MAX_PAGINAS]):
        try:
            t = pg.extract_text() or ""
        except Exception:
            t = ""
        partes.append(f"[página {i + 1}]\n{t.strip()}")
    return re.sub(r"\n{3,}", "\n\n", "\n\n".join(partes)), len(rd.pages)


def aviso_sessao(motivo: str) -> None:
    est = _carrega(ESTADO, {})
    hoje = date.today().isoformat()
    print(f"sessão do JPM vencida ({motivo}): rode `python probe_gringas.py --login jpm`", file=sys.stderr)
    try:
        from fontes import telegram
        if est.get("aviso_sessao") != hoje and telegram.disponivel():
            telegram.enviar("<b>JPMorgan Research</b> · a sessão do portal venceu. No laptop: "
                            "<code>python probe_gringas.py --login jpm</code> (entre e feche a janela)")
            est["aviso_sessao"] = hoje
            _grava(ESTADO, est)
    except Exception as e:
        print(f"  telegram: {e}", file=sys.stderr)


def coletar(dias: int, forcar_ids: bool = False) -> list[dict]:
    TXT.mkdir(parents=True, exist_ok=True)
    PDFS.mkdir(parents=True, exist_ok=True)
    indice = _carrega(INDICE, {})
    corte = (date.today() - timedelta(days=dias)).isoformat()
    novos = []
    with gringas.Portal("jpm", headless=True) as p:
        p.page.goto(AQUECE, timeout=60000, wait_until="domcontentloaded")
        p.page.wait_for_timeout(4000)
        ids = resolver(p, forcar_ids)
        vistos = {}                                   # id do documento -> tickers da cobertura que o trazem
        docs = {}
        for tk, cid in ids.items():
            if not cid:
                continue
            r = _artigos(p, cid)
            for d in (r[1] if r else []):
                dt = _data(d.get("publicationDate", ""))
                if not dt or dt < corte or d.get("documentType") not in (None, "Document"):
                    continue
                vistos.setdefault(d["id"], []).append(tk)
                docs[d["id"]] = d
        for did, d in sorted(docs.items(), key=lambda x: _data(x[1].get("publicationDate", ""))):
            if did in indice:
                continue
            r = p.ctx.request.get(PDF_URL.format(id=did), timeout=120000)
            dados = r.body()
            if dados[:4] != b"%PDF":
                if r.status in (401, 403) or b"login" in dados[:3000].lower():
                    raise SessaoVencida(f"PDF {did}: {r.status}")
                print(f"  {did}: sem PDF ({r.status}, {len(dados)} bytes) — pulado", file=sys.stderr)
                continue
            texto, npag = _texto_pdf(dados)
            (PDFS / f"{did}.pdf").write_bytes(dados)
            (TXT / f"{did}.txt").write_text("FONTE: PDF completo\n\n" + texto, encoding="utf-8")
            an = ((d.get("analysts") or {}).get("results") or [])
            prim = next((a for a in an if a.get("primary")), an[0] if an else {})
            mr = RATING.search(texto[:2500])
            titulo = " - ".join(x for x in (d.get("title"), d.get("subtitle")) if x)
            reg = {"id": did, "casa": "JPMorgan", "titulo": titulo[:200], "empresa": d.get("title") or "",
                   "analista": prim.get("displayName", ""), "rating": mr.group(1) if mr else "",
                   "data": _data(d.get("publicationDate", "")), "paginas": npag, "chars": len(texto),
                   "coletado": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "arquivo": f"{did}.pdf",
                   "sha1": hashlib.sha1(dados).hexdigest()[:16], "cobertura_jpm": vistos.get(did, []),
                   "sinopse": (d.get("synopsis") or "")[:400], "fonte_url": PDF_URL.format(id=did)}
            indice[did] = reg
            _grava(INDICE, indice)                     # grava a cada um: se cair no meio, não perde o que já veio
            novos.append(reg)
            print(f"  + {reg['data']} [{', '.join(reg['cobertura_jpm'])}] {titulo[:80]} · {npag}p")
    return novos


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Coletor de research do J.P. Morgan Markets (headless).")
    ap.add_argument("--dias", type=int, default=3, help="janela de publicação (padrão 3 dias)")
    ap.add_argument("--resolver", action="store_true", help="refaz o mapa ticker -> código JPM")
    a = ap.parse_args()
    try:
        novos = coletar(a.dias, a.resolver)
    except SessaoVencida as e:
        aviso_sessao(str(e))
        return 3
    print(f"JPMorgan: {len(novos)} relatório(s) novo(s) em {a.dias} dia(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
