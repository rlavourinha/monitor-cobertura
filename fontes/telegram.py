"""Alertas por Telegram. Token do bot e chat_id ficam em `.secrets/telegram.json` (fora do git):

    {"token": "123456:ABC...", "chat_id": "123456789"}

Para criar: no Telegram, fale com @BotFather → /newbot → copie o token; mande qualquer mensagem para o bot novo e rode
`python -m fontes.telegram --chat` para descobrir o chat_id (lê getUpdates) e gravar no arquivo.
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request

import config

ARQ = config.RAIZ / ".secrets" / "telegram.json"


def _cfg() -> dict:
    return json.loads(ARQ.read_text(encoding="utf-8")) if ARQ.exists() else {}


def disponivel() -> bool:
    c = _cfg()
    return bool(c.get("token") and c.get("chat_id"))


def enviar(texto: str, silencioso: bool = False) -> bool:
    """Envia uma mensagem (HTML simples: <b>, <i>, <code>). Devolve False se não configurado ou se a API recusar."""
    c = _cfg()
    if not (c.get("token") and c.get("chat_id")):
        print("  Telegram: sem token/chat_id em .secrets/telegram.json", file=sys.stderr)
        return False
    dados = urllib.parse.urlencode({"chat_id": c["chat_id"], "text": texto, "parse_mode": "HTML", "disable_notification": "true" if silencioso else "false"}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{c['token']}/sendMessage", data=dados)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r).get("ok", False)
    except Exception as e:
        print(f"  Telegram: {e}", file=sys.stderr)
        return False


def descobrir_chat() -> str | None:
    """Lê getUpdates e grava o chat_id do último remetente em .secrets/telegram.json (mande uma mensagem ao bot antes)."""
    c = _cfg()
    if not c.get("token"):
        print("coloque o token em .secrets/telegram.json primeiro"); return None
    with urllib.request.urlopen(f"https://api.telegram.org/bot{c['token']}/getUpdates", timeout=30) as r:
        ups = json.load(r).get("result", [])
    chats = [u.get("message", {}).get("chat", {}).get("id") for u in ups if u.get("message")]
    if not chats:
        print("nenhuma mensagem recebida ainda: mande 'oi' para o bot e rode de novo"); return None
    c["chat_id"] = str(chats[-1])
    ARQ.write_text(json.dumps(c, indent=1), encoding="utf-8")
    print("chat_id gravado")
    return c["chat_id"]


if __name__ == "__main__":
    if "--chat" in sys.argv:
        descobrir_chat()
    else:
        print("enviado" if enviar("<b>Monitor de Cobertura</b> · teste de alerta ✅") else "falhou")
