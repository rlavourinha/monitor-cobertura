"""Vigia das cartas de gestão: quando a fonte de uma carta muda (cartas/<fundo>.json → fontes[].url), baixa o documento,
RESUME com o Claude Code em linha de comando (Sonnet) e manda resumo + PDF para o Telegram (bot "cartas"; sem ele, bot padrão).

    python cartas_check.py                 # compara com data/cartas_estado.json, baixa, resume e envia o que mudou
    python cartas_check.py --silencio      # só grava o estado (primeira carga), não envia nada
    python cartas_check.py --teste         # reenvia a última carta do Itaú Janeiro (com resumo) para testar o bot
    python cartas_check.py --resumo X.pdf  # só imprime o resumo de um PDF (testa o CLI)

Como decide que é carta nova:
  · URL .pdf (ou que devolve application/pdf, caso dos informes do BB): HEAD com tamanho/ETag/Last-Modified só dispara o
    download; o que vale é o md5 do conteúdo (data/cartas_estado.json → "_hashes"). Cabeçalho que oscila não reenvia nada.
  · Página de lista: links novos para .pdf ou para posts do Substack (/p/). Link que já foi visto não volta.
  · Recência: o documento precisa trazer uma data (dd/mm/aaaa, "23 de agosto de 2026", "set/26", "1S26", "3T26")
    de até RECENTE_DIAS atrás; carta velha que só mudou de endereço é registrada e não é enviada.
Resumo: `claude -p --model claude-sonnet-5` (precisa de login feito uma vez: `claude` + /login). Sem CLI ou sem login, a
mensagem sai com o aviso "resumo indisponível" e o PDF vai do mesmo jeito. LinkedIn e Wayback ficam de fora.
Agendar pelo agendar_cartas.ps1; a ficha (cartas/<fundo>.json) continua sendo atualizada na sessão.
"""
from __future__ import annotations

import hashlib
import html
import io
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

import config
from fontes import telegram

