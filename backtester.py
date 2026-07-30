"""
Backtester: simula cómo se hubiera comportado una estrategia + perfil de
riesgo sobre datos históricos, aplicando SIEMPRE la regla de riesgo del
perfil (nunca arriesga más del % definido por operación).
"""

import numpy as np
import pandas as pd

from strategies import get_strategy
from risk_manager import position_size, effective_commission
from risk_profiles import get_profile
from safety import validate_ohlcv, CircuitBreaker, ManualKillSwitch
from regime import apply_regime_filter
from multi_timeframe import apply_multi_timeframe_filter


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


class Backtester:
    def __init__(self, df: pd.DataFrame, strategy_name: str, profile_name: str,
                 initial_capital: float = 1000.0, commission_pct: float = 0.001,
                 slippage_pct: float = 0.0005, max_drawdown_pct: float = 15.0,
                 max_daily_loss_pct: float = 5.0, validate: bool = True,
                 min_commission: float = 0.0, min_trade_value: float = 0.0,
                 lot_step: float = None, regime_filter: bool = False,
                 multi_timeframe_filter: bool = False, custom_signal=None):
        self.df = df.copy()
        self.strategy_fn = get_strategy(strategy_name)
        self.profile = get_profile(profile_name)
        self.initial_capital = initial_capital
        self.commission_pct = commission_pct
        self.slippage_pct = slippage_pct
        self.min_commission = min_commission
        self.min_trade_value = min_trade_value
        self.lot_step = lot_step
        self.regime_filter = regime_filter
        self.multi_timeframe_filter = multi_timeframe_filter
        self.custom_signal = custom_signal
        self.strategy_name = strategy_name
        self.profile_name = profile_name
        self.circuit_breaker = CircuitBreaker(max_drawdown_pct, max_daily_loss_pct)
        self.kill_switch = ManualKillSwitch()
        self.trades_rejected_by_minimum = 0

        if validate:
            problems = validate_ohlcv(self.df)
            if problems:
                raise ValueError(
                    "Los datos no pasaron la validación -- corregí esto antes de "
                    f"seguir:\n- " + "\n- ".join(problems)
                )

    def run(self) -> dict:
        df = self.df
        signal = self.custom_signal if self.custom_signal is not None else self.strategy_fn(df)
        if self.regime_filter:
            from strategies import STRATEGY_TYPE
            strategy_type = STRATEGY_TYPE.get(self.strategy_name, "tendencia")
            signal = apply_regime_filter(signal, df, strategy_type=strategy_type)
        if self.multi_timeframe_filter:
            signal = apply_multi_timeframe_filter(signal, df)
        atr = _atr(df)

        capital = self.initial_capital
        equity_curve = []
        trades = []

        in_position = False
        units = 0.0
        entry_price = 0.0
        entry_date = None
        stop_loss = None
        take_profit = None
        entry_limitado_por = None
        entry_pct_capital = None

        day_start_capital = capital
        current_day = None
        breaker_events = []

        for i in range(len(df)):
            price = df["close"].iloc[i]
            date = df.index[i]
            sig = signal.iloc[i]
            current_atr = atr.iloc[i] if not np.isnan(atr.iloc[i]) else 0.0

            # Reinicia el capital de referencia diario para el chequeo de pérdida diaria
            day_key = date.date() if hasattr(date, "date") else date
            if day_key != current_day:
                current_day = day_key
                day_start_capital = capital + (units * price if in_position else 0)

            breaker_active = self.circuit_breaker.check(equity_curve, day_start_capital, capital + (units * price if in_position else 0))
            kill_active = self.kill_switch.is_active()
            breaker_active = breaker_active or kill_active
            if breaker_active and self.circuit_breaker.trip_reason not in [e["motivo"] for e in breaker_events]:
                motivo = f"KILL_SWITCH_MANUAL: {self.kill_switch.reason()}" if kill_active and not self.circuit_breaker.tripped else self.circuit_breaker.trip_reason
                breaker_events.append({"fecha": date, "motivo": motivo})

            buy_price = price * (1 + self.slippage_pct)
            sell_price = price * (1 - self.slippage_pct)

            if not in_position and sig == 1 and current_atr > 0 and not breaker_active:
                sizing = position_size(capital, buy_price, current_atr, self.profile,
                                        min_trade_value=self.min_trade_value, lot_step=self.lot_step)
                if sizing["unidades"] > 0 and sizing["viable"]:
                    units = sizing["unidades"]
                    entry_price = buy_price
                    entry_date = date
                    stop_loss = sizing["stop_loss"]
                    take_profit = sizing["take_profit"]
                    entry_limitado_por = sizing["limitado_por"]
                    trade_value = units * buy_price
                    entry_pct_capital = (trade_value / capital * 100) if capital > 0 else 0.0
                    commission = effective_commission(trade_value, self.commission_pct, self.min_commission)
                    capital -= (trade_value + commission)
                    in_position = True
                elif sizing["unidades"] > 0 and not sizing["viable"]:
                    self.trades_rejected_by_minimum += 1

            elif in_position:
                hit_stop = price <= stop_loss
                hit_target = price >= take_profit
                strategy_exit = sig == 0

                if hit_stop or hit_target or strategy_exit or breaker_active:
                    trade_value = units * sell_price
                    commission = effective_commission(trade_value, self.commission_pct, self.min_commission)
                    proceeds = trade_value - commission
                    capital += proceeds
                    pnl = proceeds - (units * entry_price)
                    motivo = "circuit_breaker" if breaker_active else (
                        "stop_loss" if hit_stop else ("take_profit" if hit_target else "señal_estrategia")
                    )
                    trades.append({
                        "fecha_entrada": entry_date,
                        "fecha_salida": date,
                        "precio_entrada": round(entry_price, 4),
                        "precio_salida": round(sell_price, 4),
                        "unidades": round(units, 6),
                        "pnl": round(pnl, 2),
                        "motivo": motivo,
                        "limitado_por": entry_limitado_por,
                        "pct_capital_al_entrar": round(entry_pct_capital, 2) if entry_pct_capital is not None else None,
                    })
                    in_position = False
                    units = 0.0
                    entry_limitado_por = None
                    entry_pct_capital = None

            mark_to_market = capital + (units * price if in_position else 0)
            equity_curve.append(mark_to_market)

        equity_curve = pd.Series(equity_curve, index=df.index)
        return self._summarize(equity_curve, trades, breaker_events)

    def _summarize(self, equity_curve: pd.Series, trades: list, breaker_events: list = None) -> dict:
        final_capital = equity_curve.iloc[-1]
        total_return_pct = (final_capital / self.initial_capital - 1) * 100

        running_max = equity_curve.cummax()
        drawdown = (equity_curve - running_max) / running_max
        max_drawdown_pct = drawdown.min() * 100

        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] <= 0]
        win_rate = (len(wins) / len(trades) * 100) if trades else 0.0

        gross_profit = sum(t["pnl"] for t in wins)
        gross_loss = abs(sum(t["pnl"] for t in losses))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
        expectancy = (sum(t["pnl"] for t in trades) / len(trades)) if trades else 0.0

        daily_returns = equity_curve.pct_change().dropna()
        sharpe_approx = (
            (daily_returns.mean() / daily_returns.std()) * np.sqrt(252)
            if daily_returns.std() > 0 else 0.0
        )
        downside_returns = daily_returns[daily_returns < 0]
        sortino_approx = (
            (daily_returns.mean() / downside_returns.std()) * np.sqrt(252)
            if len(downside_returns) > 0 and downside_returns.std() > 0 else 0.0
        )

        # Benchmark: comprar al inicio y no tocar nada más
        buy_hold_units = self.initial_capital / self.df["close"].iloc[0]
        buy_hold_final = buy_hold_units * self.df["close"].iloc[-1]
        buy_hold_return_pct = (buy_hold_final / self.initial_capital - 1) * 100

        # Cuántas veces el tope de CONCENTRACIÓN (no el de capital
        # disponible) terminó definiendo el tamaño de una entrada -- ver
        # README, ronda de auditoría de concentración. Sin este tope
        # (max_position_pct_of_capital=1.0), estas mismas operaciones
        # hubieran quedado definidas por el tope de capital disponible, es
        # decir ~99.5% del capital (1 - capital_safety_margin_pct) en vez
        # del % del perfil.
        limitadas_por_concentracion = [t for t in trades if t["limitado_por"] == "concentracion"]
        pct_maximo_concentrado = max((t["pct_capital_al_entrar"] for t in limitadas_por_concentracion), default=0.0)

        return {
            "estrategia": self.strategy_name,
            "perfil_riesgo": self.profile_name,
            "capital_inicial": self.initial_capital,
            "capital_final": round(final_capital, 2),
            "retorno_total_pct": round(total_return_pct, 2),
            "retorno_buy_and_hold_pct": round(buy_hold_return_pct, 2),
            "le_gano_al_buy_and_hold": total_return_pct > buy_hold_return_pct,
            "max_drawdown_pct": round(max_drawdown_pct, 2),
            "num_operaciones": len(trades),
            "win_rate_pct": round(win_rate, 1),
            "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else "inf (sin pérdidas)",
            "expectancy_por_operacion": round(expectancy, 2),
            "sharpe_aprox": round(sharpe_approx, 2),
            "sortino_aprox": round(sortino_approx, 2),
            "equity_curve": equity_curve,
            "trades": trades,
            "circuit_breaker_activado": bool(breaker_events),
            "eventos_circuit_breaker": breaker_events or [],
            "operaciones_rechazadas_por_minimo": self.trades_rejected_by_minimum,
            "operaciones_limitadas_por_concentracion": len(limitadas_por_concentracion),
            "pct_capital_maximo_concentrado": round(pct_maximo_concentrado, 1),
        }
