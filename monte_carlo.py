"""
Simulación de Monte Carlo sobre la secuencia de operaciones.

Por qué hace falta: un solo backtest te muestra UN orden posible de
ganancias y pérdidas -- el que realmente ocurrió en ese período histórico.
Pero el orden importa: la misma lista de operaciones, en otro orden,
puede llevar a un drawdown mucho peor (si las pérdidas se agrupan al
principio, antes de que el capital haya crecido). Esto se llama "riesgo
de secuencia". Monte Carlo reordena (o remuestrea) las operaciones miles
de veces para estimar cuán probable es un resultado realmente malo, no
solo el que pasó a ocurrir.
"""

import numpy as np


def monte_carlo_from_trades(trades: list, initial_capital: float,
                             n_simulations: int = 2000, ruin_threshold_pct: float = 50.0,
                             method: str = "bootstrap", seed: int = 42) -> dict:
    """
    Toma la lista de operaciones (pnl en $) de un backtest ya corrido y
    simula miles de escenarios alternativos.

    method="shuffle": reordena las mismas operaciones (sin repetir ni
    quitar ninguna). Prueba el RIESGO DE SECUENCIA -- el capital final
    siempre es el mismo (reordenar no cambia una suma), pero el camino
    para llegar ahí sí varía, y eso es lo que importa para el drawdown.

    method="bootstrap" (por defecto, más estándar): remuestrea las
    operaciones CON reemplazo -- algunas se repiten, otras no aparecen.
    Esto sí genera variación en el resultado final, y es más realista si
    pensás las operaciones históricas como una muestra de lo que la
    estrategia "podría hacer", no como una secuencia fija que se repite.

    ruin_threshold_pct: % del capital inicial por debajo del cual se
    considera "ruina" (ej. 50 significa "perder la mitad o más").
    """
    if not trades:
        return {"error": "No hay operaciones para simular"}

    pnls = np.array([t["pnl"] for t in trades])
    rng = np.random.default_rng(seed)
    n_trades = len(pnls)

    final_capitals = []
    max_drawdowns = []
    ruin_count = 0
    ruin_capital = initial_capital * (1 - ruin_threshold_pct / 100)

    for _ in range(n_simulations):
        if method == "bootstrap":
            sample = rng.choice(pnls, size=n_trades, replace=True)
        elif method == "shuffle":
            sample = rng.permutation(pnls)
        else:
            raise ValueError("method debe ser 'bootstrap' o 'shuffle'")

        equity = initial_capital + np.cumsum(sample)
        equity_with_start = np.concatenate([[initial_capital], equity])

        final_capitals.append(equity_with_start[-1])

        running_max = np.maximum.accumulate(equity_with_start)
        drawdowns = (equity_with_start - running_max) / running_max
        max_drawdowns.append(drawdowns.min() * 100)

        if equity_with_start.min() <= ruin_capital:
            ruin_count += 1

    final_capitals = np.array(final_capitals)
    max_drawdowns = np.array(max_drawdowns)

    return {
        "metodo": method,
        "n_simulaciones": n_simulations,
        "capital_inicial": initial_capital,
        "capital_final_promedio": round(float(final_capitals.mean()), 2),
        "capital_final_mediana": round(float(np.median(final_capitals)), 2),
        "capital_final_peor_caso_p5": round(float(np.percentile(final_capitals, 5)), 2),
        "capital_final_mejor_caso_p95": round(float(np.percentile(final_capitals, 95)), 2),
        "drawdown_promedio_pct": round(float(max_drawdowns.mean()), 2),
        "drawdown_peor_caso_p5_pct": round(float(np.percentile(max_drawdowns, 5)), 2),  # más negativo = peor
        "probabilidad_de_ruina_pct": round(ruin_count / n_simulations * 100, 2),
        "umbral_de_ruina_definido_como": f"perder {ruin_threshold_pct}% o más del capital inicial",
    }
