"""Receptor local (WebSocket) para o modo Chrome do coletor BTG.

A página do portal (dirigida pela extensão Claude in Chrome, dentro da sessão do Claude) extrai o texto do PDF com o
pdf.js do próprio portal e manda para cá; este servidor grava em data/btg/txt/<id>.txt. Por que WebSocket: a CSP do
portal (connect-src) bloqueia fetch/XHR para localhost, mas libera `ws:`/`wss:` para qualquer origem.

    python btg_receptor.py            # escuta em ws://127.0.0.1:8766 (só loopback); agendar_receptor.ps1 mantém no ar

Protocolo: o cliente manda um JSON {"id": 122940, "txt": "..."}; resposta "ok <bytes>" ou "erro ...".
           {"ping": 1} responde "pong".
"""
from __future__ import annotations

import asyncio
import json
import re
import sys

import config

TXT = config.DATA / "btg" / "txt"
PORTA = 8766


async def trata(ws):
    async for msg in ws:
        try:
            d = json.loads(msg)
            if d.get("ping"):
                await ws.send("pong"); continue
            id_ = str(d.get("id", ""))
            txt = d.get("txt") or ""
            if not re.fullmatch(r"\d{3,8}", id_) or not txt:
                await ws.send("erro: id ou txt inválido"); continue
            TXT.mkdir(parents=True, exist_ok=True)
            dados = txt.encode("utf-8")
            (TXT / f"{id_}.txt").write_bytes(dados)
            print(f"{id_}: {len(dados)} bytes", flush=True)
            await ws.send(f"ok {len(dados)}")
        except Exception as e:
            await ws.send(f"erro: {str(e)[:200]}")


async def main():
    import websockets
    print(f"receptor BTG em ws://127.0.0.1:{PORTA} -> {TXT}", flush=True)
    async with websockets.serve(trata, "127.0.0.1", PORTA, max_size=64 * 1024 * 1024,
                                origins=["https://www.btgpactual.com"]):
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
