"""
Refresca TODOS los datasets base de real_data/ con las velas reales que
falten hasta hoy, y regenera los derivados (ARS, BRL, COP).

Generalización de refresh_btc_daily.py (que nació al descubrir que
btc_daily.csv llevaba casi dos años parado en 2024-09-17 sin que nadie
lo notara): el problema no era solo BTC -- eth_daily.csv estaba 6 días
viejo y los pares nuevos 2 días. Un dataset base viejo también corta a
los derivados que se construyen a partir de él (btc_ars, btc_brl,
eth_brl), así que refrescar "a mano un archivo cuando se nota" no
escala. Este script es la única puerta de entrada:

1. Baja de Yahoo Finance (sin API key) solo las velas reales que le
   falten a cada dataset base, sin pisar el historial ya guardado.
2. Corre build_ars_datasets.py y build_brazil_colombia_datasets.py para
   que los derivados queden consistentes con las bases recién
   refrescadas.

Los CSVs de sesiones en vivo se leen al ARRANCAR la sesión: refrescar
acá no afecta a una sesión corriendo, solo a la próxima que arranque
(o reinicie).

Correr: python refresh_datasets.py
"""

import subprocess
import sys

import pandas as pd

from app_logger import setup_logging, get_logger
from build_new_pairs_datasets import fetch_daily, PARES_NUEVOS

log = get_logger(__name__)

# dataset base -> ticker de Yahoo Finance (los pares nuevos reusan el
# mapa ya verificado de build_new_pairs_datasets.py).
DATASETS_BASE = {
    "real_data/btc_daily.csv": "BTC-USD",
    "real_data/eth_daily.csv": "ETH-USD",
    **{f"real_data/{sym.lower()}_daily.csv": tk for sym, tk in PARES_NUEVOS.items()},
}

SCRIPTS_DERIVADOS = ["build_ars_datasets.py", "build_brazil_colombia_datasets.py"]


def refresh_base(path: str, ticker: str) -> int:
    """Agrega a `path` las velas reales nuevas de Yahoo que no tenga.
    Devuelve cuántas velas se agregaron. No pisa el historial existente."""
    existing = pd.read_csv(path, parse_dates=["date"]).set_index("date")
    fresh = fetch_daily(ticker)
    if fresh.empty:
        log.warning("%s (%s): Yahoo no devolvió velas -- se deja como está", path, ticker)
        return 0
    combined = pd.concat([existing, fresh[~fresh.index.isin(existing.index)]]).sort_index()
    added = len(combined) - len(existing)
    if added:
        combined.to_csv(path)
    log.info("%s: %d velas nuevas (ahora %d, %s a %s)", path, added, len(combined),
             combined.index.min().date(), combined.index.max().date())
    return added


def main():
    setup_logging(level="INFO")
    total = 0
    for path, ticker in DATASETS_BASE.items():
        try:
            total += refresh_base(path, ticker)
        except Exception as e:
            log.error("No se pudo refrescar %s (%s): %s -- se sigue con el resto", path, ticker, e)

    log.info("Bases refrescadas (%d velas nuevas en total). Regenerando derivados...", total)
    for script in SCRIPTS_DERIVADOS:
        result = subprocess.run([sys.executable, script])
        if result.returncode != 0:
            log.error("%s terminó con código %d", script, result.returncode)


if __name__ == "__main__":
    main()
