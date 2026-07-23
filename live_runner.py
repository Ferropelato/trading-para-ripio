"""
Runner "en vivo": junta todas las piezas del motor (estrategia, gestor de
riesgo, bróker, alertas, kill-switch, heartbeat) en un solo loop operable.

Corre HOY contra el PaperBroker, "reproduciendo" datos históricos como si
llegaran en vivo, vela por vela -- así se puede probar el flujo completo
de punta a punta sin arriesgar nada. El día que se conecte un bróker real
(ver broker.py -> LibertexBrokerAdapter), el cambio es reemplazar UNA
línea (qué clase de bróker se instancia); el resto del loop no cambia.

Uso:
    python3 live_runner.py --csv real_data/btc_daily.csv --strategy momentum --profile agresivo
"""

import argparse
import time

from data_utils import load_csv
from strategies import get_strategy
from risk_manager import position_size
from risk_profiles import get_profile
from broker import PaperBroker
from alerts import ConsoleAlertChannel, format_signal_alert
from safety import ManualKillSwitch
from health import Heartbeat
from app_logger import setup_logging, get_logger


def _atr(df, period=14):
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    import pandas as pd
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def run_live(csv_path: str, strategy_name: str, profile_name: str,
             symbol: str = "ASSET", initial_balance: float = 1000.0,
             replay_delay_seconds: float = 0.0, max_ticks: int = None):
    log = get_logger("live_runner")
    log.info("Iniciando runner en vivo (modo paper trading) — %s / %s sobre %s",
              strategy_name, profile_name, symbol)

    df = load_csv(csv_path)
    strategy_fn = get_strategy(strategy_name)
    profile = get_profile(profile_name)
    signal = strategy_fn(df)
    atr = _atr(df)

    broker = PaperBroker(initial_balance=initial_balance)
    alert_channel = ConsoleAlertChannel()
    kill_switch = ManualKillSwitch()
    heartbeat = Heartbeat(max_staleness_seconds=3600)  # en un loop real, ajustar según frecuencia del feed

    in_position = False
    stop_loss = None
    take_profit = None

    ticks_processed = 0
    for date in df.index:
        if max_ticks and ticks_processed >= max_ticks:
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
            continue

        # 3) Actualizar el precio "de mercado" que ve el bróker
        broker.set_price(symbol, price)

        positions = broker.get_open_positions()
        in_position = symbol in positions

        # 4) Lógica de salida (si hay posición abierta)
        if in_position:
            pos = positions[symbol]
            hit_stop = stop_loss is not None and price <= stop_loss
            hit_target = take_profit is not None and price >= take_profit
            strategy_exit = sig == 0

            if hit_stop or hit_target or strategy_exit:
                order = broker.place_order(symbol, "sell", pos["unidades"])
                motivo = "stop_loss" if hit_stop else ("take_profit" if hit_target else "señal_estrategia")
                log.info("Cierre de posición (%s): %s", motivo, order)
                stop_loss, take_profit = None, None

        # 5) Lógica de entrada (si no hay posición abierta)
        else:
            import pandas as pd
            if sig == 1 and not pd.isna(current_atr) and current_atr > 0:
                sizing = position_size(broker.get_balance(), price, current_atr, profile)
                if sizing["unidades"] > 0 and sizing["viable"] is not False:
                    order = broker.place_order(symbol, "buy", sizing["unidades"])
                    if order["status"] == "filled":
                        stop_loss = sizing["stop_loss"]
                        take_profit = sizing["take_profit"]
                        alert_msg = format_signal_alert(
                            symbol, strategy_name, profile_name, date, price, sizing
                        )
                        alert_channel.send(alert_msg)

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
    args = parser.parse_args()

    setup_logging(level="INFO")
    run_live(args.csv, args.strategy, args.profile, symbol=args.symbol,
              initial_balance=args.capital, replay_delay_seconds=args.delay,
              max_ticks=args.max_ticks)


if __name__ == "__main__":
    main()
