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
import shutil
import subprocess
import sys
from datetime import date, datetime, time, timedelta

import config
from fontes import b3, dap, di, mt5, telegram

ARQ = config.DATA / "alertas_tempo_real.json"
ESTADO = config.DATA / "alertas_estado.json"
INTRADAY = config.DATA / "intraday_hoje.json"                 # barras de 1 min de hoje: chip "hoje" do gráfico do Ibovespa
TICKERS_INTRADAY = ["IBOV", "WDO$", "WSP$", "DAPK35", "DAPK29", "RDOR3", "SAUD3"]
try:                                                      # + papéis dos trades (trades.json): intraday para o slide de trades
    import trades as _trades
    TICKERS_INTRADAY += sorted(_trades.tickers() - set(TICKERS_INTRADAY))
except Exception:
    pass   # futuros B3 com feed na Genial: dólar mini, micro S&P 500, cupom IPCA (juro real 2035/2029)
ABRE, FECHA = time(10, 0), time(17, 0)


def _num(v, d=1, sinal=False):
    s = f"{v:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("+" + s) if (sinal and v > 0) else s


def snapshot() -> dict | None:
    """{cod: {preco, hora, vol_hoje}} pelo MT5 (tick + barra diária de hoje). None se o terminal não está disponível."""
    if not mt5.disponivel():
        return None
    C = json.loads((config.DATA / "ibov_comp.json").read_text(encoding="utf-8"))
    # carteira do Ibovespa + BDRs acompanhados + cobertura (SAUD3 saiu do índice e precisa continuar no vigia)
    import trades
    cods = sorted({i["cod"] for i in C.get("itens", [])} | set(getattr(config, "BDRS", [])) | set(getattr(config, "UNIVERSO", {})) | trades.tickers())   # + papéis dos trades (trades.json)
    out = {}
    hoje = date.today().isoformat()
    for cod in cods:
        q = mt5.intraday(cod)
        if not q or not q.get("preco"):
            continue
        if q["hora"][:10] != hoje and "--forcar" not in sys.argv:   # sem negócio hoje ainda (ou pregão fechado): ignora
            continue
        out[cod] = {"preco": q["preco"], "hora": q["hora"], "vol_hoje": q.get("volume"),   # volume = quantidade de ações do dia (barra D1)
                    "fech_ant": q.get("fech_anterior")}                                       # fechamento da sessão anterior (barra D1 do MT5)
    return out


