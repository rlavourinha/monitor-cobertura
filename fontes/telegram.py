"""Alertas por Telegram. Token do bot e chat_id ficam em `.secrets/telegram.json` (fora do git):

    {"token": "123456:ABC...", "chat_id": "123456789"}

Para criar: no Telegram, fale com @BotFather → /newbot → copie o token; mande qualquer mensagem para o bot novo e rode
`python -m fontes.telegram --chat` para descobrir o chat_id (lê getUpdates) e gravar no arquivo.
Usa `requests` (vem com o yfinance): o urllib desta máquina leva "connection reset" da API do Telegram (e do FRED).
"""
from __future__ import annotations

import json
import sys

import config

ARQ = config.RAIZ / ".secrets" / "telegram.json"
API = "https://api.telegram.org/bot{token}/{metodo}"


def _cfg() -> dict:
    return json.loads(ARQ.read_text(encoding="utf-8")) if ARQ.exists() else {}


def _chama(metodo: str, dados: dict | None = None) -> dict:
    """POST na API; a rede desta máquina às vezes devolve 'connection reset' na 1ª tentativa — tenta de novo até 3 vezes."""
    import time
    import requests
    c = _cfg()
    erro = None
    for tent in range(3):
        try:
            r = requests.post(API.format(token=c["token"], metodo=metodo), data=dados or {}, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            return r.json()
        except requests.exceptions.ConnectionError as e:
            erro = e
            time.sleep(2 + 3 * tent)
    raise erro


def disponivel() -> bool:
    c = _cfg()
    return bool(c.get("token") and c.get("chat_id"))


def enviar(texto: str, silencioso: bool = False) -> bool:
    """Envia uma mensagem (HTML simples: <b>, <i>, <code>). Devolve False se não configurado ou se a API recusar."""
    if not disponivel():
        print("  Telegram: sem token/chat_id em .secrets/telegram.json", file=sys.stderr)
        return False
    try:
        return bool(_chama("sendMessage", {"chat_id": _cfg()["chat_id"], "text": texto, "parse_mode": "HTML",
                                           "disable_notification": "true" if silencioso else "false"}).get("ok"))
    except Exception as e:
        print(f"  Telegram: {e}", file=sys.stderr)
        return False


def descobrir_chat() -> str | None:
    """Lê getUpdates e grava o chat_id do último remetente em .secrets/telegram.json (mande uma mensagem ao bot antes)."""
    c = _cfg()
    if not c.get("token"):
        print("coloque o token em .secrets/telegram.json primeiro"); return None
    ups = _chama("getUpdates").get("result", [])
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
