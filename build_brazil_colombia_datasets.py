"""
Construye datasets reales en BRL y COP para poder correr sesiones en vivo
sobre los pares de Ripio en Brasil y Colombia -- mismo criterio y misma
técnica que `build_ars_datasets.py` (no se inventa nada, se combinan dos
fuentes reales y públicas de Yahoo Finance, sin API key):

1. Tipo de cambio USD/BRL y USD/COP histórico real (tickers "BRL=X" y
   "COP=X"). Se usan directo como proxy de USDC_BRL y USDC_COP -- USDC es
   una stablecoin 1:1 con el dólar, así que el tipo de cambio oficial/de
   mercado es una aproximación razonable.

2. BTC/USD, ETH/USD (ya en este repo) y SOL/USD (bajado acá, mismo
   criterio que `build_new_pairs_datasets.py`) re-denominados en BRL
   aplicando el tipo de cambio real del mismo día -- se preserva la forma
   real de cada vela, solo se re-denomina. Aproximación documentada
   (no hay order book nativo BTC/BRL, ETH/BRL o SOL/BRL con este
   historial), mismo criterio ya usado para BTC_ARS.

Pares de Ripio cubiertos por este script: USDC_BRL, USDT_BRL (ambos ~1:1
dólar, se usa el mismo proxy de tipo de cambio para los dos), BTC_BRL,
ETH_BRL, SOL_BRL, USDC_COP.

Correr: python build_brazil_colombia_datasets.py
"""

import pandas as pd
import requests

from app_logger import setup_logging, get_logger

log = get_logger(__name__)

YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"


def fetch_daily(ticker: str, range_: str = "2y") -> pd.DataFrame:
    response = requests.get(
        YAHOO_URL.format(ticker=ticker), params={"range": range_, "interval": "1d"},
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


def redenominate(usd_df: pd.DataFrame, fx_rate: pd.DataFrame) -> pd.DataFrame:
    """Re-denomina una vela real en USD a otra moneda, aplicando el tipo
    de cambio real del MISMO día a open/high/low/close por igual --
    preserva la forma/volatilidad real del activo, solo cambia la unidad."""
    merged = usd_df.join(fx_rate[["close"]].rename(columns={"close": "fx_rate"}), how="inner")
    out = pd.DataFrame(index=merged.index)
    for col in ["open", "high", "low", "close"]:
        out[col] = merged[col] * merged["fx_rate"]
    out["volume"] = merged["volume"]
    out.index.name = "date"
    return out


def main():
    setup_logging(level="INFO")

    log.info("Descargando USD/BRL real (Yahoo Finance, BRL=X)...")
    usd_brl = fetch_daily("BRL=X")
    log.info("USD/BRL: %d velas reales, %s a %s", len(usd_brl), usd_brl.index.min().date(), usd_brl.index.max().date())
    usd_brl.to_csv("real_data/usdc_brl_daily.csv")
    usd_brl.to_csv("real_data/usdt_brl_daily.csv")  # USDT también ~1:1 dólar, mismo proxy
    log.info("Guardado real_data/usdc_brl_daily.csv y usdt_brl_daily.csv (proxy de USDC_BRL/USDT_BRL)")

    log.info("Descargando USD/COP real (Yahoo Finance, COP=X)...")
    usd_cop = fetch_daily("COP=X")
    log.info("USD/COP: %d velas reales, %s a %s", len(usd_cop), usd_cop.index.min().date(), usd_cop.index.max().date())
    usd_cop.to_csv("real_data/usdc_cop_daily.csv")
    log.info("Guardado real_data/usdc_cop_daily.csv (proxy de USDC_COP)")

    btc_usd = pd.read_csv("real_data/btc_daily.csv", parse_dates=["date"]).set_index("date")
    btc_brl = redenominate(btc_usd, usd_brl)
    btc_brl.to_csv("real_data/btc_brl_daily.csv")
    log.info("Guardado real_data/btc_brl_daily.csv (%d velas, %s a %s)",
              len(btc_brl), btc_brl.index.min().date(), btc_brl.index.max().date())

    eth_usd = pd.read_csv("real_data/eth_daily.csv", parse_dates=["date"]).set_index("date")
    eth_brl = redenominate(eth_usd, usd_brl)
    eth_brl.to_csv("real_data/eth_brl_daily.csv")
    log.info("Guardado real_data/eth_brl_daily.csv (%d velas, %s a %s)",
              len(eth_brl), eth_brl.index.min().date(), eth_brl.index.max().date())

    log.info("Descargando SOL/USD real (Yahoo Finance)...")
    sol_usd = fetch_daily("SOL-USD")
    sol_brl = redenominate(sol_usd, usd_brl)
    sol_brl.to_csv("real_data/sol_brl_daily.csv")
    log.info("Guardado real_data/sol_brl_daily.csv (%d velas, %s a %s)",
              len(sol_brl), sol_brl.index.min().date(), sol_brl.index.max().date())


if __name__ == "__main__":
    main()
