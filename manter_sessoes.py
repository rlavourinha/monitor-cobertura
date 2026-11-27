"""Mantém viva a sessão dos portais de research com sessão "deslizante", hoje só o UBS Neo: a página expira (~12 h),
mas o login de fundo segue válido e renova sem senha. Abre o portal sem janela (e, se não renovar, numa janela fora
da tela, que redireciona como um navegador normal) e grava os cookies renovados (gringas.Portal grava ao sair).
Agendado a cada 45 min por agendar_sessoes.ps1. Registro em .playwright/sessoes_estado.json (fora do git).

    python manter_sessoes.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime

import config
from fontes import gringas

LOG = config.RAIZ / ".playwright" / "sessoes_estado.json"


def manter_ubs() -> str:
    import ubs_relatorios
    try:
        with ubs_relatorios.abre_portal() as p:
            pass
        return "ok"
    except Exception as e:
        return f"falhou: {str(e)[:80]}"


PORTAIS = {"ubs": manter_ubs}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    est = json.loads(LOG.read_text(encoding="utf-8")) if LOG.exists() else {}
    for casa, fn in PORTAIS.items():
        if not gringas.estado_sessao(casa).exists():
            continue
        r = fn()
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
