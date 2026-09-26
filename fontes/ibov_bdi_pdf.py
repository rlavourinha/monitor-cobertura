"""Composição do Ibovespa a partir do Boletim Diário do Mercado em PDF (capítulo 'Indicadores e informativos').

A B3 serve o PDF por URL para qualquer data desde set/2022:
  https://arquivos.b3.com.br/bdi/download/bdi/{AAAA-MM-DD}/BDI_02_{AAAAMMDD}.pdf
O capítulo traz 'Composição das carteiras de índices' do quadrimestre vigente: código, quantidade teórica e participação,
com a nota '(2) Participação relativa ... divulgada para a abertura dos negócios do dia dd/mm/aaaa' = data da fotografia.
Uma consulta por quadrimestre basta. Sem redutor no PDF: é derivado depois (ibov_wayback.preenche_redutores).
Requer o xpdf/poppler `pdftotext` no PATH (modo -table). Uso: python -m fontes.ibov_bdi_pdf [desde AAAA-MM]
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import config

URL = "https://arquivos.b3.com.br/bdi/download/bdi/{d}/BDI_02_{dd}.pdf"


def _baixa(d: str) -> bytes | None:
    req = urllib.request.Request(URL.format(d=d, dd=d.replace("-", "")), headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            b = r.read()
        return b if b[:4] == b"%PDF" else None
    except Exception:
        return None


def _texto(pdf: bytes) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "bdi.pdf"; p.write_bytes(pdf)
        subprocess.run(["pdftotext", "-table", str(p), str(p.with_suffix(".txt"))], check=True, capture_output=True)
        return p.with_suffix(".txt").read_text(encoding="utf-8", errors="replace")


def parse(txt: str) -> dict | None:
    """{'data', 'q', 'peso', 'vigencia'} da seção IBOVESPA, ou None."""
    i = txt.find("\nIBOVESPA\n")
    if i < 0:
        return None
    cab = txt[max(0, i - 1500):i]
    m = re.search(r"abertura dos neg.cios do dia (\d{2})/(\d{2})/(\d{4})", cab)
    if not m:
        return None
    d = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    vig = re.search(r"Para (\w+) a \w+ de (\d{4})", cab)
    if vig:
        # a carteira só vale a partir do 1º pregão do quadrimestre; a data de "abertura" (ex.: 28/12) é só a referência
        # dos pesos. Datar pela vigência evita usar a composição nova numa janela do quadrimestre anterior.
        meses = {"janeiro": 1, "maio": 5, "setembro": 9}
        m0 = meses.get(vig.group(1).lower())
        if m0:
            try:
                from fontes import b3
                calend = [x for x, _ in b3.serie("PETR4")]
            except Exception:
                calend = []
            pref = f"{vig.group(2)}-{m0:02d}-"
            d = next((x for x in calend if x.startswith(pref)), d if d.startswith(pref) else f"{pref}02")
    q, peso = {}, {}
    for linha in txt[i:].splitlines()[1:]:
        s = linha.strip()
        if not s:
            continue
        if len(s.split()) == 1 and q and re.match(r"^(IBRX|IGC|IVBX|IBXX|SMLL|MLCX|IDIV|ICON|IMAT|UTIL|IFNC|IEEX|IMOB|INDX|ICO2|ISE|BDRX|GPTW|AGFS|IBSD|IBLV|IBHB|IFIX|IBRA|ITAG|IGNM|IBEE|IBEP|IBEW|IBBR|IFRA|IDVR)", s):
            break                              # cabeçalho do próximo índice; quebras de página (número solto) não encerram a seção
        m2 = re.match(r"^([A-Z][A-Z0-9]{3,5})\s+.*?\s([\d\.]{5,})\s+([\d,]+)\s*$", s)
        if m2 and m2.group(1) not in ("IBOVESPA",):
            try:
                q[m2.group(1)] = float(m2.group(2).replace(".", ""))
                peso[m2.group(1)] = float(m2.group(3).replace(",", "."))
            except ValueError:
                pass
    if len(q) < 50:
        return None
    return {"data": d, "q": q, "peso": peso, "vigencia": vig.group(0)[5:] if vig else None}


def _quadrimestres(desde: str) -> list[str]:
    """Um dia de consulta por quadrimestre (dia 10 do 1º mês, quando o boletim já traz a carteira nova)."""
    a, m = int(desde[:4]), int(desde[5:7])
    m = 1 if m < 5 else (5 if m < 9 else 9)
    out = []
    hoje = date.today()
    while date(a, m, 1) <= hoje:
        d = date(a, m, 10)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        if d <= hoje:
            out.append(d.isoformat())
        m += 4
        if m > 12:
            m = 1; a += 1
    return out


def ingerir(desde: str = "2022-09") -> list[str]:
    fdir = config.DATA / "ibov_carteira"; fdir.mkdir(parents=True, exist_ok=True)
    novas = []
    for d in _quadrimestres(desde):
        pdf = None
        for k in range(16):                                  # tenta os dias úteis seguintes se o PDF do dia faltar ou vier sem a tabela
            dk = (date.fromisoformat(d) + timedelta(days=k)).isoformat()
            if date.fromisoformat(dk).weekday() >= 5:
                continue
            pdf = _baixa(dk)
            if not pdf:
                continue
            try:
                obj = parse(_texto(pdf))
            except Exception as e:
                print(f"  BDI {dk}: {e}", file=sys.stderr); obj = None
            if obj:
                break
        else:
            obj = None
        if not obj:
            print(f"  BDI {d}: composição não encontrada", file=sys.stderr); continue
        f = fdir / f"{obj['data']}.json"
        if f.exists() and "BDI" not in json.loads(f.read_text(encoding="utf-8")).get("fonte", "BDI"):
            print(f"  BDI {d}: já existe fotografia de outra fonte em {obj['data']}"); continue
        f.write_text(json.dumps({"data": obj["data"], "redutor": None, "q": obj["q"], "peso": obj["peso"],
                                 "fonte": f"BDI PDF {dk} ({obj['vigencia']})"}, ensure_ascii=False), encoding="utf-8")
        novas.append(obj["data"])
        print(f"  BDI {dk} -> {obj['data']}: {len(obj['q'])} papéis, soma pesos {sum(obj['peso'].values()):.1f} ({obj['vigencia']})")
    return novas


if __name__ == "__main__":
    n = ingerir(sys.argv[1] if len(sys.argv) > 1 else "2022-09")
    print(f"{len(n)} fotografias: {n}")
