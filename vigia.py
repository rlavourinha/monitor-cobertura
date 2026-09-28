"""Vigia em tempo real dos papéis do Ibovespa (e BDRs): roda neste PC a cada poucos minutos durante o pregão, lê preço e
volume do dia no MT5 da Genial, calcula oscilação (z) e volume relativo projetado, grava data/alertas_tempo_real.json
(o painel "Papéis do Ibovespa" usa) e manda alerta por Telegram quando algo sai do padrão.

    python vigia.py            # uma passada (agendar a cada 5 min, dias úteis, 10h–17h30: agendar_vigia.ps1)
    python vigia.py --sem-push # não faz commit/push nem dispara o build do site

Regras (config): |z| ≥ ALERTA_Z (retorno do dia ÷ σ dos retornos diários de 60 pregões) ou volume do dia projetado
para o pregão inteiro ≥ ALERTA_VOL × média de 21 pregões (só após 30 min de pregão). Cada alerta é enviado uma vez
por papel por dia; repete se o movimento dobrar de intensidade (estado em data/alertas_estado.json).
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from datetime import date, datetime, time

import config
from fontes import b3, mt5, telegram

ARQ = config.DATA / "alertas_tempo_real.json"
ESTADO = config.DATA / "alertas_estado.json"
ABRE, FECHA = time(10, 0), time(17, 0)


def _num(v, d=1, sinal=False):
    s = f"{v:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("+" + s) if (sinal and v > 0) else s


def snapshot() -> dict | None:
    """{cod: {preco, hora, vol_hoje}} pelo MT5 (tick + barra diária de hoje). None se o terminal não está disponível."""
    if not mt5.disponivel():
        return None
    C = json.loads((config.DATA / "ibov_comp.json").read_text(encoding="utf-8"))
    cods = sorted({i["cod"] for i in C.get("itens", [])} | set(getattr(config, "BDRS", [])))
    out = {}
    hoje = date.today().isoformat()
    for cod in cods:
        q = mt5.intraday(cod)
        if not q or not q.get("preco"):
            continue
        if q["hora"][:10] != hoje and "--forcar" not in sys.argv:   # sem negócio hoje ainda (ou pregão fechado): ignora
            continue
        out[cod] = {"preco": q["preco"], "hora": q["hora"], "vol_hoje": q.get("volume")}   # volume = quantidade de ações do dia (barra D1)
    return out


def avaliar(snap: dict) -> dict:
    """Junta o intraday com o histórico (COTAHIST) e calcula z, retorno e volume relativo projetado."""
    S = b3.series_todas(("fechamento", "quantidade", "volume"), desde="2025-06-01")
    agora = datetime.now()
    frac = min(1.0, max(0.0, ((agora.hour * 60 + agora.minute) - (ABRE.hour * 60)) / ((FECHA.hour - ABRE.hour) * 60)))
    zlim, vlim = getattr(config, "ALERTA_Z", 2.0), getattr(config, "ALERTA_VOL", 2.0)
    papeis, alertas = {}, []
    for cod, q in snap.items():
        s = [r for r in S.get(cod, []) if r[1]]
        if len(s) < 25:
            continue
        fech = [r[1] for r in s]; qtd = [r[2] for r in s]
        if s[-1][0] >= q["hora"][:10]:            # o COTAHIST de hoje já saiu: o fechamento "anterior" é o de ontem
            fech, qtd = fech[:-1], qtd[:-1]
        r1 = q["preco"] / fech[-1] - 1
        rets = [math.log(fech[i] / fech[i - 1]) for i in range(max(1, len(fech) - 60), len(fech)) if fech[i - 1]]
        mu = sum(rets) / len(rets)
        sig = (sum((x - mu) ** 2 for x in rets) / (len(rets) - 1)) ** 0.5 if len(rets) > 10 else None
        z = (math.log(1 + r1) / sig) if (sig and r1 > -1) else None
        qmed = sum(qtd[-21:]) / 21 if len(qtd) >= 21 else None
        vrel = (q["vol_hoje"] / (qmed * frac)) if (qmed and q.get("vol_hoje") and frac >= 30 / 420) else None   # projetado p/ o pregão inteiro
        flags = []
        if z is not None and abs(z) >= zlim:
            flags.append(f"oscilação {_num(r1 * 100, 1, True)}% = {_num(abs(z), 1)}σ")
        if vrel is not None and vrel >= vlim:
            flags.append(f"volume {_num(vrel, 1)}× a média (projetado)")
        papeis[cod] = {"preco": q["preco"], "hora": q["hora"], "r1": r1, "z": z, "sig": sig, "vol_hoje": q.get("vol_hoje"), "vrel": vrel, "flags": flags}
        if flags:
            alertas.append({"cod": cod, "txt": " e ".join(flags), "score": (abs(z) if z else 0) + (vrel or 0), "z": z, "vrel": vrel, "r1": r1})
    alertas.sort(key=lambda a: -a["score"])
    return {"hora": agora.strftime("%Y-%m-%d %H:%M"), "fracao_pregao": round(frac, 3), "papeis": papeis, "alertas": alertas}


def notificar(res: dict) -> int:
    """Manda por Telegram os alertas novos (ou que dobraram de intensidade) do dia; devolve quantos enviou."""
    hoje = date.today().isoformat()
    est = json.loads(ESTADO.read_text(encoding="utf-8")) if ESTADO.exists() else {}
    if est.get("dia") != hoje:
        est = {"dia": hoje, "enviados": {}}
    novos = []
    for a in res["alertas"]:
        chave = a["cod"]; nivel = max(abs(a["z"] or 0), a["vrel"] or 0)
        antes = est["enviados"].get(chave)
        if antes is None or nivel >= 2 * antes:
            novos.append(a); est["enviados"][chave] = nivel
    if novos and telegram.disponivel():
        linhas = [f"<b>{a['cod']}</b> {_num(a['r1'] * 100, 1, True)}% · {a['txt']}" for a in novos[:12]]
        telegram.enviar(f"<b>Monitor · fora do padrão</b> ({res['hora'][11:]})\n" + "\n".join(linhas))
    ESTADO.write_text(json.dumps(est, ensure_ascii=False), encoding="utf-8")
    return len(novos)


def publicar(res: dict) -> None:
    """Commit + push do JSON e disparo do build no GitHub (o push em data/ não dispara sozinho)."""
    raiz = str(config.RAIZ)
    def git(*a):
        return subprocess.run(["git", *a], cwd=raiz, capture_output=True, text=True, timeout=120)
    git("add", str(ARQ.relative_to(config.RAIZ)))
    if git("diff", "--cached", "--quiet").returncode == 0:
        return
    git("commit", "-q", "-m", f"vigia: alertas em tempo real {res['hora']}")
    git("pull", "--rebase", "-q", "origin", "main"); push = git("push", "-q", "origin", "main")
    # O build do site é disparado pelo próprio push (o workflow tem exceção para data/alertas_tempo_real.json). Não usar
    # `gh workflow run` aqui: o Python do Python Install Manager (MSIX) virtualiza o AppData dos processos filhos e o gh
    # não enxerga o login (diz "not logged in") quando chamado de dentro do Python.
    with open(config.DATA / "vigia.log", "a", encoding="utf-8") as f:
        f.write(f"{res['hora']} push rc={push.returncode} {push.stderr.strip()[:160]}\n")


def main() -> int:
    agora = datetime.now()
    if "--forcar" not in sys.argv and (agora.weekday() >= 5 or not (ABRE <= agora.time() <= time(17, 40))):
        print("fora do pregão"); return 0
    snap = snapshot()
    if snap is None:
        print("MT5 indisponível"); return 1
    if not snap:
        print("sem negócios hoje ainda"); return 0
    res = avaliar(snap)
    ARQ.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    n = notificar(res)
    print(f"{res['hora']}: {len(res['papeis'])} papéis, {len(res['alertas'])} alertas, {n} enviados; pregão {res['fracao_pregao']:.0%}")
    if "--sem-push" not in sys.argv and "--forcar" not in sys.argv:
        publicar(res)
    mt5.desligar()
    return 0


if __name__ == "__main__":
    sys.exit(main())
