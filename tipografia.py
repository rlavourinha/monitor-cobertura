"""Baixa as fontes do Google Fonts (subconjunto latin, woff2) e gera assets/fonts.css com @font-face em base64.

O build embute esse CSS no HTML: o painel renderiza a mesma tipografia em qualquer máquina, mesmo com
firewall bloqueando o Google Fonts. Rodar uma vez (ou ao trocar as famílias em config.FONTES).

    python tipografia.py
"""
from __future__ import annotations

import base64
import re
import sys
import urllib.parse
import urllib.request

import config

ASSETS = config.RAIZ / "assets"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"


def main() -> int:
    ASSETS.mkdir(exist_ok=True)
    fams = "&".join("family=" + urllib.parse.quote(f, safe=":@;,") for f in config.FONTES_GOOGLE)
    url = f"https://fonts.googleapis.com/css2?{fams}&display=swap"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    css = urllib.request.urlopen(req, timeout=60).read().decode("utf-8")
    blocos = re.findall(r"/\* (\w[\w-]*) \*/\s*@font-face \{(.*?)\}", css, re.S)
    out, total = [], 0
    for sub, corpo in blocos:
        if sub != "latin":
            continue
        m = re.search(r"url\((https://[^)]+\.woff2)\)", corpo)
        if not m:
            continue
        woff = urllib.request.urlopen(urllib.request.Request(m.group(1), headers={"User-Agent": UA}), timeout=60).read()
        total += len(woff)
        b64 = base64.b64encode(woff).decode()
        corpo = corpo.replace(m.group(1), f"data:font/woff2;base64,{b64}")
        out.append("@font-face {" + corpo + "}")
    (ASSETS / "fonts.css").write_text("\n".join(out), encoding="utf-8")
    print(f"{len(out)} faces latin embutidas, {total // 1024} KB -> {ASSETS / 'fonts.css'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
