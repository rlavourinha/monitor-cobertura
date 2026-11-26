"""Sonda de viabilidade dos portais das casas globais (ver fontes/gringas.py).

    python probe_gringas.py --login jpm     # 1x no laptop: abre janela, você faz login (2FA); salva a sessão
    python probe_gringas.py --check jpm     # headless: a sessão sobrevive? o portal serve o navegador automatizado?
    python probe_gringas.py --check todas   # testa todas as que já têm perfil salvo

Decisão: --check OK => automatável (modelo BTG, roda sozinho na rotina).
         --check cai no login => precisa do Chrome real (modelo BBA), não roda headless.
"""
from __future__ import annotations

import argparse
import sys

from fontes import gringas

sys.stdout.reconfigure(encoding="utf-8")


def login(casa: str, max_min: int = 15) -> int:
    with gringas.Portal(casa, headless=False) as p:
        print(f"[{p.rotulo}] abrindo {p.url} — faça o login nesta janela (2FA incluso). Aguardo até {max_min} min.", flush=True)
        try:
            p.page.goto(p.url, timeout=60000, wait_until="domcontentloaded")
        except Exception as e:
            print(f"falha ao abrir: {e}", file=sys.stderr)
        # espera você logar: pergunta periodicamente se já saiu da tela de login
        import time
        fim = time.time() + max_min * 60
        while time.time() < fim:
            p.page.wait_for_timeout(5000)
            ok, url = p.logado()
            if ok:
                print(f"[{p.rotulo}] logado; sessão salva em {gringas.perfil(casa)}\n  URL final: {url}", flush=True)
                return 0
        print(f"[{p.rotulo}] tempo esgotado sem detectar login.", file=sys.stderr)
        return 2


def check(casa: str) -> int:
    with gringas.Portal(casa, headless=True) as p:
        ok, url = p.logado()
        veredito = "AUTOMATÁVEL (headless serve)" if ok else "PRECISA DE CHROME REAL (caiu no login/anti-bot)"
        print(f"[{p.rotulo}] {veredito}\n  URL final: {url}")
        return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Sonda de viabilidade dos portais globais.")
    ap.add_argument("--login", metavar="CASA", help="abre janela para login (jpm|ms|goldman|bofa)")
    ap.add_argument("--check", metavar="CASA", help="testa headless (jpm|ms|goldman|bofa|todas)")
    a = ap.parse_args()
    if a.login:
        return login(a.login)
    if a.check:
        if a.check == "todas":
            rc = 0
            for casa in gringas.PORTAIS:
                if gringas.perfil(casa).exists():
                    rc |= check(casa)
            return rc
        return check(a.check)
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
