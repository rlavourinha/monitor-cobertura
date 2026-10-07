"""Vigia das cartas de gestão: avisa no Telegram quando a fonte de uma carta (cartas/<fundo>.json → fontes[].url) mudar.

    python cartas_check.py            # compara com data/cartas_estado.json e avisa o que mudou
    python cartas_check.py --silencio # só grava o estado (primeira carga)

PDF: HEAD com tamanho, ETag e Last-Modified. Página HTML (lista de cartas): hash dos links para .pdf. LinkedIn fica de fora
(exige login). Agendar diariamente pelo agendar_cartas.ps1; a leitura em si continua sendo feita na sessão (ficha JSON).
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime

import requests

import config
from fontes import telegram

ESTADO = config.DATA / "cartas_estado.json"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) monitor-cobertura/1.0"}


def assinatura(url: str) -> str | None:
    try:
        if url.lower().endswith(".pdf"):
            r = requests.head(url, headers=UA, timeout=30, allow_redirects=True)
            if r.status_code >= 400:
                r = requests.get(url, headers=UA, timeout=60, stream=True)
            h = r.headers
            return f"{r.status_code}|{h.get('Content-Length', '')}|{h.get('ETag', '')}|{h.get('Last-Modified', '')}"
        r = requests.get(url, headers=UA, timeout=60)
        links = sorted(set(re.findall(r'href="([^"]+\.pdf[^"]*)"', r.text, flags=re.I)))
        return f"{r.status_code}|" + hashlib.md5("\n".join(links).encode()).hexdigest()
    except Exception as e:
        return f"erro {str(e)[:60]}"


def main() -> int:
    est = json.loads(ESTADO.read_text(encoding="utf-8")) if ESTADO.exists() else {}
    mudou = []
    for f in sorted((config.RAIZ / "cartas").glob("*.json")):
        if f.name.startswith("_"):
            continue
        C = json.loads(f.read_text(encoding="utf-8"))
        for s in C.get("fontes", []):
            u = s.get("url") or ""
            if not u.startswith("http") or "linkedin.com" in u or "web.archive.org" in u:
                continue
            a = assinatura(u)
            if a is None or a.startswith("erro"):
                continue
            k = f"{f.stem}|{u}"
            if k in est and est[k]["assinatura"] != a:
                mudou.append((C.get("fundo", f.stem), s.get("nome", ""), u))
            est[k] = {"assinatura": a, "visto": datetime.now().strftime("%Y-%m-%d %H:%M")}
    ESTADO.parent.mkdir(parents=True, exist_ok=True)
    ESTADO.write_text(json.dumps(est, ensure_ascii=False, indent=1), encoding="utf-8")
    if mudou and "--silencio" not in sys.argv:
        msg = "<b>Cartas de gestão</b> · fonte mudou:\n" + "\n".join(f"• {fu} — {n}\n{u}" for fu, n, u in mudou)
        if telegram.disponivel():
            telegram.enviar(msg)
    print(f"{len(est)} fontes vigiadas; {len(mudou)} mudaram" + ("".join(f"\n  {fu}: {u}" for fu, _, u in mudou)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