def avaliar(snap: dict) -> dict:
    """Junta o intraday com o histórico (COTAHIST) e calcula z, retorno e volume relativo projetado."""
    S = b3.series_todas(("fechamento", "quantidade", "volume"), desde="2025-06-01")
    agora = datetime.now()
    frac = min(1.0, max(0.0, ((agora.hour * 60 + agora.minute) - (ABRE.hour * 60)) / ((FECHA.hour - ABRE.hour) * 60)))
    zlim, vlim = getattr(config, "ALERTA_Z", 2.0), getattr(config, "ALERTA_VOL", 2.0)
    # Ibovespa: fechamentos diários (para o beta de 60 pregões) e retorno de hoje (BOVA11) para o alfa do dia
    ib_hist = _ibov_diario()
    rm_hoje = _ibov_r1_hoje()
    papeis, alertas = {}, []
    # base do retorno do dia: fechamento da sessão anterior. Se o COTAHIST está atrasado (não saiu ou a coleta falhou,
    # como na queda de rede de 29/09/2026), usa a barra D1 do MT5 — senão o "retorno de hoje" vira um retorno de 2 dias.
    ontem_util = agora.date() - timedelta(days=1)
    while ontem_util.weekday() >= 5:
        ontem_util -= timedelta(days=1)
    cotahist_ate = max((S[c][-1][0] for c in S if S[c]), default=None)
    base_mt5 = 0
    for cod, q in snap.items():
        s = [r for r in S.get(cod, []) if r[1]]
        if len(s) < 25:
            continue
        if s[-1][0] >= q["hora"][:10]:            # o COTAHIST de hoje já saiu: o fechamento "anterior" é o de ontem
            s = s[:-1]
        fech = [r[1] for r in s]; qtd = [r[2] for r in s]
        base = fech[-1]
        if q.get("fech_ant") and s[-1][0] < ontem_util.isoformat():
            base = q["fech_ant"]; base_mt5 += 1
        r1 = q["preco"] / base - 1
        rets = [math.log(fech[i] / fech[i - 1]) for i in range(max(1, len(fech) - 60), len(fech)) if fech[i - 1]]
        mu = sum(rets) / len(rets)
        sig = (sum((x - mu) ** 2 for x in rets) / (len(rets) - 1)) ** 0.5 if len(rets) > 10 else None
        z = (math.log(1 + r1) / sig) if (sig and r1 > -1) else None
        # alfa vs Ibovespa: beta e volatilidade residual em 60 pregões (datas comuns); alfa do dia = r1 − β·r_ibov
        beta, sig_a, alpha, za = _beta_residual(s, ib_hist)
        if beta is not None and sig_a and rm_hoje is not None and r1 > -1:
            alpha = math.log(1 + r1) - beta * math.log(1 + rm_hoje)
            za = alpha / sig_a
        qmed = sum(qtd[-21:]) / 21 if len(qtd) >= 21 else None
        vrel = (q["vol_hoje"] / (qmed * frac)) if (qmed and q.get("vol_hoje") and frac >= 30 / 420) else None   # projetado p/ o pregão inteiro
        flags = []
        if z is not None and abs(z) >= zlim:
            txt = f"oscilação {_num(r1 * 100, 1, True)}% = {_num(abs(z), 1)}σ"
            if za is not None:
                txt += f" (α {_num(alpha * 100, 1, True)}% = {_num(abs(za), 1)}σ, β {_num(beta, 1)})"
            flags.append(txt)
        if vrel is not None and vrel >= vlim:
            flags.append(f"volume {_num(vrel, 1)}× a média (projetado)")
        papeis[cod] = {"preco": q["preco"], "hora": q["hora"], "r1": r1, "z": z, "sig": sig, "vol_hoje": q.get("vol_hoje"), "vrel": vrel, "flags": flags,
                       "beta": beta, "alpha": alpha, "z_alpha": za}
        if flags:
            alertas.append({"cod": cod, "txt": " e ".join(flags), "score": (abs(z) if z else 0) + (vrel or 0), "z": z, "vrel": vrel, "r1": r1,
                            "alpha": alpha, "z_alpha": za, "beta": beta})
    alertas.sort(key=lambda a: -a["score"])
    if base_mt5:
        print(f"aviso: COTAHIST só até {cotahist_ate}; base do retorno de hoje pela barra D1 do MT5 em {base_mt5} papéis")
    return {"hora": agora.strftime("%Y-%m-%d %H:%M"), "fracao_pregao": round(frac, 3), "papeis": papeis, "alertas": alertas,
            "ibov": _ibov_proxy(papeis, S, frac), "cotahist_ate": cotahist_ate, "base_mt5": base_mt5}


def _ibov_diario() -> dict:
    """{data: fechamento} do Ibovespa (data/macro.json, série diária do Yahoo) para o beta."""
    try:
        M = json.loads((config.DATA / "macro.json").read_text(encoding="utf-8"))
        return {d: v for d, v in (M.get("hist", {}).get("Ibovespa") or []) if v}
    except Exception:
        return {}


def _ibov_r1_hoje() -> float | None:
    """Retorno do Ibovespa hoje pelo BOVA11 (ETF, mesma sessão); None se não houver tick de hoje."""
    try:
        q = mt5.intraday("BOVA11")
        if q and q.get("preco") and q.get("fech_anterior") and q["hora"][:10] == date.today().isoformat():
            return q["preco"] / q["fech_anterior"] - 1
    except Exception:
        pass
    return None


