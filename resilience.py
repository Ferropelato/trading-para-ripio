"""
Resiliencia ante fallas de conexión: reintentos con backoff exponencial
para llamadas al bróker que pueden fallar por problemas de red transitorios
(timeout, conexión rechazada, 5xx del servidor). Sin esto, un corte de
internet de 2 segundos puede hacer que el motor se caiga entero, en vez de
simplemente reintentar y seguir.

Deliberadamente NO reintenta errores que no son transitorios (credenciales
inválidas, fondos insuficientes, símbolo inexistente) -- reintentar esos
no arregla nada y solo demora darse cuenta del problema real.

Dos refinamientos surgidos de correr 7 sesiones en vivo en paralelo contra
la misma API pública de Ripio (cientos de 429 por día en los logs):

1. **Jitter**: el backoff era determinístico (1s, 2s exactos), así que
   todos los procesos que chocaban con el rate limit al mismo tiempo
   reintentaban también al mismo tiempo -- y volvían a chocar. Ahora cada
   espera se multiplica por un factor aleatorio, lo que descorrelaciona
   los procesos entre sí.

2. **RateLimitBrokerError**: un 429 es transitorio (conviene reintentar),
   pero no es lo mismo que un timeout -- el servidor está pidiendo
   explícitamente bajar el ritmo. Reintentar en 1s suele volver a chocar
   con la misma ventana de rate limit. Para 429 se usa una espera base
   más larga, y si el servidor manda el header Retry-After se respeta
   como mínimo.
"""

import random
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


class RateLimitBrokerError(TransientBrokerError):
    """
    429: el servidor pide explícitamente bajar el ritmo. Es transitorio
    (subclase de TransientBrokerError, así que todo lo que ya trataba a los
    transitorios sigue funcionando igual), pero el retry usa una espera más
    larga que para un error de red común. Si el servidor mandó Retry-After,
    viene en retry_after_seconds y se respeta como espera mínima.
    """

    def __init__(self, message: str, retry_after_seconds: float | None = None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


def retry_with_backoff(max_attempts: int = 3, base_delay_seconds: float = 1.0,
                        max_delay_seconds: float = 10.0,
                        rate_limit_base_delay_seconds: float = 5.0,
                        rate_limit_max_delay_seconds: float = 45.0,
                        jitter: bool = True):
    """
    Decorador: reintenta la función si lanza TransientBrokerError, con
    backoff exponencial (1s, 2s, 4s, ...). Si lanza PermanentBrokerError o
    cualquier otra excepción no marcada como transitoria, NO reintenta --
    la deja propagarse de inmediato.

    Para RateLimitBrokerError (429) usa la escala de espera más larga
    (rate_limit_base_delay_seconds), y si la excepción trae
    retry_after_seconds (header Retry-After del servidor) lo respeta como
    mínimo. Todas las esperas llevan un jitter aleatorio de +/-30% para
    que varios procesos que fallaron juntos no reintenten juntos
    (jitter=False solo para tests determinísticos).
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
                    if isinstance(e, RateLimitBrokerError):
                        delay = min(rate_limit_base_delay_seconds * (2 ** (attempt - 1)),
                                    rate_limit_max_delay_seconds)
                        if e.retry_after_seconds is not None and e.retry_after_seconds > 0:
                            delay = max(delay, e.retry_after_seconds)
                    else:
                        delay = min(base_delay_seconds * (2 ** (attempt - 1)), max_delay_seconds)
                    if jitter:
                        delay *= random.uniform(0.7, 1.3)
                    log.warning("Intento %d/%d falló (%s) -- reintentando en %.1fs",
                                attempt, max_attempts, e, delay)
                    time.sleep(delay)
            raise last_exception
        return wrapper
    return decorator
