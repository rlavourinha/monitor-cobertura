"""Vigia das cartas de gestão: quando a fonte de uma carta muda (cartas/<fundo>.json → fontes[].url), baixa o PDF e manda
para o Telegram (bot "cartas"; se não estiver configurado, cai no bot padrão do Monitor).

    python cartas_check.py            # compara com data/cartas_estado.json, baixa e envia o que mudou
    python cartas_check.py --silencio # só grava o estado (primeira carga), não envia nada
    python cartas_check.py --teste    # reenvia a última carta do Itaú Janeiro para testar o bot

PDF: HEAD com tamanho, ETag e Last-Modified; mudou → baixa para data/cartas/<fundo>/<AAAA-MM-DD>_<arquivo>.pdf e envia como
documento. Página HTML (lista de cartas): hash dos links para .pdf; link novo → baixa e envia cada PDF novo (até 3).
LinkedIn e Wayback ficam de fora. Agendar diariamente pelo agendar_cartas.ps1; a leitura e a ficha continuam na sessão.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

import config
from fontes import telegram

ESTADO = config.DATA / "cartas_estado.json"
PASTA = config.DATA / "cartas"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) monitor-cobertura/1.0"}
BOT = "cartas"


def _bot() -> str | None:
    return BOT if telegram.disponivel(BOT) else None


def _links_pdf(url: str, html: str) -> list[str]:
    return sorted({urljoin(url, h) for h in re.findall(r'href="([^"]+\.pdf[^"]*)"', html, flags=re.I)})


def assinatura(url: str) -> tuple[str | None, list[str]]:
    """(assinatura, links) — links só para páginas de lista."""
    try:
        if url.lower().endswith(".pdf"):
            r = requests.head(url, headers=UA, timeout=30, allow_redirects=True)
            if r.status_code >= 400:
                r = requests.get(url, headers=UA, timeout=60, stream=True)
            h = r.headers
            return f"{r.status_code}|{h.get('Content-Length', '')}|{h.get('ETag', '')}|{h.get('Last-Modified', '')}", []
        r = requests.get(url, headers=UA, timeout=60)
        links = _links_pdf(url, r.text)
        return f"{r.status_code}|" + hashlib.md5("\n".join(links).encode()).hexdigest(), links
    except Exception as e:
        return f"erro {str(e)[:60]}", []


def baixar(url: str, fundo: str) -> Path | None:
    try:
        r = requests.get(url, headers=UA, timeout=120)
        if r.status_code != 200 or not r.content[:5].startswith(b"%PDF"):
            return None
        nome = Path(urlparse(url).path).name or "carta.pdf"
        p = PASTA / fundo / f"{date.today().isoformat()}_{nome}"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(r.content)
        return p
    except Exception as e:
        print(f"  baixar {url}: {str(e)[:80]}", file=sys.stderr)
        return None


def enviar_carta(fundo_nome: str, origem: str, url: str, stem: str, silencio: bool) -> bool:
    p = baixar(url, stem)
    leg = f"<b>Carta nova · {fundo_nome}</b>\n{origem}\n{url}"
    if silencio:
        return False
    if p:
        return telegram.enviar_documento(p, leg, bot=_bot())
    return telegram.enviar(leg + "\n(não consegui baixar o PDF)", bot=_bot())


def main() -> int:
    silencio = "--silencio" in sys.argv
    est = json.loads(ESTADO.read_text(encoding="utf-8")) if ESTADO.exists() else {}
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
            print("teste:", "enviado" if enviar_carta(nome, "teste do bot", u, f.stem, False) else "falhou")
            return 0
        for s in C.get("fontes", []):
            u = s.get("url") or ""
            if not u.startswith("http") or "linkedin.com" in u or "web.archive.org" in u:
                continue
            a, links = assinatura(u)
            if a is None or a.startswith("erro"):
                continue
            k = f"{f.stem}|{u}"
            antes = est.get(k)
            if antes and antes["assinatura"] != a:
                mudaram.append((nome, u))
                if u.lower().endswith(".pdf"):
                    enviados += enviar_carta(nome, s.get("nome", ""), u, f.stem, silencio)
                else:
                    novos = [l for l in links if l not in (antes.get("links") or [])][:3]
                    for l in novos:
                        enviados += enviar_carta(nome, f"novo na página {s.get('nome', '')}", l, f.stem, silencio)
            est[k] = {"assinatura": a, "visto": datetime.now().strftime("%Y-%m-%d %H:%M"), **({"links": links} if links else {})}
    ESTADO.parent.mkdir(parents=True, exist_ok=True)
    ESTADO.write_text(json.dumps(est, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(est)} fontes vigiadas; {len(mudaram)} mudaram; {enviados} cartas enviadas (bot {_bot() or 'padrão'})"
          + "".join(f"\n  {n}: {u}" for n, u in mudaram))
    return 0


if __name__ == "__main__":
    sys.exit(main())
