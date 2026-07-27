"""
Capa de seguridad: validación de datos de entrada + freno de emergencia
(circuit breaker) que detiene la operatoria si se rompen ciertos límites.
Esto es lo que separa un motor de backtesting de un motor apto para
acercarse, con mucho cuidado, a operar con capital real.
"""

import pandas as pd
from app_logger import get_logger

log = get_logger(__name__)


def validate_ohlcv(df: pd.DataFrame) -> list:
    """
    Corre chequeos básicos de integridad sobre los datos históricos.
    Devuelve una lista de strings con problemas encontrados (vacía si
    todo está OK). Correr esto SIEMPRE antes de un backtest o de conectar
    datos en vivo -- datos corruptos generan resultados de backtest que
    parecen buenos pero son basura.
    """
    problems = []

    required_cols = {"open", "high", "low", "close", "volume"}
    missing = required_cols - set(df.columns)
    if missing:
        problems.append(f"Faltan columnas: {missing}")
        return problems  # sin estas columnas no se puede seguir chequeando

    if df.isnull().any().any():
        n_nulls = int(df.isnull().sum().sum())
        problems.append(f"Hay {n_nulls} valores nulos en el dataset")

    if (df["high"] < df["low"]).any():
        problems.append("Hay filas donde 'high' es menor que 'low' (dato corrupto)")

    if (df[["open", "high", "low", "close"]] <= 0).any().any():
        problems.append("Hay precios menores o iguales a cero")

    if not df.index.is_monotonic_increasing:
        problems.append("Las fechas no están ordenadas cronológicamente")

    if df.index.duplicated().any():
        n_dup = int(df.index.duplicated().sum())
        problems.append(f"Hay {n_dup} fechas duplicadas")

    if len(df) < 60:
        problems.append(
            f"Solo hay {len(df)} velas -- muy pocas para que las medias "
            f"móviles largas (ej. 200) tengan sentido"
        )

    return problems


class ManualKillSwitch:
    """
    Freno de emergencia MANUAL, separado del circuit breaker automático.
    El circuit breaker reacciona a números (drawdown, pérdida diaria); este
    kill-switch es para cuando una persona ve algo raro que ningún número
    todavía capturó, y quiere frenar todo YA, sin esperar a que se dispare
    ninguna condición automática.

    Funciona con un archivo de control simple: si el archivo existe, el
    motor no abre posiciones nuevas. Es deliberadamente simple (no requiere
    un servicio corriendo aparte) para que activar el freno sea tan fácil
    como crear un archivo, incluso a las 3am desde el celular por SSH.
    """

    def __init__(self, control_file: str = ".KILL_SWITCH"):
        self.control_file = control_file

    def is_active(self) -> bool:
        import os
        return os.path.exists(self.control_file)

    def activate(self, reason: str = "Detenido manualmente"):
        # encoding explícito: sin esto, Python escribe con el codepage del
        # sistema (cp1252 en Windows) -- si este archivo se crea en Windows
        # y despues se lee dentro de un container Linux (Docker, UTF-8 por
        # defecto), o al reves, un motivo con tildes puede leerse mal o
        # directamente fallar. Mismo tipo de bug que el de --config en
        # run_backtest.py, encontrado en la misma auditoria.
        with open(self.control_file, "w", encoding="utf-8") as f:
            f.write(reason)
        log.warning("KILL-SWITCH MANUAL ACTIVADO: %s", reason)

    def deactivate(self):
        import os
        if os.path.exists(self.control_file):
            os.remove(self.control_file)
        log.info("Kill-switch manual desactivado")

    def reason(self) -> str:
        """Devuelve el motivo guardado al activar el kill-switch (None si no está activo)."""
        if not self.is_active():
            return None
        with open(self.control_file, encoding="utf-8") as f:
            return f.read().strip()


class CircuitBreaker:
    """
    Freno de emergencia. Si el capital cae más de `max_drawdown_pct` desde
    su máximo histórico, o se pierde más de `max_daily_loss_pct` en un día,
    el circuit breaker se activa y el motor deja de abrir posiciones nuevas
    (las que ya están abiertas se pueden seguir cerrando por su stop loss
    normal). Esto es un límite estructural, no una sugerencia.
    """

    def __init__(self, max_drawdown_pct: float = 15.0, max_daily_loss_pct: float = 5.0):
        self.max_drawdown_pct = max_drawdown_pct
        self.max_daily_loss_pct = max_daily_loss_pct
        self.tripped = False
        self.trip_reason = None

    def check(self, equity_curve: list, today_start_capital: float, current_capital: float) -> bool:
        """Devuelve True si el circuit breaker está activo (hay que dejar de operar)."""
        if self.tripped:
            return True

        if equity_curve:
            peak = max(equity_curve)
            drawdown_pct = (peak - current_capital) / peak * 100 if peak > 0 else 0
            if drawdown_pct >= self.max_drawdown_pct:
                self.tripped = True
                self.trip_reason = f"Drawdown máximo alcanzado: {drawdown_pct:.1f}%"
                log.warning("Circuit breaker activado: %s", self.trip_reason)
                return True

        if today_start_capital > 0:
            daily_loss_pct = (today_start_capital - current_capital) / today_start_capital * 100
            if daily_loss_pct >= self.max_daily_loss_pct:
                self.tripped = True
                self.trip_reason = f"Pérdida diaria máxima alcanzada: {daily_loss_pct:.1f}%"
                log.warning("Circuit breaker activado: %s", self.trip_reason)
                return True

        return False


class ProfitLock:
    """
    Freno simétrico al circuit breaker, pero a la suba: en vez de
    proteger contra pérdidas, asegura una ganancia cuando el capital
    alcanza una meta que el propio usuario define (ej. "+20% desde que
    empecé a vigilar"). Ninguno de los dos reemplaza al otro -- protegen
    lados opuestos de la misma cuenta.

    A diferencia del circuit breaker, que se activa por un problema, este
    freno se activa por una decisión deliberada: "llegué a donde quería,
    prefiero asegurar esto antes que seguir expuesto buscando más". Una
    vez activo, el motor deja de abrir posiciones nuevas -- las que ya
    están abiertas se siguen manejando con su stop loss/take profit
    normal, igual que con el circuit breaker.

    `reference_capital` es el punto de partida contra el que se mide la
    ganancia -- a propósito NO es el pico histórico (a diferencia del
    circuit breaker): lo que importa acá es cuánto se ganó desde que se
    empezó a vigilar, no desde cualquier máximo pasado.
    """

    def __init__(self, target_pct: float, reference_capital: float):
        if target_pct <= 0:
            raise ValueError("target_pct debe ser positivo (ej. 20.0 para +20%)")
        if reference_capital <= 0:
            raise ValueError("reference_capital debe ser positivo")
        self.target_pct = target_pct
        self.reference_capital = reference_capital
        self.triggered = False
        self.trigger_reason = None

    def check(self, current_capital: float) -> bool:
        """Devuelve True si el seguro de ganancias está activo (hay que dejar de operar)."""
        if self.triggered:
            return True

        gain_pct = (current_capital - self.reference_capital) / self.reference_capital * 100
        if gain_pct >= self.target_pct:
            self.triggered = True
            self.trigger_reason = f"Meta de ganancia alcanzada: +{gain_pct:.1f}% (objetivo: +{self.target_pct:.1f}%)"
            log.warning("Seguro de ganancias activado: %s", self.trigger_reason)
            return True

        return False
