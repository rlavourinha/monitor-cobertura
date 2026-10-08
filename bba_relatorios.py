"""Coletor de relatórios novos do Itaú BBA Smart (itau.com.br/itaubba-pt/portal) — modo Chrome.

O portal é uma SPA logada (o Chrome do usuário guarda a sessão). A coleta roda dentro da sessão do Claude pela extensão
Claude in Chrome: bba_chrome_lista.js lê os cards das páginas de lista (equity, macro, renda fixa) e
bba_chrome_resumo.js junta o "Resumo" em HTML de cada relatório em destaque no sessionStorage e copia para a área
de transferência. Este script cuida do lado Python:

    python bba_relatorios.py --conhecidos --dias 3        # ids já indexados (para o JS devolver só os novos)
    python bba_relatorios.py --novos LISTA.json --dias 3  # lista do JS -> novos (destaque primeiro), JSON na saída
    python bba_relatorios.py --clip                       # grava os resumos copiados pela página em data/bba/txt/<id>.txt
    python bba_relatorios.py --registrar METAS.json       # índice, novos.json, Telegram e espelho no repo privado

Arquivos (data/bba/, fora do git): indice.json, novos.json, txt/<id>.txt. Espelho: ../btg-research/bba/ (mesmo repo
privado do BTG; as rotinas de leitura na nuvem leem bba/novos.json e bba/txt/).
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
from fontes import bba, telegram

DIR = config.DATA / "bba"
TXT = DIR / "txt"
INDICE, NOVOS = DIR / "indice.json", DIR / "novos.json"
ESPELHO = config.RAIZ.parent / "btg-research"
SUB = "bba"

DESTAQUE = re.compile(r"Rede D[’']?Or|RDOR|Hapvida|SAUD3|Localiza|RENT3|Cyrela|CYRE3|Cury|Equity Strategy|Ibovespa|Bovespa|"
                      r"Health ?care|Real Estate|Homebuilder|Construction|Brasil|Brazil|Copom|IPCA|Selic|fiscal|Petrobras|"
                      r"Vale\b|B3\b|Scenario Review", re.I)
# fora: outros países, dailies, e as versões em português dos relatórios macro (o mesmo conteúdo sai em inglês)
EXCLUI = re.compile(r"LatAm ex-Brasil|Latam Talking Points|Chile|Argentin|Colombia|Peru|Mexico|México|Paraguay|Uruguay|"
                    r"Market Data Monitor|Week Ahead|\bBRASIL –|Revisão de Cenário", re.I)


def _limpa(s) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _carrega(arq, padrao):
    return json.loads(arq.read_text(encoding="utf-8")) if arq.exists() else padrao


def _grava(arq, obj):
    arq.parent.mkdir(parents=True, exist_ok=True)
    arq.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def _meta(d: dict) -> dict:
    m = {"id": d["id"], "data": (d.get("d") or d.get("data") or "")[:10], "area": d.get("area") or "", "url": d.get("url") or "",
         "tipo": _limpa(d.get("tipo")), "empresa": _limpa(d.get("co") or d.get("empresa")),
         "titulo": _limpa(d.get("t") or d.get("titulo")), "analista": _limpa(d.get("an") or d.get("analista")),
         "chars": 0, "coletado": datetime.now().strftime("%Y-%m-%d %H:%M")}
    chave = " ".join((m["empresa"], m["titulo"], m["tipo"], m["area"]))
    m["destaque"] = bool(DESTAQUE.search(chave)) and not EXCLUI.search(chave)
    return m


def modo_conhecidos(dias: int) -> int:
    indice = _carrega(INDICE, {})
    limite = (date.today() - timedelta(days=dias + 4)).isoformat()
    print(json.dumps(sorted(k for k, m in indice.items() if m.get("data", "") >= limite)))
    return 0


def modo_novos(arq: str, dias: int) -> int:
    lista = json.loads(open(arq, encoding="utf-8").read())
    indice = _carrega(INDICE, {})
    limite = (date.today() - timedelta(days=dias)).isoformat()
    novos = [_meta(d) for d in lista if d.get("id") and d["id"] not in indice]
    novos = [m for m in novos if m["data"] >= limite]
    novos.sort(key=lambda m: (not m["destaque"], m["data"]))
    print(json.dumps(novos, ensure_ascii=False, indent=0))
    return 0


MARCA = re.compile(r"<<<BBA ([0-9a-f-]{36})>>>\n")


def modo_clip(arquivo: str | None = None) -> int:
    """Blocos '<<<BBA id>>>\\ntexto' vindos da área de transferência (padrão) ou de um arquivo (--arquivo)."""
    DIR.mkdir(parents=True, exist_ok=True)
    if arquivo:
        tmp = config.RAIZ / arquivo if not arquivo.startswith(("/", "C:", "D:")) else __import__("pathlib").Path(arquivo)
        bruto = tmp.read_text(encoding="utf-8")
        tmp = DIR / "_nao_apagar"          # não apaga o arquivo do usuário
    else:
        tmp = DIR / "_clip.txt"
        ps = f"[IO.File]::WriteAllText('{tmp}', [string](Get-Clipboard -Raw), [Text.Encoding]::UTF8)"
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60)
        bruto = tmp.read_text(encoding="utf-8") if tmp.exists() else ""
    partes = MARCA.split(bruto)
    n = 0
    for i in range(1, len(partes) - 1, 2):
        id_, txt = partes[i], partes[i + 1].strip()
        if txt:
            TXT.mkdir(parents=True, exist_ok=True)
            (TXT / f"{id_}.txt").write_text(txt, encoding="utf-8")
            n += 1
            print(f"  {id_[:8]}: {len(txt)} chars")
    tmp.unlink(missing_ok=True)
    print(f"{n} resumos gravados da área de transferência")
    return 0 if n else 1


def avisar(novos: list[dict]) -> None:
    if not novos or not telegram.disponivel():
        return
    ordem = sorted(novos, key=lambda m: (not m["destaque"], m["data"]))
    linhas = [f"{'⭐ ' if m['destaque'] else '• '}<b>{m['empresa'] or m['area']}</b> — {m['titulo'].replace('Itaú BBA on ', '')} <i>({m['analista'] or m['tipo']})</i>"
              for m in ordem[:20]]
    if len(novos) > 20:
        linhas.append(f"… e mais {len(novos) - 20}")
    telegram.enviar(f"<b>Itaú BBA Smart</b> · {len(novos)} relatório(s) novo(s)\n" + "\n".join(linhas))


def espelhar(novos: list[dict]) -> bool:
    if not (ESPELHO / ".git").exists():
        return False
    dst = ESPELHO / SUB
    (dst / "txt").mkdir(parents=True, exist_ok=True)
    shutil.copy2(INDICE, dst / "indice.json")
    shutil.copy2(NOVOS, dst / "novos.json")
    for m in novos:
        src = TXT / f"{m['id']}.txt"
        if src.exists():
            shutil.copy2(src, dst / "txt" / src.name)
    def git(*a):
        return subprocess.run(["git", *a], cwd=str(ESPELHO), capture_output=True, text=True, timeout=180)
    git("add", "-A")
    if git("diff", "--cached", "--quiet").returncode == 0:
        return True
    git("commit", "-q", "-m", f"bba: {len(novos)} relatórios novos {datetime.now():%Y-%m-%d %H:%M}")
    git("pull", "--rebase", "-q", "origin", "main")
    r = git("push", "-q", "origin", "main")
    if r.returncode:
        print(f"push do espelho falhou: {r.stderr.strip()[:200]}", file=sys.stderr)
    return r.returncode == 0


def _registrar(itens: list[dict], sem_telegram: bool, sem_espelho: bool) -> list[dict]:
    """Fecha a passada: índice, novos.json, Telegram e espelho. `itens` = dicts crus (da lista) ou metas."""
    indice = _carrega(INDICE, {})
    novos = []
    for d in itens:
        m = _meta(d)
        t = TXT / f"{m['id']}.txt"
        if t.exists():
            m["chars"] = len(t.read_text(encoding="utf-8"))
        indice[m["id"]] = m
        novos.append(m)
    _grava(INDICE, indice)
    _grava(NOVOS, {"hora": datetime.now().strftime("%Y-%m-%d %H:%M"), "novos": novos})
    espelho = None
    if novos and not sem_telegram:
        avisar(novos)
    if novos and not sem_espelho:
        espelho = espelhar(novos)
    print(f"{len(novos)} registrados ({sum(1 for m in novos if m['chars'])} com resumo); índice com {len(indice)}; "
          f"espelho {'ok' if espelho else ('falhou' if espelho is False else 'pulado')}")
    return novos


def modo_registrar(arq: str, sem_telegram: bool, sem_espelho: bool) -> int:
    _registrar(json.loads(open(arq, encoding="utf-8").read()), sem_telegram, sem_espelho)
    return 0


def _avisar_login(motivo: str, sem_telegram: bool) -> None:
    msg = {"captcha": "captcha na tela", "login": "a sessão expirou"}.get(motivo, "a sessão expirou")
    print(f"Itaú BBA: {msg}: rode `python bba_relatorios.py --login`", file=sys.stderr)
    if not sem_telegram and telegram.disponivel():
        telegram.enviar(f"<b>Itaú BBA Smart</b> · {msg}. No laptop: <code>python bba_relatorios.py --login</code>")


def modo_rotina(dias: int, maxn: int, headed: bool, sem_telegram: bool, sem_espelho: bool) -> int:
    """Rotina headless (sem modelo): lista equity+macro, lê o resumo dos destaques, registra. Só DOM."""
    indice = _carrega(INDICE, {})
    limite = (date.today() - timedelta(days=dias)).isoformat()
    try:
        with bba.Portal(headless=not headed) as p:
            st = p.confirmar_sessao()
            if st != "ok":
                _avisar_login(st, sem_telegram)
                return 1
            vistos, todos = set(), []
            for area in bba.AREAS:
                for d in p.listar_area(area, list(indice), dias):
                    if d["id"] not in vistos and d["id"] not in indice:
                        vistos.add(d["id"])
                        todos.append(d)
            novos = [d for d in todos if (d.get("d") or "")[:10] >= limite]
            metas = [_meta(d) for d in novos]
            metas.sort(key=lambda m: (not m["destaque"], m["data"]))
            print(f"{len(todos)} novos listados, {len(metas)} nos últimos {dias} dias")
            lidos = 0
            for m in metas:
                if not m["destaque"] or lidos >= maxn:
                    continue
                r = p.resumo(m["area"], m["id"])
                if r.get("texto") and r.get("chars"):
                    TXT.mkdir(parents=True, exist_ok=True)
                    (TXT / f"{m['id']}.txt").write_text(r["texto"], encoding="utf-8")
                    lidos += 1
                print(f"  {m['data']} {(m['empresa'] or m['area'])[:34]:34} {m['titulo'][:50]} ({r.get('chars', 0)} chars)")
    except bba.PrecisaLogin as e:
        _avisar_login(str(e), sem_telegram)
        return 1
    _registrar(novos, sem_telegram, sem_espelho)
    return 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", action="store_true", help="abre janela para você fazer login no portal")
    ap.add_argument("--conhecidos", action="store_true")
    ap.add_argument("--novos", metavar="LISTA.json")
    ap.add_argument("--clip", action="store_true")
    ap.add_argument("--arquivo", metavar="BLOCOS.txt", help="com --clip: lê os blocos deste arquivo em vez da área de transferência")
    ap.add_argument("--registrar", metavar="METAS.json")
    ap.add_argument("--dias", type=int, default=3)
    ap.add_argument("--max", type=int, default=40, help="máximo de resumos lidos por passada (rotina)")
    ap.add_argument("--headed", action="store_true", help="rotina com a janela visível")
    ap.add_argument("--sem-telegram", action="store_true")
    ap.add_argument("--sem-espelho", action="store_true")
    a = ap.parse_args()
    if a.login:
        return 0 if bba.login_interativo() else 1
    if a.conhecidos:
        return modo_conhecidos(a.dias)
    if a.clip:
        return modo_clip(a.arquivo)
    if a.novos:
        return modo_novos(a.novos, a.dias)
    if a.registrar:
        return modo_registrar(a.registrar, a.sem_telegram, a.sem_espelho)
    return modo_rotina(a.dias, a.max, a.headed, a.sem_telegram, a.sem_espelho)


if __name__ == "__main__":
    sys.exit(main())
