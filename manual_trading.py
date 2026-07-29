"""
Modo manual/asistido: para el usuario que ya sabe operar y prefiere
elegir él mismo cuándo entrar y salir de una posición, en vez de dejarlo
en manos de la estrategia automática. Usa la MISMA infraestructura de
límites que el modo automático (saldo asignado, circuit breaker,
kill-switch, cupo compartido de posiciones, pausa por noticias) -- la
única diferencia es quién decide el momento de entrar: acá la persona,
no la estrategia. Una compra manual respeta todos esos frenos igual que
una automática; una venta manual (salir, reducir riesgo) siempre se deja
pasar, igual que ya pasa con el stop loss/take profit -- salir nunca
está bloqueado.

Funciona igual que kill_switch.py: un archivo de control simple que
`_LiveEngine` consulta en cada tick, para no necesitar ningún servicio
corriendo aparte. Cada símbolo puede tener a lo sumo una orden manual en
cola por vez; se consume (se borra) apenas el motor la procesa, sea que
se haya ejecutado o rechazado.
"""

import json
import os


class ManualOrderQueue:
    def __init__(self, control_file: str = ".MANUAL_ORDERS"):
        self.control_file = control_file

    def queue_order(self, symbol: str, side: str) -> None:
        """Deja pedida una compra o venta manual para `symbol` -- la
        próxima vez que el motor procese un tick de ese símbolo, la va a
        ver y va a intentar ejecutarla (o rechazarla con un motivo, si
        corresponde)."""
        if side not in ("buy", "sell"):
            raise ValueError("side debe ser 'buy' o 'sell'")
        orders = self._load()
        orders[symbol] = side
        self._save(orders)

    def pending(self) -> dict:
        """Todas las órdenes manuales en cola ahora mismo (symbol -> 'buy'/'sell'),
        sin consumirlas -- para inspeccionar el estado sin efectos secundarios."""
        return self._load()

    def pop_order(self, symbol: str):
        """Devuelve 'buy'/'sell' si hay una orden manual pendiente para
        este símbolo, y la saca de la cola -- se consume una sola vez,
        no vuelve a aplicarse sola en el próximo tick."""
        orders = self._load()
        side = orders.pop(symbol, None)
        if side is not None:
            self._save(orders)
        return side

    def _load(self) -> dict:
        if not os.path.exists(self.control_file):
            return {}
        try:
            with open(self.control_file, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self, orders: dict) -> None:
        if orders:
            with open(self.control_file, "w", encoding="utf-8") as f:
                json.dump(orders, f)
        elif os.path.exists(self.control_file):
            os.remove(self.control_file)
