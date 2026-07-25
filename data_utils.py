"""
Fuente de datos para el backtester.

IMPORTANTE: este sandbox no tiene acceso de red a APIs de mercado en vivo
(Yahoo Finance, Alpha Vantage, brokers, etc). Por eso este módulo genera
datos SINTÉTICOS realistas para probar el motor end-to-end. Para uso real,
reemplazá `generate_synthetic_data` por una función que traiga datos de tu
fuente real (ver README.md -> sección "Conectar datos reales").
"""

import numpy as np
import pandas as pd


def generate_synthetic_data(n_days: int = 500, start_price: float = 100.0,
                             annual_vol: float = 0.30, annual_drift: float = 0.08,
                             seed: int = 42, regime_shifts: bool = True) -> pd.DataFrame:
    """
    Genera una serie de precios OHLCV con movimiento browniano geométrico,
    con cambios de régimen de volatilidad para simular condiciones de
    mercado variadas (tranquilo -> volátil -> tendencia fuerte).
    """
    rng = np.random.default_rng(seed)
    dt = 1 / 252
    prices = [start_price]

    vol = annual_vol
    drift = annual_drift

    for day in range(n_days):
        if regime_shifts and day % 80 == 0 and day > 0:
            vol = annual_vol * rng.uniform(0.5, 1.8)
            drift = annual_drift * rng.uniform(-1.5, 2.0)

        shock = rng.normal(
            (drift - 0.5 * vol ** 2) * dt,
            vol * np.sqrt(dt),
        )
        prices.append(prices[-1] * np.exp(shock))

    close = np.array(prices[1:])
    # pd.bdate_range(end=..., periods=n) puede devolver un día menos que
    # `periods` cuando `end` cae en fin de semana (comportamiento observado
    # en pandas 3.0.2). Se pide de más y se recorta al final para garantizar
    # exactamente n_days fechas sin importar qué día se ejecute esto.
    dates = pd.bdate_range(end=pd.Timestamp.today(), periods=n_days + 10)[-n_days:]

    daily_range = close * rng.uniform(0.005, 0.02, size=n_days)
    high = close + daily_range * rng.uniform(0.3, 1.0, size=n_days)
    low = close - daily_range * rng.uniform(0.3, 1.0, size=n_days)
    open_ = low + (high - low) * rng.uniform(0.2, 0.8, size=n_days)
    volume = rng.integers(100_000, 2_000_000, size=n_days)

    df = pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close, "volume": volume,
    }, index=dates)
    df.index.name = "date"
    return df


def generate_correlated_pair(n_days: int = 500, correlation: float = 0.8,
                              start_price_a: float = 100.0, start_price_b: float = 50.0,
                              annual_vol: float = 0.35, seed: int = 7) -> tuple:
    """
    Genera dos series de precios sintéticas con una correlación de retornos
    diarios aproximadamente igual a `correlation`. Sirve para probar lógica
    de riesgo de portafolio (ej. dos criptomonedas que suelen moverse
    juntas, como BTC y ETH) sin depender de conseguir dos datasets reales
    del mismo período exacto.
    """
    rng = np.random.default_rng(seed)
    dt = 1 / 252
    n = n_days

    # Genera un factor común y un componente independiente por activo,
    # combinados según la correlación deseada (método de Cholesky simplificado
    # para 2 variables).
    common = rng.normal(0, 1, n)
    indep_a = rng.normal(0, 1, n)
    indep_b = rng.normal(0, 1, n)

    shocks_a = correlation * common + np.sqrt(1 - correlation ** 2) * indep_a
    shocks_b = correlation * common + np.sqrt(1 - correlation ** 2) * indep_b

    def build_prices(shocks, start_price):
        prices = [start_price]
        for s in shocks:
            ret = (0.05 - 0.5 * annual_vol ** 2) * dt + annual_vol * np.sqrt(dt) * s
            prices.append(prices[-1] * np.exp(ret))
        return np.array(prices[1:])

    close_a = build_prices(shocks_a, start_price_a)
    close_b = build_prices(shocks_b, start_price_b)

    # Mismo ajuste que en generate_synthetic_data: garantizar exactamente
    # n_days fechas sin importar si `end` cae en fin de semana.
    dates = pd.bdate_range(end=pd.Timestamp.today(), periods=n_days + 10)[-n_days:]

    def to_ohlcv(close):
        daily_range = close * rng.uniform(0.005, 0.02, size=n_days)
        high = close + daily_range * rng.uniform(0.3, 1.0, size=n_days)
        low = close - daily_range * rng.uniform(0.3, 1.0, size=n_days)
        open_ = low + (high - low) * rng.uniform(0.2, 0.8, size=n_days)
        volume = rng.integers(100_000, 2_000_000, size=n_days)
        df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                            "volume": volume}, index=dates)
        df.index.name = "date"
        return df

    return to_ohlcv(close_a), to_ohlcv(close_b)


def load_csv(path: str, on_missing: str = "raise") -> pd.DataFrame:
    """
    Carga un CSV con columnas: date, open, high, low, close, volume
    (formato estándar exportado por la mayoría de fuentes de datos).

    on_missing controla qué hacer si hay valores faltantes dentro del
    archivo:
    - "raise": no toca nada, deja que la validación de safety.py lo detecte
      y frene el proceso (comportamiento por defecto, el más seguro).
    - "ffill": rellena con el último valor válido conocido (razonable para
      huecos cortos, ej. un feriado no marcado).
    - "interpolate": interpola linealmente entre el valor anterior y el
      siguiente (razonable si los huecos son puntuales, no tramos largos).
    """
    df = pd.read_csv(path, parse_dates=["date"])
    df = df.set_index("date").sort_index()
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(df.columns.str.lower())
    if missing:
        raise ValueError(f"Faltan columnas en el CSV: {missing}")

    if on_missing == "ffill":
        df = df.ffill()
    elif on_missing == "interpolate":
        df = df.interpolate(method="linear")
    # "raise" no hace nada acá -- la validación de safety.py se encarga

    return df


def load_csv_with_fallback(paths: list, on_missing: str = "raise") -> tuple:
    """
    Intenta cargar datos de una lista de rutas en orden, usando la primera
    que exista y cargue sin error. Devuelve (DataFrame, ruta_usada,
    lista_de_fuentes_que_fallaron). Pensado para el caso de producción
    donde una fuente principal de datos puede fallar (API caída, archivo
    corrupto) y hace falta una alternativa sin que el sistema se detenga
    silenciosamente.
    """
    failed = []
    for path in paths:
        try:
            df = load_csv(path, on_missing=on_missing)
            return df, path, failed
        except (FileNotFoundError, ValueError, pd.errors.EmptyDataError) as e:
            failed.append({"ruta": path, "error": str(e)})
            continue

    raise RuntimeError(
        f"Ninguna de las {len(paths)} fuentes de datos pudo cargarse. Detalle: {failed}"
    )
