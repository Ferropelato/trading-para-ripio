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
from safety import ManualKillSwitch, CircuitBreaker, ProfitLock
from health import Heartbeat
from state_store import StateStore
from trade_history import TradeHistoryLog
from manual_trading import ManualOrderQueue
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
    entre el replay de CSV y el polling de precios reales. Un tick de UN
    símbolo se procesa con `process_tick(date, price, current_atr, sig,
    symbol=...)` -- `symbol` es opcional cuando el motor solo maneja uno
    (queda implícito), y obligatorio en la práctica cuando maneja varios.

    Multi-símbolo (Fase Roadmap: "múltiples pares bajo un mismo cupo"):
    un solo `_LiveEngine` puede vigilar varios símbolos a la vez,
    compartiendo el mismo bróker (mismo pool de capital), el mismo
    circuit breaker/seguro de ganancias/kill-switch/pausa por noticias
    (protegen la cuenta entera, no un símbolo en particular), y
    opcionalmente un `max_positions` -- el cupo compartido de posiciones
    simultáneas entre TODOS los símbolos (no un cupo por símbolo). Lo que
    sí es genuinamente por símbolo: la posición abierta, su stop
    loss/take profit, y cualquier orden pendiente -- por eso esos tres
    viven en diccionarios (`symbol -> valor`), no en un único valor.
    """

    def __init__(self, broker, symbol, profile, strategy_name, profile_name,
                 alert_channel, kill_switch, circuit_breaker, heartbeat,
                 state_store, reconcile_every, log, news_guard=None, trade_history=None,
                 profit_lock=None, max_positions=None, manual_orders=None):
        self.broker = broker
        # Acepta un símbolo suelto (uso de siempre, un motor = un par) o
        # una lista (varios pares bajo el mismo motor) -- normalizado acá
        # para no tener dos code paths distintos más abajo.
        self.symbols = list(symbol) if isinstance(symbol, (list, tuple)) else [symbol]
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
        # Opcional (ver safety.ProfitLock): freno simétrico al circuit
        # breaker, pero a la suba -- asegura una ganancia cuando se
        # alcanza la meta que el usuario definió, en vez de proteger
        # contra una pérdida.
        self.profit_lock = profit_lock
        # Opcional: cupo COMPARTIDO de posiciones simultáneas entre todos
        # los símbolos que maneja este motor -- None significa sin límite
        # explícito (el comportamiento de siempre, de antes de que
        # existiera esta opción).
        self.max_positions = max_positions
        # Opcional (ver manual_trading.py): modo manual/asistido -- una
        # compra manual respeta los mismos frenos que una automática
        # (circuit breaker, pausa por noticias, cupo compartido); una
        # venta manual siempre se deja pasar, igual que el stop
        # loss/take profit -- salir nunca está bloqueado.
        self.manual_orders = manual_orders

        saved_state = state_store.load()
        extra_saved = saved_state["extra"] if saved_state["saved_at"] else {}
        self.stop_loss = extra_saved.get("stop_loss") or {}
        self.take_profit = extra_saved.get("take_profit") or {}
        self.internal_positions = saved_state["positions"] if saved_state["saved_at"] else {}
        # Orden(es) colocada(s) que quedaron "open" (sin llenar, o
        # parcialmente llenas y a la espera de mas) -- dict symbol ->
        # orden, ver Fase 3c / _resolve_pending_order. Puede haber una
        # pendiente por cada símbolo a la vez.
        self.pending_order = extra_saved.get("pending_order") or {}

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
        extra = extra_saved
        if saved_state["saved_at"]:
            self.circuit_breaker.tripped = extra.get("circuit_breaker_tripped", False)
            self.circuit_breaker.trip_reason = extra.get("circuit_breaker_trip_reason")
        saved_peak_equity = extra.get("peak_equity")

        # Restaurar el estado del seguro de ganancias -- mismo cuidado que
        # el circuit breaker: el piso de referencia (que sube cada vez que
        # se asegura una ganancia) no debe resetearse solo con un reinicio
        # -- es "desde el último aseguramiento", no "desde el --capital de
        # esta corrida en particular".
        if self.profit_lock is not None and saved_state["saved_at"]:
            self.profit_lock.times_locked = extra.get("profit_lock_times_locked", 0)
            self.profit_lock.last_lock_reason = extra.get("profit_lock_last_reason")
            saved_reference = extra.get("profit_lock_reference_capital")
            if saved_reference is not None:
                self.profit_lock.reference_capital = saved_reference

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

    def _mark_to_market(self):
        """Equity total de la cuenta: efectivo + TODAS las posiciones
        abiertas (de cualquier símbolo que maneje este motor), cada una
        valuada al último precio conocido de su propio símbolo -- no solo
        el símbolo del tick actual. Necesario para que el circuit
        breaker/seguro de ganancias midan la cuenta completa, no un par
        aislado."""
        cash = self.broker.get_balance()
        total = cash
        for sym, pos in self.broker.get_open_positions().items():
            try:
                p = self.broker.get_current_price(sym)
            except ValueError:
                p = pos["precio_entrada"]  # todavía no se conoce un precio más fresco para ese símbolo
            total += pos["unidades"] * p
        return total

    def _record_trade(self, symbol, side, motivo, units, price, pnl=None):
        if self.trade_history is None or not units or units <= 0:
            return
        self.trade_history.append(
            symbol=symbol, side=side, motivo=motivo, units=units,
            price=price, pnl=pnl, balance_resultante=self.broker.get_balance(),
        )

    def _apply_buy_fill(self, symbol, filled_units, price_filled, sizing, motivo="apertura"):
        """Registra (o suma a) la posición interna con lo EFECTIVAMENTE
        comprado -- nunca con la cantidad pedida (ver Fase 3c). `motivo`
        distingue una apertura automática (por señal de estrategia) de
        una manual (ver manual_trading.py) en el historial."""
        if filled_units <= 0:
            return
        self.stop_loss[symbol] = sizing["stop_loss"]
        self.take_profit[symbol] = sizing["take_profit"]
        self.internal_positions[symbol] = {"unidades": filled_units, "precio_entrada": price_filled}
        self._record_trade(symbol, "buy", motivo, filled_units, price_filled)

    def _apply_sell_fill(self, symbol, filled_units, price_filled=None, motivo=None):
        """Reduce (o cierra del todo) la posición interna según lo
        EFECTIVAMENTE vendido -- una venta parcial deja el resto abierto
        con el mismo stop loss/take profit."""
        pos = self.internal_positions.get(symbol)
        if not pos or filled_units <= 0:
            return
        entry_price = pos["precio_entrada"]
        remaining = pos["unidades"] - filled_units
        if remaining <= 1e-9:
            self.stop_loss.pop(symbol, None)
            self.take_profit.pop(symbol, None)
            self.internal_positions.pop(symbol, None)
        else:
            pos["unidades"] = remaining
        pnl = (price_filled - entry_price) * filled_units if price_filled is not None else None
        self._record_trade(symbol, "sell", motivo, filled_units, price_filled, pnl=pnl)
        if motivo == "seguro_de_ganancias" and self.profit_lock is not None:
            # La venta ya se ejecutó y el capital resultante quedó en el
            # bróker -- recién ACÁ la ganancia está realmente asegurada
            # (no antes, mientras todavía era una posición abierta).
            self.profit_lock.lock_in(self.broker.get_balance())

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
        for symbol, pending in list(self.pending_order.items()):
            order_id = pending["order_id"]
            try:
                status_order = self.broker.get_order_status(order_id)
            except Exception as e:
                self.log.warning("No se pudo consultar la orden pendiente %s de %s (%s) -- se reintenta el próximo tick",
                                  order_id, symbol, e)
                continue

            status = status_order["status"]
            if status == "open":
                continue

            filled_units = status_order.get("units") or 0
            side = pending["side"]
            if side == "buy":
                self._apply_buy_fill(symbol, filled_units, status_order.get("price"), pending["sizing"],
                                      motivo=pending.get("motivo", "apertura"))
            else:
                self._apply_sell_fill(symbol, filled_units, price_filled=status_order.get("price"),
                                       motivo=pending.get("motivo"))

            self.log.info("Orden pendiente %s de %s se resolvió: %s (%s unidades)", order_id, symbol, status, filled_units)
            del self.pending_order[symbol]

    def process_tick(self, date, price, current_atr, sig, symbol=None):
        """Procesa un tick de UN símbolo (`symbol`, opcional si el motor
        solo maneja uno -- queda implícito). Con varios símbolos, el
        caller invoca esto una vez por símbolo por cada ciclo de polling,
        pasando el `symbol` explícito cada vez."""
        symbol = symbol or self.symbols[0]
        self.ticks_processed += 1

        # 1) Heartbeat: registrar que "llegó" un dato nuevo del feed
        self.heartbeat.beat()

        # 2) Actualizar el precio "de mercado" que ve el bróker ANTES que
        # nada más -- así _mark_to_market ya lo ve, incluso si el
        # kill-switch corta el resto del tick más abajo.
        self.broker.set_price(symbol, price)

        # 3) Chequeo de kill-switch manual antes de cualquier decisión
        if self.kill_switch.is_active():
            self.log.warning("Kill-switch activo (%s) -- no se abren posiciones nuevas este tick",
                              self.kill_switch.reason())
            self.equity_curve.append(self._mark_to_market())
            return

        # 3b) Noticias de alto impacto: siempre alerta; en ventana automatica
        # ademas puede dejar `news_guard.entries_paused()` en True (chequeado
        # mas abajo, junto al resto de condiciones para abrir una posicion).
        if self.news_guard is not None:
            self.news_guard.check()

        # 3c) Si quedó alguna orden abierta/parcial de un tick anterior
        # (de cualquier símbolo), resolverla ANTES de decidir algo nuevo.
        self._resolve_pending_order()

        equity = self._mark_to_market()

        # Reinicia el equity de referencia diario para el chequeo de pérdida diaria
        day_key = date.date() if hasattr(date, "date") else date
        if day_key != self.current_day:
            self.current_day = day_key
            self.day_start_equity = equity

        breaker_active = self.circuit_breaker.check(self.equity_curve, self.day_start_equity, equity)
        profit_lock_hit = self.profit_lock is not None and self.profit_lock.check(equity)
        self.equity_curve.append(equity)

        # Mientras haya una orden todavía sin resolver PARA ESTE SÍMBOLO,
        # no se evalúa nada nuevo sobre él este tick -- evita mandar una
        # segunda orden encima de una que el bróker todavía no terminó de
        # procesar. Otros símbolos no se ven afectados.
        if symbol in self.pending_order:
            if self.ticks_processed % self.reconcile_every == 0:
                self.force_persist()
            return

        broker_positions = self.broker.get_open_positions()
        in_position = symbol in broker_positions

        if profit_lock_hit and not in_position:
            # OJO: bancar con `equity` (efectivo + TODAS las posiciones,
            # de cualquier símbolo), no con `self.broker.get_balance()`
            # (solo efectivo) -- este símbolo puede no tener posición
            # propia y aun así la meta haberse alcanzado por la ganancia
            # no realizada de OTRO símbolo que sí sigue abierto. Bancar
            # solo el efectivo banca un piso más bajo que el equity real
            # que disparó el gatillo, violando la garantía central del
            # trinquete (el piso nunca debería bajar) -- ver README,
            # ronda de auditoría del seguro de ganancias multi-par.
            self.profit_lock.lock_in(equity)
            profit_lock_hit = False

        # 3d) Modo manual/asistido (ver manual_trading.py): si hay una
        # orden manual en cola para este símbolo, se consume acá (una
        # sola vez). Una venta manual siempre se deja pasar más abajo,
        # igual que el stop loss/take profit -- salir nunca está
        # bloqueado. Una compra manual respeta los mismos frenos que una
        # automática (breaker, pausa por noticias, cupo compartido).
        manual_side = self.manual_orders.pop_order(symbol) if self.manual_orders is not None else None

        # 4) Lógica de salida (si hay posición abierta en ESTE símbolo)
        if in_position:
            pos = broker_positions[symbol]
            hit_stop = self.stop_loss.get(symbol) is not None and price <= self.stop_loss[symbol]
            hit_target = self.take_profit.get(symbol) is not None and price >= self.take_profit[symbol]
            strategy_exit = sig == 0
            manual_exit = manual_side == "sell"

            if hit_stop or hit_target or strategy_exit or profit_lock_hit or manual_exit:
                client_order_id = f"{symbol}-sell-{date}"
                order = self.broker.place_order(symbol, "sell", pos["unidades"], client_order_id=client_order_id)
                motivo = ("stop_loss" if hit_stop else "take_profit" if hit_target
                          else "seguro_de_ganancias" if profit_lock_hit
                          else "manual" if manual_exit else "señal_estrategia")
                status = order["status"]
                filled_units = order.get("units") or 0

                if status == "open":
                    self.pending_order[symbol] = {"order_id": order.get("broker_order_id") or order.get("order_id"),
                                                   "side": "sell", "motivo": motivo}
                    self.log.info("Orden de venta de %s (%s) quedó abierta -- se revisará en el próximo tick", symbol, motivo)
                else:
                    self.log.info("Cierre de posición de %s (%s): %s", symbol, motivo, order)
                    self._apply_sell_fill(symbol, filled_units, price_filled=order.get("price"), motivo=motivo)
                    if status == "partially_filled":
                        self.log.warning("Venta parcial en %s: %s/%s unidades -- se sigue adelante con lo efectivamente vendido",
                                          symbol, filled_units, pos["unidades"])
            elif manual_side == "buy":
                self.log.info("Orden manual de compra en %s ignorada -- ya hay una posición abierta en ese símbolo", symbol)

        # 4b) Venta manual pedida sin posición abierta para vender: sin este
        # chequeo, `manual_side == "sell"` quedaba consumido (ver 3d, arriba)
        # y descartado en silencio -- ningún log explicaba qué pasó, a
        # diferencia de todos los demás rechazos manuales (breaker, pausa
        # por noticias, ATR inválido, cupo de posiciones, tamaño no viable),
        # que sí quedan explicados. Un usuario pidiendo vender algo que ya se
        # cerró (ej. por stop loss, en un tick anterior) merece la misma
        # claridad que cualquier otro rechazo.
        elif manual_side == "sell":
            self.log.warning("Orden manual de venta en %s rechazada -- no hay posición abierta para vender", symbol)

        # 5) Lógica de entrada (si no hay posición abierta en este símbolo,
        # el breaker no está activo, y no hay una pausa automática por
        # noticias en curso -- una compra manual respeta los mismos
        # frenos que una automática. El seguro de ganancias NO bloquea
        # entradas -- solo fuerza el cierre para asegurar la ganancia y
        # sigue operando.)
        elif breaker_active or (self.news_guard is not None and self.news_guard.entries_paused()):
            if manual_side == "buy":
                motivo_bloqueo = "el circuit breaker está activo" if breaker_active else "hay una pausa automática por noticias en curso"
                self.log.warning("Orden manual de compra en %s rechazada -- %s", symbol, motivo_bloqueo)
        else:
            manual_entry = manual_side == "buy"
            if sig == 1 or manual_entry:
                if pd.isna(current_atr) or current_atr <= 0:
                    if manual_entry:
                        self.log.warning("Orden manual de compra en %s rechazada -- ATR inválido, no se puede calcular el tamaño de la posición", symbol)
                else:
                    total_open = len(self.broker.get_open_positions())
                    if self.max_positions is not None and total_open >= self.max_positions:
                        self.log.info("Cupo de posiciones simultáneas alcanzado (%d/%d) -- no se abre %s%s",
                                      total_open, self.max_positions, symbol,
                                      " (orden manual)" if manual_entry else " pese a la señal")
                    else:
                        sizing = position_size(self.broker.get_balance(), price, current_atr, self.profile)
                        if sizing["unidades"] > 0 and sizing["viable"] is not False:
                            client_order_id = f"{symbol}-buy-{date}"
                            order = self.broker.place_order(symbol, "buy", sizing["unidades"], client_order_id=client_order_id)
                            status = order["status"]
                            filled_units = order.get("units") or 0
                            motivo_compra = "apertura_manual" if manual_entry and sig != 1 else "apertura"

                            if status == "open":
                                self.pending_order[symbol] = {
                                    "order_id": order.get("broker_order_id") or order.get("order_id"),
                                    "side": "buy", "sizing": sizing, "motivo": motivo_compra,
                                }
                                self.log.info("Orden de compra de %s quedó abierta (0 unidades llenadas todavía) -- "
                                               "se revisará en el próximo tick", symbol)
                            else:
                                self._apply_buy_fill(symbol, filled_units, order.get("price"), sizing, motivo=motivo_compra)
                                if filled_units > 0:
                                    alert_msg = format_signal_alert(
                                        symbol, self.strategy_name, self.profile_name, date, price, sizing
                                    )
                                    self.alert_channel.send(alert_msg)
                                if status == "partially_filled":
                                    self.log.warning("Compra parcial en %s: %s/%s unidades -- se registra la posición con lo efectivamente comprado",
                                                      symbol, filled_units, sizing["unidades"])
                        elif manual_entry:
                            self.log.warning("Orden manual de compra en %s rechazada -- %s", symbol,
                                              sizing.get("motivo_no_viable") or "tamaño de posición no viable")

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
                                      "day_start_equity": self.day_start_equity,
                                      "profit_lock_times_locked": self.profit_lock.times_locked if self.profit_lock else None,
                                      "profit_lock_last_reason": self.profit_lock.last_lock_reason if self.profit_lock else None,
                                      "profit_lock_reference_capital": self.profit_lock.reference_capital if self.profit_lock else None})
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
             trade_history_path: str = None, kill_switch_path: str = ".KILL_SWITCH",
             profit_lock_pct: float = None):
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
    profit_lock = ProfitLock(profit_lock_pct, initial_balance) if profit_lock_pct else None
    engine = _LiveEngine(
        broker, symbol, profile, strategy_name, profile_name,
        ConsoleAlertChannel(), ManualKillSwitch(control_file=kill_switch_path),
        CircuitBreaker(max_drawdown_pct, max_daily_loss_pct),
        Heartbeat(max_staleness_seconds=3600),  # en un loop real, ajustar según frecuencia del feed
        StateStore(path=state_path), reconcile_every, log,
        trade_history=trade_history, profit_lock=profit_lock,
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


def run_live_polling(price_source, symbol, strategy_name: str, profile_name: str,
                      seed_csv, initial_balance: float = 1000.0,
                      poll_interval_seconds: float = 60.0, max_ticks: int = None,
                      state_path: str = "engine_state.json", reconcile_every: int = 10,
                      max_drawdown_pct: float = 15.0, max_daily_loss_pct: float = 5.0,
                      regime_filter: bool = True, multi_timeframe_filter: bool = True,
                      max_history_rows: int = 5000, news_guard=None, trade_history_path: str = None,
                      kill_switch_path: str = ".KILL_SWITCH", profit_lock_pct: float = None,
                      max_positions: int = None, manual_orders_path: str = None):
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

    Multi-símbolo: `symbol` acepta un símbolo suelto (uso de siempre) O
    una lista de símbolos -- en ese caso `seed_csv` debe ser un dict
    `{símbolo: ruta_csv}` (cada símbolo necesita su propio historial). Con
    varios símbolos, todos comparten UNA sola cuenta (mismo bróker, mismo
    capital, mismo circuit breaker/seguro de ganancias/kill-switch/pausa
    por noticias) y un cupo COMPARTIDO de posiciones simultáneas
    (`max_positions`, por defecto el `max_open_positions` del perfil de
    riesgo elegido) -- no un cupo por símbolo. En cada ciclo de polling se
    pide el precio de cada símbolo por separado (son instrumentos
    distintos, no hay forma de ahorrarse esas consultas) y se procesa su
    tick correspondiente.

    `manual_orders_path` (ver manual_trading.py): si se pasa, activa el
    modo manual/asistido -- un archivo de control que otro proceso (ej.
    manual_order.py) puede usar para dejar pedida una compra o venta
    manual de un símbolo. Una compra manual respeta los mismos frenos que
    una automática (breaker, pausa por noticias, cupo compartido); una
    venta manual siempre se deja pasar.
    """
    symbols = [symbol] if isinstance(symbol, str) else list(symbol)
    seed_csvs = {symbols[0]: seed_csv} if isinstance(seed_csv, str) else dict(seed_csv)

    log = get_logger("live_runner")
    log.info("Iniciando runner en vivo (modo paper trading, PRECIOS REALES en vivo) — %s / %s sobre %s",
              strategy_name, profile_name, ", ".join(symbols))

    strategy_fn = get_strategy(strategy_name)
    profile = get_profile(profile_name)
    if max_positions is None:
        max_positions = profile["max_open_positions"]

    dfs = {}
    last_close = {}
    for sym in symbols:
        df_sym = load_csv(seed_csvs[sym])
        dfs[sym] = df_sym
        last_close[sym] = df_sym["close"].iloc[-1]

    broker = PaperBroker(initial_balance=initial_balance)
    trade_history = TradeHistoryLog(trade_history_path) if trade_history_path else None
    profit_lock = ProfitLock(profit_lock_pct, initial_balance) if profit_lock_pct else None
    manual_orders = ManualOrderQueue(manual_orders_path) if manual_orders_path else None
    engine = _LiveEngine(
        broker, symbols, profile, strategy_name, profile_name,
        ConsoleAlertChannel(), ManualKillSwitch(control_file=kill_switch_path),
        CircuitBreaker(max_drawdown_pct, max_daily_loss_pct),
        Heartbeat(max_staleness_seconds=max(poll_interval_seconds * 5, 300)),
        StateStore(path=state_path), reconcile_every, log,
        news_guard=news_guard, trade_history=trade_history, profit_lock=profit_lock,
        max_positions=max_positions, manual_orders=manual_orders,
    )
    shutdown_requested = _make_shutdown_flag(log)

    try:
        while True:
            if max_ticks and engine.ticks_processed >= max_ticks:
                break
            if shutdown_requested["flag"]:
                log.info("Apagado ordenado: guardando estado final antes de salir")
                break

            for sym in symbols:
                try:
                    price = price_source.get_current_price(sym)
                except Exception as e:
                    log.error("No se pudo obtener el precio en vivo de %s (%s) -- se salta este símbolo este tick",
                              sym, e)
                    continue

                df_sym = dfs[sym]
                now = pd.Timestamp.now(tz="UTC").tz_localize(None)
                open_ = float(last_close[sym])
                close_ = float(price)
                df_sym.loc[now] = {
                    "open": open_, "high": max(open_, close_), "low": min(open_, close_),
                    "close": close_, "volume": 0,
                }
                if len(df_sym) > max_history_rows:
                    df_sym = df_sym.iloc[-max_history_rows:]
                    dfs[sym] = df_sym
                last_close[sym] = close_

                signal = strategy_fn(df_sym)
                if regime_filter:
                    strategy_type = STRATEGY_TYPE.get(strategy_name, "tendencia")
                    signal = apply_regime_filter(signal, df_sym, strategy_type=strategy_type)
                if multi_timeframe_filter:
                    signal = apply_multi_timeframe_filter(signal, df_sym)
                current_atr = _atr(df_sym).iloc[-1]
                sig = signal.iloc[-1]

                engine.process_tick(now, close_, current_atr, sig, symbol=sym)

            if poll_interval_seconds > 0:
                time.sleep(poll_interval_seconds)
    finally:
        engine.force_persist()
        log.info("Runner finalizado (precios en vivo). Balance final: %.2f | Posiciones abiertas: %s",
                  broker.get_balance(), broker.get_open_positions())

    return broker


