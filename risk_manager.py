"""
Gestor de riesgo: dado un capital, un perfil de riesgo, un precio de
entrada y un ATR, calcula cuántas unidades comprar para que si el stop
loss se ejecuta, la pérdida sea exactamente el % de riesgo definido por
el perfil -- nunca más.

También aplica límites realistas de cuenta chica: comisión mínima fija
(muchos brokers cobran un piso por operación, no solo un %) y un valor
mínimo de operación por debajo del cual ni siquiera vale la pena entrar
porque los costos fijos se comen el resultado.
"""


def position_size(capital: float, entry_price: float, atr: float,
                   profile: dict, min_trade_value: float = 0.0,
                   lot_step: float = None,
                   capital_safety_margin_pct: float = 0.005) -> dict:
    """
    Devuelve un dict con: unidades, riesgo_monetario, stop_loss, take_profit,
    y viable (False si la operación no alcanza el mínimo operable).
    """
    if atr <= 0 or entry_price <= 0:
        return {"unidades": 0, "riesgo_monetario": 0, "stop_loss": None,
                "take_profit": None, "viable": False, "motivo_no_viable": "ATR o precio inválido"}

    risk_amount = capital * profile["risk_per_trade"]
    stop_distance = atr * profile["stop_loss_atr_mult"]
    take_profit_distance = atr * profile["take_profit_atr_mult"]

    units = risk_amount / stop_distance
    stop_loss = entry_price - stop_distance
    take_profit = entry_price + take_profit_distance

    # No permitir invertir más capital del que hay disponible. Se deja un
    # margen chico (0.5% por defecto) porque este cálculo usa el precio
    # "limpio", pero el bróker ejecuta con slippage y suma comisión encima:
    # sin margen, cuando este tope es el que termina definiendo las unidades,
    # la orden queda garantizada al rechazo por saldo insuficiente en la
    # ejecución real (visto en vivo con ETH_USDC).
    max_units_by_capital = (capital * (1 - capital_safety_margin_pct)) / entry_price
    units = min(units, max_units_by_capital)

    # Redondear al step de lote del instrumento/bróker (ej. 0.001 BTC, 1 acción)
    if lot_step and lot_step > 0:
        units = (units // lot_step) * lot_step

    trade_value = units * entry_price
    viable = True
    motivo_no_viable = None
    if min_trade_value > 0 and trade_value < min_trade_value:
        viable = False
        motivo_no_viable = (
            f"El valor de la operación (${trade_value:.2f}) queda por debajo "
            f"del mínimo operable (${min_trade_value:.2f}) -- los costos fijos "
            f"se comerían el resultado."
        )

    return {
        "unidades": round(units, 6),
        "riesgo_monetario": round(risk_amount, 2),
        "stop_loss": round(stop_loss, 4),
        "take_profit": round(take_profit, 4),
        "viable": viable,
        "motivo_no_viable": motivo_no_viable,
    }


def effective_commission(trade_value: float, commission_pct: float,
                          min_commission: float = 0.0) -> float:
    """
    Muchos brokers cobran max(comisión %, piso fijo en $). Con cuentas
    chicas, el piso fijo suele ser el que manda -- esta función refleja eso.
    """
    pct_based = trade_value * commission_pct
    return max(pct_based, min_commission)

