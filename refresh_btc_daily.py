"""
Refresca real_data/btc_daily.csv con velas reales de BTC/USD hasta hoy.

Se detectó (al construir los datasets de Brasil/Colombia) que este archivo
había quedado desactualizado desde 2024-09-17 -- a diferencia de
real_data/eth_daily.csv, que sí se venía refrescando. Esto afectaba
cualquier backtest o re-denominación (BTC_ARS, y ahora BTC_BRL) que
dependiera de real_data/btc_daily.csv: usaban ~2 años menos de historial
real del que estaba disponible.

No se pisa el historial 2020-2024 ya presente (son velas reales, no hay
motivo para volver a bajarlas); solo se agregan las velas reales nuevas
que falten, bajadas de Yahoo Finance (ticker BTC-USD, sin API key).

Correr: python refresh_btc_daily.py
"""

import pandas as pd
import requests

from app_logger import setup_logging, get_logger

log = get_logger(__name__)

YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/BTC-USD"


def fetch_btc_usd(range_="2y", interval="1d") -> pd.DataFrame:
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


def main():
    setup_logging(level="INFO")

    existing = pd.read_csv("real_data/btc_daily.csv", parse_dates=["date"]).set_index("date")
    log.info("btc_daily.csv actual: %d velas, %s a %s", len(existing), existing.index.min().date(), existing.index.max().date())

    log.info("Descargando BTC/USD real (Yahoo Finance, BTC-USD)...")
    fresh = fetch_btc_usd()
    log.info("Yahoo: %d velas reales, %s a %s", len(fresh), fresh.index.min().date(), fresh.index.max().date())

    combined = pd.concat([existing, fresh[~fresh.index.isin(existing.index)]]).sort_index()
    combined.to_csv("real_data/btc_daily.csv")
    log.info("Guardado real_data/btc_daily.csv (%d velas, %s a %s) -- %d velas nuevas agregadas",
              len(combined), combined.index.min().date(), combined.index.max().date(), len(combined) - len(existing))


if __name__ == "__main__":
    main()
