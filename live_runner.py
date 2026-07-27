"""
Runner "en vivo": junta todas las piezas del motor (estrategia, gestor de
riesgo, bróker, alertas, kill-switch, heartbeat, circuit breaker, filtros
de régimen y multi-timeframe) en un solo loop operable.

Dos modos:

- `run_live`: reproduce un CSV histórico vela por vela, como si llegaran en
  vivo. Sirve para probar el flujo completo de punta a punta sin depender
  de una conexión real.
- `run_live_polling`: paper trading contra PRECIOS REALES en vivo (ej.
  RipioBrokerAdapter usado solo para *leer* el ticker público). La
  ejecución sigue siendo 100% simulada vía PaperBroker -- nunca coloca una
  orden real, sin importar qué bróker se use como fuente de precio.

Ambos modos comparten la misma lógica de decisión por-tick (`_LiveEngine`)
para no duplicarla -- ver README.md, sección de bugs encontrados: varias
de las fallas reales de este proyecto vinieron de tener la misma lógica
copiada en dos lugares y editar solo uno.

Uso:
    python3 live_runner.py --csv real_data/btc_daily.csv --strategy momentum --profile agresivo
    python3 live_runner.py --csv real_data/btc_daily.csv --live-prices --symbol BTC_USDC --poll-interval 60
"""

import argparse
import signal as system_signal
import time
from datetime import datetime, date as date_cls

import pandas as pd

from data_utils import load_csv
from strategies import get_strategy, STRATEGY_TYPE
from risk_manager import position_size
from risk_profiles import get_profile
from broker import PaperBroker
from alerts import ConsoleAlertChannel, format_signal_alert
from safety import ManualKillSwitch, CircuitBreaker
from health import Heartbeat
from state_store import StateStore
from trade_history import TradeHistoryLog
from reconciliation import reconcile
from regime import apply_regime_filter
from multi_timeframe import apply_multi_timeframe_filter
from app_logger import setup_logging, get_logger


def _atr(df, period=14):
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


