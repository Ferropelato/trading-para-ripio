"""
Feed de precios compartido: sin esto, cada sesión de usuario le pide el
precio a Ripio (o al bróker que sea) por su cuenta -- lo que dispara
rate-limits reales con apenas un puñado de sesiones concurrentes (ver
README.md: se observó un 429 real con solo dos sesiones de paper trading
corriendo en simultáneo). Con miles de usuarios sobre los mismos pares
(BTC_USDC, ETH_USDC), eso escala mal: cada usuario NO necesita su propio
llamado a la red, todos los que miran el mismo símbolo pueden compartir
una sola fuente.

`SharedPriceFeed` mantiene UN solo poller de fondo por símbolo (no por
usuario): el primer `get_current_price(symbol)` que se pide para un
símbolo dispara un único fetch y arranca su poller; cualquier otra sesión
que pida ese mismo símbolo lee del mismo caché en memoria, sin generar
una llamada de red adicional. Símbolos distintos tienen pollers
independientes (cada uno necesita su propio precio fresco).

Expone la misma forma que `BrokerBase.get_current_price(symbol)`, así que
es un reemplazo directo de `price_source` en `run_live_polling` sin
ningún cambio en `live_runner.py` -- mismo principio que ya se usó para
que `RipioBrokerAdapter` y `AlpacaBrokerAdapter` fueran intercambiables.
"""

import threading
import time
from dataclasses import dataclass

from app_logger import get_logger

log = get_logger(__name__)


@dataclass
class _CachedPrice:
    price: float
    fetched_at: float  # time.time()


class SharedPriceFeed:
    """
    Un poller de fondo por símbolo, compartido entre todos los
    consumidores que pidan ese símbolo. Thread-safe.

    - `poll_interval_seconds`: cada cuánto se refresca CADA símbolo contra
      el bróker real en segundo plano, sin importar cuántos consumidores
      lo usen.
    - `stale_after_seconds`: si el precio cacheado es más viejo que esto
      cuando alguien lo pide, se refresca de forma sincrónica en vez de
      devolver un dato demasiado viejo.

    Diseño anti-doble-fetch en el arranque de un símbolo nuevo: el poller
    de fondo espera `poll_interval_seconds` ANTES de su primer fetch (no
    al arrancar), porque el primer `get_current_price(symbol)` ya hizo un
    fetch sincrónico bajo el lock del símbolo. Sin este orden, el poller
    recién iniciado y el llamador original competían por el mismo primer
    fetch -- se detectó y corrigió durante el desarrollo antes de escribir
    los tests.
    """

    def __init__(self, price_source, poll_interval_seconds: float = 5.0,
                 stale_after_seconds: float = None):
        self.price_source = price_source
        self.poll_interval_seconds = poll_interval_seconds
        self.stale_after_seconds = (
            stale_after_seconds if stale_after_seconds is not None else poll_interval_seconds * 3
        )
        self._cache = {}  # symbol -> _CachedPrice
        self._cache_lock = threading.Lock()  # protege _cache, _threads, _symbol_locks, _fetch_count
        self._symbol_locks = {}  # symbol -> Lock (serializa fetches concurrentes del MISMO simbolo)
        self._threads = {}  # symbol -> Thread
        self._stop_event = threading.Event()
        self._fetch_count = 0  # solo para tests/observabilidad: cuantas veces se golpeo la red de verdad

    def _get_symbol_lock(self, symbol: str) -> threading.Lock:
        with self._cache_lock:
            if symbol not in self._symbol_locks:
                self._symbol_locks[symbol] = threading.Lock()
            return self._symbol_locks[symbol]

    def _fetch_and_cache(self, symbol: str) -> float:
        price = self.price_source.get_current_price(symbol)
        with self._cache_lock:
            self._cache[symbol] = _CachedPrice(price=price, fetched_at=time.time())
            self._fetch_count += 1
        return price

    def _cached_if_fresh(self, symbol: str):
        with self._cache_lock:
            cached = self._cache.get(symbol)
        if cached is not None and (time.time() - cached.fetched_at) <= self.stale_after_seconds:
            return cached.price
        return None

    def _poll_loop(self, symbol: str):
        symbol_lock = self._get_symbol_lock(symbol)
        while not self._stop_event.is_set():
            # Espera PRIMERO: el primer valor de este simbolo ya lo puso el
            # llamador original de get_current_price (fetch sincronico bajo
            # symbol_lock), asi que el poller de fondo solo se encarga de
            # los refrescos siguientes.
            if self._stop_event.wait(self.poll_interval_seconds):
                break
            with symbol_lock:
                try:
                    self._fetch_and_cache(symbol)
                except Exception as e:
                    log.warning("SharedPriceFeed: no se pudo refrescar %s (%s)", symbol, e)

    def _ensure_poller(self, symbol: str) -> None:
        with self._cache_lock:
            if symbol in self._threads:
                return
            thread = threading.Thread(target=self._poll_loop, args=(symbol,), daemon=True)
            self._threads[symbol] = thread
        thread.start()
        log.info("SharedPriceFeed: poller compartido iniciado para %s (intervalo=%ss)",
                  symbol, self.poll_interval_seconds)

    def get_current_price(self, symbol: str) -> float:
        """
        Interfaz compatible con BrokerBase.get_current_price -- drop-in
        para cualquier lugar que hoy reciba un RipioBrokerAdapter/
        AlpacaBrokerAdapter como price_source.
        """
        self._ensure_poller(symbol)

        price = self._cached_if_fresh(symbol)
        if price is not None:
            return price

        symbol_lock = self._get_symbol_lock(symbol)
        with symbol_lock:
            # Doble chequeo: mientras se esperaba el lock, otro hilo puede
            # haber refrescado ya este mismo simbolo.
            price = self._cached_if_fresh(symbol)
            if price is not None:
                return price
            return self._fetch_and_cache(symbol)

    def active_symbols(self) -> list:
        with self._cache_lock:
            return sorted(self._threads.keys())

    def fetch_count(self) -> int:
        """Cuántas veces se llamó de verdad a price_source (no al caché) -- para tests/métricas."""
        with self._cache_lock:
            return self._fetch_count

    def stop_all(self, timeout: float = None) -> None:
        """Detiene todos los pollers de fondo -- llamar al apagar el proceso."""
        self._stop_event.set()
        with self._cache_lock:
            threads = list(self._threads.values())
        wait_timeout = timeout if timeout is not None else self.poll_interval_seconds + 1
        for t in threads:
            t.join(timeout=wait_timeout)
