"""Coletor de relatórios novos do portal BTG Research (btgpactual.com/research).

    python btg_relatorios.py --login          # 1ª vez (ou quando a sessão expirar): abre janela, você faz login, fecha sozinho
    python btg_relatorios.py                  # rotina: lista os últimos 100, baixa os novos (PDF + texto), avisa no Telegram
    python btg_relatorios.py --dias 7 --max 60

O que grava (data/btg/, fora do git):
    pdf/<id>.pdf, txt/<id>.txt      relatório e texto extraído (pypdf)
    indice.json                     {id: {data, empresa, titulo, analista, rating, kb, paginas, chars, destaque, coletado}}
    novos.json                      os relatórios encontrados NESTA passada (a leitura na nuvem/sessão parte daqui)
Se existir o repositório privado ../btg-research (git), copia índice + textos novos para lá e faz push — é o que a
rotina na nuvem "BTG · leitura" lê. Agendamento: agendar_btg.ps1 (07:40, 12:40 e 18:40 em dias úteis).
"""
from __future__ import annotations

import argparse
import io
import json
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta

import config
from fontes import btg, telegram

DIR = config.DATA / "btg"
PDF, TXT = DIR / "pdf", DIR / "txt"
INDICE, NOVOS, ESTADO = DIR / "indice.json", DIR / "novos.json", DIR / "estado.json"
ESPELHO = config.RAIZ.parent / "btg-research"          # repo privado opcional (lido pela rotina na nuvem)

# O que merece estrela no aviso: cobertura, carteira 10SIM, estratégia/macro Brasil, setores acompanhados.
DESTAQUE = re.compile(r"10SIM|Rede D'?Or|RDOR|Hapvida|SAUD3|Localiza|RENT3|Cyrela|CYRE3|Cury|Brazil Strategy|Ibovespa|"
                      r"Bovespa|Health ?care|Real Estate|Homebuilder|Macroeconomic Research\s*-\s*Brazil|Brazil Macro|"
                      r"Equity Strategy|Petrobras|Vale\b|Itaú|Itau", re.I)
MAX_PAGINAS = 80