class _LiveEngine:
    """
    Lógica de decisión por-tick (heartbeat, kill-switch, circuit breaker,
    entradas/salidas, persistencia de estado, reconciliación), compartida
    entre el replay de CSV y el polling de precios reales. Un solo tick se
    procesa con `process_tick(date, price, current_atr, sig)`.
    """

    def __init__(self, broker, symbol, profile, strategy_name, profile_name,
                 alert_channel, kill_switch, circuit_breaker, heartbeat,
                 state_store, reconcile_every, log, news_guard=None, trade_history=None):
        self.broker = broker
        self.symbol = symbol
        self.profile = profile
        self.strategy_name = strategy_name
        self.profile_name = profile_name
        self.alert_channel = alert_channel
        self.kill_switch = kill_switch
        self.circuit_breaker = circuit_breaker
        self.heartbeat = heartbeat
        self.state_store = state_store
        self.reconcile_every = reconcile_every
        self.log = log
        # Opcional: solo tiene sentido en polling en vivo (run_live_polling),
        # nunca en replay historico -- ver news_monitor.py.
        self.news_guard = news_guard
        # Opcional (ver trade_history.py): a diferencia de state_store, esto
        # es un registro append-only que sobrevive intacto a que el proceso
        # se pare y arranque muchas veces -- pensado para uso intermitente
        # (activar hoy, pausar, retomar en semanas) con un reporte único de
        # todo lo operado en el medio.
        self.trade_history = trade_history

        saved_state = state_store.load()
        self.stop_loss = saved_state["extra"].get("stop_loss") if saved_state["saved_at"] else None
        self.take_profit = saved_state["extra"].get("take_profit") if saved_state["saved_at"] else None
        self.internal_positions = saved_state["positions"] if saved_state["saved_at"] else {}
        # Orden colocada que quedo "open" (sin llenar, o parcialmente llena
        # y a la espera de mas) -- ver Fase 3c / _resolve_pending_order.
        self.pending_order = saved_state["extra"].get("pending_order") if saved_state["saved_at"] else None

        # Restaurar el capital guardado -- sin esto, cada reinicio del
        # proceso volvía a arrancar el bróker con el --capital inicial de
        # la CLI, descartando en silencio cualquier ganancia/pérdida real
        # acumulada en corridas anteriores (bug real: se detectó porque
        # ETH_USDC volvió a mostrar 1000.00 tras un reinicio, pese a haber
        # cerrado en 991.87 antes de pararlo).
        if saved_state["saved_at"] and saved_state.get("capital") is not None:
            self.broker.balance = saved_state["capital"]

        # Restaurar también las posiciones abiertas EN EL BRÓKER, no solo en
        # el registro interno -- sin esto, un reinicio con una posición
        # abierta dejaba `internal_positions` sabiendo que existe pero al
        # bróker (una instancia nueva, sin memoria de nada anterior) sin
        # ella. En el primer tick tras el reinicio, `in_position` se
        # calcula mirando SOLO al bróker (`self.symbol in
        # self.broker.get_open_positions()`) -- así que el motor pensaría
        # que no hay nada abierto y podría intentar abrir una posición
        # nueva encima de la que en realidad seguía activa, en vez de
        # vigilarla con su stop loss/take profit real.
        if self.internal_positions:
            self.broker.positions.update(self.internal_positions)

        # Restaurar la memoria del circuit breaker -- sin esto, un reinicio
        # DESACTIVABA en silencio un freno que estaba activo (`tripped`
        # siempre arrancaba en False, sin importar qué tan reciente había
        # sido el drawdown que lo disparó), y el pico histórico de equity
        # que define el drawdown también se perdía (`equity_curve` volvía
        # a arrancar vacío, así que el primer tick post-reinicio se
        # convertía en el nuevo "pico", borrando cualquier caída anterior
        # al reinicio). Guardar solo el pico (no toda la curva) alcanza --
        # es lo único que `CircuitBreaker.check()` necesita.
        extra = saved_state.get("extra") or {}
        if saved_state["saved_at"]:
            self.circuit_breaker.tripped = extra.get("circuit_breaker_tripped", False)
            self.circuit_breaker.trip_reason = extra.get("circuit_breaker_trip_reason")
        saved_peak_equity = extra.get("peak_equity")

        # Restaurar la pausa automática por noticias -- mismo problema que
        # el circuit breaker: sin esto, reiniciar el proceso en medio de
        # una pausa activa (ej. tras una noticia de alto impacto) la
        # levantaba en silencio.
        if self.news_guard is not None and saved_state["saved_at"]:
            paused_until_str = extra.get("news_paused_until")
            if paused_until_str:
                self.news_guard.restore_paused_until(datetime.fromisoformat(paused_until_str))

        # Restaurar la referencia de pérdida diaria -- mismo problema, un
        # tercer lugar: si el proceso se reinicia a mitad de un día que ya
        # venía con pérdida, sin esto `day_start_equity` se reseteaba al
        # equity DE ESE MOMENTO (post-reinicio), ocultando cualquier
        # caída del día anterior al reinicio del chequeo de pérdida diaria
        # del circuit breaker.
        saved_current_day = extra.get("current_day")
        self.day_start_equity = extra.get("day_start_equity") if saved_current_day else None
        self.current_day = date_cls.fromisoformat(saved_current_day) if saved_current_day else None

        self.ticks_processed = 0
        self.equity_curve = [saved_peak_equity] if saved_peak_equity is not None else []

    def _mark_to_market(self, price):
        cash = self.broker.get_balance()
        positions = self.broker.get_open_positions()
        if self.symbol in positions:
            return cash + positions[self.symbol]["unidades"] * price
        return cash

    def _record_trade(self, side, motivo, units, price, pnl=None):
        if self.trade_history is None or not units or units <= 0:
            return
        self.trade_history.append(
            symbol=self.symbol, side=side, motivo=motivo, units=units,
            price=price, pnl=pnl, balance_resultante=self.broker.get_balance(),
        )

    def _apply_buy_fill(self, filled_units, price_filled, sizing):
        """Registra (o suma a) la posición interna con lo EFECTIVAMENTE
        comprado -- nunca con la cantidad pedida (ver Fase 3c)."""
        if filled_units <= 0:
            return
        self.stop_loss = sizing["stop_loss"]
        self.take_profit = sizing["take_profit"]
        self.internal_positions[self.symbol] = {"unidades": filled_units, "precio_entrada": price_filled}
        self._record_trade("buy", "apertura", filled_units, price_filled)

    def _apply_sell_fill(self, filled_units, price_filled=None, motivo=None):
        """Reduce (o cierra del todo) la posición interna según lo
        EFECTIVAMENTE vendido -- una venta parcial deja el resto abierto
        con el mismo stop loss/take profit."""
        pos = self.internal_positions.get(self.symbol)
        if not pos or filled_units <= 0:
            return
        entry_price = pos["precio_entrada"]
        remaining = pos["unidades"] - filled_units
        if remaining <= 1e-9:
            self.stop_loss, self.take_profit = None, None
            self.internal_positions.pop(self.symbol, None)
        else:
            pos["unidades"] = remaining
        pnl = (price_filled - entry_price) * filled_units if price_filled is not None else None
        self._record_trade("sell", motivo, filled_units, price_filled, pnl=pnl)

    def _resolve_pending_order(self):
        """
        Si quedó una orden "open" de un tick anterior, la vuelve a
        consultar. Mientras siga "open" no hace nada más (se revisa de
        nuevo el próximo tick). Cualquier otro estado (filled,
        partially_filled, canceled, rejected) se trata como definitivo
        para esta orden -- este motor no persigue el resto de una orden
        parcialmente llena indefinidamente, aplica lo que efectivamente
        se ejecutó y sigue adelante.
        """
        if self.pending_order is None:
            return

        order_id = self.pending_order["order_id"]
        try:
            status_order = self.broker.get_order_status(order_id)
        except Exception as e:
            self.log.warning("No se pudo consultar la orden pendiente %s (%s) -- se reintenta el próximo tick",
                              order_id, e)
            return

        status = status_order["status"]
        if status == "open":
            return

        filled_units = status_order.get("units") or 0
        side = self.pending_order["side"]
        if side == "buy":
            self._apply_buy_fill(filled_units, status_order.get("price"), self.pending_order["sizing"])
        else:
            self._apply_sell_fill(filled_units, price_filled=status_order.get("price"),
                                   motivo=self.pending_order.get("motivo"))

        self.log.info("Orden pendiente %s se resolvió: %s (%s unidades)", order_id, status, filled_units)
        self.pending_order = None

    def process_tick(self, date, price, current_atr, sig):
        self.ticks_processed += 1

        # 1) Heartbeat: registrar que "llegó" un dato nuevo del feed
        self.heartbeat.beat()

        # 2) Chequeo de kill-switch manual antes de cualquier decisión
        if self.kill_switch.is_active():
            self.log.warning("Kill-switch activo (%s) -- no se abren posiciones nuevas este tick",
                              self.kill_switch.reason())
            self.equity_curve.append(self._mark_to_market(price))
            return

        # 2b) Noticias de alto impacto: siempre alerta; en ventana automatica
        # ademas puede dejar `news_guard.entries_paused()` en True (chequeado
        # mas abajo, junto al resto de condiciones para abrir una posicion).
        if self.news_guard is not None:
            self.news_guard.check()

        # 3) Actualizar el precio "de mercado" que ve el bróker
        self.broker.set_price(self.symbol, price)

        # 3b) Si quedó una orden abierta/parcial del tick anterior, resolverla ANTES de decidir algo nuevo
        self._resolve_pending_order()

        equity = self._mark_to_market(price)

        # Reinicia el equity de referencia diario para el chequeo de pérdida diaria
        day_key = date.date() if hasattr(date, "date") else date
        if day_key != self.current_day:
            self.current_day = day_key
            self.day_start_equity = equity

        breaker_active = self.circuit_breaker.check(self.equity_curve, self.day_start_equity, equity)
        self.equity_curve.append(equity)

        # Mientras haya una orden todavía sin resolver, no se evalúa nada
        # nuevo este tick -- evita mandar una segunda orden encima de una
        # que el bróker todavía no terminó de procesar.
        if self.pending_order is not None:
            if self.ticks_processed % self.reconcile_every == 0:
                self.force_persist()
            return

        broker_positions = self.broker.get_open_positions()
        in_position = self.symbol in broker_positions

        # 4) Lógica de salida (si hay posición abierta)
        if in_position:
            pos = broker_positions[self.symbol]
            hit_stop = self.stop_loss is not None and price <= self.stop_loss
            hit_target = self.take_profit is not None and price >= self.take_profit
            strategy_exit = sig == 0

            if hit_stop or hit_target or strategy_exit:
                client_order_id = f"{self.symbol}-sell-{date}"
                order = self.broker.place_order(self.symbol, "sell", pos["unidades"], client_order_id=client_order_id)
                motivo = "stop_loss" if hit_stop else ("take_profit" if hit_target else "señal_estrategia")
                status = order["status"]
                filled_units = order.get("units") or 0

                if status == "open":
                    self.pending_order = {"order_id": order.get("broker_order_id") or order.get("order_id"),
                                           "side": "sell", "motivo": motivo}
                    self.log.info("Orden de venta (%s) quedó abierta -- se revisará en el próximo tick", motivo)
                else:
                    self.log.info("Cierre de posición (%s): %s", motivo, order)
                    self._apply_sell_fill(filled_units, price_filled=order.get("price"), motivo=motivo)
                    if status == "partially_filled":
                        self.log.warning("Venta parcial en %s: %s/%s unidades -- se sigue adelante con lo efectivamente vendido",
                                          self.symbol, filled_units, pos["unidades"])

        # 5) Lógica de entrada (si no hay posición abierta, el breaker no está
        # activo, y no hay una pausa automática por noticias en curso)
        elif not breaker_active and not (self.news_guard is not None and self.news_guard.entries_paused()):
            if sig == 1 and not pd.isna(current_atr) and current_atr > 0:
                sizing = position_size(self.broker.get_balance(), price, current_atr, self.profile)
                if sizing["unidades"] > 0 and sizing["viable"] is not False:
                    client_order_id = f"{self.symbol}-buy-{date}"
                    order = self.broker.place_order(self.symbol, "buy", sizing["unidades"], client_order_id=client_order_id)
                    status = order["status"]
                    filled_units = order.get("units") or 0

                    if status == "open":
                        self.pending_order = {
                            "order_id": order.get("broker_order_id") or order.get("order_id"),
                            "side": "buy", "sizing": sizing,
                        }
                        self.log.info("Orden de compra quedó abierta (0 unidades llenadas todavía) -- "
                                       "se revisará en el próximo tick")
                    else:
                        self._apply_buy_fill(filled_units, order.get("price"), sizing)
                        if filled_units > 0:
                            alert_msg = format_signal_alert(
                                self.symbol, self.strategy_name, self.profile_name, date, price, sizing
                            )
                            self.alert_channel.send(alert_msg)
                        if status == "partially_filled":
                            self.log.warning("Compra parcial en %s: %s/%s unidades -- se registra la posición con lo efectivamente comprado",
                                              self.symbol, filled_units, sizing["unidades"])

        # 6) Persistir estado y reconciliar cada N ticks
        if self.ticks_processed % self.reconcile_every == 0:
            self.force_persist()

    def force_persist(self):
        peak_equity = max(self.equity_curve) if self.equity_curve else None
        news_paused_until = self.news_guard.get_paused_until() if self.news_guard is not None else None
        self.state_store.save(self.internal_positions, capital=self.broker.get_balance(),
                               extra={"stop_loss": self.stop_loss, "take_profit": self.take_profit,
                                      "pending_order": self.pending_order,
                                      "circuit_breaker_tripped": self.circuit_breaker.tripped,
                                      "circuit_breaker_trip_reason": self.circuit_breaker.trip_reason,
                                      "peak_equity": peak_equity,
                                      "news_paused_until": news_paused_until.isoformat() if news_paused_until else None,
                                      "current_day": self.current_day.isoformat() if self.current_day else None,
                                      "day_start_equity": self.day_start_equity})
        report = reconcile(self.internal_positions, self.broker.get_open_positions())
        if not report["coincide"]:
            self.log.error("Desfasaje detectado entre el estado interno y el bróker: %s", report)


