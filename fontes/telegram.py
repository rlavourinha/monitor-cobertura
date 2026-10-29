"""Alertas por Telegram. Token do bot e chat_id ficam em `.secrets/telegram.json` (fora do git):

    {"token": "123456:ABC...", "chat_id": "123456789",
     "bots": {"sellside": {"token": "...", "chat_id": "..."}}}

O bot padrão (raiz) é o do Monitor (vigia, coletas). Bots adicionais ficam em "bots": o nome é passado em `bot=`
(ex.: "sellside" = Resumo_Sellside, usado pela Leitura da manhã/do dia). Se um bot nomeado não estiver configurado,
`disponivel(bot)` devolve False e quem chama decide o fallback.

Para criar: no Telegram, fale com @BotFather → /newbot → copie o token para o arquivo; mande qualquer mensagem para o
bot novo e rode `python -m fontes.telegram --chat [--bot NOME]` para descobrir o chat_id (lê getUpdates) e gravar.
Usa `requests` (vem com o yfinance): o urllib desta máquina leva "connection reset" da API do Telegram (e do FRED).

Robustez (29/10/2026: 11 blocos das Leituras de 26-28/10 não chegaram): o Telegram recusa a mensagem INTEIRA por um "&" ou
"<" solto ("<1% do market cap", "óleo & gás") ou por uma tag aberta e não fechada (a legenda de foto era cortada em 1.000
caracteres no meio de um <b> ou de um <a href>). Agora: html_tg() escapa o texto fora das tags aceitas e fecha as tags;
o corte em partes nunca parte tag nem entidade e reabre as tags na parte seguinte; 429 (flood) espera o retry_after; se o
HTML ainda for recusado, a mensagem vai em texto puro. Toda recusa fica em data/telegram_erros.log e em `ultimo_erro`.
"""
from __future__ import annotations

import html
import json
import re
import sys
import time
from datetime import datetime

import config

ARQ = config.RAIZ / ".secrets" / "telegram.json"
API = "https://api.telegram.org/bot{token}/{metodo}"
LOG = config.DATA / "telegram_erros.log"
ultimo_erro = ""          # descrição da última recusa (quem chama pode anexar ao relatório de falhas)


def _cfg_tudo() -> dict:
    return json.loads(ARQ.read_text(encoding="utf-8")) if ARQ.exists() else {}


def _cfg(bot: str | None = None) -> dict:
    """{token, chat_id} do bot pedido (None = padrão); {} se não configurado."""
    c = _cfg_tudo()
    if bot:
        b = (c.get("bots") or {}).get(bot) or {}
        return b if b.get("token") and b.get("chat_id") else {}
    return c


def _registra(metodo: str, resp, texto: str = "", bot: str | None = None) -> None:
    global ultimo_erro
    ultimo_erro = (resp.get("description") if isinstance(resp, dict) else str(resp)) or "sem descrição"
    try:
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {bot or 'padrão'} {metodo}: {ultimo_erro[:200]} | {texto[:80]!r}\n")
    except Exception:
        pass
    print(f"  Telegram {metodo}: {ultimo_erro[:200]}", file=sys.stderr)


def _chama(metodo: str, dados: dict | None = None, bot: str | None = None, arquivo: tuple[str, str] | None = None,
           timeout: int = 30) -> dict:
    """POST na API. Tenta de novo em 'connection reset' (a rede desta máquina às vezes recusa a 1ª tentativa), em 429
    (espera o retry_after que a API pede) e em erro 5xx. arquivo = (campo, caminho) para sendPhoto/sendDocument."""
    import requests
    c = _cfg(bot)
    erro, r = None, {}
    for tent in range(4):
        try:
            if arquivo:
                with open(arquivo[1], "rb") as fh:
                    resp = requests.post(API.format(token=c["token"], metodo=metodo), data=dados or {}, files={arquivo[0]: fh},
                                         timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
            else:
                resp = requests.post(API.format(token=c["token"], metodo=metodo), data=dados or {}, timeout=timeout,
                                     headers={"User-Agent": "Mozilla/5.0"})
            r = resp.json()
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout, ValueError) as e:
            erro = e
            time.sleep(2 + 3 * tent)
            continue
        if r.get("ok"):
            return r
        cod = r.get("error_code") or 0
        if cod == 429:
            time.sleep(min(60, int((r.get("parameters") or {}).get("retry_after") or 5)) + 1)
        elif cod >= 500:
            time.sleep(2 + 3 * tent)
        else:
            return r
    if r:
        return r
    raise erro


