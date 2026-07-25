"""
Optimización de parámetros con protección anti-overfitting OBLIGATORIA:
este módulo nunca elige "los mejores" parámetros mirando el resultado
sobre el 100% de los datos (in-sample). Cada combinación se rankea
exclusivamente por su desempeño FUERA de muestra, en varias ventanas
walk-forward -- tal como lo pedía el propio README de este proyecto
("el paso siguiente sería una búsqueda de parámetros que SIEMPRE se
valide con walk-forward, nunca optimizando sobre el 100% de los datos").

No existe en este módulo ninguna función que rankee por el retorno sobre
el dataset completo -- estructuralmente no se puede pedir ese atajo por
accidente.
"""

import itertools

from backtester import Backtester
import strategies as strategies_module

_STRATEGY_FN_NAMES = {
    "tendencia": "trend_following",
    "momentum": "momentum_breakout",
    "contraccion_volatilidad": "volatility_contraction",
    "valor_en_tendencia": "value_dip_in_uptrend",
}


def _strategy_fn(strategy_name):
    return getattr(strategies_module, _STRATEGY_FN_NAMES[strategy_name])


def _windows(df, n_windows):
    window_size = len(df) // n_windows
    if window_size < 20:
        raise ValueError(
            f"Cada ventana tendría solo {window_size} velas -- muy pocas para "
            f"que las estrategias funcionen. Usá menos ventanas o más datos."
        )
    for w in range(n_windows):
        start = w * window_size
        end = start + window_size if w < n_windows - 1 else len(df)
        yield df.iloc[start:end]


def _evaluate_combo_out_of_sample(df, strategy_name, profile_name, params,
                                   n_windows, train_pct, initial_capital):
    """
    Evalúa UNA combinación de parámetros exclusivamente por su desempeño
    fuera de muestra (nunca por el tramo in-sample) en varias ventanas.
    """
    strategy_fn = _strategy_fn(strategy_name)
    out_sample_returns = []
    per_window = []

    for idx, window_df in enumerate(_windows(df, n_windows), start=1):
        try:
            signal = strategy_fn(window_df, **params)
        except Exception as e:
            per_window.append({"ventana": idx, "error": str(e)})
            continue

        split_idx = int(len(window_df) * train_pct)
        out_sample_df = window_df.iloc[split_idx:]
        out_sample_signal = signal.iloc[split_idx:]

        bt = Backtester(out_sample_df, strategy_name=strategy_name, profile_name=profile_name,
                         initial_capital=initial_capital, custom_signal=out_sample_signal, validate=False)
        result = bt.run()
        out_sample_returns.append(result["retorno_total_pct"])
        per_window.append({
            "ventana": idx,
            "retorno_out_sample_pct": result["retorno_total_pct"],
            "num_operaciones": result["num_operaciones"],
        })

    if not out_sample_returns:
        return {
            "parametros": params, "ventanas": per_window,
            "retorno_promedio_out_sample_pct": None, "consistencia_pct": None,
            "error": "Ninguna ventana pudo evaluarse (ver errores por ventana)",
        }

    n_positive = sum(1 for r in out_sample_returns if r > 0)
    return {
        "parametros": params,
        "ventanas": per_window,
        "retorno_promedio_out_sample_pct": round(sum(out_sample_returns) / len(out_sample_returns), 2),
        "consistencia_pct": round(n_positive / len(out_sample_returns) * 100, 1),
    }


def optimize_parameters(df, strategy_name: str, profile_name: str, param_grid: dict,
                         n_windows: int = 5, train_pct: float = 0.6,
                         initial_capital: float = 1000.0) -> dict:
    """
    Prueba cada combinación de `param_grid` (dict {nombre: [valores]}, ej.
    {"fast": [15, 20, 25], "slow": [40, 50, 60]}) y la rankea
    EXCLUSIVAMENTE por su retorno promedio FUERA de muestra en
    `n_windows` ventanas walk-forward. Devuelve todas las combinaciones
    evaluadas (no solo la "ganadora") para que la decisión final sea
    informada, no una caja negra.
    """
    param_names = list(param_grid.keys())
    combinations = [dict(zip(param_names, combo)) for combo in itertools.product(*param_grid.values())]

    try:
        list(_windows(df, n_windows))  # valida una sola vez que hay datos suficientes (igual para todo el grid)
    except ValueError as e:
        return {
            "estrategia": strategy_name, "perfil_riesgo": profile_name,
            "combinaciones_evaluadas": [], "ranking_out_of_sample": [],
            "recomendado": None, "advertencia": str(e),
        }

    evaluated = [
        _evaluate_combo_out_of_sample(df, strategy_name, profile_name, params,
                                       n_windows, train_pct, initial_capital)
        for params in combinations
    ]

    valid = [e for e in evaluated if e.get("retorno_promedio_out_sample_pct") is not None]
    ranked = sorted(valid, key=lambda e: (e["retorno_promedio_out_sample_pct"], e["consistencia_pct"]), reverse=True)

    recommended = ranked[0] if ranked else None
    warning = None
    if not ranked:
        warning = "Ninguna combinación de parámetros pudo evaluarse -- revisar errores por combinación."
    elif recommended["consistencia_pct"] < 50:
        warning = (
            "Incluso la mejor combinación encontrada solo fue rentable fuera de "
            f"muestra en {recommended['consistencia_pct']}% de las ventanas -- no hay "
            "una combinación de parámetros claramente robusta para este activo/"
            "estrategia con los datos disponibles. Conseguir más datos/activos "
            "antes de confiar en este resultado."
        )

    return {
        "estrategia": strategy_name,
        "perfil_riesgo": profile_name,
        "combinaciones_evaluadas": evaluated,
        "ranking_out_of_sample": ranked,
        "recomendado": recommended,
        "advertencia": warning,
    }
