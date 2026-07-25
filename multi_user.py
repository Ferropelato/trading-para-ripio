"""
Aislamiento multi-usuario: cada usuario tiene su propio bróker, su propio
circuit breaker y su propio kill-switch -- nunca un solo pool de
decisiones para todos los usuarios (ver README.md, "qué agregaría antes
de operar con capital real" -- este era uno de los puntos pendientes).

`UserTradingSession` junta todo lo que un usuario necesita para operar de
forma completamente aislada del resto: un `PaperBroker` propio (fondeado
desde su asignación reservada en la billetera vía `WalletBalanceProvider`,
ver `wallet_integration.py`), su propio `CircuitBreaker`/`ManualKillSwitch`,
y su propio archivo de estado. `UserSessionManager` mantiene estas
sesiones por `user_id` y garantiza que ningún objeto mutable se comparta
entre dos usuarios.

Simplificación deliberada: `sync_settlement_to_wallet` usa el balance en
efectivo del bróker (`get_balance()`), no el equity marcado a mercado con
posiciones abiertas (eso requeriría el precio actual, como hace
`_mark_to_market` en `live_runner.py`). Para una integración real, se
llamaría a esto solo con la posición cerrada, o se pasaría el precio
actual -- acá el foco es demostrar el AISLAMIENTO entre usuarios, no
duplicar esa lógica de valuación.
"""

from broker import PaperBroker
from safety import CircuitBreaker, ManualKillSwitch
from state_store import StateStore
from wallet_integration import WalletBalanceProvider


class UserTradingSession:
    """Todo lo que un usuario necesita para operar de forma aislada. Ningún
    objeto de acá se comparte con otro usuario."""

    def __init__(self, user_id: str, currency: str, wallet: WalletBalanceProvider,
                 state_path: str, max_drawdown_pct: float = 15.0,
                 max_daily_loss_pct: float = 5.0, commission_pct: float = 0.001,
                 slippage_pct: float = 0.0005):
        self.user_id = user_id
        self.currency = currency
        self.wallet = wallet

        allocated = wallet.get_trading_allocation(user_id, currency)
        self.broker = PaperBroker(initial_balance=allocated, commission_pct=commission_pct,
                                   slippage_pct=slippage_pct)
        self.circuit_breaker = CircuitBreaker(max_drawdown_pct, max_daily_loss_pct)
        # OJO: ManualKillSwitch usa por defecto un archivo de control
        # COMPARTIDO (".KILL_SWITCH"). Sin pasar un control_file propio por
        # usuario, activar el kill-switch de un usuario activaria el de
        # todos -- exactamente el bug de aislamiento que este modulo existe
        # para evitar. Cada usuario necesita su propio archivo.
        self.kill_switch = ManualKillSwitch(control_file=f".KILL_SWITCH_{user_id}")
        self.state_store = StateStore(path=state_path)

    def sync_settlement_to_wallet(self) -> None:
        """Refleja en la billetera la ganancia/pérdida acumulada desde la
        última sincronización -- nunca toca el saldo disponible general del
        usuario directamente, solo su asignación de trading."""
        current_allocation = self.wallet.get_trading_allocation(self.user_id, self.currency)
        delta = self.broker.get_balance() - current_allocation
        if delta != 0:
            self.wallet.settle_trade_result(self.user_id, self.currency, delta)


class UserSessionManager:
    """
    Mantiene una `UserTradingSession` completamente aislada por usuario.
    Activar el kill-switch o disparar el circuit breaker de un usuario
    NUNCA afecta a otro -- cada uno tiene sus propias instancias.
    """

    def __init__(self, wallet: WalletBalanceProvider, state_dir: str = "."):
        self.wallet = wallet
        self.state_dir = state_dir
        self._sessions = {}  # user_id -> UserTradingSession

    def start_session(self, user_id: str, currency: str, amount_to_reserve: float, **kwargs) -> UserTradingSession:
        if user_id in self._sessions:
            raise ValueError(f"Ya existe una sesión activa para {user_id} -- cerrala antes de abrir otra")

        # Si no hay saldo suficiente, reserve_for_trading levanta
        # InsufficientWalletBalanceError ANTES de crear nada -- no queda
        # una sesión a medio abrir.
        self.wallet.reserve_for_trading(user_id, currency, amount_to_reserve)

        state_path = f"{self.state_dir}/engine_state_{user_id}.json"
        session = UserTradingSession(user_id, currency, self.wallet, state_path, **kwargs)
        self._sessions[user_id] = session
        return session

    def get_session(self, user_id: str) -> UserTradingSession:
        if user_id not in self._sessions:
            raise KeyError(f"No hay sesión activa para {user_id}")
        return self._sessions[user_id]

    def stop_session(self, user_id: str) -> None:
        """Sincroniza el resultado final con la billetera y devuelve todo
        lo que quede asignado al saldo disponible general del usuario."""
        session = self.get_session(user_id)
        session.sync_settlement_to_wallet()
        remaining = self.wallet.get_trading_allocation(user_id, session.currency)
        if remaining > 0:
            self.wallet.release_from_trading(user_id, session.currency, remaining)
        del self._sessions[user_id]
