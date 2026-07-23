"""
Backtesting de portafolio (múltiples activos a la vez) con un límite de
exposición ajustado por correlación.

El problema que resuelve: si tenés abiertas posiciones en dos activos que
en el fondo se mueven juntos (ej. BTC y ETH, o dos acciones del mismo
sector), el riesgo real combinado es MAYOR que la simple suma de los
riesgos individuales calculados por separado -- porque si el mercado se
da vuelta, las dos posiciones pierden al mismo tiempo, no de forma
independiente. Este módulo reduce el tamaño de una posición nueva si ya
hay posiciones abiertas fuertemente correlacionadas con ella.
"""

import numpy as np
import pandas as pd

from strategies import get_strategy
from risk_manager import position_size, effective_commission
from risk_profiles import get_profile
from safety import validate_ohlcv, CircuitBreaker


def compute_return_correlation(price_dict: dict, lookback: int = None) -> pd.DataFrame:
    """
    Matriz de correlación de retornos diarios entre activos. Si se pasa
    `lookback`, usa solo los últimos N días (correlación reciente, más
    relevante que la correlación de todo el historial para decidir riesgo
    hoy). Los activos se alinean por fecha (intersección de índices).
    """
    returns = {}
    for name, df in price_dict.items():
        returns[name] = df["close"].pct_change()

    returns_df = pd.DataFrame(returns).dropna(how="all")
    if lookback:
        returns_df = returns_df.tail(lookback)

    return returns_df.corr()


