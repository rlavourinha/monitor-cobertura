"""Mantém viva a sessão dos portais de research com sessão "deslizante" (expira sem uso), hoje só o UBS Neo
(~1h30 parado e cai). Abre o portal sem janela, renova pelo "Continue" se preciso e grava os cookies renovados
(gringas.Portal grava ao sair). Agendado a cada 45 min por agendar_sessoes.ps1. Registro em data/sessoes_estado.json
(em .playwright/, fora do git).

    python manter_sessoes.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime

import config
from fontes import gringas

PORTAIS = {"ubs": "https://neo.ubs.com/feed/stream/company/VZ.N/latest/company-research"}
LOG = config.RAIZ / ".playwright" / "sessoes_estado.json"


def manter(casa: str, url: str) -> str:
    try:
        with gringas.Portal(casa, headless=True) as p:
            p.page.goto(url, timeout=90000, wait_until="domcontentloaded")
            p.page.wait_for_timeout(5000)
            if "login" in p.page.url or "idp" in p.page.url:
                try:
                    p.page.get_by_text("Continue", exact=True).first.click(timeout=10000)
                    p.page.wait_for_timeout(12000)
                except Exception:
                    pass
            if "login" in p.page.url or "idp" in p.page.url:
                raise RuntimeError("caiu no login")
        return "ok"
    except Exception as e:
        return f"falhou: {str(e)[:80]}"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    est = json.loads(LOG.read_text(encoding="utf-8")) if LOG.exists() else {}
    for casa, url in PORTAIS.items():
        if not gringas.estado_sessao(casa).exists():
            continue
        r = manter(casa, url)
        reg = est.setdefault(casa, {})
        reg["ultima"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        reg["resultado"] = r
        if r == "ok":
            reg["ultimo_ok"] = reg["ultima"]
        print(f"{casa}: {r}")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text(json.dumps(est, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
