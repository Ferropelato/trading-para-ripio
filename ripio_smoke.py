"""
Smoke de solo lectura contra Ripio Trade.

Sin credenciales:
  python ripio_smoke.py                 # solo ticker público

Con credenciales en .env:
  python ripio_smoke.py --balances      # ticker + balances

NUNCA coloca órdenes.
"""

import argparse
import sys

from app_logger import setup_logging, get_logger
from broker import RipioBrokerAdapter


def main():
    parser = argparse.ArgumentParser(description="Smoke de lectura Ripio Trade")
    parser.add_argument("--pair", default="BTC_USDC", help="Par Ripio, ej. BTC_USDC")
    parser.add_argument("--balances", action="store_true",
                        help="También consultar balances (requiere .env)")
    args = parser.parse_args()

    setup_logging(level="INFO")
    log = get_logger("ripio_smoke")
    broker = RipioBrokerAdapter(allow_trading=False)

    price = broker.get_current_price(args.pair)
    log.info("Precio %s = %s", args.pair, price)
    print(f"OK precio {args.pair}: {price}")

    if args.balances:
        balance = broker.get_balance()
        positions = broker.get_open_positions()
        log.info("Balance %s = %s | posiciones=%s", broker.quote_currency, balance, positions)
        print(f"OK balance {broker.quote_currency}: {balance}")
        print(f"OK posiciones: {positions}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
