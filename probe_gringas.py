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


def login(casa: str, max_min: int = 20) -> int:
    """Abre a janela e ESPERA VOCÊ FECHÁ-LA depois de entrar no portal (não adivinha o login: a página pública de
    apresentação engana). Enquanto isso, guarda URL/título/texto da página aberta em .playwright/gringas-<casa>-logado.txt,
    para aprender como é o portal logado."""
    import time
    snap = gringas.perfil(casa).with_name(f"gringas-{casa}-logado.txt")
    with gringas.Portal(casa, headless=False) as p:
        print(f"[{p.rotulo}] abrindo {p.url}\n  1) faça o login nesta janela (2FA incluso)\n  2) espere a área logada carregar"
              f" (research/home do portal)\n  3) FECHE a janela — a sessão fica salva. Aguardo até {max_min} min.", flush=True)
        try:
            p.page.goto(p.url, timeout=60000, wait_until="domcontentloaded")
        except Exception as e:
            print(f"falha ao abrir: {e}", file=sys.stderr)
        ultimo = None
        fim = time.time() + max_min * 60
        while time.time() < fim:
            try:
                pags = [pg for pg in p.ctx.pages if not pg.is_closed()]
            except Exception:
                pags = []
            if not pags:
                break
            pg = pags[-1]
            try:
                pg.wait_for_timeout(3000)
                ultimo = (pg.url, pg.title(), pg.inner_text("body")[:4000])
                # guarda TODOS os cookies, inclusive os de sessão (que o Chrome descarta ao fechar: caso do Morgan
                # Stanley); o Portal reinjeta na próxima abertura
                p.ctx.storage_state(path=str(gringas.estado_sessao(casa)))
            except Exception:
                pass
        if ultimo:
            snap.write_text(f"URL: {ultimo[0]}\nTITULO: {ultimo[1]}\n\n{ultimo[2]}", encoding="utf-8")
            print(f"[{p.rotulo}] janela fechada. Última página: {ultimo[1][:60]} | {ultimo[0][:100]}\n  sessão salva em "
                  f"{gringas.perfil(casa)}; agora rode --check {casa}", flush=True)
            return 0
        print(f"[{p.rotulo}] nada registrado (tempo esgotado ou janela fechada sem carregar).", file=sys.stderr)
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
