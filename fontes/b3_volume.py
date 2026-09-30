"""Volume financeiro diário de TODA a B3 (mercado à vista) a partir do COTAHIST — o "value traded" do mercado.

O cache data/cotahist/{ano}.csv guarda só os papéis do universo; aqui somamos todos os registros do arquivo por dia
antes de filtrar. Série em data/b3_volume_diario.csv (versionada), uma linha por pregão:

  data, total, acoes, bdr, etf, fii, fracionario, negocios, papeis

  total       R$ de todo o mercado à vista (TPMERC 010 lote-padrão + 020 fracionário), qualquer CODBDI
  acoes       ações e units no lote-padrão (CODBDI 02/05..11: ON, PN, UNT...)
  bdr         BDRs (CODBDI 34/35/36, ESPECI DR*)
  etf         ETFs (CODBDI 14, ESPECI CI; e IBOV11)
  fii         FIIs/Fiagro/FI-Infra (CODBDI 12)
  fracionario mercado fracionário (TPMERC 020)
  negocios    nº de negócios no total
  papeis      nº de códigos negociados no total

Backfill (uma vez, baixa os anuais): `python -m fontes.b3_volume --backfill 2019`
Diário: b3.atualiza_cotahist_diario chama `acrescenta(dados)` para cada COTAHIST_D novo.
"""
from __future__ import annotations

import csv
import sys
from datetime import date

import config

ARQ = config.DATA / "b3_volume_diario.csv"
CAB = ["data", "total", "acoes", "bdr", "etf", "fii", "fracionario", "negocios", "papeis"]
_ACOES_BDI = {b"02", b"05", b"06", b"07", b"08", b"09", b"10", b"11"}


def _classe(codbdi: bytes, tpmerc: bytes, especi: bytes) -> str | None:
    if tpmerc == b"020":
        return "fracionario"
    if tpmerc != b"010":
        return None
    if codbdi == b"12":
        return "fii"
    e = especi.strip()
    if e.startswith(b"DR"):                                   # BDI 34 (DRN), 35 (DR1/2/3), 36 (DRE)
        return "bdr"
    if codbdi == b"14" or e.startswith(b"CI") or e.startswith(b"IBO"):   # BDI 14 = ETFs (ESPECI CI); IBOV11 vem como BDI 02/IBO
        return "etf"
    if codbdi in _ACOES_BDI:
        return "acoes"
    return "outros"


def totais(dados: bytes) -> dict[str, dict]:
    """{data: {total, acoes, bdr, etf, fii, fracionario, negocios, papeis}} de um COTAHIST (anual ou diário), em R$."""
    out: dict[str, dict] = {}
    cods: dict[str, set] = {}
    for raw in dados.splitlines():
        if len(raw) < 217 or raw[:2] != b"01":
            continue
        cl = _classe(raw[10:12], raw[24:27], raw[39:49])
        if cl is None:
            continue
        try:
            vol = int(raw[170:188]) / 100.0
            neg = int(raw[147:152])
        except ValueError:
            continue
        d = raw[2:10].decode()
        d = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        r = out.setdefault(d, {k: 0.0 for k in CAB[1:]})
        r["total"] += vol
        r["negocios"] += neg
        if cl in r:
            r[cl] += vol
        cods.setdefault(d, set()).add(raw[12:24])
    for d, r in out.items():
        r["papeis"] = len(cods[d])
    return out


def ler() -> dict[str, dict]:
    if not ARQ.exists():
        return {}
    with ARQ.open(encoding="utf-8") as fh:
        return {row["data"]: {k: float(row[k]) for k in CAB[1:]} for row in csv.DictReader(fh)}


def _grava(rows: dict[str, dict]) -> None:
    ARQ.parent.mkdir(parents=True, exist_ok=True)
    with ARQ.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(CAB)
        for d in sorted(rows):
            r = rows[d]
            w.writerow([d] + [f"{r[k]:.2f}" if k in ("total", "acoes", "bdr", "etf", "fii", "fracionario") else str(int(r[k])) for k in CAB[1:]])


def acrescenta(dados: bytes) -> int:
    """Soma um COTAHIST (diário ou anual) na série; devolve quantos pregões entraram ou mudaram."""
    novos = totais(dados)
    if not novos:
        return 0
    rows = ler()
    n = sum(1 for d in novos if rows.get(d) != novos[d])
    rows.update(novos)
    _grava(rows)
    return n


def serie() -> list[tuple[str, dict]]:
    """[(data, linha)] ordenada."""
    rows = ler()
    return [(d, rows[d]) for d in sorted(rows)]


def backfill(ano_ini: int, ano_fim: int | None = None) -> None:
    from fontes import b3
    ano_fim = ano_fim or date.today().year
    for ano in range(ano_ini, ano_fim + 1):
        print(f"  COTAHIST {ano}: baixando para o volume total...", flush=True)
        dados = b3._baixa_zip(ano)
        if dados is None:
            continue
        n = acrescenta(dados)
        print(f"  COTAHIST {ano}: {n} pregões", flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if "--backfill" in sys.argv:
        i = sys.argv.index("--backfill")
        backfill(int(sys.argv[i + 1]) if len(sys.argv) > i + 1 else 2019)
    for d, r in serie()[-5:]:
        print(d, f"total R$ {r['total'] / 1e9:.1f} bi", f"ações {r['acoes'] / 1e9:.1f}", f"BDR {r['bdr'] / 1e9:.1f}", f"ETF {r['etf'] / 1e9:.1f}",
              f"FII {r['fii'] / 1e9:.1f}", f"frac {r['fracionario'] / 1e9:.2f}", f"negócios {int(r['negocios']):,}", f"papéis {int(r['papeis'])}")
