"""
Registro de experimentos: cada vez que se corre un backtest, guarda los
parámetros usados y el resultado obtenido en un CSV histórico. Esto
resuelve un problema muy común: probar 15 combinaciones distintas a lo
largo de varios días y no poder recordar (ni comparar) qué se probó y
qué dio mejor resultado.
"""

import os
from datetime import datetime
import pandas as pd

DEFAULT_LOG_PATH = "experiments.csv"

LOGGED_FIELDS = [
    "timestamp", "estrategia", "perfil_riesgo", "capital_inicial",
    "capital_final", "retorno_total_pct", "retorno_buy_and_hold_pct",
    "le_gano_al_buy_and_hold", "max_drawdown_pct", "num_operaciones",
    "win_rate_pct", "profit_factor", "expectancy_por_operacion",
    "sharpe_aprox", "sortino_aprox", "circuit_breaker_activado",
    "operaciones_rechazadas_por_minimo", "fuente_datos", "notas",
]


def log_experiment(result: dict, fuente_datos: str = "", notas: str = "",
                    log_path: str = DEFAULT_LOG_PATH) -> None:
    """Agrega una fila al CSV de experimentos con el resultado de un backtest."""
    row = {field: result.get(field, "") for field in LOGGED_FIELDS}
    row["timestamp"] = datetime.utcnow().isoformat(timespec="seconds")
    row["fuente_datos"] = fuente_datos
    row["notas"] = notas

    df_row = pd.DataFrame([row])[LOGGED_FIELDS]
    file_exists = os.path.exists(log_path)
    df_row.to_csv(log_path, mode="a", header=not file_exists, index=False)


def list_experiments(log_path: str = DEFAULT_LOG_PATH) -> pd.DataFrame:
    """Lee el historial completo de experimentos corridos hasta ahora."""
    if not os.path.exists(log_path):
        return pd.DataFrame(columns=LOGGED_FIELDS)
    return pd.read_csv(log_path)


def best_experiments(log_path: str = DEFAULT_LOG_PATH, metric: str = "retorno_total_pct",
                      top_n: int = 5) -> pd.DataFrame:
    """Devuelve los N mejores experimentos según una métrica (por defecto, retorno)."""
    df = list_experiments(log_path)
    if df.empty:
        return df
    return df.sort_values(metric, ascending=False).head(top_n)
