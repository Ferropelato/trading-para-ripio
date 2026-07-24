"""
Resiliencia ante fallas de conexión: reintentos con backoff exponencial
para llamadas al bróker que pueden fallar por problemas de red transitorios
(timeout, conexión rechazada, 5xx del servidor). Sin esto, un corte de
internet de 2 segundos puede hacer que el motor se caiga entero, en vez de
simplemente reintentar y seguir.

Deliberadamente NO reintenta errores que no son transitorios (credenciales
inválidas, fondos insuficientes, símbolo inexistente) -- reintentar esos
no arregla nada y solo demora darse cuenta del problema real.
"""

import time
from functools import wraps

from app_logger import get_logger

log = get_logger(__name__)


class TransientBrokerError(Exception):
    """Error de red/conexión que tiene sentido reintentar (timeout, 5xx, conexión perdida)."""
    pass


class PermanentBrokerError(Exception):
    """Error que NO tiene sentido reintentar (credenciales inválidas, fondos insuficientes, etc)."""
    pass


def retry_with_backoff(max_attempts: int = 3, base_delay_seconds: float = 1.0,
                        max_delay_seconds: float = 10.0):
    """
    Decorador: reintenta la función si lanza TransientBrokerError, con
    backoff exponencial (1s, 2s, 4s, ...). Si lanza PermanentBrokerError o
    cualquier otra excepción no marcada como transitoria, NO reintenta --
    la deja propagarse de inmediato.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except TransientBrokerError as e:
                    last_exception = e
                    if attempt == max_attempts:
                        log.error("Falló tras %d intentos: %s", max_attempts, e)
                        raise
                    delay = min(base_delay_seconds * (2 ** (attempt - 1)), max_delay_seconds)
                    log.warning("Intento %d/%d falló (%s) -- reintentando en %.1fs",
                                attempt, max_attempts, e, delay)
                    time.sleep(delay)
            raise last_exception
        return wrapper
    return decorator