def disponivel(bot: str | None = None) -> bool:
    c = _cfg(bot)
    return bool(c.get("token") and c.get("chat_id"))


_TAGS_OK = {"b", "strong", "i", "em", "u", "s", "code", "pre", "a", "tg-spoiler"}
_TAG_RX = re.compile(r"<(/?)(b|strong|i|em|u|s|code|pre|a|tg-spoiler)((?:\s[^<>]*)?)>", re.I)
_ENT_RX = re.compile(r"&(?!(?:amp|lt|gt|quot|#\d+|#x[0-9a-fA-F]+);)")


def _escapa(t: str) -> str:
    return _ENT_RX.sub("&amp;", t).replace("<", "&lt;").replace(">", "&gt;")


def _tokens(t: str) -> list[tuple[str, str, str]]:
    """[(tipo, valor, nome_da_tag)]: tipo 'abre' / 'fecha' / 'txt' (texto já escapado)."""
    out, i = [], 0
    for m in _TAG_RX.finditer(t):
        if m.start() > i:
            out.append(("txt", _escapa(t[i:m.start()]), ""))
        nome = m.group(2).lower()
        out.append(("fecha" if m.group(1) else "abre", f"</{nome}>" if m.group(1) else f"<{nome}{_ENT_RX.sub('&amp;', m.group(3))}>", nome))
        i = m.end()
    if i < len(t):
        out.append(("txt", _escapa(t[i:]), ""))
    return out


def _equilibra(toks: list[tuple[str, str, str]]) -> str:
    """Remonta o HTML descartando fechamentos sem abertura e fechando o que ficou aberto (o Telegram não aninha errado)."""
    out, pilha = [], []
    for tipo, v, nome in toks:
        if tipo == "abre":
            pilha.append((nome, v)); out.append(v)
        elif tipo == "fecha":
            if nome in [n for n, _ in pilha]:
                while pilha:
                    n, _ = pilha.pop(); out.append(f"</{n}>")
                    if n == nome:
                        break
        else:
            out.append(v)
    out += [f"</{n}>" for n, _ in reversed(pilha)]
    return "".join(out)


def html_tg(texto: str) -> str:
    """Deixa só o HTML que o Telegram aceita: <br>/<p>/<li> viram quebra de linha; qualquer outra tag some; "&", "<" e ">"
    soltos são escapados e as tags ficam equilibradas (o Telegram recusa a mensagem inteira por qualquer um desses)."""
    t = re.sub(r"<br\s*/?>", "\n", texto or "", flags=re.I)
    t = re.sub(r"</(p|div|li|h\d)\s*>", "\n", t, flags=re.I)
    t = re.sub(r"<li\b[^>]*>", "• ", t, flags=re.I)
    t = re.sub(r"</?([a-zA-Z][\w-]*)\b[^>]*>", lambda m: m.group(0) if m.group(1).lower() in _TAGS_OK else "", t)
    t = _equilibra(_tokens(t))
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def texto_puro(h: str) -> str:
    """HTML do Telegram → texto sem formatação (fallback quando a API ainda recusa o HTML)."""
    return html.unescape(re.sub(r"<[^>]+>", "", h or ""))


def _partes(h: str, n: int) -> list[str]:
    """Divide HTML já limpo (html_tg) em partes de até n caracteres: corta em fim de parágrafo, senão em espaço, nunca
    dentro de tag ou entidade; as tags abertas no corte são fechadas e reabertas na parte seguinte."""
    if len(h) <= n:
        return [h]
    partes, atual, tam, pilha = [], [], 0, []          # pilha: [(nome, tag de abertura)]

    def fecha():
        nonlocal atual, tam
        s = "".join(atual) + "".join(f"</{nm}>" for nm, _ in reversed(pilha))
        partes.append(s.strip())
        atual = [ab for _, ab in pilha]; tam = sum(len(x) for x in atual)

    def margem():
        return n - sum(len(nm) + 3 for nm, _ in pilha)

    for tipo, v, nome in _tokens(h):
        if tipo != "txt":
            if tipo == "abre" and tam + len(v) + len(nome) + 3 > margem():
                fecha()
            atual.append(v); tam += len(v)
            if tipo == "abre":
                pilha.append((nome, v))
            elif pilha and pilha[-1][0] == nome:
                pilha.pop()
            continue
        while v:
            cabe = margem() - tam
            if len(v) <= cabe:
                atual.append(v); tam += len(v); break
            corte = v.rfind("\n", 0, cabe)
            if corte < cabe // 3:
                corte = v.rfind(" ", 0, cabe)
            if corte <= 0 and tam > 0:
                fecha(); continue
            if corte <= 0:
                corte = cabe
                amp = v.rfind("&", 0, corte)
                if amp >= 0 and ";" not in v[amp:corte]:
                    corte = amp or corte
            atual.append(v[:corte]); tam += corte
            v = v[corte:].lstrip(" \n")
            fecha()
    if "".join(atual).strip() and tam > sum(len(ab) for _, ab in pilha):
        partes.append(("".join(atual) + "".join(f"</{nm}>" for nm, _ in reversed(pilha))).strip())
    return [p for p in partes if texto_puro(p).strip()]


