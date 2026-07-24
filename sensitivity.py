"""
Análisis de sensibilidad de parámetros: corre la misma estrategia con
variaciones pequeñas de sus parámetros (ej. período de media móvil 18,
20, 22 en vez de solo 20) y compara los resultados. Si el rendimiento es
parecido en todo el rango, la estrategia es robusta. Si un cambio chico
en el parámetro hace que pase de ganar a perder, es señal de sobreajuste
-- el "buen resultado" original probablemente fue suerte con ese valor
específico, no una ventaja real y estable.
"""

import itertools

from backtester import Backtester
import strategies as strategies_module


def parameter_sensitivity(df, strategy_name: str, profile_name: str, param_grid: dict,
                           initial_capital: float = 1000.0) -> dict:
    """
    param_grid: dict de {nombre_parametro: [valores a probar]}, ej.
        {"fast": [15, 20, 25], "slow": [40, 50, 60]}  # para 'tendencia'
        {"lookback": [15, 20, 25]}                     # para 'momentum'
    """
    strategy_fn = getattr(strategies_module, {
        "tendencia": "trend_following",
        "momentum": "momentum_breakout",
        "contraccion_volatilidad": "volatility_contraction",
        "valor_en_tendencia": "value_dip_in_uptrend",
    }[strategy_name])

    param_names = list(param_grid.keys())
    combinations = list(itertools.product(*param_grid.values()))

    results = []
    for combo in combinations:
        params = dict(zip(param_names, combo))
        try:
            signal = strategy_fn(df, **params)
        except Exception as e:
            results.append({"parametros": params, "error": str(e)})
            continue

        bt = Backtester(df, strategy_name=strategy_name, profile_name=profile_name,
                         initial_capital=initial_capital, custom_signal=signal, validate=False)
        r = bt.run()
        results.append({
            "parametros": params,
            "retorno_total_pct": r["retorno_total_pct"],
            "num_operaciones": r["num_operaciones"],
            "max_drawdown_pct": r["max_drawdown_pct"],
        })

    valid_results = [r for r in results if "error" not in r]
    returns = [r["retorno_total_pct"] for r in valid_results]

    if not returns:
        return {"combinaciones": results, "advertencia": "Ninguna combinación de parámetros corrió sin error"}

    n_positive = sum(1 for r in returns if r > 0)
    n_negative = sum(1 for r in returns if r <= 0)
    sign_flips = n_positive > 0 and n_negative > 0

    advertencia = None
    if sign_flips:
        advertencia = (
            f"De {len(returns)} combinaciones de parámetros probadas, {n_positive} dieron "
            f"ganancia y {n_negative} dieron pérdida -- el resultado depende fuertemente del "
            f"valor exacto del parámetro. Esto es señal de sobreajuste: no confiar en un único "
            f"valor 'que funcionó' sin entender por qué el vecino no."
        )

    return {
        "combinaciones": results,
        "retorno_min_pct": round(min(returns), 2),
        "retorno_max_pct": round(max(returns), 2),
        "retorno_promedio_pct": round(sum(returns) / len(returns), 2),
        "combinaciones_rentables": n_positive,
        "combinaciones_perdedoras": n_negative,
        "cambia_de_signo": sign_flips,
        "advertencia": advertencia,
    }
