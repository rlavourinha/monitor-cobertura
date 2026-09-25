"""Lê a MINHA estimativa direto de um modelo Excel (aba E-A / Painel no padrão JGP).

Padrão esperado (TimMOD/CYREMod): rótulos na coluna B, anos na linha de cabeçalho.
Os rótulos são procurados por regex (não hardcode de célula), então inserir linhas
no modelo não quebra. Mapeamento por empresa em config.UNIVERSO[tk]['modelo'] =
{"arquivo": caminho, "aba": "E-A", "rotulos": {"lucro": r"lucro l[ií]quido", ...}}.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import config

ROTULOS_PADRAO = {
    "receita": r"^receita (l[ií]quida|total)",
    "ebitda": r"^ebitda( ajustado)?$",
    "lucro": r"^lucro l[ií]quido",
    "target": r"(pre[çc]o[- ]alvo|target)",
}


def le_modelo(ticker: str, spec: dict) -> list[dict]:
    import openpyxl
    arq = Path(spec["arquivo"])
    if not arq.exists():
        print(f"  modelo {ticker}: não encontrado em {arq}")
        return []
    wb = openpyxl.load_workbook(arq, data_only=True, read_only=True)
    ws = wb[spec.get("aba", "E-A")]
    rotulos = {**ROTULOS_PADRAO, **spec.get("rotulos", {})}
    rows = list(ws.iter_rows(values_only=True))
    # linha de anos: a primeira que contém >= 2 inteiros entre 2000 e 2100
    col_ano = {}
    for row in rows[:15]:
        anos = {j: int(v) for j, v in enumerate(row) if isinstance(v, (int, float)) and 2000 <= v <= 2100}
        if len(anos) >= 2:
            col_ano = {a: j for j, a in anos.items()}
            break
    if not col_ano:
        print(f"  modelo {ticker}: linha de anos não encontrada")
        return []
    dt = datetime.fromtimestamp(arq.stat().st_mtime).date().isoformat()
    achados: dict[int, dict] = {a: {} for a in config.ANOS_FISCAIS}
    for row in rows:
        rot = next((str(v).strip() for v in row[:3] if isinstance(v, str)), None)
        if not rot:
            continue
        for campo, rx in rotulos.items():
            if re.search(rx, rot, re.I):
                for ano in config.ANOS_FISCAIS:
                    j = col_ano.get(ano)
                    if j is not None and isinstance(row[j], (int, float)) and campo not in achados[ano]:
                        achados[ano][campo] = float(row[j])
    out = []
    for ano, d in achados.items():
        if not d:
            continue
        out.append({"ticker": ticker, "data": dt, "ano": ano, "receita": d.get("receita"),
                    "ebitda": d.get("ebitda"), "lucro": d.get("lucro"), "eps": None,
                    "target": d.get("target"), "n_analistas": None, "fonte": f"modelo:{arq.name}"})
    return out
