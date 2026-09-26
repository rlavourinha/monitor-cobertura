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


def _simbolo(ticker: str) -> str | None:
    """Garante o símbolo no Observação do Mercado (senão o terminal não entrega tick)."""
    if not _mt5.symbol_select(ticker, True):
        return None
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
        "hora": datetime.fromtimestamp(tick.time).strftime("%Y-%m-%d %H:%M"),
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
    return [[datetime.fromtimestamp(int(b["time"])).strftime("%Y-%m-%d %H:%M"), float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"]), int(b["real_volume"])] for b in r]


def ticks(ticker: str, desde: datetime | None = None, n: int = 100000) -> list[list]:
    """Negócios (ticks) desde `desde` (padrão: início do dia): [[hora, último, quantidade, bid, ask]]."""
    if not disponivel() or not _simbolo(ticker):
        return []
    desde = desde or datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    r = _mt5.copy_ticks_from(ticker, desde, n, _mt5.COPY_TICKS_ALL)
    if r is None:
        return []
    return [[datetime.fromtimestamp(int(t["time"])).strftime("%Y-%m-%d %H:%M:%S"), float(t["last"]), int(t["volume"]), float(t["bid"]), float(t["ask"])] for t in r if t["last"]]


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
