#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Leituras automáticas via Claude Code CLI headless (modelo Sonnet 5), para rodar como
Tarefa Agendada do Windows (ver agendar_leituras.ps1) no lugar dos crons de sessão.

Modos (um por rotina):
    python leitura_headless.py --manha   # jornais + sell-side (ex-cron 48 8)
    python leitura_headless.py --dia      # só sell-side, edição noite (ex-cron 4 19)
    python leitura_headless.py --extra    # edição extra dos jornais em dia de evento (ex-cron 33 12)

Opções: --sem-telegram (não manda nada), --sem-claude (pula o modelo; só testa o pipe de coleta).

Divisão de trabalho: este script roda os passos de SCRIPT (coleta/montar e envio) importando
`leitura` como módulo; o MODELO é chamado só para o trabalho cognitivo — ler o material e ESCREVER
o digest JSON. Por isso o claude roda com --allowedTools restrito a Read/Write/Glob/Grep (não roda
shell, não baixa nada, não commita). Coleta e envio ficam com o Python, que valida o JSON antes de mandar.

Log: data/leitura_headless.log (hora, modelo, tamanho do digest, resultado do envio).
Em falha, manda uma linha pelo bot padrão (fontes.telegram.enviar).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

import config  # noqa: E402
import leitura  # noqa: E402
from fontes import telegram  # noqa: E402

MODELO = "claude-sonnet-5"
LOG = config.DATA / "leitura_headless.log"
JORNAIS_DIR = RAIZ.parent / "jornais-resumo-diario"

# ---------------------------------------------------------------------------- prompts
# Adaptados dos crons de sessão: o modelo só LÊ o material e ESCREVE o digest JSON.
# Nada de shell/coleta/envio aqui (isso é do wrapper Python).

_REGRAS_GRAFICO = (
    'grafico: "fiscal" | "inflacao" | "juros" | "cambio" | "fluxo" | "volume" | "ibov" | '
    '"credito" | "atividade" | "emprego" | "commodities" | {"tipo":"setor","setor":"<setor do Ibov>"} | '
    '{"tipo":"papel","ticker":"RDOR3"} | {"tipo":"papeis","tickers":[...]} | null'
)

PROMPT_JORNAIS = """Tarefa automática (não é um pedido interativo; não faça perguntas). Você vai gerar o digest dos JORNAIS do dia.
NÃO rode comandos de shell, NÃO baixe nada, NÃO commite: só use Read para ler e Write para gravar o arquivo abaixo.

1. Leia o arquivo INTEIRO: {MATERIAL}
   (todas as matérias, texto integral; se for grande, leia em partes com offset/limit no Read).
2. Escreva (Write) o arquivo: {DIGEST}
   com este JSON exato: {{"data": "AAAA-MM-DD", "edicao": "{EDICAO}", "cabecalho": "<{CAB}>", "blocos": [{{"titulo", "texto", "grafico", "ids"}}], "fora": [ids]}}

Regras:
- SEM teto de itens (dias com mais ou menos conteúdo faz parte). Blocos por assunto com substância
  (eleição/política, fiscal e juros, atividade, câmbio/fluxo, petróleo/commodities, empresas e setores do Ibov,
  cobertura RDOR3/SAUD3/RENT3/CYRE3/CURY3, internacional, tecnologia). Fontes primárias (Tesouro, Anbima, B3) entram pelo dado, com o número.
- Mesma notícia em vários jornais = um só bloco citando todos os ids (dedupe). Matérias em inglês, traduzidas.
- Cada texto em HTML simples do Telegram (<b>, <i>; nada de & < > soltos), com <b>O que dizem.</b> (fatos, números, datas,
  divergências entre jornais), <b>Dado primário.</b> quando houver gráfico e <b>Nossa leitura.</b> (o que muda para o book
  e para as teses da cobertura; contrarian quando o consenso dos jornais for unânime).
- Colunas e editoriais ENTRAM, num bloco próprio {{"titulo": "Colunas e editoriais do dia", "opiniao": true, ...}}: para cada
  coluna, <b>Colunista</b> (jornal): argumento em 1-3 linhas + <i>Viés:</i> linha editorial/posição do autor e o que é fato versus inferência.
- "ids" = índices [n] do material; toda matéria com substância deve estar em algum bloco (o que ficar de fora será listado como "vista, não entrou").
  "fora" = ids de cultura, esporte, lazer, novela, variedades, eventos institucionais.
- {GRAFICO} — use quando o dado primário ilustra o bloco.
{EXTRA}
Ao terminar de gravar o arquivo, responda apenas "ok"."""

