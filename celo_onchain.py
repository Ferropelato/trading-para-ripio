"""
Monitoreo on-chain, real, de la infraestructura de Ripio en Celo -- lee
directo de la blockchain pública (forno.celo.org, gratis, sin API key),
en vez de confiar únicamente en anuncios/blog posts.

Contexto: Ripio lanzó wFIAT (sus 6 stablecoins locales) sobre Celo el
2026-07-31, y al día siguiente Textile FX abrió pools de liquidez
wARS<>USDT y wBRL<>USDT -- direcciones reales, tomadas del propio post
de @ripiohq en X (ver README). Este módulo verifica esas direcciones
contra la cadena real: que el contrato siga desplegado, y cuánta
actividad (eventos on-chain) tuvo en una ventana reciente.

Investigación previa (ver README) descartó tratar de inferir un precio
implícito de estas pools: el contrato NO tiene la interfaz de un par
Uniswap V2 (sin getReserves/token0/token1), sino selectores de un vault
tipo ERC-4626 (asset(), totalAssets(), deposit(uint256,address)) -- un
mecanismo de pricing propio y no documentado públicamente. Inventar un
precio a partir de eso sería adivinar, no medir -- y presentarle un
número inventado al propio equipo que construyó el contrato sería peor
que no tener el dato. Por eso el alcance de este módulo es más angosto
pero 100% verificable: existencia del contrato + actividad reciente.
"""

import requests

from app_logger import get_logger
from resilience import TransientBrokerError, retry_with_backoff

log = get_logger(__name__)

CELO_RPC_URL = "https://forno.celo.org"
CELO_CHAIN_ID = 42220

# Límite duro del RPC público de Celo: cada eth_getLogs acepta como máximo
# 5000 bloques de rango (confirmado contra el propio RPC, no documentado
# de antemano) -- por eso count_recent_events pagina en ventanas de este tamaño.
MAX_LOG_BLOCK_RANGE = 5000

# Pools reales de Textile FX en Celo para los corredores de Ripio,
# confirmadas contra el post real de @ripiohq en X del 2026-08-01
# (ver README) -- no inventadas ni de documentación de terceros.
KNOWN_POOLS = {
    "wARS_USDT": "0x14a9aec2bbdb21f86612b2a97a74d380b10d6fa4",
    "wBRL_USDT": "0x0ea5b44cad7624cd8f5ffdc184022f630a05efac",
}


@retry_with_backoff(max_attempts=3, base_delay_seconds=1.0)
def _rpc_call(method: str, params: list, timeout: float = 15.0):
    try:
        response = requests.post(
            CELO_RPC_URL, timeout=timeout,
            json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
        )
    except requests.Timeout as e:
        raise TransientBrokerError(f"Timeout hablando con el RPC de Celo: {e}") from e
    except requests.ConnectionError as e:
        raise TransientBrokerError(f"Error de conexión con el RPC de Celo: {e}") from e

    payload = response.json()
    if "error" in payload:
        # Un error JSON-RPC (ej. rango de bloques inválido, método no
        # soportado) no es un problema de red -- no tiene sentido
        # reintentar un pedido mal formado.
        raise ValueError(f"RPC de Celo devolvió un error: {payload['error']}")
    return payload["result"]


def get_latest_block() -> int:
    return int(_rpc_call("eth_blockNumber", []), 16)


def contract_is_deployed(address: str) -> bool:
    """True si la dirección tiene bytecode desplegado ahora mismo --
    confirma que el contrato existe de verdad en la red, no solo en un
    anuncio o una captura de pantalla."""
    code = _rpc_call("eth_getCode", [address, "latest"])
    return code not in ("0x", "0x0", None, "")


def count_recent_events(address: str, lookback_blocks: int = 20000,
                         latest_block: int | None = None) -> dict:
    """
    Cuenta eventos emitidos por `address` en los últimos `lookback_blocks`
    bloques, paginando en ventanas de MAX_LOG_BLOCK_RANGE.

    Nota honesta (ver docstring del módulo): un resultado de 0 puede
    significar "sin actividad todavía" O que este contrato es un
    intermediario que no emite logs bajo su propia dirección -- no se
    puede distinguir entre ambos casos solo con esto, y el nombre del
    campo ("events_seen", no "actividad_total") es deliberado para no
    sobre-afirmar.
    """
    to_block = latest_block if latest_block is not None else get_latest_block()
    from_block = max(0, to_block - lookback_blocks + 1)

    total_events = 0
    window_end = to_block
    while window_end >= from_block:
        window_start = max(from_block, window_end - MAX_LOG_BLOCK_RANGE + 1)
        logs = _rpc_call("eth_getLogs", [{
            "address": address,
            "fromBlock": hex(window_start),
            "toBlock": hex(window_end),
        }])
        total_events += len(logs)
        window_end = window_start - 1

    return {
        "address": address,
        "events_seen": total_events,
        "from_block": from_block,
        "to_block": to_block,
        "lookback_blocks": to_block - from_block + 1,
    }


def check_known_pools(lookback_blocks: int = 20000) -> dict:
    """
    Chequeo de salud de las pools conocidas de Textile FX en Celo: para
    cada una, confirma que sigue desplegada y cuenta eventos recientes.
    Pensado para correr como reporte puntual (ver onchain_activity_report.py),
    NO desde el loop crítico de las sesiones en vivo -- es deliberadamente
    una pieza aislada, sin ningún cambio en live_runner.py ni en broker.py.
    """
    latest = get_latest_block()
    pools = {}
    for name, address in KNOWN_POOLS.items():
        deployed = contract_is_deployed(address)
        activity = (
            count_recent_events(address, lookback_blocks=lookback_blocks, latest_block=latest)
            if deployed else None
        )
        pools[name] = {"address": address, "deployed": deployed, "activity": activity}
    return {"latest_block": latest, "pools": pools}
