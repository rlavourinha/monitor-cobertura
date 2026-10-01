"""Alertas por Telegram. Token do bot e chat_id ficam em `.secrets/telegram.json` (fora do git):

    {"token": "123456:ABC...", "chat_id": "123456789",
     "bots": {"sellside": {"token": "...", "chat_id": "..."}}}

O bot padrão (raiz) é o do Monitor (vigia, coletas). Bots adicionais ficam em "bots": o nome é passado em `bot=`
(ex.: "sellside" = Resumo_Sellside, usado pela Leitura da manhã/do dia). Se um bot nomeado não estiver configurado,
`disponivel(bot)` devolve False e quem chama decide o fallback.

Para criar: no Telegram, fale com @BotFather → /newbot → copie o token para o arquivo; mande qualquer mensagem para o
bot novo e rode `python -m fontes.telegram --chat [--bot NOME]` para descobrir o chat_id (lê getUpdates) e gravar.
Usa `requests` (vem com o yfinance): o urllib desta máquina leva "connection reset" da API do Telegram (e do FRED).
"""
from __future__ import annotations

import json
import sys

import config

ARQ = config.RAIZ / ".secrets" / "telegram.json"
API = "https://api.telegram.org/bot{token}/{metodo}"


def _cfg_tudo() -> dict:
    return json.loads(ARQ.read_text(encoding="utf-8")) if ARQ.exists() else {}


def _cfg(bot: str | None = None) -> dict:
    """{token, chat_id} do bot pedido (None = padrão); {} se não configurado."""
    c = _cfg_tudo()
    if bot:
        b = (c.get("bots") or {}).get(bot) or {}
        return b if b.get("token") and b.get("chat_id") else {}
    return c


def _chama(metodo: str, dados: dict | None = None, bot: str | None = None) -> dict:
    """POST na API; a rede desta máquina às vezes devolve 'connection reset' na 1ª tentativa — tenta de novo até 3 vezes."""
    import time
    import requests
    c = _cfg(bot)
    erro = None
    for tent in range(3):
        try:
            r = requests.post(API.format(token=c["token"], metodo=metodo), data=dados or {}, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            return r.json()
        except requests.exceptions.ConnectionError as e:
            erro = e
            time.sleep(2 + 3 * tent)
    raise erro


def disponivel(bot: str | None = None) -> bool:
    c = _cfg(bot)
    return bool(c.get("token") and c.get("chat_id"))


def enviar(texto: str, silencioso: bool = False, bot: str | None = None) -> bool:
    """Envia uma mensagem (HTML simples: <b>, <i>, <code>). Devolve False se não configurado ou se a API recusar.
    Textos acima de 4096 caracteres são divididos em parágrafos e enviados em sequência."""
    if not disponivel(bot):
        print(f"  Telegram: bot {bot or 'padrão'} sem token/chat_id em .secrets/telegram.json", file=sys.stderr)
        return False
    partes = []
    while len(texto) > 4000:
        a, texto = _corta(texto, 3900)
        partes.append(a)
    partes.append(texto)
    try:
        ok = True
        for i, p in enumerate(partes):
            r = _chama("sendMessage", {"chat_id": _cfg(bot)["chat_id"], "text": p, "parse_mode": "HTML",
                                       "disable_notification": "true" if (silencioso or i) else "false"}, bot=bot)
            if not r.get("ok"):
                print(f"  Telegram sendMessage: {str(r)[:200]}", file=sys.stderr)
            ok = ok and bool(r.get("ok"))
        return ok
    except Exception as e:
        print(f"  Telegram: {e}", file=sys.stderr)
        return False


def enviar_foto(caminho, legenda: str = "", silencioso: bool = False, bot: str | None = None) -> bool:
    """Envia uma imagem (PNG/JPG) com legenda em HTML (até 1024 caracteres; o excedente vai numa mensagem seguinte)."""
    if not disponivel(bot):
        print(f"  Telegram: bot {bot or 'padrão'} sem token/chat_id em .secrets/telegram.json", file=sys.stderr)
        return False
    import time
    import requests
    c = _cfg(bot)
    cap, resto = (legenda[:1024], "") if len(legenda) <= 1024 else _corta(legenda, 1000)
    erro = None
    for tent in range(3):
        try:
            with open(caminho, "rb") as fh:
                r = requests.post(API.format(token=c["token"], metodo="sendPhoto"),
                                  data={"chat_id": c["chat_id"], "caption": cap, "parse_mode": "HTML",
                                        "disable_notification": "true" if silencioso else "false"},
                                  files={"photo": fh}, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
            ok = bool(r.json().get("ok"))
            if not ok:
                print(f"  Telegram sendPhoto: {r.text[:200]}", file=sys.stderr)
            if ok and resto:
                ok = enviar(resto, silencioso=True, bot=bot)
            return ok
        except requests.exceptions.ConnectionError as e:
            erro = e
            time.sleep(2 + 3 * tent)
    print(f"  Telegram: {erro}", file=sys.stderr)
    return False


def _corta(texto: str, n: int) -> tuple[str, str]:
    """Divide num limite de paragrafo antes de n caracteres, sem partir tags HTML simples."""
    i = texto.rfind(chr(10), 0, n)
    i = i if i > n // 2 else n
    return texto[:i].rstrip(), texto[i:].lstrip()


def descobrir_chat(bot: str | None = None) -> str | None:
    """Lê getUpdates e grava o chat_id do último remetente em .secrets/telegram.json (mande uma mensagem ao bot antes)."""
    c = _cfg_tudo()
    alvo = (c.setdefault("bots", {}).setdefault(bot, {}) if bot else c)
    if not alvo.get("token"):
        print(f"coloque o token do bot {bot or 'padrão'} em .secrets/telegram.json primeiro"); return None
    import requests
    ups = requests.get(API.format(token=alvo["token"], metodo="getUpdates"), timeout=30, headers={"User-Agent": "Mozilla/5.0"}).json().get("result", [])
    chats = [u.get("message", {}).get("chat", {}).get("id") for u in ups if u.get("message")]
    if not chats:
        print("nenhuma mensagem recebida ainda: mande 'oi' para o bot e rode de novo"); return None
    alvo["chat_id"] = str(chats[-1])
    ARQ.write_text(json.dumps(c, indent=1), encoding="utf-8")
    print(f"chat_id do bot {bot or 'padrão'} gravado")
    return alvo["chat_id"]


if __name__ == "__main__":
    bot = sys.argv[sys.argv.index("--bot") + 1] if "--bot" in sys.argv else None
    if "--chat" in sys.argv:
        descobrir_chat(bot)
    else:
        print("enviado" if enviar(f"<b>Monitor de Cobertura</b> · teste do bot {bot or 'padrão'} ✅", bot=bot) else "falhou")
