"""
Puntaje de "qué tan rentable fue este símbolo hasta ahora" -- usado
cuando el cupo compartido de posiciones está lleno, para decidir si vale
la pena rotar (cerrar la posición abierta con peor historial probado
para abrir una candidata mejor) en vez de simplemente rechazar la señal
nueva por orden de llegada. Basado en el HISTORIAL REAL de operaciones
cerradas (expectancy: ganancia promedio por operación) -- no en la
fuerza de la señal del momento, que mide momentum, no rentabilidad
comprobada.
"""

MIN_TRADES_FOR_SCORE = 3  # con menos, no hay evidencia suficiente -- score desconocido, no cero


def symbol_expectancy(symbol: str, trade_history_rows: list):
    """Expectancy (ganancia promedio por operación cerrada, en USD) de
    `symbol`, a partir de las filas crudas de `TradeHistoryLog.load_all()`.
    Devuelve None si hay menos de MIN_TRADES_FOR_SCORE operaciones
    cerradas todavía -- un símbolo nuevo o con poca historia no tiene por
    qué tratarse como "malo", solo como "todavía desconocido"."""
    pnls = []
    for row in trade_history_rows:
        if row.get("symbol") != symbol or row.get("side") != "sell":
            continue
        pnl = row.get("pnl")
        if pnl in (None, ""):
            continue
        pnls.append(float(pnl))
    if len(pnls) < MIN_TRADES_FOR_SCORE:
        return None
    return sum(pnls) / len(pnls)


def pick_weakest_open_position(open_symbols: list, trade_history_rows: list):
    """De los símbolos con posición abierta, cuál es el candidato más
    débil para rotar -- el de MENOR expectancy conocida. Los símbolos sin
    historia suficiente (None) nunca se eligen para rotar: no hay
    evidencia de que sean peores, solo que todavía no se sabe. Devuelve
    None si ningún símbolo abierto tiene historia suficiente todavía."""
    scored = [(sym, symbol_expectancy(sym, trade_history_rows)) for sym in open_symbols]
    known = [(sym, score) for sym, score in scored if score is not None]
    if not known:
        return None
    return min(known, key=lambda pair: pair[1])[0]
