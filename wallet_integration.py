"""
Capa de abstracción "saldo de billetera -> asignación a trading". Este es
el punto de integración conceptual con el backend de una billetera como
Ripio: define QUÉ necesitaría exponer para que un usuario asigne parte de
su saldo YA DEPOSITADO a este motor, sin que la plata salga nunca de la
app -- ver README.md, sección "Capa de integración con la billetera".

`WalletBalanceProvider` es el contrato. Una billetera real lo
implementaría contra su propio ledger interno (base de datos
transaccional). `SimulatedWalletBalanceProvider` es una implementación de
referencia en memoria, solo para demos y tests, que modela el mismo
comportamiento (reservar/liberar dentro de UNA sola cuenta) sin tocar
ningún sistema real.

Cómo se conectaría con el resto del motor (conceptual, no implementado
acá porque requiere el ledger real de la billetera):
1. Antes de arrancar una sesión de trading para un usuario, llamar a
   `reserve_for_trading(user_id, currency, monto)` -- si no hay saldo
   disponible suficiente, falla ANTES de arrancar nada.
2. Usar ese monto reservado como `initial_balance` de un `PaperBroker`
   dedicado a ese usuario (ver `multi_user.py` para el aislamiento por
   usuario).
3. Cada vez que el resultado operado cambia el balance del bróker,
   reflejar esa diferencia con `settle_trade_result` -- nunca tocar
   `available` directamente desde el motor de trading.
4. Cuando el usuario pausa/cierra, `release_from_trading` devuelve lo que
   quede al saldo general disponible de la billetera.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


class InsufficientWalletBalanceError(Exception):
    pass


class WalletBalanceProvider(ABC):
    """
    Contrato que el backend de una billetera debería implementar para
    integrar este motor sin mover fondos fuera de la app. Todas las
    operaciones son sobre el MISMO saldo del usuario -- "reservar para
    trading" es una reclasificación contable interna (como una
    sub-cuenta), nunca una transferencia externa ni un movimiento de
    fondos fuera del sistema de la billetera.
    """

    @abstractmethod
    def get_available_balance(self, user_id: str, currency: str) -> float:
        """Saldo disponible del usuario en esa moneda, SIN lo ya reservado para trading."""
        raise NotImplementedError

    @abstractmethod
    def get_trading_allocation(self, user_id: str, currency: str) -> float:
        """Cuánto tiene ese usuario reservado/asignado a este módulo de trading, en esa moneda."""
        raise NotImplementedError

    @abstractmethod
    def reserve_for_trading(self, user_id: str, currency: str, amount: float) -> None:
        """
        Mueve `amount` del saldo disponible a la asignación de trading del
        usuario. Debe fallar (InsufficientWalletBalanceError) si no hay
        saldo disponible suficiente -- nunca crear saldo de la nada.
        """
        raise NotImplementedError

    @abstractmethod
    def release_from_trading(self, user_id: str, currency: str, amount: float) -> None:
        """Devuelve `amount` de la asignación de trading al saldo disponible general del usuario."""
        raise NotImplementedError

    @abstractmethod
    def settle_trade_result(self, user_id: str, currency: str, delta: float) -> None:
        """
        Aplica el resultado (positivo o negativo) de una operación ya
        ejecutada a la asignación de trading del usuario -- nunca al
        saldo disponible general directamente. Un `delta` negativo nunca
        debería dejar la asignación por debajo de cero si el gestor de
        riesgo del motor funciona correctamente (nunca arriesga más de lo
        asignado); igual se protege acá contra ese caso límite.
        """
        raise NotImplementedError


@dataclass
class _UserLedger:
    available: float = 0.0
    allocated_to_trading: float = 0.0


class SimulatedWalletBalanceProvider(WalletBalanceProvider):
    """
    Implementación de referencia en memoria -- para demos y tests. Una
    billetera real reemplazaría esto por su ledger transaccional (base de
    datos), pero el CONTRATO (los métodos de `WalletBalanceProvider`) es
    lo que necesitaría exponer para integrar el motor sin tocar cómo
    maneja el resto de la plata de sus usuarios.
    """

    def __init__(self):
        self._ledgers = {}  # (user_id, currency) -> _UserLedger

    def _ledger(self, user_id: str, currency: str) -> _UserLedger:
        key = (user_id, currency)
        if key not in self._ledgers:
            self._ledgers[key] = _UserLedger()
        return self._ledgers[key]

    def deposit(self, user_id: str, currency: str, amount: float) -> None:
        """Solo para pruebas/demos: simula que el usuario ya tiene ese saldo depositado en la billetera."""
        if amount < 0:
            raise ValueError("amount debe ser >= 0")
        self._ledger(user_id, currency).available += amount

    def get_available_balance(self, user_id: str, currency: str) -> float:
        return self._ledger(user_id, currency).available

    def get_trading_allocation(self, user_id: str, currency: str) -> float:
        return self._ledger(user_id, currency).allocated_to_trading

    def reserve_for_trading(self, user_id: str, currency: str, amount: float) -> None:
        if amount <= 0:
            raise ValueError("amount debe ser > 0")
        ledger = self._ledger(user_id, currency)
        if amount > ledger.available:
            raise InsufficientWalletBalanceError(
                f"Saldo disponible insuficiente para {user_id}/{currency}: "
                f"pidió reservar {amount}, disponible {ledger.available}"
            )
        ledger.available -= amount
        ledger.allocated_to_trading += amount

    def release_from_trading(self, user_id: str, currency: str, amount: float) -> None:
        if amount <= 0:
            raise ValueError("amount debe ser > 0")
        ledger = self._ledger(user_id, currency)
        if amount > ledger.allocated_to_trading + 1e-9:
            raise ValueError(
                f"No se puede liberar más de lo asignado: pidió {amount}, "
                f"asignado {ledger.allocated_to_trading}"
            )
        ledger.allocated_to_trading -= amount
        ledger.available += amount

    def settle_trade_result(self, user_id: str, currency: str, delta: float) -> None:
        ledger = self._ledger(user_id, currency)
        ledger.allocated_to_trading = max(0.0, ledger.allocated_to_trading + delta)
