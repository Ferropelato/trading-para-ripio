"""
Logging centralizado. Reemplaza los `print` sueltos por logs con nivel de
severidad, timestamp y origen (qué módulo lo generó) -- esencial para
cuando el motor corre desatendido (sin alguien mirando la terminal) y
después hay que reconstruir qué pasó y cuándo.

Uso:
    from app_logger import get_logger
    log = get_logger(__name__)
    log.info("Backtest iniciado")
    log.warning("Circuit breaker activado: %s", motivo)
    log.error("Fuente de datos principal falló, usando respaldo")
"""

import logging
import sys

_CONFIGURED = False


def setup_logging(level: str = "INFO", log_file: str = None) -> None:
    """
    Configura el logging raíz una sola vez por proceso. Llamar al principio
    de cualquier script (run_backtest.py, un futuro daemon en vivo, etc).
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    if not _CONFIGURED:
        setup_logging()
    return logging.getLogger(name)
