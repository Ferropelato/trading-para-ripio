"""
Estrategias basadas en FILOSOFÍAS públicas y conocidas de distintos estilos de
inversión -- no son réplicas de ningún fondo o persona real, son
interpretaciones de reglas de dominio público (medias móviles, momentum,
contracción de volatilidad, etc). Cada función recibe un DataFrame con
columnas: open, high, low, close, volume (indexado por fecha) y devuelve
una Serie de señales: 1 = comprar/mantener largo, 0 = estar afuera.
"""

import numpy as np
import pandas as pd


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def trend_following(df: pd.DataFrame, fast: int = 20, slow: int = 50) -> pd.Series:
    """
    Estilo seguimiento de tendencia (similar en espíritu a lo publicado
    por Larry Williams / Turtle Traders): cruce de medias móviles.
    Entra cuando la media rápida cruza por encima de la lenta.
    """
    fast_ma = df["close"].rolling(fast).mean()
    slow_ma = df["close"].rolling(slow).mean()
    signal = (fast_ma > slow_ma).astype(int)
    return signal


def momentum_breakout(df: pd.DataFrame, lookback: int = 20) -> pd.Series:
    """
    Estilo momentum de corto plazo: entra cuando el precio hace un máximo
    de N períodos (ruptura), sale cuando hace un mínimo de N períodos.
    """
    rolling_high = df["close"].rolling(lookback).max()
    rolling_low = df["close"].rolling(lookback).min()
    signal = pd.Series(0, index=df.index)
    in_position = False
    for i in range(lookback, len(df)):
        price = df["close"].iloc[i]
        if not in_position and price >= rolling_high.iloc[i - 1]:
            in_position = True
        elif in_position and price <= rolling_low.iloc[i - 1]:
            in_position = False
        signal.iloc[i] = int(in_position)
    return signal


def volatility_contraction(df: pd.DataFrame, atr_period: int = 14,
                            contraction_window: int = 10) -> pd.Series:
    """
    Estilo contracción de volatilidad (en espíritu similar al patrón VCP
    popularizado por Mark Minervini): busca períodos donde el ATR se
    contrae marcadamente respecto a su propio promedio reciente, y luego
    una ruptura de precio con esa volatilidad comprimida.
    """
    atr = _atr(df, atr_period)
    atr_avg = atr.rolling(contraction_window).mean()
    is_contracted = atr < (atr_avg * 0.7)
    rolling_high = df["close"].rolling(contraction_window).max()

    signal = pd.Series(0, index=df.index)
    in_position = False
    for i in range(atr_period + contraction_window, len(df)):
        price = df["close"].iloc[i]
        contracted_recently = is_contracted.iloc[i - contraction_window:i].any()
        breakout = price >= rolling_high.iloc[i - 1]
        if not in_position and contracted_recently and breakout:
            in_position = True
        elif in_position and price < df["close"].rolling(5).mean().iloc[i]:
            in_position = False
        signal.iloc[i] = int(in_position)
    return signal


def value_dip_in_uptrend(df: pd.DataFrame, trend_ma: int = 200,
                          dip_lookback: int = 10) -> pd.Series:
    """
    Estilo "comprar calidad en baja dentro de tendencia alcista" (en
    espíritu con la idea de comprar con margen de seguridad dentro de un
    negocio/activo que ya viene demostrando fortaleza de largo plazo).
    No usa fundamentals (este motor solo ve precio) -- es una aproximación
    técnica a la idea, no un reemplazo del análisis de balances real.
    """
    long_trend = df["close"].rolling(trend_ma).mean()
    is_uptrend = df["close"] > long_trend
    recent_dip = df["close"] < df["close"].rolling(dip_lookback).max() * 0.95
    signal = (is_uptrend & recent_dip).astype(int)
    return signal


STRATEGIES = {
    "tendencia": trend_following,
    "momentum": momentum_breakout,
    "contraccion_volatilidad": volatility_contraction,
    "valor_en_tendencia": value_dip_in_uptrend,
}

# Usado por el filtro de régimen de mercado (regime.py): estrategias de
# tendencia solo deberían operar cuando el ADX confirma que hay tendencia.
STRATEGY_TYPE = {
    "tendencia": "tendencia",
    "momentum": "tendencia",
    "contraccion_volatilidad": "tendencia",
    "valor_en_tendencia": "cualquiera",
}


def get_strategy(name: str):
    name = name.lower().strip()
    if name not in STRATEGIES:
        raise ValueError(f"Estrategia '{name}' no existe. Opciones: {list(STRATEGIES.keys())}")
    return STRATEGIES[name]
