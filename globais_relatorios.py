"""Ingestor de relatórios das casas GLOBAIS (JPMorgan, Goldman Sachs, BofA, Morgan Stanley...).

As globais NÃO têm portal web para raspar: os relatórios chegam por PDF (salvos no navegador ou
encaminhados por e-mail). Este módulo não usa Chrome nem login — ele LÊ PDFs de uma pasta de entrada e
registra o texto COMPLETO (regra do projeto: nunca só o resumo), detectando a casa pelo conteúdo do PDF.

    python globais_relatorios.py                 # processa a pasta inbox + PDFs recentes do Downloads
    python globais_relatorios.py --inbox "C:/caminho/para/pasta"
    python globais_relatorios.py --minutos 240   # janela de idade dos PDFs no Downloads
    python globais_relatorios.py --sem-downloads  # só a pasta inbox

Onde salvar/encaminhar os PDFs:
    btg-research/globais/inbox/   -> pasta dedicada (crie um atalho; arraste o PDF para cá)
    Downloads                     -> varrido por padrão (PDFs dos últimos `--minutos`)

O que grava (em btg-research/globais/, que a leitura.py já lê como 4ª fonte):
    txt/<id>.txt      "FONTE: PDF completo" + texto extraído (pypdf)
    pdf/<id>.pdf      arquivo arquivado (dedupe por hash do conteúdo)
    indice.json       {id: {data, casa, empresa, titulo, analista, rating, paginas, chars, coletado, arquivo}}
    processados.json  hashes já ingeridos (evita reprocessar o mesmo PDF do Downloads)

A casa é detectada pelo timbre/disclaimer da 1ª página. PDF sem casa reconhecida fica de lado (não é
apagado do Downloads) e aparece em "sem dono" no resumo — aí a gente adiciona o padrão da casa.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

import config

ESPELHO = config.RAIZ.parent / "btg-research" / "globais"
TXT, PDF = ESPELHO / "txt", ESPELHO / "pdf"
INDICE, PROCESSADOS = ESPELHO / "indice.json", ESPELHO / "processados.json"
DOWNLOADS = Path.home() / "Downloads"
MAX_PAGINAS = 1000   # sem corte por página: a leitura corta nos avisos legais (fontes/recorte.py)

# Casa detectada pelo CONTEÚDO (timbre/disclaimer da 1ª página). Padrões específicos para não colidir
# ("J.P. Morgan" x "Morgan Stanley" não se confundem). Ordem = prioridade. Extensível.
CASAS: list[tuple[str, re.Pattern]] = [
    ("Morgan Stanley", re.compile(r"Morgan\s+Stanley", re.I)),
    ("JPMorgan",       re.compile(r"J\.?\s*P\.?\s*Morgan", re.I)),
    ("Goldman Sachs",  re.compile(r"Goldman\s+Sachs", re.I)),
    ("BofA",           re.compile(r"BofA\b|Bank\s+of\s+America|Merrill\s+Lynch|BofA\s+Global\s+Research", re.I)),
]
RATING = re.compile(r"\b(Overweight|Underweight|Equal[- ]?weight|Neutral|Buy|Sell|Hold|"
                    r"Outperform|Underperform|Market\s+Perform)\b", re.I)


def _carrega(arq: Path, padrao):
    return json.loads(arq.read_text(encoding="utf-8")) if arq.exists() else padrao


def _grava(arq: Path, obj):
    arq.parent.mkdir(parents=True, exist_ok=True)
    arq.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def texto_pdf(dados: bytes) -> tuple[str, int, dict]:
    """Devolve (texto, nº de páginas, metadados). Espelha btg_relatorios.texto_pdf."""
    from pypdf import PdfReader
    r = PdfReader(io.BytesIO(dados))
    n = len(r.pages)
    partes = []
    for i, pg in enumerate(r.pages[:MAX_PAGINAS]):
        try:
            t = pg.extract_text() or ""
        except Exception:
            t = ""
        partes.append(f"[página {i + 1}]\n{t.strip()}")
    txt = re.sub(r"\n{3,}", "\n\n", "\n\n".join(partes))
    meta = {}
    try:
        md = r.metadata or {}
        meta = {"titulo": (md.get("/Title") or "").strip(), "data": (md.get("/CreationDate") or "")}
    except Exception:
        pass
    return txt, n, meta


def detecta_casa(texto: str) -> str | None:
    """Casa pela assinatura no texto (1ª página costuma bastar; varre os 6000 primeiros caracteres)."""
    amostra = texto[:6000]
    for nome, rx in CASAS:
        if rx.search(amostra):
            return nome
    return None


def _data_pdf(meta_data: str) -> str:
    """D:YYYYMMDD... -> YYYY-MM-DD; senão hoje."""
    m = re.search(r"(\d{4})(\d{2})(\d{2})", meta_data or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else datetime.now().strftime("%Y-%m-%d")


def _titulo(meta_titulo: str, texto: str) -> str:
    """Título dos metadados; senão a 1ª linha significativa da página 1."""
    t = (meta_titulo or "").strip()
    if len(t) >= 15 and not t.lower().endswith(".pdf"):
        return t[:160]
    corpo = re.sub(r"^\[página \d+\]\n", "", texto, flags=re.M)
    for linha in corpo.splitlines():
        linha = linha.strip()
        if len(linha) >= 25 and not re.match(r"^(page|página|\d+$)", linha, re.I):
            return linha[:160]
    return "(sem título)"


def ingerir(pdfs: list[Path], mover: bool) -> dict:
    """Processa uma lista de PDFs. `mover`=True arquiva o PDF (pasta inbox); False só copia (Downloads)."""
    TXT.mkdir(parents=True, exist_ok=True)
    PDF.mkdir(parents=True, exist_ok=True)
    indice = _carrega(INDICE, {})
    processados = set(_carrega(PROCESSADOS, []))
    novos, sem_dono, repetidos = [], [], 0

    for p in pdfs:
        try:
            dados = p.read_bytes()
        except Exception as e:
            print(f"[{p.name}] falha ao ler: {e}", file=sys.stderr)
            continue
        h = hashlib.sha1(dados).hexdigest()[:16]
        if h in processados or h in indice:
            repetidos += 1
            continue
        try:
            texto, paginas, meta = texto_pdf(dados)
        except Exception as e:
            print(f"[{p.name}] falha ao extrair texto: {e}", file=sys.stderr)
            continue
        casa = detecta_casa(texto)
        if not casa:
            sem_dono.append(p.name)
            processados.add(h)                       # não reprocessa a cada passada; registra como visto
            continue
        mr = RATING.search(texto[:4000])
        reg = {
            "id": h, "casa": casa,
            "titulo": _titulo(meta.get("titulo", ""), texto),
            "empresa": "", "analista": "",
            "rating": mr.group(1) if mr else "",
            "data": _data_pdf(meta.get("data", "")),
            "paginas": paginas, "chars": len(texto),
            "coletado": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "arquivo": p.name,
        }
        (TXT / f"{h}.txt").write_text("FONTE: PDF completo\n\n" + texto, encoding="utf-8")
        shutil.copy2(p, PDF / f"{h}.pdf")
        indice[h] = reg
        processados.add(h)
        novos.append(reg)
        if mover:
            try:
                p.unlink()                           # da inbox pode remover; do Downloads nunca
            except Exception:
                pass

    _grava(INDICE, indice)
    _grava(PROCESSADOS, sorted(processados))
    return {"novos": novos, "sem_dono": sem_dono, "repetidos": repetidos}


def _recentes(pasta: Path, minutos: int) -> list[Path]:
    if not pasta.exists():
        return []
    lim = datetime.now().timestamp() - minutos * 60
    return [p for p in pasta.glob("*.pdf") if p.stat().st_mtime >= lim]


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingere PDFs das casas globais (sem portal).")
    ap.add_argument("--inbox", default=str(ESPELHO / "inbox"), help="pasta de entrada dedicada")
    ap.add_argument("--minutos", type=int, default=240, help="idade máxima dos PDFs varridos do Downloads")
    ap.add_argument("--sem-downloads", action="store_true", help="não varre o Downloads, só a inbox")
    a = ap.parse_args()

    inbox = Path(a.inbox)
    inbox.mkdir(parents=True, exist_ok=True)
    pdfs_inbox = sorted(inbox.glob("*.pdf"))
    pdfs_dl = [] if a.sem_downloads else _recentes(DOWNLOADS, a.minutos)

    r1 = ingerir(pdfs_inbox, mover=True)
    r2 = ingerir(pdfs_dl, mover=False)
    novos = r1["novos"] + r2["novos"]
    sem_dono = r1["sem_dono"] + r2["sem_dono"]

    por_casa = {}
    for n in novos:
        por_casa[n["casa"]] = por_casa.get(n["casa"], 0) + 1
    print(f"Globais: {len(novos)} novos " + (f"({', '.join(f'{k} {v}' for k, v in por_casa.items())})" if por_casa else "")
          + f" · {r1['repetidos'] + r2['repetidos']} repetidos"
          + (f" · {len(sem_dono)} sem casa reconhecida: {', '.join(sem_dono[:5])}" if sem_dono else ""))
    for n in novos:
        print(f"  + [{n['casa']}] {n['titulo'][:70]} · {n['paginas']}p · {n['chars']} car.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
