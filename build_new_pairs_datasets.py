"""
Construye datasets reales de historial diario para los pares nuevos que
se suman a las sesiones en vivo (ver README, ronda de ranking/rotación +
ampliación de pares): LINK_USDC, LTC_USDC, UNI_USDC, WLD_USDC, RPC_USDC,
RIF_USDC. Mismo criterio del resto del proyecto -- datos REALES y
públicos (Yahoo Finance, sin API key), nunca inventados.

Se probaron los 15 pares habilitados para Argentina hoy contra
`/trade/public/pairs` de Ripio (ver informe de mercado). De los 8 que
todavía no tenían sesión en vivo, 6 tienen historial real verificable acá
(nombre completo confirmado contra la respuesta de Yahoo, no solo el
ticker): Chainlink, Litecoin, Uniswap, Worldcoin, Ripio Coin y Rootstock
Infrastructure Framework. **HYPE_USDC y LAC_USDC quedan afuera de esta
ronda**: Yahoo Finance no tiene historial para esos dos tickers todavía
(devuelve 0 velas) -- no hay una fuente real y pública alternativa
verificada, así que no se inventa un dataset para ellos. Se pueden operar
igual en modo MANUAL (no necesitan historial para calentar indicadores en
ese modo), solo no entran a la rotación automática hasta conseguir una
fuente real de historial.

Correr: python build_new_pairs_datasets.py
Genera: real_data/<symbol>_daily.csv para cada uno de los 6 pares.
"""

import pandas as pd
import requests

from app_logger import setup_logging, get_logger

log = get_logger(__name__)

YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"

# symbol de Ripio -> ticker de Yahoo Finance (verificado contra el
# "shortName" real de la respuesta, no solo asumido por el prefijo).
PARES_NUEVOS = {
    "LINK_USDC": "LINK-USD",
    "LTC_USDC": "LTC-USD",
    "UNI_USDC": "UNI7083-USD",
    "WLD_USDC": "WLD-USD",
    "RPC_USDC": "RPC-USD",
    "RIF_USDC": "RIF-USD",
}


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


def main():
    setup_logging(level="INFO")
    for symbol, ticker in PARES_NUEVOS.items():
        log.info("Descargando %s (%s) real de Yahoo Finance...", symbol, ticker)
        df = fetch_daily(ticker)
        if df.empty:
            log.warning("%s (%s): Yahoo Finance no devolvió velas -- se omite, no se inventa nada", symbol, ticker)
            continue
        out_path = f"real_data/{symbol.lower()}_daily.csv"
        df.to_csv(out_path)
        log.info("Guardado %s: %d velas reales, %s a %s", out_path, len(df), df.index.min().date(), df.index.max().date())


if __name__ == "__main__":
    main()
