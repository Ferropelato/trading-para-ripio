"""
Significancia estadística: la pregunta que ningún backtest simple
responde es "¿esta estrategia tiene una ventaja real, o el resultado que
vimos podría haber salido igual con señales completamente al azar?".

Este módulo genera cientos de estrategias "de mentira" -- que entran y
salen en momentos aleatorios, pero respetando la MISMA cantidad de
operaciones y el MISMO gestor de riesgo que la estrategia real -- y
compara el resultado real contra esa distribución. Si la estrategia real
queda en el percentil 90+ de esa distribución aleatoria, hay evidencia de
que agrega algo. Si queda en el medio del montón, probablemente no.
"""

import numpy as np
import pandas as pd

from risk_manager import position_size
from risk_profiles import get_profile


def _atr(df, period=14):
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def _run_random_strategy(df: pd.DataFrame, profile: dict, initial_capital: float,
                          n_trades_target: int, rng: np.random.Generator,
                          commission_pct: float = 0.001) -> float:
    """
    Corre una "estrategia" que entra en `n_trades_target` momentos
    elegidos al azar del historial, usa el MISMO gestor de riesgo (mismo
    stop loss/take profit por ATR) que la estrategia real, y sostiene
    cada posición hasta que toque el stop o el take profit (o se acabe el
    historial). Devuelve el retorno total en %.
    """
    atr = _atr(df)
    valid_days = df.index[atr.notna() & (atr > 0)]
    if len(valid_days) < n_trades_target:
        n_trades_target = len(valid_days)

    entry_days = rng.choice(valid_days, size=n_trades_target, replace=False)
    capital = initial_capital

    for entry_date in sorted(entry_days):
        entry_price = df.loc[entry_date, "close"]
        current_atr = atr.loc[entry_date]
        sizing = position_size(capital, entry_price, current_atr, profile)
        units = sizing["unidades"]
        if units <= 0:
            continue

        stop_loss = sizing["stop_loss"]
        take_profit = sizing["take_profit"]
        cost = units * entry_price * (1 + commission_pct)
        if cost > capital:
            continue
        capital -= cost

        future = df.loc[entry_date:, "close"]
        exit_price = future.iloc[-1]  # si nunca toca stop/take, cierra al último precio disponible
        for date, price in future.items():
            if price <= stop_loss or price >= take_profit:
                exit_price = price
                break

        proceeds = units * exit_price * (1 - commission_pct)
        capital += proceeds

    return (capital / initial_capital - 1) * 100


def test_significance_vs_random(df: pd.DataFrame, strategy_result: dict, profile_name: str,
                                 initial_capital: float = 1000.0, n_simulations: int = 200,
                                 seed: int = 123) -> dict:
    """
    Compara el retorno de una estrategia real (ya corrida, pasada en
    `strategy_result`) contra `n_simulations` estrategias aleatorias con
    la misma cantidad de operaciones y el mismo perfil de riesgo.
    """
    profile = get_profile(profile_name)
    n_trades = strategy_result["num_operaciones"]

    if n_trades == 0:
        return {"error": "La estrategia no hizo ninguna operación -- no hay nada que comparar"}

    rng = np.random.default_rng(seed)
    random_returns = [
        _run_random_strategy(df, profile, initial_capital, n_trades, rng)
        for _ in range(n_simulations)
    ]
    random_returns = np.array(random_returns)

    real_return = strategy_result["retorno_total_pct"]
    percentile = float((random_returns < real_return).mean() * 100)

    interpretation = None
    if percentile >= 90:
        interpretation = "La estrategia superó al 90%+ de las versiones aleatorias -- hay evidencia de ventaja real."
    elif percentile <= 60:
        interpretation = (
            "La estrategia no se diferencia claramente de entrar en momentos al azar "
            "con la misma gestión de riesgo -- el resultado podría explicarse por casualidad."
        )
    else:
        interpretation = "Resultado ambiguo -- ni claramente superior ni claramente indistinguible del azar."

    return {
        "retorno_estrategia_real_pct": real_return,
        "retorno_promedio_aleatorio_pct": round(float(random_returns.mean()), 2),
        "percentil_de_la_estrategia_real": round(percentile, 1),
        "n_simulaciones_aleatorias": n_simulations,
        "interpretacion": interpretation,
    }
