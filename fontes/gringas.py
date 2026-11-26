"""Portais de research das casas GLOBAIS (JPMorgan, Morgan Stanley, Goldman, BofA) via Playwright
com perfil persistente — mesmo modelo do BTG (fontes/btg.py).

A pergunta que este módulo responde ANTES de escrever cada coletor: a casa deixa logar uma vez e depois
rodar HEADLESS (automatável, modelo BTG), ou o anti-bot bloqueia o navegador automatizado mesmo com cookie
válido (modelo BBA/Akamai, aí precisa do Chrome real)?

    python probe_gringas.py --login jpm        # 1x, no laptop: abre janela, você loga (2FA), sessão salva
    python probe_gringas.py --check jpm        # headless: diz se a sessão sobrevive e o portal serve o bot

Perfis em .playwright/gringas-<casa> (fora do git). URLs são o palpite inicial; confirme/corrija no login.
"""
from __future__ import annotations

import re
from pathlib import Path

import config

# casa -> (rótulo, url inicial, regex de "ainda na tela de login" na URL final)
PORTAIS: dict[str, tuple[str, str, str]] = {
    "jpm":     ("JPMorgan",       "https://markets.jpmorgan.com",  r"logon|login|sso|auth|idp"),
    "ms":      ("Morgan Stanley", "https://matrix.ms.com",         r"login|logon|sso|auth|pingfederate"),
    "goldman": ("Goldman Sachs",  "https://research.gs.com",       r"login|signin|sso|auth|okta"),
    "bofa":    ("BofA",           "https://rsch.baml.com",         r"login|logon|sso|auth|sign-?in"),
}


def perfil(casa: str) -> Path:
    return config.RAIZ / ".playwright" / f"gringas-{casa}"


class Portal:
    """Contexto Playwright persistente apontado para o portal de uma casa. `with Portal('jpm') as p:`."""

    def __init__(self, casa: str, headless: bool = True):
        if casa not in PORTAIS:
            raise SystemExit(f"casa '{casa}' desconhecida; use: {', '.join(PORTAIS)}")
        self.casa = casa
        self.rotulo, self.url, self._rx_login = PORTAIS[casa]
        self.headless = headless

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        p = perfil(self.casa)
        p.mkdir(parents=True, exist_ok=True)
        opts = dict(headless=self.headless, viewport={"width": 1360, "height": 920}, locale="en-US",
                    args=["--disable-blink-features=AutomationControlled"])
        try:                                   # Chrome do sistema (impressão digital mais natural); senão o Chromium do Playwright
            self.ctx = self._pw.chromium.launch_persistent_context(str(p), channel="chrome", **opts)
        except Exception:
            self.ctx = self._pw.chromium.launch_persistent_context(str(p), **opts)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        return self

    def __exit__(self, *a):
        try:
            self.ctx.close()                   # se você já fechou a janela, o contexto pode estar fechado
        except Exception:
            pass
        finally:
            self._pw.stop()

    def logado(self) -> tuple[bool, str]:
        """Heurística: navega ao portal e vê se caiu na tela de login (URL ou campo de senha na página)."""
        try:
            self.page.goto(self.url, timeout=45000, wait_until="domcontentloaded")
            self.page.wait_for_timeout(3500)                 # SSO costuma redirecionar depois do load
        except Exception as e:
            return False, f"falha ao abrir: {e}"
        url = self.page.url
        na_tela_login = bool(re.search(self._rx_login, url, re.I))
        try:
            tem_senha = self.page.locator("input[type=password]").count() > 0
        except Exception:
            tem_senha = False
        # Página pública de apresentação (ex.: markets.jpmorgan.com/home deslogado) não tem campo de senha, só um botão
        # "Login": conta como deslogado se oferece entrar e não oferece sair.
        try:
            txt = self.page.inner_text("body")[:6000]
        except Exception:
            txt = ""
        oferece_entrar = bool(re.search(r"\b(log ?in|sign ?in|log ?on)\b", txt, re.I))
        oferece_sair = bool(re.search(r"\b(log ?out|sign ?out|log ?off)\b", txt, re.I))
        ok = not (na_tela_login or tem_senha or (oferece_entrar and not oferece_sair))
        return ok, url
