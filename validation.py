"""
Validación walk-forward: corre el backtest en dos tramos separados
(entrenamiento/in-sample y prueba/out-of-sample) para detectar si una
estrategia solo funciona en el período específico que se miró, o si
sostiene su comportamiento en datos que "no vio" antes.

Regla práctica: si el resultado out-of-sample es mucho peor que el
in-sample, hay sobreajuste -- no uses esa combinación estrategia/perfil
con capital real todavía.
"""

import pandas as pd
from backtester import Backtester


def walk_forward_validate(df: pd.DataFrame, strategy_name: str, profile_name: str,
                           initial_capital: float = 1000.0, split_pct: float = 0.6) -> dict:
    split_idx = int(len(df) * split_pct)
    in_sample = df.iloc[:split_idx]
    out_sample = df.iloc[split_idx:]

    bt_in = Backtester(in_sample, strategy_name, profile_name, initial_capital)
    result_in = bt_in.run()

    bt_out = Backtester(out_sample, strategy_name, profile_name, initial_capital)
    result_out = bt_out.run()

    degradation = None
    warning = None
    if result_in["retorno_total_pct"] > 0:
        degradation = round(
            (result_in["retorno_total_pct"] - result_out["retorno_total_pct"])
            / abs(result_in["retorno_total_pct"]) * 100, 1
        )
        if result_out["retorno_total_pct"] < 0 or degradation > 60:
            warning = (
                "Posible sobreajuste: el rendimiento cae fuertemente en el "
                "período que la estrategia 'no vio'. No usar esta combinación "
                "con capital real sin revisar más períodos primero."
            )

    return {
        "in_sample": {k: v for k, v in result_in.items() if k not in ("equity_curve", "trades")},
        "out_sample": {k: v for k, v in result_out.items() if k not in ("equity_curve", "trades")},
        "degradacion_pct": degradation,
        "advertencia": warning,
    }


def rolling_walk_forward_validate(df: pd.DataFrame, strategy_name: str, profile_name: str,
                                   initial_capital: float = 1000.0, n_windows: int = 5,
                                   train_pct: float = 0.6) -> dict:
    """
    Repite la validación walk-forward sobre `n_windows` ventanas consecutivas
    del historial (no solapadas), en vez de un único corte. Un solo split
    puede ser optimista o pesimista por casualidad del período elegido --
    varias ventanas dan una foto mucho más confiable de si la estrategia
    sostiene su comportamiento fuera de muestra de forma consistente.
    """
    if n_windows < 1:
        raise ValueError("n_windows debe ser >= 1")

    window_size = len(df) // n_windows
    if window_size < 20:
        raise ValueError(
            f"Cada ventana tendría solo {window_size} velas -- muy pocas para "
            f"que las estrategias funcionen. Usá menos ventanas o más datos."
        )

    windows = []
    for w in range(n_windows):
        start = w * window_size
        end = start + window_size if w < n_windows - 1 else len(df)
        window_df = df.iloc[start:end]

        result = walk_forward_validate(window_df, strategy_name, profile_name, initial_capital, train_pct)
        windows.append({
            "ventana": w + 1,
            "fecha_inicio": window_df.index[0],
            "fecha_fin": window_df.index[-1],
            "retorno_in_sample_pct": result["in_sample"]["retorno_total_pct"],
            "retorno_out_sample_pct": result["out_sample"]["retorno_total_pct"],
            "degradacion_pct": result["degradacion_pct"],
            "advertencia": result["advertencia"],
        })

    out_sample_returns = [w["retorno_out_sample_pct"] for w in windows]
    n_positive = sum(1 for r in out_sample_returns if r > 0)
    n_warnings = sum(1 for w in windows if w["advertencia"])

    consistency_pct = round(n_positive / n_windows * 100, 1)
    avg_out_sample_return = round(sum(out_sample_returns) / n_windows, 2)

    overall_warning = None
    if consistency_pct < 50:
        overall_warning = (
            f"La estrategia solo fue rentable fuera de muestra en {consistency_pct}% "
            f"de las ventanas probadas. Esto sugiere que el resultado depende mucho "
            f"del período elegido, no de una ventaja real y sostenida."
        )

    return {
        "ventanas": windows,
        "n_ventanas": n_windows,
        "consistencia_pct": consistency_pct,
        "retorno_promedio_out_sample_pct": avg_out_sample_return,
        "ventanas_con_advertencia": n_warnings,
        "advertencia_general": overall_warning,
    }
