"""MetaTrader 5 (Genial) como fonte de cotação em tempo real, book e barras intraday, via pacote oficial `MetaTrader5`.

Só leitura: nenhuma função aqui envia ordem (não existe order_send neste módulo). Funciona apenas neste PC, com o
terminal MT5 aberto e logado (senha de investidor basta). Se o terminal não estiver disponível, tudo devolve None/{}
e o coletor segue com o Yahoo (atraso ~15 min).

Uso rápido:  python -m fontes.mt5 PETR4 VALE3
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

try:
    import MetaTrader5 as _mt5
except ImportError:                      # pacote ausente (ex.: GitHub Actions/Linux): fonte indisponível
    _mt5 = None

_ligado = False


def _hora(ts) -> datetime:
    """Horários do MT5 vêm no fuso do servidor (Genial = horário de Brasília) como segundos 'UTC': lê sem converter."""
    from datetime import timezone
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).replace(tzinfo=None)


def _caminhos() -> list[str]:
    """Caminhos candidatos do terminal64.exe: config.MT5_PATH, o processo aberto (Get-Process) e as pastas usuais."""
    import os, subprocess
    out = []
    try:
        import config
        if getattr(config, "MT5_PATH", None):
            out.append(config.MT5_PATH)
    except Exception:
        pass
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", "(Get-Process terminal64 -ErrorAction SilentlyContinue).Path"],
                           capture_output=True, text=True, timeout=20)
        out += [l.strip() for l in r.stdout.splitlines() if l.strip()]
    except Exception:
        pass
    for base in (r"D:\Program Files", r"C:\Program Files"):
        for nome in ("", "Genial Investimentos MetaTrader 5", "MetaTrader 5"):
            p = os.path.join(base, nome, "terminal64.exe")
            if os.path.exists(p):
                out.append(p)
    return list(dict.fromkeys(out))


def disponivel() -> bool:
    """True se o pacote existe e o terminal MT5 está aberto e logado neste PC (initialize sem login: usa a sessão aberta)."""
    global _ligado
    if _mt5 is None:
        return False
    if _ligado:
        return True
    try:
        ok = _mt5.initialize()
        if not ok:                       # terminal fora do caminho padrão (Genial: D:\Program Files\terminal64.exe): acha pelo processo aberto
            for caminho in _caminhos():
                if _mt5.initialize(path=caminho):
                    ok = True
                    break
        if not ok:
            return False
        info = _mt5.account_info()
        _ligado = info is not None
        if not _ligado:
            _mt5.shutdown()
        return _ligado
    except Exception as e:
        print(f"  MT5: {e}", file=sys.stderr)
        return False


def desligar() -> None:
    global _ligado
    if _mt5 is not None and _ligado:
        _mt5.shutdown()
        _ligado = False


def _simbolo(ticker: str, espera: float = 8.0) -> str | None:
    """Garante o símbolo no Observação do Mercado (senão o terminal não entrega tick) e espera o primeiro tick chegar.

    Quando `initialize()` acabou de abrir o terminal, o histórico do símbolo leva alguns segundos para sincronizar e
    `symbol_info_tick` devolve last=0 nesse meio-tempo; sem a espera, a primeira coleta após abrir o terminal vinha vazia.
    """
    import time
    if not _mt5.symbol_select(ticker, True):
        return None
    fim = time.monotonic() + espera
    while time.monotonic() < fim:
        t = _mt5.symbol_info_tick(ticker)
        if t and t.last:
            break
        time.sleep(0.5)
    return ticker


def intraday(ticker: str) -> dict | None:
    """Mesmo formato de yahoo.intraday: {preco, fech_anterior, max_dia, min_dia, volume, hora} — mas em tempo real."""
    if not disponivel() or not _simbolo(ticker):
        return None
    tick = _mt5.symbol_info_tick(ticker)
    info = _mt5.symbol_info(ticker)
    if not tick or not info or not tick.last:
        return None
    hoje = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    barras = _mt5.copy_rates_from(ticker, _mt5.TIMEFRAME_D1, datetime.now(), 2)   # [ontem, hoje]
    fech_ant = float(barras[0]["close"]) if barras is not None and len(barras) >= 2 else None
    return {
        "preco": float(tick.last),
        "fech_anterior": fech_ant,
        "max_dia": float(barras[-1]["high"]) if barras is not None and len(barras) else None,
        "min_dia": float(barras[-1]["low"]) if barras is not None and len(barras) else None,
        "max_52s": None, "min_52s": None,
        "volume": int(barras[-1]["real_volume"]) if barras is not None and len(barras) else None,
        "hora": _hora(tick.time).strftime("%Y-%m-%d %H:%M"),
        "bid": float(tick.bid) if tick.bid else None, "ask": float(tick.ask) if tick.ask else None,
        "fonte": "mt5",
    }


def cotacoes(tickers: list[str]) -> dict[str, dict]:
    """{ticker: intraday} para uma lista; ignora os que o terminal não conhece."""
    out = {}
    for tk in tickers:
        q = intraday(tk)
        if q:
            out[tk] = q
    return out


def barras(ticker: str, minutos: int = 1, n: int = 500) -> list[list]:
    """Últimas n barras intraday: [[hora ISO, abertura, máxima, mínima, fechamento, volume financeiro? não: negócios/qtd]]."""
    if not disponivel() or not _simbolo(ticker):
        return []
    tf = {1: _mt5.TIMEFRAME_M1, 5: _mt5.TIMEFRAME_M5, 15: _mt5.TIMEFRAME_M15, 30: _mt5.TIMEFRAME_M30, 60: _mt5.TIMEFRAME_H1}.get(minutos, _mt5.TIMEFRAME_M1)
    r = _mt5.copy_rates_from_pos(ticker, tf, 0, n)
    if r is None:
        return []
    return [[_hora(b["time"]).strftime("%Y-%m-%d %H:%M"), float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"]), int(b["real_volume"])] for b in r]


def ticks(ticker: str, desde: datetime | None = None, n: int = 100000) -> list[list]:
    """Negócios (ticks) desde `desde` (padrão: início do dia): [[hora, último, quantidade, bid, ask]]."""
    if not disponivel() or not _simbolo(ticker):
        return []
    desde = desde or datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    r = _mt5.copy_ticks_from(ticker, desde, n, _mt5.COPY_TICKS_ALL)
    if r is None:
        return []
    return [[_hora(t["time"]).strftime("%Y-%m-%d %H:%M:%S"), float(t["last"]), int(t["volume"]), float(t["bid"]), float(t["ask"])] for t in r if t["last"]]


def book(ticker: str, niveis: int = 10) -> dict | None:
    """Livro de ofertas (Level 2, se a Genial liberar): {"compra": [[preço, qtd]...], "venda": [[preço, qtd]...]}."""
    if not disponivel() or not _simbolo(ticker):
        return None
    if not _mt5.market_book_add(ticker):
        return None
    try:
        itens = _mt5.market_book_get(ticker) or []
    finally:
        _mt5.market_book_release(ticker)
    compra = sorted([[float(i.price), int(i.volume)] for i in itens if i.type == _mt5.BOOK_TYPE_BUY], key=lambda x: -x[0])[:niveis]
    venda = sorted([[float(i.price), int(i.volume)] for i in itens if i.type == _mt5.BOOK_TYPE_SELL], key=lambda x: x[0])[:niveis]
    return {"compra": compra, "venda": venda, "hora": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


# ----------------------------------------------------------------------------- histórico de barras → data/mt5/
# O servidor da Genial serve ~10 anos de barras diárias e barras intraday (1 min) do ano corrente (meses antigos vêm
# vazios). Guardamos por papel: data/mt5/diario/{TICKER}.csv (desde 2016) e data/mt5/m1/{TICKER}_{ano}.csv.gz.
#
# ATENÇÃO: os preços de AÇÕES no MT5 da Genial são AJUSTADOS POR PROVENTOS (série de retorno total, como o "Adj Close"
# do Yahoo): PETR4 em 02/01/2019 = 6,20 no MT5 contra 24,06 no COTAHIST; só a última data coincide. Preço bruto
# continua sendo o COTAHIST (fontes/b3.py). Como cada provento novo reajusta a série INTEIRA para trás, a coleta rebaixa
# tudo a cada execução (não dá para ser incremental); índices (IBOV) não sofrem ajuste.
CAMPOS_BARRA = ["abertura", "maxima", "minima", "fechamento", "quantidade", "negocios"]


def _linha(b, com_hora: bool) -> dict:
    t = _hora(b["time"])
    return {"data" if not com_hora else "hora": t.strftime("%Y-%m-%d" if not com_hora else "%Y-%m-%d %H:%M"),
            "abertura": float(b["open"]), "maxima": float(b["high"]), "minima": float(b["low"]), "fechamento": float(b["close"]),
            "quantidade": int(b["real_volume"]), "negocios": int(b["tick_volume"])}


def _range(ticker: str, tf, ini: datetime, fim: datetime, com_hora: bool) -> list[dict]:
    """copy_rates_range entre ini e fim (horário do servidor, passado como UTC). Devolve [] se o terminal não tem nada."""
    from datetime import timezone
    r = _mt5.copy_rates_range(ticker, tf, ini.replace(tzinfo=timezone.utc), fim.replace(tzinfo=timezone.utc))
    return [_linha(b, com_hora) for b in r] if r is not None and len(r) else []


def historico_diario(ticker: str, desde: datetime) -> list[dict]:
    """Barras diárias desde `desde` (uma chamada; o servidor aceita janelas de anos para D1)."""
    if not disponivel() or not _simbolo(ticker, espera=3):
        return []
    return _range(ticker, _mt5.TIMEFRAME_D1, desde, datetime.now() + timedelta(days=1), com_hora=False)


def historico_m1(ticker: str, ini: datetime, fim: datetime | None = None) -> list[dict]:
    """Barras de 1 minuto entre ini e fim, pedidas mês a mês (janela grande dá 'Invalid params' no terminal)."""
    if not disponivel() or not _simbolo(ticker, espera=3):
        return []
    fim = fim or datetime.now() + timedelta(days=1)
    out, a = [], ini.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while a < fim:
        b = (a.replace(day=28) + timedelta(days=4)).replace(day=1)          # 1º dia do mês seguinte
        out += [l for l in _range(ticker, _mt5.TIMEFRAME_M1, max(a, ini), min(b, fim), com_hora=True)]
        a = b
    return out


def _le(p, chave: str) -> list[dict]:
    import csv, gzip
    if not p.exists():
        return []
    op = gzip.open if p.suffix == ".gz" else open
    with op(p, "rt", encoding="utf-8", newline="") as fh:
        return [r for r in csv.DictReader(fh) if r.get(chave)]


def _grava(p, linhas: list[dict], chave: str) -> None:
    import csv, gzip
    p.parent.mkdir(parents=True, exist_ok=True)
    op = gzip.open if p.suffix == ".gz" else open
    with op(p, "wt", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=[chave] + CAMPOS_BARRA)
        w.writeheader()
        w.writerows(linhas)


def _mescla(antigas: list[dict], novas: list[dict], chave: str) -> list[dict]:
    d = {r[chave]: r for r in antigas}
    d.update({r[chave]: r for r in novas})
    return [d[k] for k in sorted(d)]


def atualiza_diario(tickers: list[str], pasta, desde: datetime = datetime(2016, 1, 1)) -> str:
    """Regrava pasta/diario/{T}.csv com as barras diárias (ajustadas) desde `desde`. Rebaixa a série inteira (ver nota
    acima); se o terminal não devolver nada para um papel, o arquivo anterior é mantido."""
    pasta = pasta / "diario"
    novos = barras_n = sem = 0
    for tk in tickers:
        p = pasta / f"{tk}.csv"
        novas = historico_diario(tk, desde)
        if not novas:
            sem += 1
            continue
        novos += 0 if p.exists() else 1
        barras_n += len(novas)
        _grava(p, novas, "data")
    return f"{len(tickers)} papéis: {novos} novos, {barras_n} barras lidas, {sem} sem dados no MT5"


def atualiza_m1(tickers: list[str], pasta, ano: int) -> str:
    """Regrava pasta/m1/{T}_{ano}.csv.gz com as barras de 1 min (ajustadas) do ano inteiro até hoje (ver nota acima)."""
    pasta = pasta / "m1"
    novos = barras_n = sem = 0
    fim = min(datetime(ano + 1, 1, 1), datetime.now() + timedelta(days=1))
    for tk in tickers:
        p = pasta / f"{tk}_{ano}.csv.gz"
        novas = historico_m1(tk, datetime(ano, 1, 1), fim)
        if not novas:
            sem += 1
            continue
        novos += 0 if p.exists() else 1
        barras_n += len(novas)
        _grava(p, novas, "hora")
    return f"{len(tickers)} papéis: {novos} novos, {barras_n} barras lidas, {sem} sem dados no MT5"


if __name__ == "__main__":
    tks = sys.argv[1:] or ["PETR4", "VALE3", "ITUB4"]
    if not disponivel():
        print("MT5 indisponível: abra o terminal da Genial e faça login (senha de investidor).")
        sys.exit(1)
    ai = _mt5.account_info()
    print(f"conectado: conta {ai.login} · servidor {ai.server} · {_mt5.terminal_info().name}")
    for tk in tks:
        q = intraday(tk)
        print(tk, q)
        b = barras(tk, 1, 3)
        print("  últimas barras 1 min:", b)
    desligar()