def _limpa(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def texto_pdf(dados: bytes) -> tuple[str, int]:
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
    return txt, n


def _carrega(arq, padrao):
    return json.loads(arq.read_text(encoding="utf-8")) if arq.exists() else padrao


def _grava(arq, obj):
    arq.parent.mkdir(parents=True, exist_ok=True)
    arq.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def aviso_sessao() -> None:
    """Sessão do portal expirou: avisa uma vez por dia."""
    est = _carrega(ESTADO, {})
    hoje = date.today().isoformat()
    print("sessão do portal BTG expirada: rode `python btg_relatorios.py --login`", file=sys.stderr)
    if est.get("aviso_sessao") != hoje and telegram.disponivel():
        telegram.enviar("<b>BTG Research</b> · a sessão do portal expirou. No laptop: <code>python btg_relatorios.py --login</code>")
        est["aviso_sessao"] = hoje
        _grava(ESTADO, est)


def espelhar(novos: list[dict]) -> bool:
    """Copia índice, novos.json e os textos novos para o repo privado ../btg-research e faz push."""
    if not (ESPELHO / ".git").exists():
        return False
    (ESPELHO / "txt").mkdir(exist_ok=True)
    shutil.copy2(INDICE, ESPELHO / "indice.json")
    shutil.copy2(NOVOS, ESPELHO / "novos.json")
    for m in novos:
        src = TXT / f"{m['id']}.txt"
        if src.exists():
            shutil.copy2(src, ESPELHO / "txt" / src.name)
    def git(*a):
        return subprocess.run(["git", *a], cwd=str(ESPELHO), capture_output=True, text=True, timeout=180)
    git("add", "-A")
    if git("diff", "--cached", "--quiet").returncode == 0:
        return True
    git("commit", "-q", "-m", f"btg: {len(novos)} relatórios novos {datetime.now():%Y-%m-%d %H:%M}")
    git("pull", "--rebase", "-q", "origin", "main")
    r = git("push", "-q", "origin", "main")
    if r.returncode:
        print(f"push do espelho falhou: {r.stderr.strip()[:200]}", file=sys.stderr)
    return r.returncode == 0


def avisar(novos: list[dict]) -> None:
    if not novos or not telegram.disponivel():
        return
    ordem = sorted(novos, key=lambda m: (not m["destaque"], m["data"]), reverse=False)
    linhas = []
    for m in ordem[:20]:
        est = "⭐ " if m["destaque"] else "• "
        linhas.append(f"{est}<b>{m['empresa']}</b> — {m['titulo']} <i>({m['analista']})</i>")
    if len(novos) > 20:
        linhas.append(f"… e mais {len(novos) - 20}")
    dez = [m for m in novos if "10SIM" in m["titulo"].upper()]
    cab = f"<b>BTG Research</b> · {len(novos)} relatório(s) novo(s)"
    if dez:
        cab += " · <b>10SIM novo!</b>"
    telegram.enviar(cab + "\n" + "\n".join(linhas))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", action="store_true", help="abre janela para você fazer login no portal")
    ap.add_argument("--dias", type=int, default=3, help="só relatórios publicados nos últimos N dias (padrão 3)")
    ap.add_argument("--max", type=int, default=40, help="máximo de PDFs por passada")
    ap.add_argument("--headed", action="store_true", help="mostra a janela do navegador")
    ap.add_argument("--sem-telegram", action="store_true")
    ap.add_argument("--sem-espelho", action="store_true")
    args = ap.parse_args()

    if args.login:
        return 0 if btg.login_interativo() else 1

    for d in (PDF, TXT):
        d.mkdir(parents=True, exist_ok=True)
    indice = _carrega(INDICE, {})
    limite = (date.today() - timedelta(days=args.dias)).isoformat()
    novos: list[dict] = []
    with btg.Portal(headless=not args.headed) as p:
        if not p.abrir_lista():
            aviso_sessao()
            return 1
        docs = p.listar(100)
        cand = [d for d in docs if str(d.get("document_id")) not in indice and (d.get("publish_date") or "")[:10] >= limite]
        print(f"{len(docs)} listados, {len(cand)} novos (últimos {args.dias} dias)")
        for d in sorted(cand, key=lambda d: d["publish_date"])[-args.max:]:       # do mais antigo ao mais novo
            id_ = int(d["document_id"])
            meta = {"id": id_, "data": d["publish_date"][:10], "empresa": _limpa(d.get("company_sector")),
                    "titulo": _limpa(d.get("title")), "analista": _limpa(d.get("main_analyst")), "rating": d.get("rating"),
                    "kb": int(d.get("file_size") or 0), "paginas": None, "chars": 0, "erro": None,
                    "coletado": datetime.now().strftime("%Y-%m-%d %H:%M")}
            meta["destaque"] = bool(DESTAQUE.search(meta["empresa"] + " " + meta["titulo"]))
            try:
                dados = p.pdf(id_)
                (PDF / f"{id_}.pdf").write_bytes(dados)
                txt, n = texto_pdf(dados)
                (TXT / f"{id_}.txt").write_text(f"{meta['empresa']} — {meta['titulo']} ({meta['analista']}, {meta['data']})\n\n{txt}", encoding="utf-8")
                meta["paginas"], meta["chars"] = n, len(txt)
            except Exception as e:
                meta["erro"] = str(e)[:200]
                print(f"  {id_}: {meta['erro']}", file=sys.stderr)
            indice[str(id_)] = meta
            novos.append(meta)
            print(f"  {meta['data']} {meta['empresa'][:40]:40} {meta['titulo'][:60]} ({meta['paginas']} p.)")
    _grava(INDICE, indice)
    _grava(NOVOS, {"hora": datetime.now().strftime("%Y-%m-%d %H:%M"), "novos": novos})
    if not args.sem_telegram:
        avisar(novos)
    if not args.sem_espelho and novos:
        espelhar(novos)
    print(f"{len(novos)} novos; índice com {len(indice)} relatórios")
    return 0


if __name__ == "__main__":
    sys.exit(main())