class PortfolioBacktester:
    """
    Corre la misma estrategia + perfil de riesgo sobre varios activos a la
    vez, compartiendo un único pool de capital, y reduce el tamaño de
    nuevas posiciones cuando ya hay exposición abierta en activos
    correlacionados por encima de `correlation_threshold`.
    """

    def __init__(self, price_dict: dict, strategy_name: str, profile_name: str,
                 initial_capital: float = 1000.0, correlation_threshold: float = 0.6,
                 correlation_lookback: int = 60, commission_pct: float = 0.001,
                 slippage_pct: float = 0.0005, validate: bool = True):
        self.assets = list(price_dict.keys())
        self.data = {name: df.copy() for name, df in price_dict.items()}
        self.strategy_fn = get_strategy(strategy_name)
        self.profile = get_profile(profile_name)
        self.strategy_name = strategy_name
        self.profile_name = profile_name
        self.initial_capital = initial_capital
        self.correlation_threshold = correlation_threshold
        self.correlation_lookback = correlation_lookback
        self.commission_pct = commission_pct
        self.slippage_pct = slippage_pct

        if validate:
            for name, df in self.data.items():
                problems = validate_ohlcv(df)
                if problems:
                    raise ValueError(f"Datos de '{name}' no pasaron validación:\n- " + "\n- ".join(problems))

        # Fechas comunes a todos los activos (intersección)
        common_dates = None
        for df in self.data.values():
            common_dates = df.index if common_dates is None else common_dates.intersection(df.index)
        self.common_dates = common_dates.sort_values()

        if len(self.common_dates) < 60:
            raise ValueError(
                f"Los activos solo comparten {len(self.common_dates)} fechas en común -- "
                f"muy pocas para un backtest de portafolio útil. Verificá que cubran el mismo período."
            )

    def _atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        high, low, close = df["high"], df["low"], df["close"]
        prev_close = close.shift(1)
        tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        return tr.rolling(period).mean()

    def run(self) -> dict:
        signals = {name: self.strategy_fn(df) for name, df in self.data.items()}
        atrs = {name: self._atr(df) for name, df in self.data.items()}

        capital = self.initial_capital
        positions = {name: None for name in self.assets}  # None o dict con detalles de la posición abierta
        equity_curve = []
        trades = []
        scaled_down_events = []

        for date in self.common_dates:
            # 1) Calcular correlación reciente entre activos con datos hasta hoy
            recent_prices = {name: df.loc[df.index <= date] for name, df in self.data.items()}
            if all(len(p) >= 20 for p in recent_prices.values()):
                corr_matrix = compute_return_correlation(recent_prices, lookback=self.correlation_lookback)
            else:
                corr_matrix = None

            open_assets = [name for name, pos in positions.items() if pos is not None]

            # 2) Revisar cierres primero (stop loss / take profit / señal)
            for name in self.assets:
                pos = positions[name]
                if pos is None:
                    continue
                price = self.data[name].loc[date, "close"] if date in self.data[name].index else None
                if price is None:
                    continue
                sig = signals[name].loc[date] if date in signals[name].index else 1

                hit_stop = price <= pos["stop_loss"]
                hit_target = price >= pos["take_profit"]
                strategy_exit = sig == 0

                if hit_stop or hit_target or strategy_exit:
                    sell_price = price * (1 - self.slippage_pct)
                    trade_value = pos["unidades"] * sell_price
                    commission = effective_commission(trade_value, self.commission_pct)
                    proceeds = trade_value - commission
                    capital += proceeds
                    pnl = proceeds - (pos["unidades"] * pos["entry_price"])
                    trades.append({
                        "activo": name,
                        "fecha_entrada": pos["entry_date"],
                        "fecha_salida": date,
                        "precio_entrada": round(pos["entry_price"], 4),
                        "precio_salida": round(sell_price, 4),
                        "unidades": round(pos["unidades"], 6),
                        "pnl": round(pnl, 2),
                        "motivo": "stop_loss" if hit_stop else ("take_profit" if hit_target else "señal_estrategia"),
                        "escalado_por_correlacion": pos.get("escalado_por_correlacion", False),
                    })
                    positions[name] = None

            open_assets = [name for name, pos in positions.items() if pos is not None]

            # 3) Revisar aperturas nuevas
            for name in self.assets:
                if positions[name] is not None:
                    continue
                if date not in signals[name].index or date not in atrs[name].index:
                    continue
                sig = signals[name].loc[date]
                current_atr = atrs[name].loc[date]
                if sig != 1 or pd.isna(current_atr) or current_atr <= 0:
                    continue

                price = self.data[name].loc[date, "close"]
                buy_price = price * (1 + self.slippage_pct)

                sizing = position_size(capital, buy_price, current_atr, self.profile)
                if sizing["unidades"] <= 0:
                    continue

                # Ajuste por correlación: si ya hay posiciones abiertas en
                # activos correlacionados por encima del umbral, reducir el
                # tamaño de la posición nueva proporcionalmente a la
                # correlación promedio con esas posiciones.
                scale_factor = 1.0
                scaled = False
                if corr_matrix is not None and open_assets:
                    correlations_with_open = []
                    for open_name in open_assets:
                        if open_name in corr_matrix.index and name in corr_matrix.columns:
                            c = corr_matrix.loc[open_name, name]
                            if not pd.isna(c) and c >= self.correlation_threshold:
                                correlations_with_open.append(c)
                    if correlations_with_open:
                        avg_corr = sum(correlations_with_open) / len(correlations_with_open)
                        scale_factor = max(0.0, 1 - avg_corr)  # más correlación -> menos tamaño
                        scaled = True

                units = sizing["unidades"] * scale_factor
                if units <= 0:
                    if scaled:
                        scaled_down_events.append({
                            "fecha": date, "activo": name,
                            "motivo": "Correlación con posiciones abiertas demasiado alta -- posición evitada",
                        })
                    continue

                trade_value = units * buy_price
                if trade_value > capital:
                    continue

                commission = effective_commission(trade_value, self.commission_pct)
                capital -= (trade_value + commission)
                positions[name] = {
                    "entry_price": buy_price,
                    "entry_date": date,
                    "unidades": units,
                    "stop_loss": sizing["stop_loss"],
                    "take_profit": sizing["take_profit"],
                    "escalado_por_correlacion": scaled,
                }
                if scaled:
                    scaled_down_events.append({
                        "fecha": date, "activo": name,
                        "motivo": f"Tamaño reducido a {scale_factor:.0%} del original por correlación con {open_assets}",
                    })

            # 4) Marcar a mercado el equity total del portafolio
            mtm = capital
            for name, pos in positions.items():
                if pos is not None and date in self.data[name].index:
                    mtm += pos["unidades"] * self.data[name].loc[date, "close"]
            equity_curve.append(mtm)

        equity_curve = pd.Series(equity_curve, index=self.common_dates)
        return self._summarize(equity_curve, trades, scaled_down_events)

    def _summarize(self, equity_curve: pd.Series, trades: list, scaled_down_events: list) -> dict:
        final_capital = equity_curve.iloc[-1]
        total_return_pct = (final_capital / self.initial_capital - 1) * 100
        running_max = equity_curve.cummax()
        drawdown = (equity_curve - running_max) / running_max
        max_drawdown_pct = drawdown.min() * 100

        wins = [t for t in trades if t["pnl"] > 0]
        win_rate = (len(wins) / len(trades) * 100) if trades else 0.0

        return {
            "activos": self.assets,
            "estrategia": self.strategy_name,
            "perfil_riesgo": self.profile_name,
            "capital_inicial": self.initial_capital,
            "capital_final": round(final_capital, 2),
            "retorno_total_pct": round(total_return_pct, 2),
            "max_drawdown_pct": round(max_drawdown_pct, 2),
            "num_operaciones": len(trades),
            "win_rate_pct": round(win_rate, 1),
            "operaciones_escaladas_por_correlacion": sum(1 for t in trades if t.get("escalado_por_correlacion")),
            "eventos_ajuste_correlacion": scaled_down_events,
            "equity_curve": equity_curve,
            "trades": trades,
        }
