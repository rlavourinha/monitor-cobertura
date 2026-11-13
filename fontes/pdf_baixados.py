"""PDFs de relatórios de sell-side baixados pelo Chrome (pasta Downloads) -> texto COMPLETO em txt/<id>.txt.

Regra do usuário (13/11/2026): resumo e análise de relatório saem do documento inteiro, nunca do resumo do portal.
Os coletores em modo Chrome (Itaú BBA e Bradesco BBI) clicam no download do PDF de cada relatório em destaque; este
módulo acha os PDFs recentes na pasta Downloads, casa cada um com o relatório pelo título (que aparece na 1ª página),
grava o texto inteiro (sem as páginas de disclosure) em txt/<id>.txt e move o PDF para pdf/<id>.pdf.

O Chrome só deixa UM download automático por aba: cada PDF tem que ser pedido numa aba nova.
"""
from __future__ import annotations

import re
import shutil
import time
import unicodedata
from pathlib import Path

DOWNLOADS = Path.home() / "Downloads"
MARCA_COMPLETO = "FONTE: PDF completo"
# primeira ocorrência (depois do começo) marca o fim do conteúdo e o início dos disclosures
FIM = ("Analyst Certification", "ANALYST CERTIFICATION", "Important Disclosures", "IMPORTANT DISCLOSURES",
       "Ratings: Definitions, Dispersion", "Third-Party Disclosures", "Required Disclosure", "Material Information")
MAX_CHARS = 60000


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _chaves(titulo: str) -> list[str]:
    """Título inteiro e a parte depois de 'Casa on Empresa: ' / 'Tipo: ' (a 1ª página nem sempre repete o prefixo)."""
    t = (titulo or "").strip()
    cand = [t]
    if ": " in t:
        cand.append(t.split(": ", 1)[1])
    return [k[:70] for k in (_norm(c) for c in cand) if len(k) >= 12]


def _paginas(pdf: Path) -> list[str]:
    from pypdf import PdfReader
    return [(p.extract_text() or "") for p in PdfReader(str(pdf)).pages]


def _conteudo(pags: list[str]) -> str:
    txt = "\n\n".join(f"[página {i}]\n{p.strip()}" for i, p in enumerate(pags, 1))
    cortes = [i for i in (txt.find(k, 600) for k in FIM) if i > 0]
    if cortes:
        txt = txt[:min(cortes)]
    txt = re.sub(r"[ \t]+\n", "\n", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
    return txt[:MAX_CHARS]


def e_completo(txt_path: Path) -> bool:
    try:
        return txt_path.read_text(encoding="utf-8", errors="replace")[:60].startswith(MARCA_COMPLETO)
    except OSError:
        return False


def casar(metas: list[dict], dir_txt: Path, dir_pdf: Path, minutos: int = 60, downloads: Path | None = None) -> dict:
    """metas = [{'id', 'titulo'}]. Devolve {'casados': {id: chars}, 'sem_pdf': [ids], 'pdfs_sem_dono': [nomes]}."""
    downloads = downloads or DOWNLOADS
    agora = time.time()
    pdfs = sorted((p for p in downloads.glob("*.pdf") if agora - p.stat().st_mtime <= minutos * 60),
                  key=lambda p: p.stat().st_mtime)
    lidos = []
    for p in pdfs:
        try:
            pags = _paginas(p)
        except Exception as e:  # PDF ainda sendo gravado, corrompido ou protegido
            print(f"  não li {p.name}: {type(e).__name__}")
            continue
        lidos.append((p, pags, _norm(" ".join(pags[:2]))))
    casados, usados = {}, set()
    for m in metas:
        chaves = _chaves(m.get("titulo") or m.get("t") or "")
        melhor = None
        for i, (p, pags, cab) in enumerate(lidos):
            if i in usados:
                continue
            achou = max((len(k) for k in chaves if k in cab), default=0)
            if achou and (melhor is None or achou > melhor[0]):
                melhor = (achou, i)
        if melhor is None:
            continue
        i = melhor[1]
        usados.add(i)
        p, pags, _ = lidos[i]
        corpo = _conteudo(pags)
        if len(corpo) < 400:
            continue
        dir_txt.mkdir(parents=True, exist_ok=True)
        dir_pdf.mkdir(parents=True, exist_ok=True)
        (dir_txt / f"{m['id']}.txt").write_text(f"{MARCA_COMPLETO} ({len(pags)} páginas)\n\n{corpo}", encoding="utf-8")
        shutil.move(str(p), str(dir_pdf / f"{m['id']}.pdf"))
        casados[m["id"]] = len(corpo)
        print(f"  {m['id'][:8]}: PDF completo, {len(pags)} páginas, {len(corpo)} chars  <- {p.name[:60]}")
    sem = [m["id"] for m in metas if m["id"] not in casados]
    orfaos = [p.name for i, (p, _, _) in enumerate(lidos) if i not in usados]
    return {"casados": casados, "sem_pdf": sem, "pdfs_sem_dono": orfaos}
