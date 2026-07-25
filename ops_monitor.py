"""
Monitoreo centralizado con alertas agregadas (Fase 3b). Sin esto, cada
sesión de usuario solo emite logs locales sueltos -- con miles de
usuarios, nadie en el equipo de operaciones se entera de un patrón
sistémico (ej. "el 40% de los usuarios activó su circuit breaker en la
última hora", señal de un movimiento de mercado real o de un bug, no de
la mala suerte de una persona) hasta que ya es tarde.

`OperationsMonitor` registra sesiones de usuario (o cualquier objeto con
`.circuit_breaker` / `.kill_switch` / opcionalmente `.heartbeat` /
`.broker` + `.state_store`) y en cada `check_all()` arma un reporte
consolidado, alertando vía el mismo `AlertChannel` que ya usa el resto
del sistema. Si el % de usuarios afectados por el MISMO tipo de problema
cruza un umbral configurable, emite además una alerta "sistémica"
distinta de las individuales.
"""

from dataclasses import dataclass

from app_logger import get_logger
from reconciliation import reconcile

log = get_logger(__name__)


@dataclass
class _Issue:
    user_id: str
    tipo: str  # "circuit_breaker", "kill_switch", "heartbeat_stale", "reconciliacion"
    detalle: str


class OperationsMonitor:
    def __init__(self, alert_channel, systemic_threshold_pct: float = 20.0, min_users_for_systemic: int = 5):
        """
        `systemic_threshold_pct`: si el % de usuarios registrados con el
        MISMO tipo de problema supera esto, se emite además una alerta
        "sistémica" (distinta de las alertas individuales por usuario).

        `min_users_for_systemic`: no tiene sentido hablar de "% de
        usuarios afectados" con muy pocos usuarios registrados (2 de 3 ya
        es 66%) -- por debajo de este mínimo, nunca se escala a sistémica,
        solo se reportan los problemas individuales.
        """
        self.alert_channel = alert_channel
        self.systemic_threshold_pct = systemic_threshold_pct
        self.min_users_for_systemic = min_users_for_systemic
        self._sessions = {}  # user_id -> session

    def register(self, user_id: str, session) -> None:
        self._sessions[user_id] = session

    def unregister(self, user_id: str) -> None:
        self._sessions.pop(user_id, None)

    def _check_session(self, user_id: str, session) -> list:
        issues = []

        circuit_breaker = getattr(session, "circuit_breaker", None)
        if circuit_breaker is not None and circuit_breaker.tripped:
            issues.append(_Issue(user_id, "circuit_breaker", circuit_breaker.trip_reason or "circuit breaker activo"))

        kill_switch = getattr(session, "kill_switch", None)
        if kill_switch is not None and kill_switch.is_active():
            issues.append(_Issue(user_id, "kill_switch", kill_switch.reason()))

        heartbeat = getattr(session, "heartbeat", None)
        if heartbeat is not None:
            status = heartbeat.status()
            if not status["saludable"]:
                issues.append(_Issue(user_id, "heartbeat_stale", status["motivo"]))

        state_store = getattr(session, "state_store", None)
        broker = getattr(session, "broker", None)
        if state_store is not None and broker is not None:
            internal_positions = state_store.load().get("positions", {})
            report = reconcile(internal_positions, broker.get_open_positions())
            if not report["coincide"]:
                issues.append(_Issue(user_id, "reconciliacion", str(report)))

        return issues

    def check_all(self) -> dict:
        """
        Chequea todas las sesiones registradas, alerta individualmente por
        cada problema encontrado, y devuelve un reporte consolidado
        (incluyendo alertas sistémicas si corresponde).
        """
        all_issues = []
        for user_id, session in list(self._sessions.items()):
            all_issues.extend(self._check_session(user_id, session))

        for issue in all_issues:
            self.alert_channel.send(f"[OPS] Usuario {issue.user_id}: {issue.tipo} -- {issue.detalle}")

        by_type = {}
        for issue in all_issues:
            by_type.setdefault(issue.tipo, []).append(issue.user_id)

        n_total = len(self._sessions)
        systemic_alerts = []
        if n_total >= self.min_users_for_systemic:
            for tipo, user_ids in by_type.items():
                pct = len(user_ids) / n_total * 100
                if pct >= self.systemic_threshold_pct:
                    msg = (
                        f"[OPS - ALERTA SISTEMICA] {len(user_ids)}/{n_total} usuarios "
                        f"({pct:.1f}%) tienen '{tipo}' activo ahora mismo -- esto no es "
                        f"la mala suerte de una persona, revisar si es un movimiento de "
                        f"mercado real o un bug."
                    )
                    self.alert_channel.send(msg)
                    log.error(msg)
                    systemic_alerts.append({"tipo": tipo, "usuarios_afectados": user_ids, "porcentaje": round(pct, 1)})

        return {
            "usuarios_monitoreados": n_total,
            "problemas_individuales": [
                {"user_id": i.user_id, "tipo": i.tipo, "detalle": i.detalle} for i in all_issues
            ],
            "alertas_sistemicas": systemic_alerts,
        }
