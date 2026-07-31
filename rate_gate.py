"""
Rate gate compartido ENTRE PROCESOS para la API de Ripio.

El problema que resuelve (medido en producción, ver README rondas 45-46):
7 sesiones en vivo en procesos separados consultan ~17 símbolos contra la
misma API pública. El `SharedPriceFeed` de Fase 2 deduplica dentro de UN
proceso, y el backoff con jitter de la ronda 45 descorrelaciona los
reintentos -- pero nada limitaba el ritmo GLOBAL de requests entre
procesos, así que cuando varias sesiones tickeaban a la vez salía una
ráfaga y Ripio devolvía 429 igual (46-66 por sesión en una hora, algunos
terminando en tick perdido).

Cómo funciona: un archivo SQLite (el mismo para todos los procesos de la
máquina) guarda "cuándo es el próximo turno libre". Antes de cada request,
cada proceso reserva atómicamente su turno (BEGIN IMMEDIATE serializa la
reserva; SQLite ya resuelve el locking cross-proceso), commitea enseguida
y duerme hasta que su turno llegue. Nadie retiene el lock mientras duerme,
así que reservar es barato aunque haya muchos procesos.

Decisiones deliberadas:
- **Sin proceso demonio**: la alternativa (un poller central que sirva
  precios) agrega una pieza que hay que vigilar y un modo de falla nuevo
  (el demonio se cae y TODAS las sesiones quedan ciegas). Acá cada sesión
  sigue siendo autónoma; el gate solo espacia.
- **Fail-open**: si el gate falla por lo que sea (disco lleno, DB
  corrupta, lock imposible), se loguea y se deja pasar la request SIN
  esperar. Preferimos arriesgar un 429 (que ya se maneja con retry) antes
  que bloquear el fetch de precios por un problema del propio gate.
- **Autodefensa contra relojes**: si el turno guardado quedó absurdamente
  en el futuro (salto de reloj, dato corrupto), se resetea a "ahora" en
  vez de dejar a todos los procesos esperando un turno que no llega.
"""

import sqlite3
import time

from app_logger import get_logger

log = get_logger(__name__)

DEFAULT_DB_PATH = ".ripio_rate_gate.sqlite"

# Si el turno guardado está más de esto en el futuro, es un dato corrupto
# (nunca reservamos tan lejos) y se resetea en vez de esperarlo.
_MAX_REASONABLE_FUTURE_SECONDS = 120.0


class SharedRateGate:
    """Espaciador global de requests entre procesos de la misma máquina.

    min_interval_seconds: separación mínima entre dos requests globales.
    Con 0 el gate queda desactivado (acquire vuelve sin esperar).
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH,
                 min_interval_seconds: float = 1.0):
        self.db_path = db_path
        self.min_interval_seconds = min_interval_seconds
        self._warned_failure = False

    def _reserve_slot(self) -> float:
        """Reserva atómicamente el próximo turno y devuelve su timestamp
        (epoch). El lock se retiene solo durante la reserva, no la espera."""
        conn = sqlite3.connect(self.db_path, timeout=15)
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS gate (id INTEGER PRIMARY KEY, next_slot REAL NOT NULL)"
            )
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT next_slot FROM gate WHERE id = 1").fetchone()
            now = time.time()
            stored = row[0] if row else 0.0
            if stored > now + _MAX_REASONABLE_FUTURE_SECONDS:
                log.warning(
                    "Rate gate: turno guardado %.0fs en el futuro (reloj/dato corrupto) -- se resetea",
                    stored - now,
                )
                stored = 0.0
            slot = max(now, stored)
            conn.execute(
                "INSERT INTO gate (id, next_slot) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET next_slot = excluded.next_slot",
                (slot + self.min_interval_seconds,),
            )
            conn.commit()
            return slot
        finally:
            conn.close()

    def acquire(self) -> float:
        """Espera hasta el próximo turno global libre. Devuelve cuántos
        segundos esperó (útil para tests/telemetría). Nunca lanza: si el
        gate falla, se abre (fail-open) y devuelve 0.0."""
        if self.min_interval_seconds <= 0:
            return 0.0
        try:
            slot = self._reserve_slot()
        except Exception as e:
            if not self._warned_failure:
                log.warning("Rate gate compartido falló (%s) -- se sigue SIN espaciado "
                            "global (fail-open, se avisa una sola vez)", e)
                self._warned_failure = True
            return 0.0
        wait = slot - time.time()
        if wait > 0:
            time.sleep(wait)
            return wait
        return 0.0
