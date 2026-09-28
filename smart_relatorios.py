"""Coletor de relatórios novos do portal Itaú BBA Smart. Irmão de btg_relatorios.py (mesmos modos, mesmos arquivos).

    python smart_relatorios.py --login          # 1ª vez (ou quando a sessão expirar): abre janela, você faz login
    python smart_relatorios.py                  # rotina: lista os últimos 100, baixa os novos (PDF + texto), avisa no Telegram
    python smart_relatorios.py --dias 7 --max 60

O que grava (data/smart/, fora do git):
    pdf/<id>.pdf, txt/<id>.txt      relatório e texto extraído (pypdf)
    indice.json                     {id: {data, empresa, titulo, analista, rating, kb, paginas, chars, destaque, coletado}}
    novos.json                      os relatórios encontrados NESTA passada (a leitura na nuvem parte daqui)
Se existir o repositório privado ../smart-research (git), copia índice + textos novos para lá e faz push — é o que a
rotina na nuvem "Smart · leitura" lê. Agendamento: agendar_smart.ps1 (dias úteis, 10 min depois do BTG).

A API interna do Smart ainda precisa ser mapeada na aba logada: ver fontes/smart.py (MAPA e CAMPOS). Até lá, o modo
Playwright para com mensagem clara; o modo Chrome (--novos/--clip/--registrar) já funciona com a lista normalizada
[{id, d, co, t, an, rating, kb}] que a página devolver.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta

import config
from btg_relatorios import DESTAQUE, EXCLUI, _carrega, _grava, texto_pdf
from fontes import smart, telegram

DIR = config.DATA / "smart"
PDF, TXT = DIR / "pdf", DIR / "txt"
INDICE, NOVOS, ESTADO = DIR / "indice.json", DIR / "novos.json", DIR / "estado.json"
ESPELHO = config.RAIZ.parent / "smart-research"        # repo privado opcional (lido pela rotina na nuvem)
NOME = "Itaú BBA Smart"


def _limpa(s: str | None) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def aviso_sessao() -> None:
    """Sessão do portal expirou: avisa uma vez por dia."""
    est = _carrega(ESTADO, {})
    hoje = date.today().isoformat()
    print("sessão do Smart expirada: rode `python smart_relatorios.py --login`", file=sys.stderr)
    if est.get("aviso_sessao") != hoje and telegram.disponivel():
        telegram.enviar(f"<b>{NOME}</b> · a sessão do portal expirou. No laptop: <code>python smart_relatorios.py --login</code>")
        est["aviso_sessao"] = hoje
        _grava(ESTADO, est)


def espelhar(novos: list[dict]) -> bool:
    """Copia índice, novos.json e os textos novos para o repo privado ../smart-research e faz push."""
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
    git("commit", "-q", "-m", f"smart: {len(novos)} relatórios novos {datetime.now():%Y-%m-%d %H:%M}")
    git("pull", "--rebase", "-q", "origin", "main")
    r = git("push", "-q", "origin", "main")
    if r.returncode:
        print(f"push do espelho falhou: {r.stderr.strip()[:200]}", file=sys.stderr)
    return r.returncode == 0


def avisar(novos: list[dict]) -> None:
    if not novos or not telegram.disponivel():
        return
    ordem = sorted(novos, key=lambda m: (not m["destaque"], m["data"]))
    linhas = []
    for m in ordem[:20]:
        est = "⭐ " if m["destaque"] else "• "
        linhas.append(f"{est}<b>{m['empresa']}</b> — {m['titulo']} <i>({m['analista']})</i>")
    if len(novos) > 20:
        linhas.append(f"… e mais {len(novos) - 20}")
    telegram.enviar(f"<b>{NOME}</b> · {len(novos)} relatório(s) novo(s)\n" + "\n".join(linhas))


def _meta(d: dict) -> dict:
    """Metadados a partir de um item normalizado (smart.Portal.listar) ou dos campos curtos {id,d,co,t,an,rating,kb}."""
    meta = {"id": str(d["id"]), "data": str(d.get("data") or d.get("d") or "")[:10],
            "empresa": _limpa(d.get("empresa") or d.get("co")), "titulo": _limpa(d.get("titulo") or d.get("t")),
            "analista": _limpa(d.get("analista") or d.get("an")), "rating": d.get("rating") or "",
            "kb": int(d.get("kb") or 0), "paginas": None, "chars": 0, "erro": None,
            "coletado": datetime.now().strftime("%Y-%m-%d %H:%M")}
    chave = meta["empresa"] + " " + meta["titulo"]
    meta["destaque"] = bool(DESTAQUE.search(chave)) and not EXCLUI.search(chave)
    return meta


def modo_novos(arq: str, dias: int) -> int:
    """Modo Chrome: recebe a lista normalizada em JSON e imprime os novos, destaque primeiro."""
    lista = json.loads(open(arq, encoding="utf-8").read())
    indice = _carrega(INDICE, {})
    limite = (date.today() - timedelta(days=dias)).isoformat()
    novos = [_meta(d) for d in lista if str(d.get("id")) not in indice]
    novos = [m for m in novos if m["data"] >= limite]
    novos.sort(key=lambda m: (not m["destaque"], m["data"]))
    print(json.dumps([{k: m[k] for k in ("id", "data", "empresa", "titulo", "analista", "rating", "kb", "destaque")} for m in novos],
                     ensure_ascii=False, indent=0))
    return 0


def modo_conhecidos(dias: int) -> int:
    """Modo Chrome: imprime os ids já indexados com data recente (para o JS da lista devolver só os novos)."""
    indice = _carrega(INDICE, {})
    limite = (date.today() - timedelta(days=dias + 4)).isoformat()
    print(json.dumps(sorted(k for k, m in indice.items() if m.get("data", "") >= limite)))
    return 0


MARCA = re.compile(r"<<<SMART ([\w-]{1,64})>>>\n")


def modo_clip() -> int:
    """Modo Chrome: a página copiou para a área de transferência blocos '<<<SMART id>>>\\ntexto'; grava txt/<id>.txt."""
    tmp = DIR / "_clip.txt"
    ps = f"[IO.File]::WriteAllText('{tmp}', [string](Get-Clipboard -Raw), [Text.Encoding]::UTF8)"
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60)
    bruto = tmp.read_text(encoding="utf-8") if tmp.exists() else ""
    partes = MARCA.split(bruto)          # ['', id1, txt1, id2, txt2, ...]
    n = 0
    for i in range(1, len(partes) - 1, 2):
        id_, txt = partes[i], partes[i + 1].strip()
        if txt:
            TXT.mkdir(parents=True, exist_ok=True)
            (TXT / f"{id_}.txt").write_text(txt, encoding="utf-8")
            n += 1
            print(f"  {id_}: {len(txt)} chars")
    tmp.unlink(missing_ok=True)
    print(f"{n} textos gravados da área de transferência")
    return 0 if n else 1


def modo_registrar(arq: str, sem_telegram: bool, sem_espelho: bool) -> int:
    """Modo Chrome: recebe a lista de metadados dos novos (texto já em txt/<id>.txt quando houver) e fecha a passada."""
    metas = json.loads(open(arq, encoding="utf-8").read())
    indice = _carrega(INDICE, {})
    novos = []
    for d in metas:
        m = _meta(d)
        t = TXT / f"{m['id']}.txt"
        if t.exists():
            txt = t.read_text(encoding="utf-8")
            m["chars"] = len(txt)
            m["paginas"] = d.get("paginas") or (txt.count("[página ") or None)
        indice[m["id"]] = m
        novos.append(m)
    _grava(INDICE, indice)
    _grava(NOVOS, {"hora": datetime.now().strftime("%Y-%m-%d %H:%M"), "novos": novos})
    if novos and not sem_telegram:
        avisar(novos)
    if novos and not sem_espelho:
        espelhar(novos)
    print(f"{len(novos)} registrados ({sum(1 for m in novos if m['chars'])} com texto); índice com {len(indice)}")
    return 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", action="store_true", help="abre janela para você fazer login no Smart")
    ap.add_argument("--novos", metavar="LISTA.json", help="modo Chrome: lista normalizada -> imprime os novos")
    ap.add_argument("--registrar", metavar="METAS.json", help="modo Chrome: fecha a passada (índice, novos.json, Telegram, espelho)")
    ap.add_argument("--conhecidos", action="store_true", help="modo Chrome: imprime ids já indexados (recentes)")
    ap.add_argument("--clip", action="store_true", help="modo Chrome: grava os textos que a página copiou para a área de transferência")
    ap.add_argument("--dias", type=int, default=3, help="só relatórios publicados nos últimos N dias (padrão 3)")
    ap.add_argument("--max", type=int, default=40, help="máximo de PDFs por passada")
    ap.add_argument("--headed", action="store_true", help="mostra a janela do navegador")
    ap.add_argument("--sem-telegram", action="store_true")
    ap.add_argument("--sem-espelho", action="store_true")
    args = ap.parse_args()

    if args.login:
        return 0 if smart.login_interativo() else 1
    for d in (PDF, TXT):
        d.mkdir(parents=True, exist_ok=True)
    if args.conhecidos:
        return modo_conhecidos(args.dias)
    if args.clip:
        return modo_clip()
    if args.novos:
        return modo_novos(args.novos, args.dias)
    if args.registrar:
        return modo_registrar(args.registrar, args.sem_telegram, args.sem_espelho)

    indice = _carrega(INDICE, {})
    limite = (date.today() - timedelta(days=args.dias)).isoformat()
    novos: list[dict] = []
    with smart.Portal(headless=not args.headed) as p:
        if not p.abrir_lista():
            aviso_sessao()
            return 1
        docs = p.listar(100)
        cand = [d for d in docs if str(d["id"]) not in indice and d["data"] >= limite]
        print(f"{len(docs)} listados, {len(cand)} novos (últimos {args.dias} dias)")
        for d in sorted(cand, key=lambda d: d["data"])[-args.max:]:       # do mais antigo ao mais novo
            meta = _meta(d)
            id_ = meta["id"]
            try:
                dados = p.pdf(id_)
                (PDF / f"{id_}.pdf").write_bytes(dados)
                txt, n = texto_pdf(dados)
                (TXT / f"{id_}.txt").write_text(f"{meta['empresa']} — {meta['titulo']} ({meta['analista']}, {meta['data']})\n\n{txt}", encoding="utf-8")
                meta["paginas"], meta["chars"] = n, len(txt)
            except Exception as e:
                meta["erro"] = str(e)[:200]
                print(f"  {id_}: {meta['erro']}", file=sys.stderr)
            indice[id_] = meta
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