ESTADO = config.DATA / "cartas_estado.json"
PASTA = config.DATA / "cartas"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36", "Accept": "*/*"}
BOT = "cartas"
MODELO = "claude-sonnet-5"
RECENTE_DIAS = 75
MAX_TXT = 45000
MESES = {m: i + 1 for i, m in enumerate(["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"])}
MESES.update({"janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
              "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12, "january": 1, "february": 2, "march": 3, "april": 4,
              "may": 5, "june": 6, "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12})


def _bot() -> str | None:
    return BOT if telegram.disponivel(BOT) else None


def _md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


# ----------------------------------------------------------------------------- fontes
def _links(url: str, html_: str) -> list[str]:
    pdfs = re.findall(r'href="([^"]+\.pdf[^"]*)"', html_, flags=re.I)
    posts = re.findall(r'href="(https?://[^"]*substack\.com/p/[^"#?]+)', html_, flags=re.I) if "substack" in url else []
    return sorted({urljoin(url, h) for h in pdfs + posts})


def assinatura(url: str) -> tuple[str | None, list[str], bytes | None]:
    """(assinatura, links, conteúdo) — links só para páginas; conteúdo quando a URL sem .pdf devolve um PDF."""
    try:
        if url.lower().endswith(".pdf"):
            r = requests.head(url, headers=UA, timeout=30, allow_redirects=True)
            if r.status_code >= 400:
                r = requests.get(url, headers=UA, timeout=60, stream=True)
            h = r.headers
            return f"{r.status_code}|{h.get('Content-Length', '')}|{h.get('ETag', '')}|{h.get('Last-Modified', '')}", [], None
        r = requests.get(url, headers=UA, timeout=60)
        if r.content[:5].startswith(b"%PDF") or "pdf" in (r.headers.get("Content-Type") or "").lower():
            return f"{r.status_code}|pdf|{_md5(r.content)}", [], r.content
        links = _links(url, r.text)
        return f"{r.status_code}|" + _md5("\n".join(links).encode()), links, None
    except Exception as e:
        return f"erro {str(e)[:60]}", [], None


def baixar(url: str) -> bytes | None:
    try:
        r = requests.get(url, headers=UA, timeout=120)
        return r.content if r.status_code == 200 else None
    except Exception as e:
        print(f"  baixar {url}: {str(e)[:80]}", file=sys.stderr)
        return None


# ----------------------------------------------------------------------------- texto, data, resumo
def texto_pdf(conteudo: bytes, max_chars: int = MAX_TXT) -> str:
    try:
        from pypdf import PdfReader
        rd = PdfReader(io.BytesIO(conteudo))
        out = []
        for p in rd.pages:
            out.append(p.extract_text() or "")
            if sum(len(x) for x in out) > max_chars:
                break
        return "\n".join(out)[:max_chars]
    except Exception as e:
        print(f"  pypdf: {str(e)[:80]}", file=sys.stderr)
        return ""


def texto_html(html_: str, max_chars: int = MAX_TXT) -> str:
    m = re.search(r"<article.*?</article>", html_, flags=re.S | re.I)
    t = m.group(0) if m else html_
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", t, flags=re.S | re.I)
    t = re.sub(r"</(p|div|h\d|li|br)>", "\n", t, flags=re.I)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", t)).strip()[:max_chars]


def data_documento(texto: str, nome: str = "") -> date | None:
    """Data mais recente citada no começo do documento (ou no nome do arquivo); None se não achar."""
    t = (nome + "\n" + texto[:6000]).lower()
    hoje = date.today()
    achadas: list[date] = []

    def add(y, m, d=1):
        try:
            dt = date(int(y), int(m), int(d))
            if date(2015, 1, 1) <= dt <= date(hoje.year, hoje.month, 28) or dt <= hoje:
                achadas.append(dt)
        except ValueError:
            pass

    for d, m, y in re.findall(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b", t):
        add(y, m, d)
    for d, m, y in re.findall(r"\b(\d{1,2}) de ([a-zç]+) de (20\d{2})\b", t):
        if m in MESES:
            add(y, MESES[m], d)
    for m, y in re.findall(r"\b([a-zç]{3,9})[/ \-](20\d{2}|\d{2})\b", t):
        if m in MESES:
            add(y if len(y) == 4 else "20" + y, MESES[m])
    for m, y in re.findall(r"\b([a-z]+)\s*(?:de\s*)?(20\d{2})\b", t):
        if m in MESES:
            add(y, MESES[m])
    for n, k, y in re.findall(r"\b([1-4])([st])(20\d{2}|\d{2})\b", t):
        yy = y if len(y) == 4 else "20" + y
        add(yy, (int(n) * 3) if k == "t" else (int(n) * 6))
    return max(achadas) if achadas else None


def _claude() -> str | None:
    """Caminho do Claude Code CLI: PATH ou a instalação global do npm (o Agendador não tem o PATH do usuário)."""
    exe = shutil.which("claude")
    if exe:
        return exe
    # o Python da Microsoft Store não enxerga a pasta npm do AppData (virtualização MSIX): cópia do CLI fora do AppData
    for c in (Path.home() / ".local/claude-cli/claude.cmd", Path.home() / ".local/bin/claude.exe",
              Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd"):
        if c.exists():
            return str(c)
    return None


def resumir(texto: str, fundo: str, origem: str) -> str | None:
    """Resumo em HTML do Telegram pelo Claude Code (Sonnet). None se o CLI não estiver disponível/logado."""
    exe = _claude()
    texto = texto.replace("\x00", " ").strip()
    if not exe or not texto:
        return None
    prompt = (
        f"Você é analista buy-side. Resuma a carta/comentário abaixo do fundo {fundo} ({origem}) em no máximo 1800 caracteres, "
        "em português, usando SÓ as tags <b> e <i> (HTML do Telegram; nada de markdown, listas com '•'). Estrutura: "
        "<b>Cenário</b> (3 a 5 frases curtas com os números citados), <b>Posições</b> (só se o texto trouxer), "
        "<b>Resultado</b> (retornos do mês/ano/12m se houver), <b>O que mudou</b> (em relação ao que o próprio texto diz que "
        "pensava antes; se não disser, escreva 'o texto não compara'). Não invente nada que não esteja no texto. "
        "Responda só com o resumo.\n\n=== TEXTO ===\n" + texto[:MAX_TXT]
    )
    try:
        # o prompt (até ~45 mil chars) estoura o limite de argv do Windows: vai pelo stdin
        r = subprocess.run([exe, "-p", "--model", MODELO, "--max-turns", "1", "--output-format", "text"],
                           input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=300, cwd=str(config.RAIZ))
        out = (r.stdout or "").strip()
        if r.returncode != 0 or not out or "not logged in" in out.lower() or "/login" in out[:200]:
            print(f"  claude: rc={r.returncode} {out[:120]} {(r.stderr or '')[:120]}", file=sys.stderr)
            return None
        out = re.sub(r"</?(?!b>|i>|/b>|/i>)[a-z][^>]*>", "", out)   # só <b> e <i>
        return out[:3500]
    except Exception as e:
        print(f"  claude: {str(e)[:100]}", file=sys.stderr)
        return None


# ----------------------------------------------------------------------------- envio
def _guarda(conteudo: bytes, stem: str, url: str) -> Path:
    nome = Path(urlparse(url).path).name or "carta"
    if not nome.lower().endswith(".pdf"):
        nome += ".pdf"
    p = PASTA / stem / f"{date.today().isoformat()}_{nome}"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(conteudo)
    return p


def enviar_carta(est: dict, fundo_nome: str, origem: str, url: str, stem: str, silencio: bool,
                 conteudo: bytes | None = None, forcar: bool = False) -> int:
    """Baixa (se preciso), confere md5 e recência, resume e envia. Devolve 1 se enviou."""
    hashes = est.setdefault("_hashes", {})
    conteudo = conteudo if conteudo is not None else baixar(url)
    if conteudo is None:
        if not silencio:
            telegram.enviar(f"<b>Carta nova · {fundo_nome}</b>\n{origem}\n{url}\n(não consegui baixar)", bot=_bot())
        return 0
    eh_pdf = conteudo[:5].startswith(b"%PDF")
    h = _md5(conteudo)
    if h in hashes and not forcar:
        return 0
    texto = texto_pdf(conteudo) if eh_pdf else texto_html(conteudo.decode("utf-8", "replace"))
    dt = data_documento(texto, Path(urlparse(url).path).name)
    hashes[h] = {"fundo": stem, "url": url, "data_doc": dt.isoformat() if dt else None, "visto": date.today().isoformat()}
    if silencio:
        return 0
    if eh_pdf and dt and (date.today() - dt).days > RECENTE_DIAS and not forcar:
        print(f"  {fundo_nome}: documento de {dt} é antigo, só registrado ({url})")
        return 0
    pago = "7-day free trial" in texto or "Already a paid subscriber" in texto
    resumo = None if pago else resumir(texto, fundo_nome, origem)
    cab = f"<b>Carta nova · {fundo_nome}</b>\n{origem}" + (f" · {dt.strftime('%d/%m/%Y')}" if dt else "") + f"\n{url}\n\n"
    if resumo:
        corpo = resumo
    elif pago:
        corpo = "<i>Post pago: só a abertura é pública.</i>\n" + texto[:900]
    else:
        corpo = "<i>Resumo indisponível (Claude CLI sem login ou sem texto).</i>"
    ok = telegram.enviar((cab + corpo)[:4000], bot=_bot())
    if eh_pdf:
        p = _guarda(conteudo, stem, url)
        ok = telegram.enviar_documento(p, f"{fundo_nome} · PDF", bot=_bot()) or ok
    return 1 if ok else 0


# ----------------------------------------------------------------------------- principal
def main() -> int:
    silencio = "--silencio" in sys.argv
    est = json.loads(ESTADO.read_text(encoding="utf-8")) if ESTADO.exists() else {}
    if "--resumo" in sys.argv:
        p = Path(sys.argv[sys.argv.index("--resumo") + 1])
        t = texto_pdf(p.read_bytes())
        print("data:", data_documento(t, p.name), "| chars:", len(t))
        print(resumir(t, p.stem, "teste") or "(sem resumo: CLI indisponível ou sem login)")
        return 0
    enviados, mudaram = 0, []
    for f in sorted((config.RAIZ / "cartas").glob("*.json")):
        if f.name.startswith("_"):
            continue
        C = json.loads(f.read_text(encoding="utf-8"))
        nome = C.get("fundo", f.stem)
        if "--teste" in sys.argv:
            if f.stem != "janeiro":
                continue
            u = next(s["url"] for s in C["fontes"] if s["url"].lower().endswith(".pdf"))
            n = enviar_carta(est, nome, "teste do bot", u, f.stem, False, forcar=True)
            print("teste:", "enviado" if n else "falhou")
            break
        for s in C.get("fontes", []):
            u = s.get("url") or ""
            if not u.startswith("http") or "linkedin.com" in u or "web.archive.org" in u:
                continue
            a, links, conteudo = assinatura(u)
            if a is None or a.startswith("erro"):
                continue
            k = f"{f.stem}|{u}"
            antes = est.get(k)
            if antes and antes["assinatura"] != a:
                mudaram.append((nome, u))
                if u.lower().endswith(".pdf") or conteudo is not None:
                    enviados += enviar_carta(est, nome, s.get("nome", ""), u, f.stem, silencio, conteudo)
                else:
                    novos = [l for l in links if l not in (antes.get("links") or [])][:3]
                    for l in novos:
                        enviados += enviar_carta(est, nome, f"novo na página {s.get('nome', '')}", l, f.stem, silencio)
            elif antes is None and conteudo is not None:
                enviar_carta(est, nome, s.get("nome", ""), u, f.stem, True, conteudo)   # 1ª vez: só registra o md5
            est[k] = {"assinatura": a, "visto": datetime.now().strftime("%Y-%m-%d %H:%M"), **({"links": links} if links else {})}
    ESTADO.parent.mkdir(parents=True, exist_ok=True)
    ESTADO.write_text(json.dumps(est, ensure_ascii=False, indent=1), encoding="utf-8")
    n_fontes = sum(1 for k in est if not k.startswith("_"))
    print(f"{n_fontes} fontes vigiadas; {len(mudaram)} mudaram; {enviados} cartas enviadas (bot {_bot() or 'padrão'}; "
          f"resumo: {'claude ' + MODELO if _claude() else 'sem CLI'})"
          + "".join(f"\n  {n}: {u}" for n, u in mudaram))
    return 0


if __name__ == "__main__":
    sys.exit(main())