PROMPT_SELLSIDE = """Tarefa automática (não é um pedido interativo; não faça perguntas). Você vai gerar o digest do SELL-SIDE.
NÃO rode comandos de shell, NÃO baixe nada, NÃO commite: só use Read para ler e Write para gravar o arquivo abaixo.

1. Leia o arquivo INTEIRO: {MATERIAL}
2. Escreva (Write) o arquivo: {DIGEST}
   com este JSON exato: {{"data": "AAAA-MM-DD", "edicao": "{EDICAO}", "cabecalho": "<um parágrafo>", "blocos": [{{"titulo", "texto", "grafico", "ids"}}]}}

Regras:
- {NBLOCOS} blocos pelos temas com substância (fiscal, juros, inflação, atividade/emprego, crédito, câmbio/fluxo, commodities,
  estratégia/carteiras como 10SIM e baskets, setores do Ibov, cobertura RDOR3/SAUD3/RENT3/CYRE3/CURY3).
- "ids" = ids dos relatórios usados no bloco, exatamente como aparecem no material (id=...); todo relatório com substância deve aparecer em algum bloco.
- Cada texto em HTML simples do Telegram (<b>, <i>), até ~900 caracteres, com três partes: <i>O que dizem.</i> (BTG e Itaú, números e
  divergências entre eles), <i>Dado primário.</i> (o número da nossa série) e <i>Nossa leitura.</i> (conclusão própria, contrarian quando o
  dado não sustenta a casa; o que muda para as teses da cobertura).
- {GRAFICO}
- LatAm ex-Brasil, diários e renda fixa ficam fora.
Ao terminar de gravar o arquivo, responda apenas "ok"."""


def _prompt_jornais(digest: Path, edicao: str, extra: str = "") -> str:
    cab = ("2-4 linhas: o evento e o que mudou desde a manhã" if edicao == "evento"
           else "4-6 linhas: o dia em um parágrafo")
    return PROMPT_JORNAIS.format(MATERIAL=leitura.JORNAIS_MD, DIGEST=digest, EDICAO=edicao,
                                 CAB=cab, GRAFICO=_REGRAS_GRAFICO, EXTRA=extra)


def _prompt_sellside(digest: Path, edicao: str, nblocos: str) -> str:
    return PROMPT_SELLSIDE.format(MATERIAL=leitura.MATERIAL_MD, DIGEST=digest, EDICAO=edicao,
                                  NBLOCOS=nblocos, GRAFICO=_REGRAS_GRAFICO)


# ---------------------------------------------------------------------------- infra
def _log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    linha = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(linha + "\n")
    print(linha)


_SILENCIO = False  # setado por --sem-telegram: não manda nada (nem alertas)


def _alerta(linha: str) -> None:
    """Uma linha pelo bot padrão (Monitor) quando algo falha."""
    if _SILENCIO:
        _log(f"alerta (silenciado): {linha}")
        return
    try:
        telegram.enviar(f"<b>Leitura headless</b> ⚠️ {linha}")
    except Exception as e:  # nunca deixar o alerta derrubar o script
        _log(f"alerta falhou: {e}")


def _claude_exe() -> str | None:
    """Resolve o executável do CLI (o shim npm nem sempre está no PATH da Tarefa Agendada)."""
    for cand in ("claude", "claude.cmd"):
        p = shutil.which(cand)
        if p:
            return p
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        for nome in ("claude.cmd", "claude.exe", "claude"):
            p = Path(appdata) / "npm" / nome
            if p.exists():
                return str(p)
    return None