def main():
    parser = argparse.ArgumentParser(description="Runner en vivo (paper trading) del motor")
    parser.add_argument("--csv", type=str, default=None,
                         help="CSV histórico: en modo replay se reproduce entero; en --live-prices se usa para 'calentar' los indicadores. "
                              "Requerido salvo que se use --symbols/--csvs (modo multi-símbolo).")
    parser.add_argument("--strategy", type=str, default="momentum")
    parser.add_argument("--profile", type=str, default="agresivo")
    parser.add_argument("--symbol", type=str, default="ASSET")
    parser.add_argument("--symbols", type=str, default=None,
                         help="[--live-prices] Varios símbolos separados por coma (ej. BTC_USDC,ETH_USDC,LINK_USDC) "
                              "bajo UNA sola cuenta -- mismo capital, mismo circuit breaker/seguro de ganancias/"
                              "kill-switch/pausa por noticias, y un cupo COMPARTIDO de posiciones simultáneas "
                              "(ver --max-positions). Reemplaza a --symbol; requiere --csvs en el mismo orden.")
    parser.add_argument("--csvs", type=str, default=None,
                         help="[--symbols] CSVs de historial separados por coma, en el MISMO ORDEN que --symbols "
                              "(uno por símbolo -- cada uno necesita su propio historial para calentar indicadores).")
    parser.add_argument("--max-positions", type=int, default=None,
                         help="[--symbols] Cupo compartido de posiciones simultáneas entre todos los símbolos. "
                              "Por defecto, el max_open_positions del --profile elegido.")
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
    parser.add_argument("--profit-lock-pct", type=float, default=None,
                         help="Meta de ganancia (%%) que, al alcanzarse, CIERRA la posición abierta para asegurar "
                              "la ganancia (no solo pausa) y sigue operando -- mide la próxima meta desde ese nuevo "
                              "piso, en escalones, sin techo para la ganancia total. Freno simétrico al circuit "
                              "breaker, pero para asegurar lo ganado en vez de proteger contra una pérdida. Sin "
                              "esta opción, desactivado. Ej.: --profit-lock-pct 20 asegura cada +20%%.")
    parser.add_argument("--manual-orders-file", type=str, default=None,
                         help="Ruta de un archivo de control para el modo manual/asistido (ver manual_trading.py) "
                              "-- si se pasa, otro proceso (ej. manual_order.py) puede dejar pedida una compra o "
                              "venta manual de un símbolo, con los mismos frenos de seguridad que una orden "
                              "automática. Sin esta opción, desactivado (comportamiento de siempre).")
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

    multi_symbol = args.symbols is not None
    if multi_symbol:
        if not args.live_prices:
            parser.error("--symbols solo tiene sentido junto con --live-prices (no hay modo multi-símbolo para replay histórico)")
        if not args.csvs:
            parser.error("--symbols requiere --csvs con un CSV por símbolo, en el mismo orden")
        symbol_list = [s.strip() for s in args.symbols.split(",") if s.strip()]
        csv_list = [c.strip() for c in args.csvs.split(",") if c.strip()]
        if len(symbol_list) != len(csv_list):
            parser.error(f"--symbols tiene {len(symbol_list)} símbolos pero --csvs tiene {len(csv_list)} rutas -- deben coincidir")
        symbol_arg = symbol_list
        csv_arg = dict(zip(symbol_list, csv_list))
    else:
        if not args.csv:
            parser.error("--csv es requerido (salvo que se use --symbols/--csvs)")
        symbol_arg = args.symbol
        csv_arg = args.csv

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
            price_source, symbol_arg, args.strategy, args.profile, seed_csv=csv_arg,
            initial_balance=args.capital, poll_interval_seconds=args.poll_interval,
            max_ticks=args.max_ticks, state_path=args.state_path,
            max_drawdown_pct=args.max_drawdown, max_daily_loss_pct=args.max_daily_loss,
            regime_filter=not args.no_regime_filter, multi_timeframe_filter=not args.no_mtf_filter,
            news_guard=news_guard, trade_history_path=args.trade_history_path,
            kill_switch_path=args.kill_switch_file, profit_lock_pct=args.profit_lock_pct,
            max_positions=args.max_positions, manual_orders_path=args.manual_orders_file,
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
            kill_switch_path=args.kill_switch_file, profit_lock_pct=args.profit_lock_pct,
        )


if __name__ == "__main__":
    main()
