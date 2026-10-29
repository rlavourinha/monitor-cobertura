"""IBGE, Contas Nacionais Trimestrais (SIDRA), série completa desde 1996 (contas econômicas desde 2000).

Tabelas:
- 5932: taxas de variação do volume (t/t−1 com ajuste sazonal, contra o mesmo trimestre do ano anterior, acumulada em 4
  trimestres e no ano) do PIB pelas óticas da oferta (agro, indústria e aberturas, serviços) e da demanda (famílias, governo,
  FBCF, exportação, importação);
- 1621: índice de volume com ajuste sazonal (média 1995 = 100): o nível do PIB e dos componentes;
- 1846: valores correntes (R$ milhões) por componente, inclusive variação de estoque: participações no PIB e taxa de
  investimento (FBCF ÷ PIB);
- 2072: contas econômicas trimestrais (renda nacional, poupança bruta, capacidade/necessidade de financiamento).
Saída: data/contas_nacionais.json. Datas = 1º dia do último mês do trimestre (2T26 = 2026-06-01).
"""
from __future__ import annotations

import json
import time
from datetime import date

import config

ARQ = config.DATA / "contas_nacionais.json"
SIDRA = "https://apisidra.ibge.gov.br/values/t/{t}/n1/all/v/{v}/p/all/c11255/{c}"
SIDRA_SEM_CLASSE = "https://apisidra.ibge.gov.br/values/t/{t}/n1/all/v/{v}/p/all"
# chave -> (código na classificação 11255, rótulo)
COMP = {
    "pib": (90707, "PIB"), "agro": (90687, "Agropecuária"), "industria": (90691, "Indústria"),
    "extrativa": (90692, "Indústria extrativa"), "transformacao": (90693, "Indústria de transformação"),
    "construcao": (90694, "Construção"), "servicos": (90696, "Serviços"), "familias": (93404, "Consumo das famílias"),
    "governo": (93405, "Consumo do governo"), "fbcf": (93406, "Investimento (FBCF)"), "exportacao": (93407, "Exportação"),
    "importacao": (93408, "Importação"),
}
VAR_5932 = {"tt": 6564, "aa": 6561, "4t": 6562, "ano": 6563}
CONTAS_2072 = {"pib": 933, "rnb": 936, "rndb": 938, "consumo": 939, "poupanca": 940, "fbc": 941, "cap_fin": 943}


def _get(url: str) -> list[dict]:
    import requests
    erro = None
    for tent in range(4):
        try:
            r = requests.get(url, timeout=120, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200:
                return r.json()[1:]
            erro = RuntimeError(f"SIDRA {r.status_code}: {r.text[:120]}")
        except Exception as e:
            erro = e
        time.sleep(3 + 5 * tent)
    raise erro


def _data(per: str) -> str:
    """'202602' (2º tri de 2026) -> '2026-06-01'."""
    return f"{per[:4]}-{int(per[4:]) * 3:02d}-01"


def _valor(s: str) -> float | None:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _por_comp(linhas: list[dict], var: int | None = None) -> dict[str, list[list]]:
    cod_chave = {str(c): k for k, (c, _) in COMP.items()}
    cod_chave["102880"] = "estoque"
    out: dict[str, list[list]] = {}
    for x in linhas:
        if var is not None and x["D2C"] != str(var):
            continue
        k = cod_chave.get(x.get("D4C"))
        v = _valor(x["V"])
        if k and v is not None:
            out.setdefault(k, []).append([_data(x["D3C"]), v])
    return {k: sorted(v) for k, v in out.items()}


def atualiza() -> dict:
    cods = ",".join(str(c) for c, _ in COMP.values())
    var = _get(SIDRA.format(t=5932, v=",".join(map(str, VAR_5932.values())), c=cods))
    taxas = {k: _por_comp(var, v) for k, v in VAR_5932.items()}
    nivel = _por_comp(_get(SIDRA.format(t=1621, v=584, c=cods)))
    corr = _por_comp(_get(SIDRA.format(t=1846, v=585, c=cods + ",102880")))
    ce = _get(SIDRA_SEM_CLASSE.format(t=2072, v=",".join(map(str, CONTAS_2072.values()))))
    contas: dict[str, list[list]] = {}
    inv = {str(v): k for k, v in CONTAS_2072.items()}
    for x in ce:
        v = _valor(x["V"])
        if v is not None and x["D2C"] in inv:
            contas.setdefault(inv[x["D2C"]], []).append([_data(x["D3C"]), v])
    contas = {k: sorted(v) for k, v in contas.items()}
    D = {"atualizado": date.today().isoformat(), "ultimo": taxas["tt"]["pib"][-1][0],
         "rotulos": {k: r for k, (_, r) in COMP.items()}, "taxas": taxas, "nivel_sa": nivel, "correntes": corr, "contas": contas}
    ARQ.write_text(json.dumps(D, ensure_ascii=False), encoding="utf-8")
    return D


def ler() -> dict:
    return json.loads(ARQ.read_text(encoding="utf-8")) if ARQ.exists() else {}


def tri(d: str) -> str:
    """'2026-06-01' -> '2T26'."""
    return f"{int(d[5:7]) // 3}T{d[2:4]}"


def soma4(pts: list[list]) -> list[list]:
    """Soma móvel de 4 trimestres (só quando os 4 trimestres consecutivos existem)."""
    out = []
    for i in range(3, len(pts)):
        if all(pts[i - j][1] is not None for j in range(4)):
            out.append([pts[i][0], sum(pts[i - j][1] for j in range(4))])
    return out


def razao(num: list[list], den: list[list], fator: float = 100.0) -> list[list]:
    d = dict(den)
    return [[t, v / d[t] * fator] for t, v in num if d.get(t)]


def indicadores(D: dict) -> dict[str, list[list]]:
    """Razões derivadas (% do PIB), no trimestre e acumuladas em 4 trimestres: taxa de investimento (FBCF ÷ PIB),
    poupança bruta ÷ PIB, capacidade (+) / necessidade (−) de financiamento ÷ PIB, participação de cada componente."""
    C, K = D.get("correntes", {}), D.get("contas", {})
    pib = C.get("pib", [])
    out = {}
    if C.get("fbcf") and pib:
        out["inv_tri"] = razao(C["fbcf"], pib)
        out["inv_4t"] = razao(soma4(C["fbcf"]), soma4(pib))
    if K.get("poupanca") and K.get("pib"):
        out["poup_tri"] = razao(K["poupanca"], K["pib"])
        out["poup_4t"] = razao(soma4(K["poupanca"]), soma4(K["pib"]))
    if K.get("cap_fin") and K.get("pib"):
        out["fin_4t"] = razao(soma4(K["cap_fin"]), soma4(K["pib"]))
    for k in ("familias", "governo", "fbcf", "exportacao", "importacao", "estoque"):
        if C.get(k) and pib:
            out[f"part_{k}"] = razao(soma4(C[k]), soma4(pib))
    return out


if __name__ == "__main__":
    D = atualiza()
    print("último trimestre:", tri(D["ultimo"]), {k: len(v) for k, v in D["taxas"]["tt"].items()})
    I = indicadores(D)
    print({k: [tri(v[-1][0]), round(v[-1][1], 1)] for k, v in I.items()})