def _manda_texto(p: str, silencioso: bool, bot: str | None) -> bool:
    dados = {"chat_id": _cfg(bot)["chat_id"], "text": p, "parse_mode": "HTML", "disable_notification": "true" if silencioso else "false"}
    r = _chama("sendMessage", dados, bot=bot)
    if not r.get("ok") and "parse" in str(r.get("description", "")).lower():
        _registra("sendMessage (HTML recusado; vai em texto puro)", r, p, bot)
        dados.pop("parse_mode"); dados["text"] = texto_puro(p)
        r = _chama("sendMessage", dados, bot=bot)
    if not r.get("ok"):
        _registra("sendMessage", r, p, bot)
    return bool(r.get("ok"))


def enviar(texto: str, silencioso: bool = False, bot: str | None = None) -> bool:
    """Envia uma mensagem (HTML simples: <b>, <i>, <code>, <a>). Devolve False se não configurado ou se a API recusar.
    Textos acima de 4096 caracteres são divididos em parágrafos e enviados em sequência."""
    if not disponivel(bot):
        print(f"  Telegram: bot {bot or 'padrão'} sem token/chat_id em .secrets/telegram.json", file=sys.stderr)
        return False
    try:
        ok = True
        for i, p in enumerate(_partes(html_tg(texto), 3900)):
            ok = _manda_texto(p, silencioso or bool(i), bot) and ok
        return ok
    except Exception as e:
        _registra("sendMessage", str(e), texto, bot)
        return False


def _envia_arquivo(metodo: str, campo: str, caminho, legenda: str, silencioso: bool, bot: str | None, timeout: int) -> bool:
    if not disponivel(bot):
        print(f"  Telegram: bot {bot or 'padrão'} sem token/chat_id em .secrets/telegram.json", file=sys.stderr)
        return False
    partes = _partes(html_tg(legenda), 1000) or [""]
    cap, resto = partes[0], "\n".join(partes[1:])
    dados = {"chat_id": _cfg(bot)["chat_id"], "caption": cap, "parse_mode": "HTML", "disable_notification": "true" if silencioso else "false"}
    try:
        r = _chama(metodo, dados, bot=bot, arquivo=(campo, str(caminho)), timeout=timeout)
        if not r.get("ok") and "parse" in str(r.get("description", "")).lower():
            _registra(f"{metodo} (HTML recusado; legenda em texto puro)", r, cap, bot)
            dados.pop("parse_mode"); dados["caption"] = texto_puro(cap)
            r = _chama(metodo, dados, bot=bot, arquivo=(campo, str(caminho)), timeout=timeout)
    except Exception as e:
        _registra(metodo, str(e), cap, bot)
        return False
    if not r.get("ok"):
        _registra(metodo, r, cap, bot)
        return False
    return enviar(resto, silencioso=True, bot=bot) if resto.strip() else True


def enviar_foto(caminho, legenda: str = "", silencioso: bool = False, bot: str | None = None) -> bool:
    """Envia uma imagem (PNG/JPG) com legenda em HTML (até 1.000 caracteres; o excedente vai numa mensagem seguinte)."""
    return _envia_arquivo("sendPhoto", "photo", caminho, legenda, silencioso, bot, 60)


def enviar_documento(caminho, legenda: str = "", silencioso: bool = False, bot: str | None = None) -> bool:
    """Envia um arquivo (PDF etc., até 50 MB) com legenda em HTML (até 1.000 caracteres; o excedente vai em mensagem seguinte)."""
    return _envia_arquivo("sendDocument", "document", caminho, legenda, silencioso, bot, 180)


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
