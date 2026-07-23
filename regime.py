"""
Detección de régimen de mercado: identifica si el mercado está en
tendencia fuerte o lateral/sin dirección clara, usando ADX (Average
Directional Index), un indicador estándar y de dominio público.

La idea: las estrategias de tendencia/momentum tienden a perder plata en
mercados laterales (muchas señales falsas, entradas y salidas seguidas).
Si el motor puede "darse cuenta" de que no hay tendencia, puede evitar
operar en esas condiciones en vez de forzar señales que no tienen buena
base.
"""

import numpy as np
import pandas as pd


def compute_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)

    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr.replace(0, np.nan)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1 / period, adjust=False).mean()
    return adx.fillna(0)


def classify_regime(df: pd.DataFrame, adx_period: int = 14, trend_threshold: float = 25.0) -> pd.Series:
    """
    Devuelve una Serie con valores "tendencia" o "lateral" para cada vela,
    según si el ADX supera el umbral (por defecto 25, el valor estándar
    usado en la literatura técnica para "hay tendencia").
    """
    adx = compute_adx(df, adx_period)
    return adx.apply(lambda x: "tendencia" if x >= trend_threshold else "lateral")


def apply_regime_filter(signal: pd.Series, df: pd.DataFrame, strategy_type: str = "tendencia",
                         adx_period: int = 14, trend_threshold: float = 25.0) -> pd.Series:
    """
    Filtra una señal de estrategia según el régimen de mercado.

    - Si `strategy_type` es "tendencia" (aplica a tendencia/momentum/
      contracción de volatilidad): solo deja pasar señales de compra cuando
      el régimen es "tendencia" -- en lateral, fuerza la señal a 0 (afuera).
    - Si `strategy_type` es "cualquiera" (ej. valor_en_tendencia, que puede
      tener sentido en cualquier régimen): no filtra nada.
    """
    if strategy_type == "cualquiera":
        return signal

    regime = classify_regime(df, adx_period, trend_threshold)
    filtered = signal.copy()
    filtered[regime == "lateral"] = 0
    return filtered
