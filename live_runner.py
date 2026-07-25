"""
Runner "en vivo": junta todas las piezas del motor (estrategia, gestor de
riesgo, bróker, alertas, kill-switch, heartbeat, circuit breaker, filtros
de régimen y multi-timeframe) en un solo loop operable.

Corre HOY contra el PaperBroker, "reproduciendo" datos históricos como si
llegaran en vivo, vela por vela -- así se puede probar el flujo completo
de punta a punta sin arriesgar nada. El día que se conecte un bróker real
(ver broker.py -> RipioBrokerAdapter / LibertexBrokerAdapter), el cambio
es reemplazar UNA línea (qué clase de bróker se instancia); el resto del
loop no cambia.

Uso:
    python3 live_runner.py --csv real_data/btc_daily.csv --strategy momentum --profile agresivo
"""

import argparse
import signal as system_signal
import time

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
from reconciliation import reconcile
from regime import apply_regime_filter
from multi_timeframe import apply_multi_timeframe_filter
from app_logger import setup_logging, get_logger


def _atr(df, period=14):
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def _mark_to_market(broker, symbol, price):
    """Capital total = cash + valor de mercado de posiciones abiertas."""
    cash = broker.get_balance()
    positions = broker.get_open_positions()
    if symbol in positions:
        return cash + positions[symbol]["unidades"] * price
    return cash


