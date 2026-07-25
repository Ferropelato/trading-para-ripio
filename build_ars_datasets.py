"""
Construye datasets reales en ARS para poder backtestear sobre los pares
que de verdad importan a los usuarios de Ripio Argentina (USDC_ARS,
BTC_ARS), no solo BTC/USD o AAPL.

Ripio Trade no publica un endpoint público de velas históricas (se
confirmó contra apidocs.ripiotrade.co -- solo expone ticker de 24hs,
orderbook y trades recientes, nada de historial OHLC). Por eso este
script combina dos fuentes REALES y públicas en vez de inventar datos:

1. Tipo de cambio USD/ARS histórico real, de Yahoo Finance (ticker
   "ARS=X", sin API key). Se usa como proxy de USDC_ARS -- USDC es una
   stablecoin 1:1 con el dólar, así que el tipo de cambio oficial/de
   mercado de USD/ARS es una aproximación razonable (con la salvedad de
   que el spread real de Ripio puede diferir un poco del que capta
   Yahoo).

2. BTC/USD real ya incluido en este repo (real_data/btc_daily.csv,
   2020-2024). Para "BTC_ARS" se aplica el tipo de cambio USD/ARS del
   MISMO día a la forma real (open/high/low/close) de la vela de BTC/USD
   -- o sea, se re-denomina en pesos el movimiento real de BTC, en vez de
   inventar una serie sintética. Es una aproximación (no hay order book
   nativo BTC/ARS con ese historial), documentada explícitamente acá y en
   el README.

Correr: python build_ars_datasets.py
Genera: real_data/usdc_ars_daily.csv, real_data/btc_ars_daily.csv
"""

import pandas as pd
import requests

from app_logger import setup_logging, get_logger

log = get_logger(__name__)

YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/ARS=X"


def fetch_usd_ars(range_="5y", interval="1d") -> pd.DataFrame:
    """Tipo de cambio USD/ARS real via Yahoo Finance (sin API key)."""
    response = requests.get(
        YAHOO_URL, params={"range": range_, "interval": interval},
        headers={"User-Agent": "Mozilla/5.0"}, timeout=20,
    )
    response.raise_for_status()
    result = response.json()["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]

    df = pd.DataFrame({
        "date": pd.to_datetime(result["timestamp"], unit="s").normalize(),
        "open": quote["open"], "high": quote["high"],
        "low": quote["low"], "close": quote["close"],
        "volume": quote["volume"],
    })
    df = df.dropna(subset=["open", "high", "low", "close"])
    df["volume"] = df["volume"].fillna(0)
    df = df.drop_duplicates(subset="date").set_index("date").sort_index()
    return df


def build_btc_ars(btc_usd: pd.DataFrame, usd_ars: pd.DataFrame) -> pd.DataFrame:
    """
    BTC_ARS = forma real de la vela BTC/USD * tipo de cambio ARS del mismo
    día. Se usa el cierre de ARS del día como tasa única para toda la vela
    (no se tiene el tipo de cambio intradía real), aplicada por igual a
    open/high/low/close -- preserva la forma/volatilidad real de BTC, solo
    la re-denomina en pesos.
    """
    merged = btc_usd.join(usd_ars[["close"]].rename(columns={"close": "ars_rate"}), how="inner")
    out = pd.DataFrame(index=merged.index)
    for col in ["open", "high", "low", "close"]:
        out[col] = merged[col] * merged["ars_rate"]
    out["volume"] = merged["volume"]
    out.index.name = "date"
    return out


def main():
    setup_logging(level="INFO")
    log.info("Descargando USD/ARS real (Yahoo Finance, ARS=X)...")
    usd_ars = fetch_usd_ars()
    log.info("USD/ARS: %d velas reales, %s a %s", len(usd_ars), usd_ars.index.min().date(), usd_ars.index.max().date())

    usd_ars.to_csv("real_data/usdc_ars_daily.csv")
    log.info("Guardado real_data/usdc_ars_daily.csv (proxy de USDC_ARS)")

    btc_usd = pd.read_csv("real_data/btc_daily.csv", parse_dates=["date"]).set_index("date")
    btc_ars = build_btc_ars(btc_usd, usd_ars)
    btc_ars.to_csv("real_data/btc_ars_daily.csv")
    log.info(
        "Guardado real_data/btc_ars_daily.csv (%d velas, solapamiento real %s a %s)",
        len(btc_ars), btc_ars.index.min().date(), btc_ars.index.max().date(),
    )


if __name__ == "__main__":
    main()