def _beta_residual(s: list, ib_hist: dict, n: int = 60):
    """Beta e desvio-padrão residual dos últimos n pregões com data comum entre o papel (s = [(data, fech, ...)]) e o
    Ibovespa. Devolve (beta, sig_residual, None, None) — alfa e z_alfa são preenchidos pelo chamador."""
    fech = {r[0]: r[1] for r in s if r[1]}
    datas = sorted(d for d in fech if d in ib_hist)[-(n + 1):]
    if len(datas) < 30:
        return None, None, None, None
    rs = [math.log(fech[b] / fech[a]) for a, b in zip(datas, datas[1:])]
    rm = [math.log(ib_hist[b] / ib_hist[a]) for a, b in zip(datas, datas[1:])]
    ms, mm = sum(rs) / len(rs), sum(rm) / len(rm)
    var = sum((x - mm) ** 2 for x in rm)
    if var <= 0:
        return None, None, None, None
    beta = sum((x - ms) * (y - mm) for x, y in zip(rs, rm)) / var
    res = [x - beta * y for x, y in zip(rs, rm)]
    mr = sum(res) / len(res)
    sig_a = (sum((x - mr) ** 2 for x in res) / (len(res) - 1)) ** 0.5
    return beta, sig_a, None, None


def _fech_anterior_ibov() -> float | None:
    """Último fechamento diário do Ibovespa anterior a hoje (barra D1 do MT5; o feed diário do índice funciona)."""
    try:
        from datetime import timedelta
        d = mt5.historico_diario("IBOV", datetime.now() - timedelta(days=15))
        d = [r for r in d if str(r.get("data", ""))[:10] < date.today().isoformat() and r.get("fechamento")]
        return float(d[-1]["fechamento"]) if d else None
    except Exception:
        return None


def _ibov_proxy(papeis: dict, S: dict, frac: float) -> dict:
    """Ibovespa do dia a partir da própria carteira (pesos oficiais de data/ibov_comp.json): variação = média dos retornos
    ponderada pelo peso; volume relativo = soma do volume de hoje ÷ soma das médias de 21 pregões × fração do pregão
    (o índice à vista não vem confiável do MT5 da Genial). Devolve {r1, vrel, n, cobertura} — cobertura = % do peso usado."""
    try:
        C = json.loads((config.DATA / "ibov_comp.json").read_text(encoding="utf-8"))
        peso = {i["cod"]: float(i.get("peso") or 0) for i in C.get("itens", [])}
    except Exception:
        peso = {}
    sw = sr = 0.0
    vh = vm = 0.0
    n = 0
    for cod, p in papeis.items():
        w = peso.get(cod, 0)
        if not w or p.get("r1") is None:
            continue
        sw += w; sr += w * p["r1"]; n += 1
        s = [r for r in S.get(cod, []) if r[1]]
        qtd = [r[2] for r in s][-21:]
        if p.get("vol_hoje") and len(qtd) >= 21 and frac >= 30 / 420:
            vh += p["vol_hoje"]; vm += (sum(qtd) / 21) * frac
    out = {"r1": (sr / sw) if sw else None, "vrel": (vh / vm) if vm else None, "n": n, "cobertura": round(sw, 1), "fonte_r1": "carteira"}
    # variação do dia preferencialmente pelo BOVA11 (ETF negociado na mesma sessão): um preço de mercado, sem o atraso das
    # últimas negociações de papéis ilíquidos; a carteira ponderada fica como reserva e para o volume
    try:
        q = mt5.intraday("BOVA11")
        if q and q.get("preco") and q.get("fech_anterior") and q["hora"][:10] == date.today().isoformat():
            out["r1_carteira"] = out["r1"]
            out["r1"] = q["preco"] / q["fech_anterior"] - 1
            out["fonte_r1"] = "BOVA11"
    except Exception:
        pass
    return out


def _linha_ibov(res: dict) -> str:
    """'Ibov −0,3% · volume 1,2× a média' (proxy pela carteira); vazio se não deu para calcular."""
    ib = res.get("ibov") or {}
    partes = []
    if ib.get("r1") is not None:
        partes.append(f"Ibov {_num(ib['r1'] * 100, 1, True)}%")
    if ib.get("vrel") is not None:
        partes.append(f"volume {_num(ib['vrel'], 1)}× a média")
    return " · ".join(partes)


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
        ib = _linha_ibov(res)
        telegram.enviar(f"<b>Monitor · fora do padrão</b> ({res['hora'][11:]})" + (f"\n<i>{ib}</i>" if ib else "") + "\n" + "\n".join(linhas))
    ESTADO.write_text(json.dumps(est, ensure_ascii=False), encoding="utf-8")
    return len(novos)


