"""Coletor de research do Morgan Stanley (Matrix), sem janela. Sessão: `python probe_gringas.py --login ms` (entre e
feche a janela). O cookie de autenticação do Matrix é "de sessão" (o Chrome descarta ao fechar): o login grava todos os
cookies em .playwright/gringas-ms-sessao.json e o Portal reinjeta. O portal informa ~21 dias de validade.

Para cada empresa do cobertura.json com cobertura no MS:
  - relatórios recentes (busca do próprio portal, company==RIC) -> PDF COMPLETO -> texto onde a leitura.py já lê:
        btg-research/globais/txt/<uuid>.txt + indice.json (casa "Morgan Stanley");  PDF em Claude/research-pdfs/ms/
  - modelos (Excel do analista): cada versão nova vai para Claude/research-pdfs/ms/modelos/<RIC>_<data>_<arquivo>
    (histórico; fora de git). Não entram na leitura.

    python ms_relatorios.py               # últimos 3 dias
    python ms_relatorios.py --dias 10
    python ms_relatorios.py --resolver    # refaz o mapa ticker -> RIC do MS (globais/ms_ids.json)
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

H = "https://ny.matrix.ms.com"
BUSCA = H + "/eqr/research/webapp/portalservices/portal-content-service/search/reports"
LOOKUP = H + "/eqr/research/webapp/portalservices/portal-data-service/data/lookupall/allbylanguagecode"
ART = H + "/eqr/article/webapp/services/published/"
HOME = H + "/eqr/research/portal/home/global"
IDS, ESTADO = ESPELHO / "ms_ids.json", ESPELHO / "ms_estado.json"
PDFS = config.RAIZ.parent / "research-pdfs" / "ms"
MODELOS = PDFS / "modelos"
# O MS cobre várias brasileiras pela ADR, não pelo papel da B3
ADR = {"ITUB4": "ITUB.N", "BBDC4": "BBD.N", "VIVT3": "VIV.N", "TIMS3": "TIMB.N", "SANB11": "BSBR.N",
       "NU": "NU.N", "XP": "XP.O", "INTR": "INTR.O", "V": "V.N", "MA": "MA.N", "VZ": "VZ.N", "TEF": "TEF.MC"}
RATING = re.compile(r"\b(Overweight|Underweight|Equal-?weight|Not-Rated)\b", re.I)


def _post_busca(p, consulta: str, tam: int = 50) -> list[dict]:
    corpo = {"search": consulta, "sort": "d", "noSearch": False, "gn": False, "didyoumean": False, "countMode": "best",
             "showcard": False, "page": 1, "size": tam}
    r = p.ctx.request.post(BUSCA, data=json.dumps(corpo), headers={"content-type": "application/json",
                                                                    "accept": "application/json, text/plain, */*"}, timeout=90000)
    try:
        j = r.json()
    except Exception:
        raise SessaoVencida(f"busca MS {r.status} não-JSON (login?)")
    return j.get("reportcards") or []


def resolver(p, forcar: bool = False) -> dict:
    ids = {} if forcar else _carrega(IDS, {})
    emp = json.loads((config.RAIZ / "cobertura.json").read_text(encoding="utf-8"))["empresas"]
    faltam = [tk for tk in emp if tk not in ids]
    if not faltam:
        return ids
    r = p.ctx.request.get(LOOKUP, timeout=90000)
    try:
        comp = {c["id"] for c in next(g for g in r.json() if g.get("type") == "COMPANY")["sl"]}
    except Exception:
        raise SessaoVencida(f"lookup MS {r.status}")
    for tk in faltam:
        cands = [ADR[tk]] if tk in ADR else [f"{tk}.SA"]
        ids[tk] = next((c for c in cands if c in comp), None)
        print(f"  {tk:7s} -> {ids[tk] or 'sem cobertura no MS'}")
    _grava(IDS, ids)
    return ids


def _pdf(p, uuid: str) -> bytes | None:
    fm = p.ctx.request.get(f"{ART}article/frontmatter?uuid={uuid}", timeout=60000).text()
    cob = re.search(r'cobalt\w*"?\s*[:=]\s*"?([0-9a-f-]{20,})', fm, re.I)
    fn = re.search(r"[A-Za-z0-9]+_\d{8}_\d{4}\.pdf", fm)
    if not (cob and fn):
        return None
    b = p.ctx.request.get(f"{ART}rendition/pdf/{fn.group(0)}?cobaltId={cob.group(1)}&uuid={uuid}", timeout=120000).body()
    return b if b[:4] == b"%PDF" else None


def coletar(dias: int, forcar_ids: bool = False) -> tuple[list[dict], list[str]]:
    TXT.mkdir(parents=True, exist_ok=True)
    PDFS.mkdir(parents=True, exist_ok=True)
    MODELOS.mkdir(parents=True, exist_ok=True)
    indice = _carrega(INDICE, {})
    est = _carrega(ESTADO, {})
    vistos_mod = set(est.get("modelos", []))
    d1, d0 = date.today() + timedelta(days=1), date.today() - timedelta(days=dias)
    novos, mods = [], []
    with gringas.Portal("ms", headless=True) as p:
        p.page.goto(HOME, timeout=90000, wait_until="domcontentloaded")
        p.page.wait_for_timeout(4000)
        if "login" in p.page.url:
            raise SessaoVencida("portal redirecionou para o login")
        ids = resolver(p, forcar_ids)
        docs, quem = {}, {}
        for tk, ric in ids.items():
            if not ric:
                continue
            for c in _post_busca(p, f"date==1M={d0:%d/%m/%Y}..{d1:%d/%m/%Y};(company=={ric})"):
                docs[c["id"]] = c
                quem.setdefault(c["id"], []).append(tk)
        for uid, c in sorted(docs.items(), key=lambda x: x[1].get("pd", "")):
            data_pub = (c.get("pd") or "")[:10]
            modelo = c.get("pcat1") == "Model" or "xls" in (c.get("dt") or "")
            if modelo:
                if uid in vistos_mod:
                    continue
                r = p.ctx.request.get(f"{ART}rendition/excel?uuid={uid}", timeout=180000)
                b = r.body()
                if b[:2] != b"PK" and b[:4] != b"\xd0\xcf\x11\xe0":
                    print(f"  modelo {uid}: sem arquivo ({r.status})", file=sys.stderr)
                    continue
                nome = re.search(r'filename="?([^";]+)', r.headers.get("content-disposition", ""))
                arq = MODELOS / f"{c.get('pt') or quem[uid][0]}_{data_pub}_{nome.group(1) if nome else uid + '.xlsm'}"
                arq.write_bytes(b)
                vistos_mod.add(uid)
                mods.append(arq.name)
                print(f"  modelo {data_pub} {arq.name} ({len(b) // 1024} KB)")
                continue
            if uid in indice:
                continue
            dados = _pdf(p, uid)
            if not dados:
                print(f"  {uid}: sem PDF — pulado ({(c.get('hl') or '')[:60]})", file=sys.stderr)
                continue
            texto, npag = _texto_pdf(dados)
            (PDFS / f"{uid}.pdf").write_bytes(dados)
            (TXT / f"{uid}.txt").write_text("FONTE: PDF completo\n\n" + texto, encoding="utf-8")
            mr = RATING.search(texto[:2500])
            reg = {"id": uid, "casa": "Morgan Stanley", "titulo": (c.get("hl") or "")[:200], "empresa": c.get("topic") or "",
                   "analista": ((c.get("a") or {}).get("n") or "").strip(), "rating": mr.group(1) if mr else "",
                   "data": data_pub, "paginas": npag, "chars": len(texto),
                   "coletado": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "arquivo": f"{uid}.pdf",
                   "sha1": hashlib.sha1(dados).hexdigest()[:16], "cobertura_ms": quem.get(uid, []),
                   "sinopse": re.sub(r"\s+", " ", c.get("ab") or "")[:400], "fonte_url": H + (c.get("reportUrl") or "")}
            indice[uid] = reg
            _grava(INDICE, indice)
            novos.append(reg)
            print(f"  + {data_pub} [{', '.join(reg['cobertura_ms'])}] {reg['titulo'][:80]} · {npag}p")
    est["modelos"] = sorted(vistos_mod)
    _grava(ESTADO, est)
    return novos, mods


def modelos_base() -> list[str]:
    """Última versão do modelo de cada empresa coberta (busca de 12 meses), se ainda não baixada. Para a carga inicial."""
    MODELOS.mkdir(parents=True, exist_ok=True)
    est = _carrega(ESTADO, {})
    vistos = set(est.get("modelos", []))
    d1, d0 = date.today() + timedelta(days=1), date.today() - timedelta(days=365)
    out = []
    with gringas.Portal("ms", headless=True) as p:
        p.page.goto(HOME, timeout=90000, wait_until="domcontentloaded")
        p.page.wait_for_timeout(4000)
        for tk, ric in resolver(p).items():
            if not ric:
                continue
            cs = _post_busca(p, f"date==1Y={d0:%d/%m/%Y}..{d1:%d/%m/%Y};reporttype==Model;(primaryric=={ric})", 5)
            if not cs:
                print(f"  {tk:7s} sem modelo em 12 meses")
                continue
            c = cs[0]
            if c["id"] in vistos:
                print(f"  {tk:7s} já tenho o último ({c.get('pd', '')[:10]})")
                continue
            r = p.ctx.request.get(f"{ART}rendition/excel?uuid={c['id']}", timeout=180000)
            b = r.body()
            if b[:2] != b"PK" and b[:4] != b"\xd0\xcf\x11\xe0":
                print(f"  {tk:7s} sem arquivo ({r.status})")
                continue
            nome = re.search(r'filename="?([^";]+)', r.headers.get("content-disposition", ""))
            arq = MODELOS / f"{ric}_{c.get('pd', '')[:10]}_{nome.group(1) if nome else c['id'] + '.xlsm'}"
            arq.write_bytes(b)
            vistos.add(c["id"])
            out.append(arq.name)
            print(f"  {tk:7s} {arq.name} ({len(b) // 1024} KB)")
    est["modelos"] = sorted(vistos)
    _grava(ESTADO, est)
    return out


def aviso_sessao(motivo: str) -> None:
    est = _carrega(ESTADO, {})
    hoje = date.today().isoformat()
    print(f"sessão do Morgan Stanley vencida ({motivo}): rode `python probe_gringas.py --login ms`", file=sys.stderr)
    try:
        from fontes import telegram
        if est.get("aviso_sessao") != hoje and telegram.disponivel():
            telegram.enviar("<b>Morgan Stanley Research</b> · a sessão do Matrix venceu. No laptop: "
                            "<code>python probe_gringas.py --login ms</code> (entre e feche a janela)")
            est["aviso_sessao"] = hoje
            _grava(ESTADO, est)
    except Exception as e:
        print(f"  telegram: {e}", file=sys.stderr)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Coletor de research do Morgan Stanley Matrix (headless).")
    ap.add_argument("--dias", type=int, default=3)
    ap.add_argument("--resolver", action="store_true")
    ap.add_argument("--modelos-base", action="store_true", help="baixa a última versão do modelo de cada empresa coberta")
    a = ap.parse_args()
    try:
        if a.modelos_base:
            print(f"Morgan Stanley: {len(modelos_base())} modelo(s) baixado(s) na carga inicial")
            return 0
        novos, mods = coletar(a.dias, a.resolver)
    except SessaoVencida as e:
        aviso_sessao(str(e))
        return 3
    print(f"Morgan Stanley: {len(novos)} relatório(s) e {len(mods)} modelo(s) novo(s) em {a.dias} dia(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