def _make_shutdown_flag(log):
    flag = {"flag": False}

    def _handler(signum, frame):
        log.warning("Señal de apagado recibida (%s) -- terminando de forma ordenada tras este tick", signum)
        flag["flag"] = True

    system_signal.signal(system_signal.SIGINT, _handler)
    system_signal.signal(system_signal.SIGTERM, _handler)
    return flag


def run_live(csv_path: str, strategy_name: str, profile_name: str,
             symbol: str = "ASSET", initial_balance: float = 1000.0,
             replay_delay_seconds: float = 0.0, max_ticks: int = None,
             state_path: str = "engine_state.json", reconcile_every: int = 10,
             max_drawdown_pct: float = 15.0, max_daily_loss_pct: float = 5.0,
             regime_filter: bool = True, multi_timeframe_filter: bool = True,
             trade_history_path: str = None, kill_switch_path: str = ".KILL_SWITCH"):
    log = get_logger("live_runner")
    log.info("Iniciando runner en vivo (modo paper trading, replay histórico) — %s / %s sobre %s",
              strategy_name, profile_name, symbol)

    df = load_csv(csv_path)
    strategy_fn = get_strategy(strategy_name)
    profile = get_profile(profile_name)
    signal = strategy_fn(df)

    if regime_filter:
        strategy_type = STRATEGY_TYPE.get(strategy_name, "tendencia")
        signal = apply_regime_filter(signal, df, strategy_type=strategy_type)
        log.info("Filtro de régimen aplicado (tipo=%s)", strategy_type)
    if multi_timeframe_filter:
        signal = apply_multi_timeframe_filter(signal, df)
        log.info("Filtro multi-timeframe aplicado")

    atr = _atr(df)

    broker = PaperBroker(initial_balance=initial_balance)
    trade_history = TradeHistoryLog(trade_history_path) if trade_history_path else None
    engine = _LiveEngine(
        broker, symbol, profile, strategy_name, profile_name,
        ConsoleAlertChannel(), ManualKillSwitch(control_file=kill_switch_path),
        CircuitBreaker(max_drawdown_pct, max_daily_loss_pct),
        Heartbeat(max_staleness_seconds=3600),  # en un loop real, ajustar según frecuencia del feed
        StateStore(path=state_path), reconcile_every, log,
        trade_history=trade_history,
    )
    shutdown_requested = _make_shutdown_flag(log)

    for date in df.index:
        if max_ticks and engine.ticks_processed >= max_ticks:
            break
        if shutdown_requested["flag"]:
            log.info("Apagado ordenado: guardando estado final antes de salir")
            break

        price = df.loc[date, "close"]
        current_atr = atr.loc[date]
        sig = signal.loc[date]
        engine.process_tick(date, price, current_atr, sig)

        if replay_delay_seconds > 0:
            time.sleep(replay_delay_seconds)

    engine.force_persist()
    log.info("Runner finalizado. Balance final: %.2f | Posiciones abiertas: %s",
              broker.get_balance(), broker.get_open_positions())
    return broker


