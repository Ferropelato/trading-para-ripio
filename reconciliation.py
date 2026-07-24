"""
Reconciliación: compara lo que el motor CREE que tiene abierto contra lo
que el bróker REALMENTE reporta. Sin esto, un bug, una orden que falló a
medias, o una operación manual hecha por fuera del bot pueden generar un
desfasaje silencioso que se acumula durante semanas sin que nadie se dé
cuenta -- hasta que aparece como una sorpresa cara.

En un sistema real, esto debería correr periódicamente (ej. cada N
minutos) contra el bróker real, no solo al arrancar.
"""

from app_logger import get_logger

log = get_logger(__name__)

TOLERANCE_UNITS = 1e-6  # diferencias menores a esto se consideran redondeo, no desfasaje real


def reconcile(internal_positions: dict, broker_positions: dict) -> dict:
    """
    Compara dos diccionarios de posiciones: {symbol: {"unidades": float, ...}}

    Devuelve un reporte con:
      - coincide: True si todo está en orden
      - solo_en_interno: símbolos que el motor cree tener pero el bróker no reporta
      - solo_en_broker: símbolos que el bróker reporta pero el motor no sabía que tenía
        (ej. alguien operó manualmente por fuera del bot)
      - diferencias_de_cantidad: símbolos presentes en ambos lados pero con
        unidades distintas más allá de la tolerancia de redondeo
    """
    internal_symbols = set(internal_positions.keys())
    broker_symbols = set(broker_positions.keys())

    solo_en_interno = internal_symbols - broker_symbols
    solo_en_broker = broker_symbols - internal_symbols

    diferencias_de_cantidad = {}
    for symbol in internal_symbols & broker_symbols:
        internal_units = internal_positions[symbol].get("unidades", 0)
        broker_units = broker_positions[symbol].get("unidades", 0)
        if abs(internal_units - broker_units) > TOLERANCE_UNITS:
            diferencias_de_cantidad[symbol] = {
                "interno": internal_units, "broker": broker_units,
                "diferencia": round(internal_units - broker_units, 8),
            }

    coincide = not solo_en_interno and not solo_en_broker and not diferencias_de_cantidad

    report = {
        "coincide": coincide,
        "solo_en_interno": sorted(solo_en_interno),
        "solo_en_broker": sorted(solo_en_broker),
        "diferencias_de_cantidad": diferencias_de_cantidad,
    }

    if not coincide:
        log.warning("RECONCILIACIÓN FALLÓ: %s", report)
    else:
        log.info("Reconciliación OK: el estado interno coincide con el del bróker")

    return report