def exportar_intraday() -> int:
    """Barras de 1 min de hoje (Ibovespa, dólar futuro e cobertura) em data/intraday_hoje.json; o build mescla no
    extrato intraday e o chip "hoje" do Painel mostra o dia ao vivo (atraso = cadência do vigia). Devolve nº de séries."""
    hoje = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    d = hoje.strftime("%Y-%m-%d")
    series, proxy, fech = {}, {}, {}
    def barras(tk):
        try:
            bars = mt5.historico_m1(tk, hoje.replace(hour=9))
        except Exception:
            bars = []
        return [[b["hora"], float(b["fechamento"])] for b in bars if b.get("hora") and str(b["hora"])[:10] == d and b.get("fechamento")]
    for tk in TICKERS_INTRADAY:
        pts = barras(tk)
        if not pts and tk == "IBOV":
            # O símbolo IBOV da Genial não tem tick/barras intraday (feed parado). Proxy à vista: BOVA11 (ETF do índice,
            # mesma sessão, sem base de futuro), reescalado para pontos pelo fechamento diário anterior do índice; se o
            # ETF também faltar, o futuro cheio IND$.
            b = barras("BOVA11")
            q = mt5.intraday("BOVA11")
            fech_ibov = _fech_anterior_ibov()
            if b and q and q.get("fech_anterior") and fech_ibov:
                k = fech_ibov / q["fech_anterior"]
                pts = [[h, round(v * k, 0)] for h, v in b]
                proxy[tk] = "BOVA11"
            else:
                pts = barras("IND$")
                if pts:
                    proxy[tk] = "IND$"
        if pts:
            series[tk] = pts
            if tk != "IBOV":                                   # fechamento da sessão anterior (barra D1) para a variação do dia
                try:
                    q = mt5.intraday(tk)
                    if q and q.get("fech_anterior"):
                        fech[tk] = q["fech_anterior"]
                except Exception:
                    pass
    if series:
        INTRADAY.write_text(json.dumps({"data": d, "hora": datetime.now().strftime("%Y-%m-%d %H:%M"), "series": series, "proxy": proxy, "fech_ant": fech},
                                       ensure_ascii=False), encoding="utf-8")
    return len(series)


DIARIO = config.DATA / "vigia_diario.csv"


def exportar_di() -> None:
    """Curva DI: fotografia dos contratos e vértices (data/di_curva.json) a cada passada; histórico diário (D1) dos
    contratos de janeiro (data/di_hist.json) no máximo uma vez por hora. Falha do MT5 não derruba o vigia."""
    try:
        di.snapshot()
        idade = (datetime.now().timestamp() - di.HIST.stat().st_mtime) / 60 if di.HIST.exists() else 1e9
        if idade > 60:
            di.atualiza_historico()
    except Exception as e:
        print(f"  DI: {e}", file=sys.stderr)
    try:                                   # juro real (DAP = cupom de IPCA): mesma rotina, arquivos data/dap_*.json
        dap.snapshot()
        idade = (datetime.now().timestamp() - dap.HIST.stat().st_mtime) / 60 if dap.HIST.exists() else 1e9
        if idade > 60:
            dap.atualiza_historico()
    except Exception as e:
        print(f"  DAP: {e}", file=sys.stderr)


