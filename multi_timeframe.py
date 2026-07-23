"""
Análisis multi-timeframe: confirma la señal del timeframe operativo (ej.
diario) con la tendencia de un timeframe mayor (ej. semanal) antes de
operar. La idea, muy usada en trading discrecional y sistemático: no
importa cuán buena se vea una señal en el diario si la tendencia semanal
va en contra -- suele ser una señal de menor calidad (un rebote dentro de
una tendencia bajista mayor, por ejemplo).
"""

import pandas as pd


def resample_to_higher_timeframe(df: pd.DataFrame, rule: str = "W") -> pd.DataFrame:
    """
    Re-muestrea datos OHLCV a un timeframe mayor. rule usa la sintaxis de
    pandas: "W" = semanal, "M" = mensual, "3D" = cada 3 días, etc.
    """
    resampled = df.resample(rule).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    })
    return resampled.dropna()


def higher_timeframe_trend(df: pd.DataFrame, rule: str = "W", ma_period: int = 10) -> pd.Series:
    """
    Calcula la tendencia del timeframe mayor: True si el precio de cierre
    del timeframe mayor está por encima de su propia media móvil (o sea,
    "la tendencia semanal es alcista").
    """
    higher_tf = resample_to_higher_timeframe(df, rule)
    higher_ma = higher_tf["close"].rolling(ma_period).mean()
    higher_trend = higher_tf["close"] > higher_ma
    return higher_trend


def apply_multi_timeframe_filter(signal: pd.Series, df: pd.DataFrame,
                                  higher_rule: str = "W", ma_period: int = 10) -> pd.Series:
    """
    Filtra una señal del timeframe operativo (ej. diario) para que solo
    se mantenga activa cuando el timeframe mayor (ej. semanal) también
    confirma tendencia alcista. Reindexar el timeframe mayor hacia abajo
    usando forward-fill: cada vela diaria "hereda" el estado de la última
    vela semanal ya cerrada (nunca mira datos semanales del futuro).
    """
    higher_trend = higher_timeframe_trend(df, higher_rule, ma_period)

    # Reindexar al índice diario, propagando el último valor semanal conocido
    # (shift para asegurar que solo se usa la vela semanal ya CERRADA, nunca
    # la que todavía está en curso -- evita look-ahead bias)
    higher_trend_shifted = higher_trend.shift(1)
    daily_trend = higher_trend_shifted.reindex(df.index, method="ffill")

    filtered = signal.copy()
    # Donde no hay confirmación de tendencia mayor (False o NaN por falta de histórico), apagar la señal
    no_confirmation = daily_trend.fillna(False) == False
    filtered[no_confirmation] = 0
    return filtered