def run_live(csv_path: str, strategy_name: str, profile_name: str,
             symbol: str = "ASSET", initial_balance: float = 1000.0,
             replay_delay_seconds: float = 0.0, max_ticks: int = None,
             state_path: str = "engine_state.json", reconcile_every: int = 10,
             max_drawdown_pct: float = 15.0, max_daily_loss_pct: float = 5.0,
             regime_filter: bool = True, multi_timeframe_filter: bool = True):
    log = get_logger("live_runner")
    log.info("Iniciando runner en vivo (modo paper trading) — %s / %s sobre %s",
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
    alert_channel = ConsoleAlertChannel()
    kill_switch = ManualKillSwitch()
    circuit_breaker = CircuitBreaker(max_drawdown_pct, max_daily_loss_pct)
    heartbeat = Heartbeat(max_staleness_seconds=3600)  # en un loop real, ajustar según frecuencia del feed
    state_store = StateStore(path=state_path)

    # Restaurar estado previo si existe (stop_loss/take_profit no los sabe
    # el bróker -- viven en nuestro propio registro, por eso hace falta
    # guardarlos y restaurarlos nosotros mismos).
    saved_state = state_store.load()
    stop_loss = saved_state["extra"].get("stop_loss") if saved_state["saved_at"] else None
    take_profit = saved_state["extra"].get("take_profit") if saved_state["saved_at"] else None
    internal_positions = saved_state["positions"] if saved_state["saved_at"] else {}

    ticks_processed = 0
    shutdown_requested = {"flag": False}
    equity_curve = []
    day_start_equity = initial_balance
    current_day = None

    def _handle_shutdown_signal(signum, frame):
        log.warning("Señal de apagado recibida (%s) -- terminando de forma ordenada tras este tick", signum)
        shutdown_requested["flag"] = True

    system_signal.signal(system_signal.SIGINT, _handle_shutdown_signal)
    system_signal.signal(system_signal.SIGTERM, _handle_shutdown_signal)

    for date in df.index:
        if max_ticks and ticks_processed >= max_ticks:
            break
        if shutdown_requested["flag"]:
            log.info("Apagado ordenado: guardando estado final antes de salir")
            state_store.save(internal_positions, capital=broker.get_balance(),
                              extra={"stop_loss": stop_loss, "take_profit": take_profit})
            break
        ticks_processed += 1

        price = df.loc[date, "close"]
        current_atr = atr.loc[date]
        sig = signal.loc[date]

        # 1) Heartbeat: registrar que "llegó" un dato nuevo del feed
        heartbeat.beat()

        # 2) Chequeo de kill-switch manual antes de cualquier decisión
        if kill_switch.is_active():
            log.warning("Kill-switch activo (%s) -- no se abren posiciones nuevas este tick", kill_switch.reason())
            equity_curve.append(_mark_to_market(broker, symbol, price))
            continue

        # 3) Actualizar el precio "de mercado" que ve el bróker
        broker.set_price(symbol, price)
        equity = _mark_to_market(broker, symbol, price)

        # Reinicia el equity de referencia diario para el chequeo de pérdida diaria
        day_key = date.date() if hasattr(date, "date") else date
        if day_key != current_day:
            current_day = day_key
            day_start_equity = equity

        breaker_active = circuit_breaker.check(equity_curve, day_start_equity, equity)
        equity_curve.append(equity)

        broker_positions = broker.get_open_positions()
        in_position = symbol in broker_positions

        # 4) Lógica de salida (si hay posición abierta)
        if in_position:
            pos = broker_positions[symbol]
            hit_stop = stop_loss is not None and price <= stop_loss
            hit_target = take_profit is not None and price >= take_profit
            strategy_exit = sig == 0

            if hit_stop or hit_target or strategy_exit:
                client_order_id = f"{symbol}-sell-{date}"
                order = broker.place_order(symbol, "sell", pos["unidades"], client_order_id=client_order_id)
                motivo = "stop_loss" if hit_stop else ("take_profit" if hit_target else "señal_estrategia")
                log.info("Cierre de posición (%s): %s", motivo, order)
                stop_loss, take_profit = None, None
                internal_positions.pop(symbol, None)

        # 5) Lógica de entrada (si no hay posición abierta y el breaker no está activo)
        elif not breaker_active:
            if sig == 1 and not pd.isna(current_atr) and current_atr > 0:
                sizing = position_size(broker.get_balance(), price, current_atr, profile)
                if sizing["unidades"] > 0 and sizing["viable"] is not False:
                    client_order_id = f"{symbol}-buy-{date}"
                    order = broker.place_order(symbol, "buy", sizing["unidades"], client_order_id=client_order_id)
                    if order["status"] == "filled":
                        stop_loss = sizing["stop_loss"]
                        take_profit = sizing["take_profit"]
                        internal_positions[symbol] = {"unidades": sizing["unidades"], "precio_entrada": order["price"]}
                        alert_msg = format_signal_alert(
                            symbol, strategy_name, profile_name, date, price, sizing
                        )
                        alert_channel.send(alert_msg)

        # 6) Persistir estado y reconciliar cada N ticks (y siempre al final)
        is_last_tick = max_ticks and ticks_processed >= max_ticks
        if ticks_processed % reconcile_every == 0 or is_last_tick:
            state_store.save(internal_positions, capital=broker.get_balance(),
                              extra={"stop_loss": stop_loss, "take_profit": take_profit})
            report = reconcile(internal_positions, broker.get_open_positions())
            if not report["coincide"]:
                log.error("Desfasaje detectado entre el estado interno y el bróker: %s", report)

        if replay_delay_seconds > 0:
            time.sleep(replay_delay_seconds)

    log.info("Runner finalizado. Balance final: %.2f | Posiciones abiertas: %s",
              broker.get_balance(), broker.get_open_positions())
    return broker


def main():
    parser = argparse.ArgumentParser(description="Runner en vivo (paper trading) del motor")
    parser.add_argument("--csv", type=str, required=True)
    parser.add_argument("--strategy", type=str, default="momentum")
    parser.add_argument("--profile", type=str, default="agresivo")
    parser.add_argument("--symbol", type=str, default="ASSET")
    parser.add_argument("--capital", type=float, default=1000.0)
    parser.add_argument("--max-ticks", type=int, default=None,
                         help="Limitar a los primeros N días, útil para pruebas rápidas")
    parser.add_argument("--delay", type=float, default=0.0,
                         help="Segundos de pausa entre cada 'vela' simulada (0 = lo más rápido posible)")
    parser.add_argument("--state-path", type=str, default="engine_state.json",
                         help="Ruta del archivo de estado persistente (posiciones, stop/take profit)")
    parser.add_argument("--max-drawdown", type=float, default=15.0)
    parser.add_argument("--max-daily-loss", type=float, default=5.0)
    parser.add_argument("--no-regime-filter", action="store_true",
                         help="Desactiva el filtro de régimen de mercado")
    parser.add_argument("--no-mtf-filter", action="store_true",
                         help="Desactiva el filtro multi-timeframe")
    args = parser.parse_args()

    setup_logging(level="INFO")
    run_live(
        args.csv, args.strategy, args.profile, symbol=args.symbol,
        initial_balance=args.capital, replay_delay_seconds=args.delay,
        max_ticks=args.max_ticks, state_path=args.state_path,
        max_drawdown_pct=args.max_drawdown, max_daily_loss_pct=args.max_daily_loss,
        regime_filter=not args.no_regime_filter,
        multi_timeframe_filter=not args.no_mtf_filter,
    )


if __name__ == "__main__":
    main()
