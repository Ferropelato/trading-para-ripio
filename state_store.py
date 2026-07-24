"""
Persistencia de estado: guarda en disco qué posiciones están abiertas
(activo, unidades, precio de entrada, stop loss, take profit) para que si
el proceso se cae o se reinicia, no pierda ese registro. Sin esto, un
reinicio podría hacer que el motor "olvide" que ya tiene una posición
abierta y trate de abrir otra encima, o pierda el stop loss/take profit
de algo que ya está corriendo con plata real.
"""

import json
import os
from datetime import datetime

from app_logger import get_logger

log = get_logger(__name__)

DEFAULT_STATE_PATH = "engine_state.json"


class StateStore:
    """
    Guarda/carga el estado del motor en un archivo JSON. Cada escritura
    sobreescribe primero un archivo temporal y después lo renombra (patrón
    "write-then-rename") para que un corte de luz a mitad de la escritura
    no deje el archivo de estado corrupto a medias.
    """

    def __init__(self, path: str = DEFAULT_STATE_PATH):
        self.path = path

    def save(self, positions: dict, capital: float, extra: dict = None) -> None:
        state = {
            "positions": positions,
            "capital": capital,
            "extra": extra or {},
            "saved_at": datetime.utcnow().isoformat(),
        }
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(state, f, indent=2, default=str)
        os.replace(tmp_path, self.path)  # atómico en la mayoría de los sistemas de archivos
        log.debug("Estado guardado: %d posiciones, capital=%.2f", len(positions), capital)

    def load(self) -> dict:
        if not os.path.exists(self.path):
            log.info("No hay estado previo guardado en %s -- arrancando desde cero", self.path)
            return {"positions": {}, "capital": None, "extra": {}, "saved_at": None}

        try:
            with open(self.path) as f:
                state = json.load(f)
            log.info("Estado restaurado desde %s (guardado el %s): %d posiciones abiertas",
                      self.path, state.get("saved_at"), len(state.get("positions", {})))
            return state
        except (json.JSONDecodeError, OSError) as e:
            log.error("El archivo de estado %s está corrupto o ilegible (%s) -- "
                       "arrancando desde cero en vez de operar con datos dudosos.", self.path, e)
            return {"positions": {}, "capital": None, "extra": {}, "saved_at": None}

    def clear(self) -> None:
        if os.path.exists(self.path):
            os.remove(self.path)
            log.info("Estado eliminado (%s)", self.path)