def run_live_polling(price_source, symbol: str, strategy_name: str, profile_name: str,
                      seed_csv: str, initial_balance: float = 1000.0,
                      poll_interval_seconds: float = 60.0, max_ticks: int = None,
                      state_path: str = "engine_state.json", reconcile_every: int = 10,
                      max_drawdown_pct: float = 15.0, max_daily_loss_pct: float = 5.0,
                      regime_filter: bool = True, multi_timeframe_filter: bool = True,
                      max_history_rows: int = 5000, news_guard=None, trade_history_path: str = None,
                      kill_switch_path: str = ".KILL_SWITCH"):
    """
    Paper trading contra un feed de precios REAL (no replay histórico): en
    cada intervalo de `poll_interval_seconds` pide el precio actual a
    `price_source.get_current_price(symbol)` (ej. un RipioBrokerAdapter
    usado SOLO para leer -- la ejecución sigue siendo 100% simulada vía
    PaperBroker, nunca coloca una orden real) y lo procesa como una vela
    nueva.

    LIMITACIÓN IMPORTANTE: un ticker da el último precio, no un candle OHLC
    real. Cada tick se aproxima como open=último cierre conocido,
    high/low=envolvente de open/close, volume=0. Alcanza para probar el
    flujo de decisión de punta a punta con precios reales, pero
    indicadores que dependen fuerte de high/low intradía (ej. ADX) van a
    ser menos precisos que con velas reales -- para uso serio, reemplazar
    por un feed de velas real si el bróker lo ofrece.

    `seed_csv` precarga historial real para que las medias móviles/ADX/
    filtro semanal tengan contexto suficiente desde el primer tick en vivo.
    `max_history_rows` acota cuánto crece el historial en memoria en
    sesiones largas (recorta las filas más viejas, conservando siempre las
    últimas -- de sobra para cualquiera de los lookbacks que usan las
    estrategias).

    `news_guard` (opcional, ver news_monitor.NewsGuard): si se pasa, en
    cada tick chequea noticias de alto impacto y siempre alerta; en las
    ventanas horarias configuradas como automáticas además pausa
    temporalmente la apertura de posiciones nuevas. Nunca coloca ni cierra
    una orden por sí solo.
    """
    log = get_logger("live_runner")
    log.info("Iniciando runner en vivo (modo paper trading, PRECIOS REALES en vivo) — %s / %s sobre %s",
              strategy_name, profile_name, symbol)

    df = load_csv(seed_csv)
    strategy_fn = get_strategy(strategy_name)
    profile = get_profile(profile_name)

    broker = PaperBroker(initial_balance=initial_balance)
    trade_history = TradeHistoryLog(trade_history_path) if trade_history_path else None
    engine = _LiveEngine(
        broker, symbol, profile, strategy_name, profile_name,
        ConsoleAlertChannel(), ManualKillSwitch(control_file=kill_switch_path),
        CircuitBreaker(max_drawdown_pct, max_daily_loss_pct),
        Heartbeat(max_staleness_seconds=max(poll_interval_seconds * 5, 300)),
        StateStore(path=state_path), reconcile_every, log,
        news_guard=news_guard, trade_history=trade_history,
    )
    shutdown_requested = _make_shutdown_flag(log)

    last_close = df["close"].iloc[-1]

    try:
        while True:
            if max_ticks and engine.ticks_processed >= max_ticks:
                break
            if shutdown_requested["flag"]:
                log.info("Apagado ordenado: guardando estado final antes de salir")
                break

            try:
                price = price_source.get_current_price(symbol)
            except Exception as e:
                log.error("No se pudo obtener el precio en vivo (%s) -- se salta este tick", e)
                if poll_interval_seconds > 0:
                    time.sleep(poll_interval_seconds)
                continue

            now = pd.Timestamp.now(tz="UTC").tz_localize(None)
            open_ = float(last_close)
            close_ = float(price)
            df.loc[now] = {
                "open": open_, "high": max(open_, close_), "low": min(open_, close_),
                "close": close_, "volume": 0,
            }
            if len(df) > max_history_rows:
                df = df.iloc[-max_history_rows:]
            last_close = close_

            signal = strategy_fn(df)
            if regime_filter:
                strategy_type = STRATEGY_TYPE.get(strategy_name, "tendencia")
                signal = apply_regime_filter(signal, df, strategy_type=strategy_type)
            if multi_timeframe_filter:
                signal = apply_multi_timeframe_filter(signal, df)
            current_atr = _atr(df).iloc[-1]
            sig = signal.iloc[-1]

            engine.process_tick(now, close_, current_atr, sig)

            if poll_interval_seconds > 0:
                time.sleep(poll_interval_seconds)
    finally:
        engine.force_persist()
        log.info("Runner finalizado (precios en vivo). Balance final: %.2f | Posiciones abiertas: %s",
                  broker.get_balance(), broker.get_open_positions())

    return broker


