"""
Monitoreo de salud: detecta cuando el sistema no está recibiendo datos
frescos. En backtesting esto se traduce en detectar huecos sospechosos en
el historial; en vivo, se traduce en un "heartbeat" que avisa si el feed
de precios dejó de actualizarse. Ambos casos son la misma idea: un
sistema que opera con datos viejos sin darse cuenta es peligroso -- puede
tomar decisiones sobre un precio que ya no es real.
"""

from datetime import datetime, timedelta
import pandas as pd


def check_data_gaps(df: pd.DataFrame, max_gap_days: int = 3) -> list:
    """
    Revisa el historial en busca de huecos entre fechas consecutivas más
    grandes de lo esperable (feriados/fines de semana normales aparte).
    Devuelve una lista de huecos encontrados con su fecha y duración.
    """
    if len(df) < 2:
        return []

    gaps = []
    dates = df.index.to_series()
    diffs = dates.diff().dt.days.dropna()

    for date, gap_days in zip(dates.index[1:], diffs):
        if gap_days > max_gap_days:
            gaps.append({
                "fecha_antes_del_hueco": (date - pd.Timedelta(days=int(gap_days))),
                "fecha_despues_del_hueco": date,
                "dias_de_hueco": int(gap_days),
            })
    return gaps


class Heartbeat:
    """
    Para uso en vivo (no en backtest): registra la última vez que llegó un
    dato nuevo del feed de precios, y permite chequear si pasó demasiado
    tiempo sin actualizaciones -- señal de que la conexión se cayó o el
    bróker dejó de mandar datos, y el motor NO debería seguir operando
    como si nada, decidiendo sobre un precio potencialmente obsoleto.
    """

    def __init__(self, max_staleness_seconds: int = 120):
        self.max_staleness_seconds = max_staleness_seconds
        self.last_update = None

    def beat(self):
        """Llamar cada vez que llega un dato nuevo del feed."""
        self.last_update = datetime.utcnow()

    def is_stale(self) -> bool:
        if self.last_update is None:
            return True  # nunca recibió un dato -> tratarlo como no saludable
        elapsed = (datetime.utcnow() - self.last_update).total_seconds()
        return elapsed > self.max_staleness_seconds

    def status(self) -> dict:
        if self.last_update is None:
            return {"saludable": False, "motivo": "Nunca se recibió un dato del feed"}
        elapsed = (datetime.utcnow() - self.last_update).total_seconds()
        if elapsed > self.max_staleness_seconds:
            return {
                "saludable": False,
                "motivo": f"Sin datos nuevos hace {elapsed:.0f}s (límite: {self.max_staleness_seconds}s)",
            }
        return {"saludable": True, "motivo": None}
