"""
Reporte puntual de la infraestructura on-chain real de Ripio en Celo --
lee directo de la blockchain pública (forno.celo.org, gratis, sin API
key), en vez de confiar solo en lo que dicen los anuncios.

Correr: python onchain_activity_report.py
"""

from celo_onchain import check_known_pools
from app_logger import setup_logging, get_logger

log = get_logger(__name__)


def main():
    setup_logging(level="INFO")
    report = check_known_pools(lookback_blocks=200000)  # ~55hs a ~1s/bloque en Celo
    log.info("Último bloque de Celo consultado: %d", report["latest_block"])

    for name, info in report["pools"].items():
        if not info["deployed"]:
            log.warning("%s (%s): NO tiene bytecode desplegado ahora mismo -- revisar la dirección",
                        name, info["address"])
            continue
        act = info["activity"]
        log.info(
            "%s (%s): contrato desplegado OK -- %d eventos vistos en los últimos %d bloques (bloques %d-%d)",
            name, info["address"], act["events_seen"], act["lookback_blocks"],
            act["from_block"], act["to_block"],
        )


if __name__ == "__main__":
    main()