def _chama_claude(prompt: str, rotulo: str, sem_claude: bool = False) -> Path | None:
    """Roda `claude -p` com o prompt (via stdin), restrito a Read/Write/Glob/Grep. Devolve None em falha."""
    if sem_claude:
        _log(f"[{rotulo}] --sem-claude: pulando o modelo")
        return None
    exe = _claude_exe()
    if not exe:
        _log(f"[{rotulo}] CLI 'claude' não encontrado no PATH nem em %APPDATA%\\npm")
        _alerta(f"{rotulo}: CLI claude não encontrado")
        return None
    t0 = time.time()
    cmd = [exe, "-p", "--model", MODELO, "--output-format", "text",
           "--permission-mode", "acceptEdits",
           "--allowedTools", "Read", "Write", "Glob", "Grep",
           "--max-turns", "40"]
    try:
        r = subprocess.run(cmd, input=prompt, cwd=str(RAIZ), capture_output=True,
                           text=True, encoding="utf-8", timeout=1200)
    except subprocess.TimeoutExpired:
        _log(f"[{rotulo}] claude TIMEOUT (1200s)")
        return None
    dt = time.time() - t0
    saida = (r.stdout or "").strip()
    if "Not logged in" in saida or "Please run /login" in saida:
        _log(f"[{rotulo}] claude NÃO LOGADO — rode `claude` uma vez e autentique")
        _alerta(f"{rotulo}: CLI não logado (rode `claude` e /login)")
        return None
    if r.returncode != 0:
        _log(f"[{rotulo}] claude rc={r.returncode} em {dt:.0f}s · stderr={(r.stderr or '')[:300]}")
        return None
    _log(f"[{rotulo}] claude ok em {dt:.0f}s · modelo={MODELO} · resposta={saida[:80]!r}")
    return RAIZ  # marcador de sucesso; o arquivo é validado por quem chamou


def _valida_digest(arq: Path, t0: float, rotulo: str) -> dict | None:
    """Garante que o digest foi (re)escrito agora e tem a estrutura mínima."""
    if not arq.exists():
        _log(f"[{rotulo}] digest não foi escrito: {arq}")
        return None
    if arq.stat().st_mtime < t0 - 2:
        _log(f"[{rotulo}] digest é antigo (não foi reescrito nesta rodada): {arq}")
        return None
    try:
        d = json.loads(arq.read_text(encoding="utf-8"))
    except Exception as e:
        _log(f"[{rotulo}] digest não é JSON válido: {e}")
        return None
    blocos = d.get("blocos")
    if not isinstance(blocos, list) or not blocos:
        _log(f"[{rotulo}] digest sem 'blocos'")
        return None
    for b in blocos:
        if not (b.get("titulo") and b.get("texto")):
            _log(f"[{rotulo}] bloco sem titulo/texto")
            return None
    _log(f"[{rotulo}] digest ok · {len(blocos)} blocos · {arq.stat().st_size} bytes")
    return d


# ---------------------------------------------------------------------------- partes
def parte_jornais(edicao: str, novos: bool, extra_prompt: str, sem_claude: bool, sem_telegram: bool) -> str:
    r = leitura.montar_jornais(novos=novos)
    col, n = r.get("coleta"), r.get("n", 0)
    hoje = datetime.now().strftime("%Y-%m-%d")
    if not novos:  # manhã: a coleta das 06:00 tem que ser de hoje
        col_data = (col or "")[:10]
        if col_data != hoje or n == 0:
            return f"Jornais: coleta de hoje não rodou (última {col})"
    if n == 0:
        return f"Jornais ({edicao}): nada novo"
    t0 = time.time()
    digest = config.OUTPUT / "jornais_digest.json"
    if _chama_claude(_prompt_jornais(digest, edicao, extra_prompt), f"jornais/{edicao}", sem_claude) is None and not sem_claude:
        _alerta(f"jornais/{edicao}: digest não gerado pelo modelo")
        return f"Jornais ({edicao}): modelo falhou"
    if _valida_digest(digest, t0, f"jornais/{edicao}") is None:
        _alerta(f"jornais/{edicao}: digest inválido")
        return f"Jornais ({edicao}): digest inválido"
    env = leitura.enviar_jornais(str(digest), sem_telegram=sem_telegram)
    _log(f"[jornais/{edicao}] enviar: {json.dumps(env, ensure_ascii=False)}")
    if env.get("falhas"):
        _alerta(f"jornais/{edicao}: {env['falhas']} falha(s) no envio")
    return f"Jornais ({edicao}): {n} matérias, {env.get('blocos')} blocos, push {env.get('push')}"


