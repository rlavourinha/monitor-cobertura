"""Receptor local (WebSocket) para o modo Chrome do coletor Smart — irmão de btg_receptor.py, em outra porta.

A página do Smart (dirigida pela extensão Claude in Chrome) extrai o texto do PDF e manda para cá; este servidor grava em
data/smart/txt/<id>.txt. Origens aceitas em ORIGENS: ajuste para o host real da SPA depois de mapear a API.

    python smart_receptor.py          # escuta em ws://127.0.0.1:8767 (só loopback); agendar_receptor.ps1 é o modelo

Protocolo: {"id": "abc-123", "txt": "..."} -> "ok <bytes>" ou "erro ..."; {"ping": 1} -> "pong".
"""
from __future__ import annotations

import asyncio
import json
import re
import sys

import config

TXT = config.DATA / "smart" / "txt"
PORTA = 8767
ORIGENS = ["https://www.itau.com.br"]


async def trata(ws):
    async for msg in ws:
        try:
            d = json.loads(msg)
            if d.get("ping"):
                await ws.send("pong"); continue
            id_ = str(d.get("id", ""))
            txt = d.get("txt") or ""
            if not re.fullmatch(r"[\w-]{1,64}", id_) or not txt:
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
    print(f"receptor Smart em ws://127.0.0.1:{PORTA} -> {TXT}", flush=True)
    async with websockets.serve(trata, "127.0.0.1", PORTA, max_size=64 * 1024 * 1024, origins=ORIGENS):
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
