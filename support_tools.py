"""
Herramienta de soporte (Fase 4): dado un usuario, arma un reporte legible
con todo lo que un agente de soporte necesitaría para responder un
reclamo -- sin tener que ir a buscar cada dato por separado en cinco
lugares distintos. Es el tipo de herramienta que evita que "¿por qué mi
cuenta hizo X?" tarde una hora en responderse.

`generate_user_support_snapshot` no inventa ningún dato: lee directamente
del bróker, la billetera y el estado persistido de la sesión del usuario
-- es una vista consolidada, no una fuente de verdad nueva.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from reconciliation import reconcile


@dataclass
class SupportSnapshot:
    user_id: str
    currency: str
    generado_en: str
    saldo_disponible_billetera: float
    asignado_a_trading: float
    balance_broker: float
    posiciones_abiertas: dict
    circuit_breaker_activo: bool
    circuit_breaker_motivo: str
    kill_switch_activo: bool
    kill_switch_motivo: str
    reconciliacion: dict
    ultimo_estado_guardado: str
    pending_order: dict
    ordenes_recientes: list = field(default_factory=list)
    advertencias: list = field(default_factory=list)

    def to_text(self) -> str:
        """Reporte en texto plano, pensado para pegar directo en un ticket de soporte."""
        lines = []
        lines.append(f"=== Snapshot de soporte -- usuario {self.user_id} ===")
        lines.append(f"Generado: {self.generado_en}")
        lines.append("")
        lines.append("-- Billetera --")
        lines.append(f"Saldo disponible ({self.currency}): {self.saldo_disponible_billetera:.2f}")
        lines.append(f"Asignado a trading ({self.currency}): {self.asignado_a_trading:.2f}")
        lines.append("")
        lines.append("-- Bróker --")
        lines.append(f"Balance en el bróker: {self.balance_broker:.2f}")
        if self.posiciones_abiertas:
            lines.append("Posiciones abiertas:")
            for symbol, pos in self.posiciones_abiertas.items():
                lines.append(f"  - {symbol}: {pos.get('unidades')} unidades @ {pos.get('precio_entrada')}")
        else:
            lines.append("Posiciones abiertas: ninguna")
        lines.append("")
        lines.append("-- Frenos de seguridad --")
        lines.append(f"Circuit breaker: {'ACTIVO -- ' + self.circuit_breaker_motivo if self.circuit_breaker_activo else 'inactivo'}")
        lines.append(f"Kill-switch: {'ACTIVO -- ' + self.kill_switch_motivo if self.kill_switch_activo else 'inactivo'}")
        lines.append("")
        lines.append("-- Reconciliación (estado interno vs. bróker) --")
        if self.reconciliacion["coincide"]:
            lines.append("OK -- coincide")
        else:
            lines.append("DESFASAJE DETECTADO:")
            if self.reconciliacion["solo_en_interno"]:
                lines.append(f"  Solo en el estado interno (el bróker no los reporta): {self.reconciliacion['solo_en_interno']}")
            if self.reconciliacion["solo_en_broker"]:
                lines.append(f"  Solo en el bróker (no registrados internamente): {self.reconciliacion['solo_en_broker']}")
            if self.reconciliacion["diferencias_de_cantidad"]:
                lines.append(f"  Diferencias de cantidad: {self.reconciliacion['diferencias_de_cantidad']}")
        lines.append("")
        lines.append(f"-- Último estado guardado: {self.ultimo_estado_guardado or 'nunca'} --")
        if self.pending_order:
            lines.append(f"Orden pendiente sin resolver: {self.pending_order}")
        if self.ordenes_recientes:
            lines.append("")
            lines.append("-- Últimas operaciones --")
            for order in self.ordenes_recientes:
                lines.append(f"  {order.get('timestamp')}: {order.get('side')} {order.get('units')} "
                             f"{order.get('symbol')} @ {order.get('price')} -- {order.get('status')}")
        if self.advertencias:
            lines.append("")
            lines.append("-- ADVERTENCIAS PARA EL AGENTE --")
            for w in self.advertencias:
                lines.append(f"  ⚠ {w}")
        return "\n".join(lines)


def generate_user_support_snapshot(user_id: str, session, wallet=None, currency: str = "USDC",
                                    max_recent_orders: int = 10) -> SupportSnapshot:
    """
    `session`: una UserTradingSession (o cualquier objeto con .broker,
    .circuit_breaker, .kill_switch, .state_store -- ver multi_user.py).
    `wallet` es opcional: si se pasa, se usa para mostrar el saldo
    disponible general del usuario además de lo asignado a trading.
    """
    saved_state = session.state_store.load()
    broker_positions = session.broker.get_open_positions()
    internal_positions = saved_state.get("positions", {})

    reconciliation_report = reconcile(internal_positions, broker_positions)

    advertencias = []
    if not reconciliation_report["coincide"]:
        advertencias.append(
            "Hay un desfasaje entre lo que el sistema cree tener abierto y lo que el bróker reporta -- "
            "no confirmar nada al usuario sobre sus posiciones sin escalar esto primero."
        )
    if session.circuit_breaker.tripped:
        advertencias.append(
            "El circuit breaker de este usuario está activo -- es esperable que no se hayan abierto "
            "posiciones nuevas recientemente, no es un error del sistema."
        )
    if session.kill_switch.is_active():
        advertencias.append(
            "El kill-switch de este usuario está activo -- probablemente lo pausó él mismo o un agente "
            "de soporte anterior. Confirmar con el usuario antes de reactivar nada."
        )

    saldo_disponible = wallet.get_available_balance(user_id, currency) if wallet is not None else None
    asignado = wallet.get_trading_allocation(user_id, currency) if wallet is not None else session.broker.get_balance()

    order_history = getattr(session.broker, "order_history", [])
    ordenes_recientes = list(order_history[-max_recent_orders:])

    return SupportSnapshot(
        user_id=user_id,
        currency=currency,
        generado_en=datetime.now(timezone.utc).isoformat(),
        saldo_disponible_billetera=saldo_disponible if saldo_disponible is not None else 0.0,
        asignado_a_trading=asignado,
        balance_broker=session.broker.get_balance(),
        posiciones_abiertas=broker_positions,
        circuit_breaker_activo=session.circuit_breaker.tripped,
        circuit_breaker_motivo=session.circuit_breaker.trip_reason or "",
        kill_switch_activo=session.kill_switch.is_active(),
        kill_switch_motivo=session.kill_switch.reason() if session.kill_switch.is_active() else "",
        reconciliacion=reconciliation_report,
        ultimo_estado_guardado=saved_state.get("saved_at"),
        pending_order=saved_state.get("extra", {}).get("pending_order"),
        ordenes_recientes=ordenes_recientes,
        advertencias=advertencias,
    )
