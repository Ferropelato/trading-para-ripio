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
import sqlite3
import threading
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


class SQLiteStateStore:
    """
    Persistencia de estado en una base SQLite real y compartida, en vez de
    un archivo JSON por usuario. Mismo contrato que StateStore (save/load/
    clear) -- reemplazo directo, sin tocar `_LiveEngine` ni ningún otro
    consumidor.

    Todas las sesiones (de todos los usuarios) comparten el MISMO archivo
    de base de datos (`db_path`); cada una se identifica por su propio
    `key` (ej. user_id). Esto evita terminar con miles de archivos JSON
    sueltos en disco, y permite consultar el estado de todos los usuarios
    desde un solo lugar (`all_keys`, usado por `ops_monitor.py`).
    """

    def __init__(self, db_path: str, key: str):
        self.db_path = db_path
        self.key = key
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS engine_state (
                    key TEXT PRIMARY KEY,
                    positions_json TEXT NOT NULL,
                    capital REAL,
                    extra_json TEXT NOT NULL,
                    saved_at TEXT NOT NULL
                )
            """)
            self._conn.commit()

    def save(self, positions: dict, capital: float, extra: dict = None) -> None:
        saved_at = datetime.utcnow().isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO engine_state (key, positions_json, capital, extra_json, saved_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    positions_json=excluded.positions_json,
                    capital=excluded.capital,
                    extra_json=excluded.extra_json,
                    saved_at=excluded.saved_at
                """,
                (self.key, json.dumps(positions, default=str), capital,
                 json.dumps(extra or {}, default=str), saved_at),
            )
            self._conn.commit()
        log.debug("Estado guardado en SQLite (%s): %d posiciones, capital=%.2f", self.key, len(positions), capital)

    def load(self) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT positions_json, capital, extra_json, saved_at FROM engine_state WHERE key = ?",
                (self.key,),
            ).fetchone()

        if row is None:
            log.info("No hay estado previo guardado en SQLite para '%s' -- arrancando desde cero", self.key)
            return {"positions": {}, "capital": None, "extra": {}, "saved_at": None}

        positions_json, capital, extra_json, saved_at = row
        try:
            positions = json.loads(positions_json)
            extra = json.loads(extra_json)
        except json.JSONDecodeError as e:
            log.error("El estado en SQLite para '%s' está corrupto (%s) -- arrancando desde cero en vez de "
                       "operar con datos dudosos.", self.key, e)
            return {"positions": {}, "capital": None, "extra": {}, "saved_at": None}

        log.info("Estado restaurado desde SQLite para '%s' (guardado el %s): %d posiciones abiertas",
                  self.key, saved_at, len(positions))
        return {"positions": positions, "capital": capital, "extra": extra, "saved_at": saved_at}

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM engine_state WHERE key = ?", (self.key,))
            self._conn.commit()
        log.info("Estado eliminado de SQLite para '%s'", self.key)

    @staticmethod
    def all_keys(db_path: str) -> list:
        """Lista todas las keys (ej. user_id) con estado guardado en esta base --
        usado por el monitoreo centralizado (ver ops_monitor.py)."""
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS engine_state (
                    key TEXT PRIMARY KEY, positions_json TEXT NOT NULL,
                    capital REAL, extra_json TEXT NOT NULL, saved_at TEXT NOT NULL
                )
            """)
            rows = conn.execute("SELECT key FROM engine_state").fetchall()
            return sorted(r[0] for r in rows)
        finally:
            conn.close()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
