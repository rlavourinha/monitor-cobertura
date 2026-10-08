"""Coletor de relatórios novos do Bradesco BBI (portal BlueMatrix: bradesco-portal.bluematrix.com) — modo Chrome.

O portal é uma SPA (Vuetify) com acesso liberado no navegador por 365 dias depois da verificação por e-mail (feita em
05/10/2026 no Chrome do usuário). A coleta roda dentro da sessão do Claude pela extensão Claude in Chrome, só DOM:
bradesco_chrome_lista.js lê os cards da Busca Avançada (visão LIST, mais recente primeiro), bradesco_chrome_texto.js
lê o texto integral de cada relatório em destaque (a página do iframe, bradescobbi.bluematrix.com/links2/doc/html/<id>,
tem o relatório todo em HTML), bradesco_chrome_empacota.js leva o acumulado em window.name até o portal e
bradesco_chrome_copia.js copia para a área de transferência (no mesmo lote de um clique: a página do documento bloqueia o clipboard). Este script cuida do Python:

    python bradesco_relatorios.py --conhecidos --dias 3        # ids já indexados (para o JS devolver só os novos)
    python bradesco_relatorios.py --novos LISTA.json --dias 3  # lista do JS -> novos (destaque primeiro); LISTA = "clip" lê a área de transferência
    python bradesco_relatorios.py --clip [--arquivo X]         # grava os textos copiados em data/bradesco/txt/<id>.txt
    python bradesco_relatorios.py --registrar METAS.json       # índice, novos.json, Telegram e espelho no repo privado

Arquivos (data/bradesco/, fora do git): indice.json, novos.json, txt/<id>.txt. Espelho: ../btg-research/bradesco/ (mesmo
repo privado do BTG e do Itaú BBA; a Leitura do dia lê os três índices).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import config
from fontes import bradesco, telegram

DIR = config.DATA / "bradesco"
TXT = DIR / "txt"
INDICE, NOVOS = DIR / "indice.json", DIR / "novos.json"
ESPELHO = config.RAIZ.parent / "btg-research"
SUB = "bradesco"

# destaque = tudo que não é diário/coluna/outro país: notas de empresa, setor, macro e estratégia do Brasil
EXCLUI = re.compile(r"Mexico|M[ée]xico|La Mañanera|Argentin|Chile|Colombia|Peru|Paraguay|Uruguay|Andean|LatAm ex-Brazil|"
                    r"Morning (Checkup|Assembly|Bytes|Call|Note)|Fast Lane|Oil Column|Daily Crusher|Don'?t Miz|Stock Guide|"
                    r"Week Ahead|Weekly Market|Daily|Destaques Semanais|Semanal|Monitor de Deb", re.I)


def _limpa(s) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _carrega(arq, padrao):
    return json.loads(arq.read_text(encoding="utf-8")) if arq.exists() else padrao


def _grava(arq, obj):
    arq.parent.mkdir(parents=True, exist_ok=True)
    arq.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def _meta(d: dict) -> dict:
    """Metadados no mesmo formato do BTG/BBA (leitura.py lê empresa/titulo/analista/tipo/area/destaque/coletado)."""
    hora = d.get("d") or d.get("data") or ""
    m = {"id": d["id"], "data": hora[:10], "hora": hora[:16], "area": _limpa(d.get("co") or d.get("area")), "url": d.get("url") or f"/report/{d['id']}",
         "tipo": "", "empresa": _limpa(d.get("co") or d.get("empresa")), "titulo": _limpa(d.get("t") or d.get("titulo")),
         "analista": _limpa(d.get("an") or d.get("analista")), "sinopse": _limpa(d.get("sin") or d.get("sinopse")),
         "chars": 0, "coletado": datetime.now().strftime("%Y-%m-%d %H:%M")}
    chave = " ".join((m["empresa"], m["titulo"], m["sinopse"][:120]))
    m["destaque"] = not EXCLUI.search(chave)
    return m


def modo_conhecidos(dias: int) -> int:
    indice = _carrega(INDICE, {})
    limite = (date.today() - timedelta(days=dias + 4)).isoformat()
    print(json.dumps(sorted(k for k, m in indice.items() if m.get("data", "") >= limite)))
    return 0


def _clipboard() -> str:
    tmp = DIR / "_clip.txt"
    DIR.mkdir(parents=True, exist_ok=True)
    ps = f"[IO.File]::WriteAllText('{tmp}', [string](Get-Clipboard -Raw), [Text.Encoding]::UTF8)"
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60)
    bruto = tmp.read_text(encoding="utf-8-sig") if tmp.exists() else ""   # o PowerShell grava com BOM
    tmp.unlink(missing_ok=True)
    return bruto


def modo_novos(arq: str, dias: int) -> int:
    bruto = _clipboard() if arq == "clip" else open(arq, encoding="utf-8").read()
    lista = json.loads(bruto.strip() or "[]")
    indice = _carrega(INDICE, {})
    limite = (date.today() - timedelta(days=dias)).isoformat()
    novos = [_meta(d) for d in lista if d.get("id") and d["id"] not in indice]
    novos = [m for m in novos if m["data"] >= limite]
    novos.sort(key=lambda m: (not m["destaque"], m["hora"]))
    print(json.dumps(novos, ensure_ascii=False, indent=0))
    return 0


MARCA = re.compile(r"<<<BDX ([0-9a-f-]{36})>>>\n")


def modo_clip(arquivo: str | None = None) -> int:
    """Blocos '<<<BDX id>>>\\ntexto' vindos da área de transferência (padrão) ou de um arquivo (--arquivo)."""
    if arquivo:
        p = Path(arquivo) if re.match(r"^([A-Za-z]:|/)", arquivo) else config.RAIZ / arquivo
        bruto = p.read_text(encoding="utf-8")
    else:
        bruto = _clipboard()
    partes = MARCA.split(bruto)
    n = 0
    for i in range(1, len(partes) - 1, 2):
        id_, txt = partes[i], partes[i + 1].strip()
        if txt:
            TXT.mkdir(parents=True, exist_ok=True)
            (TXT / f"{id_}.txt").write_text(txt, encoding="utf-8")
            n += 1
            print(f"  {id_[:8]}: {len(txt)} chars")
    print(f"{n} textos gravados")
    return 0 if n else 1


def avisar(novos: list[dict]) -> None:
    if not novos or not telegram.disponivel():
        return
    ordem = sorted(novos, key=lambda m: (not m["destaque"], m["hora"]))
    linhas = [f"{'⭐ ' if m['destaque'] else '• '}<b>{m['empresa'] or '—'}</b> — {m['titulo']} <i>({m['analista']})</i>" for m in ordem[:20]]
    if len(novos) > 20:
        linhas.append(f"… e mais {len(novos) - 20}")
    telegram.enviar(f"<b>Bradesco BBI</b> · {len(novos)} relatório(s) novo(s)\n" + "\n".join(linhas))


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
    git("commit", "-q", "-m", f"bradesco: {len(novos)} relatórios novos {datetime.now():%Y-%m-%d %H:%M}")
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
        m = _meta(d) if "coletado" not in d else d
        t = TXT / f"{m['id']}.txt"
        if t.exists():
            m["chars"] = len(t.read_text(encoding="utf-8"))
        indice[m["id"]] = m
        novos.append(m)
    _grava(INDICE, indice)
    _grava(NOVOS, {"hora": datetime.now().strftime("%Y-%m-%d %H:%M"), "novos": novos})
    if novos and not sem_telegram:
        avisar(novos)
    espelho = espelhar(novos) if (novos and not sem_espelho) else None
    print(f"{len(novos)} registrados ({sum(1 for m in novos if m['chars'])} com texto); índice com {len(indice)}; espelho {'ok' if espelho else ('falhou' if espelho is False else 'pulado')}")
    return novos


def modo_registrar(arq: str, sem_telegram: bool, sem_espelho: bool) -> int:
    _registrar(json.loads(open(arq, encoding="utf-8").read()), sem_telegram, sem_espelho)
    return 0


def _avisar_login(motivo: str, sem_telegram: bool) -> None:
    msg = {"captcha": "captcha na tela", "login": "a sessão expirou"}.get(motivo, "a sessão expirou")
    print(f"Bradesco BBI: {msg}: rode `python bradesco_relatorios.py --login`", file=sys.stderr)
    if not sem_telegram and telegram.disponivel():
        telegram.enviar(f"<b>Bradesco BBI</b> · {msg}. No laptop: <code>python bradesco_relatorios.py --login</code>")


def modo_rotina(dias: int, maxn: int, paginas: int, headed: bool, sem_telegram: bool, sem_espelho: bool) -> int:
    """Rotina headless (sem modelo): lista a Busca Avançada, lê o texto integral dos destaques, registra. Só DOM."""
    indice = _carrega(INDICE, {})
    limite = (date.today() - timedelta(days=dias)).isoformat()
    try:
        with bradesco.Portal(headless=not headed) as p:
            todos = p.coletar_lista(paginas=paginas)
            novos = [d for d in todos if d.get("id") and d["id"] not in indice and (d.get("d") or "")[:10] >= limite]
            metas = [_meta(d) for d in novos]
            metas.sort(key=lambda m: (not m["destaque"], m["hora"]))
            print(f"{len(todos)} listados, {len(novos)} novos nos últimos {dias} dias")
            lidos = 0
            for m in metas:
                if not m["destaque"] or lidos >= maxn:
                    continue
                r = p.texto(m["id"])
                if r.get("texto") and r.get("chars"):
                    TXT.mkdir(parents=True, exist_ok=True)
                    (TXT / f"{m['id']}.txt").write_text(r["texto"], encoding="utf-8")
                    lidos += 1
                print(f"  {m['hora']} {(m['empresa'] or '—')[:30]:30} {m['titulo'][:50]} ({r.get('chars', 0)} chars)")
    except bradesco.PrecisaLogin as e:
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
    ap.add_argument("--login", action="store_true", help="abre janela para você fazer a verificação por e-mail")
    ap.add_argument("--conhecidos", action="store_true")
    ap.add_argument("--novos", metavar="LISTA.json|clip")
    ap.add_argument("--clip", action="store_true")
    ap.add_argument("--arquivo", metavar="BLOCOS.txt", help="com --clip: lê os blocos deste arquivo em vez da área de transferência")
    ap.add_argument("--registrar", metavar="METAS.json")
    ap.add_argument("--dias", type=int, default=3)
    ap.add_argument("--max", type=int, default=40, help="máximo de textos lidos por passada (rotina)")
    ap.add_argument("--paginas", type=int, default=2, help="páginas de 15 cards na Busca Avançada (rotina)")
    ap.add_argument("--headed", action="store_true", help="rotina com a janela visível")
    ap.add_argument("--sem-telegram", action="store_true")
    ap.add_argument("--sem-espelho", action="store_true")
    a = ap.parse_args()
    if a.login:
        return 0 if bradesco.login_interativo() else 1
    if a.conhecidos:
        return modo_conhecidos(a.dias)
    if a.clip:
        return modo_clip(a.arquivo)
    if a.novos:
        return modo_novos(a.novos, a.dias)
    if a.registrar:
        return modo_registrar(a.registrar, a.sem_telegram, a.sem_espelho)
    return modo_rotina(a.dias, a.max, a.paginas, a.headed, a.sem_telegram, a.sem_espelho)


if __name__ == "__main__":
    sys.exit(main())
