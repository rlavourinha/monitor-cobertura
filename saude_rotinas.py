"""Vigia das rotinas: avisa no Telegram quando uma coleta NÃO RODOU (não só quando falha).

Motivo (22/10/2026): as coletas do Itaú BBA e do Bradesco eram agendamentos de uma sessão do Claude, venceram em 20/10
sem aviso, nada rodou em 21/10 e a sessão do portal do Itaú BBA caiu por inatividade. Cada coleta avisava quando falhava,
mas ninguém avisava quando uma coleta simplesmente não acontecia.

Checa (tarefa do Windows "Monitor saude das rotinas", de hora em hora):
- Itaú BBA e Bradesco: batimento data/<casa>/ultima_execucao.txt (gravado no passo --conhecidos de toda coleta) contra
  os horários previstos em dias úteis;
- sessão do portal do Itaú BBA: data/bba/sessao.json (gravado pela visita de manutenção a cada 3-4 h);
- tarefas do Windows (BTG, checagem de preços, cartas, vigia): última execução e resultado;
- vigia de preços no pregão: última linha de data/vigia.log;
- GitHub Actions: última coleta agendada com sucesso.
Cada problema é avisado uma vez a cada 6 h (data/saude_estado.json). `python saude_rotinas.py --teste` só imprime.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, datetime, time, timedelta

import config

ESTADO = config.DATA / "saude_estado.json"
REPETE_H = 6
# coletas pelo Chrome: horários previstos (dias úteis) e tolerância para o atraso do agendador
# Itaú BBA: desde 23/10 a coleta roda dentro da visita de sessão (a cada 3 h, uma aba só); o batimento só é gravado
# quando a sessão está ok, e a sessão caída já é avisada pelo sessao.json
COLETAS = {"bba": ("Itaú BBA", [time(7, 40), time(10, 40), time(13, 40), time(16, 40), time(19, 40), time(22, 40)]),
           "bradesco": ("Bradesco", [time(9, 5), time(19, 5)])}
TOLERANCIA = timedelta(minutes=75)
# tarefas do Windows: nome -> horas máximas desde a última execução em dia útil (None = só olha o resultado)
TAREFAS = {"Monitor BTG relatorios": 14, "Monitor checagem de precos": 30, "Monitor cartas de gestao": 30}


def _ultimo_previsto(agora: datetime, horarios: list[time]) -> datetime | None:
    """Último horário previsto que já deveria ter rodado (com tolerância), voltando até 4 dias em dias úteis."""
    for k in range(0, 5):
        d = (agora - timedelta(days=k)).date()
        if d.weekday() >= 5:
            continue
        for h in sorted(horarios, reverse=True):
            t = datetime.combine(d, h)
            if t + TOLERANCIA <= agora:
                return t
    return None


def _ler(p, padrao=None):
    try:
        return p.read_text(encoding="utf-8").strip()
    except Exception:
        return padrao


def checa_coletas(agora: datetime) -> list[str]:
    out = []
    s = _ler(config.DATA / "bba" / "sessao.json")
    bba_caiu = bool(s) and '"login"' in s
    for casa, (nome, horarios) in COLETAS.items():
        prev = _ultimo_previsto(agora, horarios)
        if not prev or (casa == "bba" and bba_caiu):    # sessão caída já vira aviso próprio abaixo
            continue
        ult = _ler(config.DATA / casa / "ultima_execucao.txt")
        try:
            ult_dt = datetime.strptime(ult, "%Y-%m-%d %H:%M") if ult else None
        except ValueError:
            ult_dt = None
        if ult_dt is None or ult_dt < prev - timedelta(minutes=10):
            out.append(f"{nome}: a coleta das {prev:%H:%M} de {prev:%d/%m} não rodou (última: {ult or 'nunca'}). "
                       f"Agendamento parado ou app fechado.")
    s = _ler(config.DATA / "bba" / "sessao.json")
    if s:
        try:
            S = json.loads(s)
            if S.get("estado") == "login":
                out.append(f"Itaú BBA: sessão do portal caiu ({S.get('hora')}). Entre de novo no portal no Chrome (senha salva, só clicar em Continuar).")
            elif S.get("hora") and datetime.strptime(S["hora"], "%Y-%m-%d %H:%M") < agora - timedelta(hours=9):
                out.append(f"Itaú BBA: a visita de manutenção da sessão não roda desde {S['hora']}.")
        except Exception:
            pass
    return out


def checa_tarefas(agora: datetime) -> list[str]:
    out = []
    ps = ("Get-ScheduledTask | Where-Object {$_.TaskName -like 'Monitor*'} | ForEach-Object { $i = $_ | Get-ScheduledTaskInfo; "
          "[pscustomobject]@{n=$_.TaskName; s=[string]$_.State; r=$i.LastTaskResult; t=$i.LastRunTime.ToString('yyyy-MM-dd HH:mm')} } | ConvertTo-Json")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60)
        L = json.loads(r.stdout or "[]")
        L = L if isinstance(L, list) else [L]
    except Exception as e:
        return [f"saúde: não consegui ler as tarefas do Windows ({e})"]
    por = {x["n"]: x for x in L}
    for nome, horas in TAREFAS.items():
        x = por.get(nome)
        if not x:
            out.append(f"tarefa do Windows '{nome}' não existe mais.")
            continue
        if x.get("s") == "Disabled":
            out.append(f"tarefa do Windows '{nome}' está desativada.")
            continue
        if x.get("r") not in (0, 267009, 267011):           # 267009 = rodando agora; 267011 = ainda não rodou
            out.append(f"tarefa '{nome}' terminou com erro {x.get('r')} em {x.get('t')}.")
        try:
            t = datetime.strptime(x["t"], "%Y-%m-%d %H:%M")
            if horas and agora.weekday() < 5 and agora - t > timedelta(hours=horas) and agora.time() > time(9, 30):
                out.append(f"tarefa '{nome}' não roda desde {t:%d/%m %H:%M}.")
        except Exception:
            pass
    return out


def checa_vigia(agora: datetime) -> list[str]:
    if agora.weekday() >= 5 or not (time(10, 30) <= agora.time() <= time(17, 30)):
        return []
    linhas = (_ler(config.DATA / "vigia.log", "") or "").splitlines()
    if not linhas:
        return ["vigia de preços: sem log."]
    try:
        t = datetime.strptime(linhas[-1][:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return []
    if t.date() == agora.date() and agora - t <= timedelta(minutes=25):
        return []
    # feriado na B3: o vigia escreve "sem pregão" e não publica; não é falha
    return [f"vigia de preços: última publicação {linhas[-1][:16]} (pregão aberto). MT5 fechado ou tarefa parada?"]


def checa_github(agora: datetime) -> list[str]:
    import urllib.request                      # repositório público: API sem login (o gh da tarefa do Windows não tem token)
    try:
        req = urllib.request.Request("https://api.github.com/repos/rlavourinha/monitor-cobertura/actions/runs?per_page=50",
                                     headers={"User-Agent": "saude-rotinas", "Accept": "application/vnd.github+json"})
        R = json.loads(urllib.request.urlopen(req, timeout=60).read()).get("workflow_runs", [])
    except Exception as e:
        return [f"GitHub: não consegui consultar as execuções ({e})"]
    ag = [x for x in R if x["event"] in ("schedule", "workflow_dispatch") and x.get("conclusion") == "success"]
    if not ag:
        return ["GitHub: nenhuma coleta agendada com sucesso nas últimas 50 execuções."]
    ult = datetime.fromisoformat(ag[0]["created_at"].replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
    if agora - ult > timedelta(hours=16):
        return [f"GitHub: a última coleta agendada com sucesso foi em {ult:%d/%m %H:%M}."]
    return []


def main() -> int:
    agora = datetime.now()
    probs = checa_coletas(agora) + checa_tarefas(agora) + checa_vigia(agora) + checa_github(agora)
    E = json.loads(_ler(ESTADO, "{}") or "{}")
    novos = [p for p in probs if not E.get(p) or datetime.fromisoformat(E[p]) < agora - timedelta(hours=REPETE_H)]
    E = {p: (agora.isoformat(timespec="minutes") if p in novos else E.get(p)) for p in probs}   # problema resolvido sai do estado
    ESTADO.write_text(json.dumps(E, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{agora:%Y-%m-%d %H:%M} saúde: {len(probs)} problema(s), {len(novos)} novo(s)")
    for p in probs:
        print("  -", p)
    if novos and "--teste" not in sys.argv:
        from fontes import telegram
        telegram.enviar("⚠️ <b>Rotinas do screening</b>\n" + "\n".join(f"• {p}" for p in novos))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