def registrar_diario(res: dict) -> None:
    """Uma linha por dia (a última passada sobrescreve a do mesmo dia): Ibov do dia (BOVA11), volume relativo do mercado,
    quantidade total negociada nos papéis do índice e nº de alertas. O alertas_tempo_real.json é sobrescrito a cada
    passada; este CSV é o registro que fica (o volume financeiro oficial continua vindo do COTAHIST na janela diária)."""
    ib = res.get("ibov") or {}
    tot = sum((p.get("vol_hoje") or 0) for p in res["papeis"].values())
    linha = [res["hora"][:10], res["hora"][11:], f"{res['fracao_pregao']:.2f}", f"{ib['r1'] * 100:.2f}" if ib.get("r1") is not None else "",
             f"{ib['vrel']:.2f}" if ib.get("vrel") is not None else "", str(int(tot)), str(len(res["papeis"])), str(len(res["alertas"])),
             ";".join(a["cod"] for a in res["alertas"])]
    cab = "data,hora,fracao_pregao,ibov_var_pct,ibov_vol_rel,qtd_negociada_indice,papeis,alertas,codigos\n"
    antigas = DIARIO.read_text(encoding="utf-8").splitlines()[1:] if DIARIO.exists() else []
    antigas = [l for l in antigas if not l.startswith(linha[0] + ",")]
    DIARIO.write_text(cab + "\n".join(antigas + [",".join(linha)]) + "\n", encoding="utf-8")


def publicar(res: dict) -> None:
    """Commit + push do JSON de alertas (+ intraday de hoje); o push dispara o build no GitHub (exceção em `paths`)."""
    raiz = str(config.RAIZ)
    def git(*a):
        return subprocess.run(["git", *a], cwd=raiz, capture_output=True, text=True, timeout=120)
    # Saneamento: um `pull --rebase` que parou em conflito (ex.: 30/09/2026 13:45, contra o commit mensal do GitHub) deixa
    # o repositório em rebase com HEAD solto; commits seguintes caem fora de main e todo push é rejeitado. Aborta e reata.
    for d in (".git/rebase-merge", ".git/rebase-apply"):
        if (config.RAIZ / d).exists():
            if git("rebase", "--abort").returncode != 0:
                shutil.rmtree(config.RAIZ / d, ignore_errors=True)
    if git("symbolic-ref", "-q", "HEAD").returncode != 0:          # HEAD solto: reata main aqui (mantém os commits locais)
        git("checkout", "-q", "-B", "main")
    git("add", str(ARQ.relative_to(config.RAIZ)))
    if INTRADAY.exists():
        git("add", str(INTRADAY.relative_to(config.RAIZ)))
    if DIARIO.exists():
        git("add", str(DIARIO.relative_to(config.RAIZ)))
    for p in (di.ARQ, di.HIST, dap.ARQ, dap.HIST):
        if p.exists():
            git("add", str(p.relative_to(config.RAIZ)))
    if git("diff", "--cached", "--quiet").returncode == 0:
        return
    git("commit", "-q", "-m", f"vigia: alertas em tempo real {res['hora']}")
    # Rebase favorecendo os commits locais (-X theirs = o que está sendo reaplicado); se ainda assim travar, aborta e
    # funde origin/main preferindo a versão local dos arquivos gerados (nunca fica em rebase pela metade).
    pull = git("pull", "--rebase", "-X", "theirs", "-q", "origin", "main")
    if pull.returncode != 0 and "would be overwritten" in (pull.stderr or ""):
        # arquivo de cache criado localmente (ex.: coletar.py rodado à mão) que o GitHub passou a versionar: apaga a cópia
        # local não rastreada e tenta de novo (01/10/2026: data/cda/*.json travou os pushes das 13h às 17h35)
        for ln in pull.stderr.splitlines():
            ln = ln.strip()
            if ln and not ln.startswith(("error", "Please", "Aborting", "fatal")) and (config.RAIZ / ln).exists():
                try:
                    (config.RAIZ / ln).unlink()
                except OSError:
                    pass
        pull = git("pull", "--rebase", "-X", "theirs", "-q", "origin", "main")
    if pull.returncode != 0:
        git("rebase", "--abort")
        git("fetch", "-q", "origin", "main")
        git("merge", "-q", "-X", "ours", "--no-edit", "origin/main")
    push = git("push", "-q", "origin", "main")
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
    ni = exportar_intraday()
    exportar_di()
    registrar_diario(res)
    print(f"{res['hora']}: {len(res['papeis'])} papéis, {len(res['alertas'])} alertas, {n} enviados; pregão {res['fracao_pregao']:.0%}; intraday {ni} séries")
    if "--sem-push" not in sys.argv and "--forcar" not in sys.argv:
        publicar(res)
    mt5.desligar()
    return 0


if __name__ == "__main__":
    sys.exit(main())
