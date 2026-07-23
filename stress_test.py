"""
Stress test sobre períodos de crisis: en vez de correr el backtest sobre
todo el historial (que promedia lo bueno con lo malo), lo corre
específicamente sobre ventanas conocidas de estrés extremo del mercado.
Un sistema puede verse muy bien en el promedio y ser un desastre
específicamente en el momento en que más importa no perder plata.
"""

import pandas as pd
from backtester import Backtester

# Períodos de crisis conocidos relevantes para los activos de este motor.
# Fechas aproximadas, documentadas para que quede claro qué se está probando.
CRISIS_PERIODS = {
    "crash_covid_2020": {
        "inicio": "2020-02-15", "fin": "2020-04-15",
        "descripcion": "Crash de marzo 2020 por COVID-19 -- caída y recuperación muy rápidas",
    },
    "crash_cripto_luna_2022": {
        "inicio": "2022-05-01", "fin": "2022-07-15",
        "descripcion": "Colapso de Terra/LUNA, mayo 2022 -- contagio a todo el mercado cripto",
    },
    "crash_ftx_2022": {
        "inicio": "2022-11-01", "fin": "2022-12-15",
        "descripcion": "Colapso de FTX, noviembre 2022 -- pánico y liquidaciones forzadas",
    },
    "bear_market_2022_completo": {
        "inicio": "2021-11-01", "fin": "2022-12-31",
        "descripcion": "Mercado bajista completo 2021-2022 (de máximos históricos a mínimos)",
    },
}


def run_crisis_stress_test(df: pd.DataFrame, strategy_name: str, profile_name: str,
                            initial_capital: float = 1000.0) -> dict:
    """
    Corre el backtest sobre cada período de crisis conocido que esté
    cubierto por los datos disponibles, y devuelve un resumen comparativo.
    Períodos no cubiertos por el rango de fechas del dataset se omiten
    (y se informan como tal).
    """
    results = {}
    for name, period in CRISIS_PERIODS.items():
        start = pd.Timestamp(period["inicio"])
        end = pd.Timestamp(period["fin"])

        if start < df.index.min() or end > df.index.max():
            results[name] = {
                "descripcion": period["descripcion"],
                "cubierto": False,
                "motivo": f"El dataset no cubre este período (rango disponible: {df.index.min().date()} a {df.index.max().date()})",
            }
            continue

        window = df.loc[start:end]
        if len(window) < 15:
            results[name] = {
                "descripcion": period["descripcion"],
                "cubierto": False,
                "motivo": f"Muy pocas velas en la ventana ({len(window)})",
            }
            continue

        bt = Backtester(window, strategy_name, profile_name, initial_capital, validate=False)
        r = bt.run()
        results[name] = {
            "descripcion": period["descripcion"],
            "cubierto": True,
            "retorno_pct": r["retorno_total_pct"],
            "retorno_buy_and_hold_pct": r["retorno_buy_and_hold_pct"],
            "max_drawdown_pct": r["max_drawdown_pct"],
            "num_operaciones": r["num_operaciones"],
            "circuit_breaker_activado": r["circuit_breaker_activado"],
        }

    return results