def parte_sellside(edicao: str, nblocos: str, sem_claude: bool, sem_telegram: bool) -> str:
    r = leitura.montar()
    n = r.get("n", 0)
    if n == 0:
        return "Sell-side: nada novo"
    t0 = time.time()
    digest = config.OUTPUT / "leitura_digest.json"
    if _chama_claude(_prompt_sellside(digest, edicao, nblocos), f"sellside/{edicao}", sem_claude) is None and not sem_claude:
        _alerta(f"sellside/{edicao}: digest não gerado pelo modelo")
        return f"Sell-side ({edicao}): modelo falhou"
    if _valida_digest(digest, t0, f"sellside/{edicao}") is None:
        _alerta(f"sellside/{edicao}: digest inválido")
        return f"Sell-side ({edicao}): digest inválido"
    env = leitura.enviar(str(digest), sem_telegram=sem_telegram)
    _log(f"[sellside/{edicao}] enviar: {json.dumps(env, ensure_ascii=False)}")
    if env.get("falhas"):
        _alerta(f"sellside/{edicao}: {env['falhas']} falha(s) no envio")
    return f"Sell-side ({edicao}): {n} relatórios, {env.get('blocos')} blocos, push {env.get('push')}"


# ---------------------------------------------------------------------------- rotinas
def manha(sem_claude: bool, sem_telegram: bool) -> None:
    _log("=== MANHÃ ===")
    a = parte_jornais("manhã", novos=False, extra_prompt="", sem_claude=sem_claude, sem_telegram=sem_telegram)
    b = parte_sellside("manhã", "2 a 5", sem_claude, sem_telegram)
    _log(f"MANHÃ fim · {a} · {b}")


def dia(sem_claude: bool, sem_telegram: bool) -> None:
    _log("=== DIA ===")
    b = parte_sellside("noite", "3 a 7", sem_claude, sem_telegram)
    _log(f"DIA fim · {b}")


def extra(sem_claude: bool, sem_telegram: bool) -> None:
    _log("=== EXTRA ===")
    ev = leitura.evento_hoje().get("evento")
    if not ev:
        _log("EXTRA fim · sem evento, sem edição extra")
        return
    _log(f"evento de hoje: {ev}")
    # coleta do momento (só coleta; ~10 min) no projeto dos jornais
    run_daily = JORNAIS_DIR / "run_daily.py"
    if run_daily.exists():
        try:
            cr = subprocess.run([sys.executable, "run_daily.py"], cwd=str(JORNAIS_DIR),
                                capture_output=True, text=True, encoding="utf-8", timeout=1200)
            _log(f"run_daily rc={cr.returncode}")
        except subprocess.TimeoutExpired:
            _log("run_daily TIMEOUT")
            _alerta("extra: run_daily (coleta) estourou o tempo")
            return
    extra_regra = ("Foque no EVENTO de hoje: o número divulgado contra o esperado, a leitura dos jornais e o que muda "
                   "para juros, câmbio e cobertura. grafico: 'inflacao' para IPCA/IPCA-15, 'fiscal', 'juros', 'cambio', "
                   "'fluxo', 'ibov', 'commodities', setor/papel, ou null.")
    a = parte_jornais("evento", novos=True, extra_prompt=extra_regra, sem_claude=sem_claude, sem_telegram=sem_telegram)
    _log(f"EXTRA fim (evento {ev}) · {a}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    sem_claude = "--sem-claude" in args
    sem_telegram = "--sem-telegram" in args
    _SILENCIO = sem_telegram
    try:
        if "--manha" in args:
            manha(sem_claude, sem_telegram)
        elif "--dia" in args:
            dia(sem_claude, sem_telegram)
        elif "--extra" in args:
            extra(sem_claude, sem_telegram)
        else:
            print(__doc__)
            sys.exit(2)
    except Exception as e:
        import traceback
        _log(f"ERRO FATAL: {e}\n{traceback.format_exc()}")
        _alerta(f"erro fatal: {e}")
        sys.exit(1)
