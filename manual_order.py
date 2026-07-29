"""
Control manual del modo asistido (ver manual_trading.py). Uso:

    python3 manual_order.py comprar BTC_USDC --file .MANUAL_ORDERS
    python3 manual_order.py vender BTC_USDC --file .MANUAL_ORDERS
    python3 manual_order.py estado --file .MANUAL_ORDERS

`--file` debe ser el mismo que la sesión arrancó con
`--manual-orders-file` -- si no coincide, este comando y el motor están
mirando archivos distintos y no se van a "ver" entre sí.

Dejar pedida una orden NO garantiza que se ejecute: una compra manual
respeta los mismos frenos que una automática (circuit breaker, pausa por
noticias, cupo compartido de posiciones) -- si algo la bloquea, el motor
la descarta y explica el motivo en su log, no queda reintentando sola. Una
venta manual (salir de una posición) siempre se deja pasar.
"""

import argparse
from manual_trading import ManualOrderQueue


def main():
    parser = argparse.ArgumentParser(description="Control manual del modo asistido")
    parser.add_argument("action", choices=["comprar", "vender", "estado"])
    parser.add_argument("symbol", nargs="?", help="Símbolo a operar (ej. BTC_USDC) -- requerido salvo en 'estado'")
    parser.add_argument("--file", default=".MANUAL_ORDERS",
                         help="Archivo de control -- debe coincidir con el --manual-orders-file de la sesión "
                              "que querés operar (por defecto: .MANUAL_ORDERS)")
    args = parser.parse_args()

    queue = ManualOrderQueue(control_file=args.file)

    if args.action in ("comprar", "vender"):
        if not args.symbol:
            parser.error(f"'{args.action}' necesita un símbolo (ej. BTC_USDC)")
        side = "buy" if args.action == "comprar" else "sell"
        queue.queue_order(args.symbol, side)
        print(f"Orden manual de {args.action} en {args.symbol} dejada pedida ({args.file}).")
        print("Se procesa en el próximo tick de esa sesión -- si algo la bloquea (breaker, pausa por "
              "noticias, cupo de posiciones), se descarta y queda explicado en el log de esa sesión.")
    elif args.action == "estado":
        pending = queue.pending()
        if not pending:
            print(f"No hay órdenes manuales pendientes en {args.file}.")
        else:
            print(f"Órdenes manuales pendientes en {args.file}:")
            for symbol, side in pending.items():
                print(f"  {symbol}: {'comprar' if side == 'buy' else 'vender'}")


if __name__ == "__main__":
    main()
