"""FRED (Federal Reserve Bank of St. Louis): séries diárias em CSV público, sem chave.

    DFII10 = Treasury de 10 anos indexado à inflação (TIPS), juro real, % a.a.  -> prêmio de juro real do Brasil (NTN-B − TIPS)
    DGS10  = Treasury de 10 anos nominal (já temos pelo Yahoo ^TNX; fica como reserva)
"""
from __future__ import annotations

import csv
import io
import sys
import urllib.request  # noqa: F401 (mantido para a URL)

URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={id}"


def serie(id_: str, desde: str = "2010-01-01") -> list[list]:
    """[[data ISO, valor], ...] desde `desde`; dias sem dado ('.') são pulados."""
    # urllib leva "connection reset" do FRED (fingerprint TLS); requests (vem com o yfinance) passa; curl é a reserva
    txt = ""
    try:
        import requests
        r = requests.get(URL.format(id=id_), headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "text/csv,*/*"}, timeout=60)
        if r.ok:
            txt = r.text
    except Exception as e:
        print(f"  FRED {id_} (requests): {e}", file=sys.stderr)
    if not txt.startswith("observation_date"):
        try:
            import subprocess
            out = subprocess.run(["curl", "-sL", "--max-time", "60", URL.format(id=id_)], capture_output=True, text=True, timeout=90)
            txt = out.stdout
        except Exception as e:
            print(f"  FRED {id_} (curl): {e}", file=sys.stderr)
            return []
    if not txt.startswith("observation_date"):
        return []
    out = []
    for row in csv.DictReader(io.StringIO(txt)):
        d = row.get("observation_date") or row.get("DATE")
        v = row.get(id_)
        if not d or d < desde or v in (None, "", "."):
            continue
        try:
            out.append([d, float(v)])
        except ValueError:
            continue
    return out
