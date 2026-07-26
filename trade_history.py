"""
Historial de operaciones cerradas, persistente entre reinicios del
proceso -- a diferencia del estado (posiciones abiertas y capital, ver
state_store.py), pensado para uso intermitente: el motor puede arrancar
hoy, pararse esta noche, y volver a arrancar en tres semanas, y este
archivo sigue acumulando cada operación desde el principio, sin importar
cuántas veces se reinició el proceso entre medio. Es la fuente de datos
para un reporte de soporte o una revisión de actividad que cubra todo el
tiempo que estuvo operando, no solo la corrida actual.

Formato simple (CSV, append-only) para poder abrirlo con cualquier
planilla de cálculo sin herramientas adicionales.
"""

import csv
import os
from datetime import datetime, timezone

FIELDNAMES = ["timestamp", "symbol", "side", "motivo", "units", "price", "pnl", "balance_resultante"]


class TradeHistoryLog:
    def __init__(self, csv_path: str):
        self.csv_path = csv_path
        if not os.path.exists(csv_path):
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=FIELDNAMES).writeheader()

    def append(self, symbol: str, side: str, motivo: str, units: float,
               price: float, pnl: float, balance_resultante: float) -> None:
        with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writerow({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "symbol": symbol,
                "side": side,
                "motivo": motivo or "",
                "units": units,
                "price": price if price is not None else "",
                "pnl": round(pnl, 6) if pnl is not None else "",
                "balance_resultante": round(balance_resultante, 2),
            })

    def load_all(self) -> list:
        """Todo el historial acumulado hasta ahora, sin importar cuántos
        reinicios del proceso hubo entre las distintas filas."""
        if not os.path.exists(self.csv_path):
            return []
        with open(self.csv_path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