def main():
    parser = argparse.ArgumentParser(description="Runner en vivo (paper trading) del motor")
    parser.add_argument("--csv", type=str, required=True,
                         help="CSV histórico: en modo replay se reproduce entero; en --live-prices se usa para 'calentar' los indicadores")
    parser.add_argument("--strategy", type=str, default="momentum")
    parser.add_argument("--profile", type=str, default="agresivo")
    parser.add_argument("--symbol", type=str, default="ASSET")
    parser.add_argument("--capital", type=float, default=1000.0)
    parser.add_argument("--max-ticks", type=int, default=None,
                         help="Limitar a los primeros N ticks, útil para pruebas rápidas")
    parser.add_argument("--delay", type=float, default=0.0,
                         help="[modo replay] Segundos de pausa entre cada 'vela' simulada (0 = lo más rápido posible)")
    parser.add_argument("--state-path", type=str, default="engine_state.json",
                         help="Ruta del archivo de estado persistente (posiciones, stop/take profit)")
    parser.add_argument("--trade-history-path", type=str, default=None,
                         help="Ruta de un CSV donde se registra cada operación cerrada, acumulado entre reinicios "
                              "(pensado para uso intermitente: parar y retomar días o semanas después y tener un "
                              "único reporte de todo lo operado en el medio). Si no se pasa, no se registra.")
    parser.add_argument("--kill-switch-file", type=str, default=".KILL_SWITCH",
                         help="Ruta del archivo de control del kill-switch manual. Por defecto todas las sesiones "
                              "usan el mismo (.KILL_SWITCH) -- si corrés varias sesiones a la vez (ej. un símbolo "
                              "por proceso) y querés poder pausar cada una por separado, pasale un archivo distinto "
                              "a cada una (ej. .KILL_SWITCH_BTC_USDC).")
    parser.add_argument("--max-drawdown", type=float, default=15.0)
    parser.add_argument("--max-daily-loss", type=float, default=5.0)
    parser.add_argument("--no-regime-filter", action="store_true",
                         help="Desactiva el filtro de régimen de mercado")
    parser.add_argument("--no-mtf-filter", action="store_true",
                         help="Desactiva el filtro multi-timeframe")
    parser.add_argument("--live-prices", action="store_true",
                         help="En vez de reproducir el CSV, pedir el precio real en vivo en cada intervalo. Nunca coloca órdenes reales, solo lee precio.")
    parser.add_argument("--broker", type=str, default="ripio", choices=["ripio", "alpaca"],
                         help="[--live-prices] Fuente de precio en vivo: 'ripio' (cripto, ej. BTC_USDC) o 'alpaca' (acciones de EE.UU., ej. AAPL, KO -- prueba de concepto de que el mismo motor sirve para otra clase de activo, ver README)")
    parser.add_argument("--poll-interval", type=float, default=60.0,
                         help="[--live-prices] Segundos entre cada consulta de precio en vivo")
    parser.add_argument("--news-alerts", action="store_true",
                         help="[--live-prices] Activar el monitor de noticias de alto impacto (RSS público, sin API key)")
    parser.add_argument("--news-auto-window", action="append", default=[], metavar="HH-HH",
                         help="Ventana horaria UTC 'HH-HH' donde, ademas de alertar, se pausan automaticamente las entradas nuevas ante una noticia de alto impacto (ej. 22-6). Repetible. Sin ninguna, el modo es siempre manual (solo alerta, nunca pausa sola).")
    parser.add_argument("--news-cooldown-minutes", type=float, default=60.0,
                         help="[--news-alerts] Minutos que dura la pausa automatica de entradas tras una noticia de alto impacto, en ventana automatica")
    parser.add_argument("--news-check-interval", type=float, default=300.0,
                         help="[--news-alerts] Segundos minimos entre consultas reales a los feeds de noticias")
    args = parser.parse_args()

    setup_logging(level="INFO")

    if args.live_prices:
        if args.broker == "alpaca":
            from broker import AlpacaBrokerAdapter
            price_source = AlpacaBrokerAdapter(allow_trading=False)
        else:
            from broker import RipioBrokerAdapter
            price_source = RipioBrokerAdapter(allow_trading=False)

        news_guard = None
        if args.news_alerts:
            from news_monitor import NewsMonitor, NewsAutomationSchedule, AutomationWindow, NewsGuard

            windows = []
            for spec in args.news_auto_window:
                start_s, end_s = spec.split("-")
                windows.append(AutomationWindow(int(start_s), int(end_s)))

            news_guard = NewsGuard(
                NewsMonitor(), NewsAutomationSchedule(windows, cooldown_minutes=args.news_cooldown_minutes),
                ConsoleAlertChannel(), min_interval_seconds=args.news_check_interval,
            )
            modo = "hibrido (automatico en horario configurado)" if windows else "siempre manual (solo alerta)"
            get_logger("live_runner").info("Monitor de noticias activado -- modo %s", modo)

        run_live_polling(
            price_source, args.symbol, args.strategy, args.profile, seed_csv=args.csv,
            initial_balance=args.capital, poll_interval_seconds=args.poll_interval,
            max_ticks=args.max_ticks, state_path=args.state_path,
            max_drawdown_pct=args.max_drawdown, max_daily_loss_pct=args.max_daily_loss,
            regime_filter=not args.no_regime_filter, multi_timeframe_filter=not args.no_mtf_filter,
            news_guard=news_guard, trade_history_path=args.trade_history_path,
            kill_switch_path=args.kill_switch_file,
        )
    else:
        run_live(
            args.csv, args.strategy, args.profile, symbol=args.symbol,
            initial_balance=args.capital, replay_delay_seconds=args.delay,
            max_ticks=args.max_ticks, state_path=args.state_path,
            max_drawdown_pct=args.max_drawdown, max_daily_loss_pct=args.max_daily_loss,
            regime_filter=not args.no_regime_filter,
            multi_timeframe_filter=not args.no_mtf_filter,
            trade_history_path=args.trade_history_path,
            kill_switch_path=args.kill_switch_file,
        )


if __name__ == "__main__":
    main()
