"""
Perfiles de riesgo configurables.

Cada perfil define:
- risk_per_trade: % del capital que se arriesga como máximo en una sola operación
- max_open_positions: cuántas posiciones simultáneas permite
- stop_loss_atr_mult: qué tan lejos (en múltiplos de ATR) se ubica el stop loss
- take_profit_atr_mult: qué tan lejos se ubica el take profit
- max_position_pct_of_capital: tope de CONCENTRACIÓN -- ninguna posición
  sola puede pesar más que este % del capital, sin importar qué tan chico
  sea el ATR (ver risk_manager.position_size). Es un freno distinto al
  margen de seguridad por slippage/comisión: ese existe para que la orden
  no se rechace por saldo insuficiente; este existe para que un ATR muy
  chico (activo calmo, o poca historia todavía) no termine convirtiendo
  "arriesgar el 1% del capital" en "apostar el 99% del capital a un solo
  símbolo" -- visto en vivo con LINK_USDC (ver README, ronda de
  auditoría de concentración).
- horizon: "corto" (días), "medio" (semanas), "largo" (meses) -> afecta qué estrategias
  tienen sentido combinar con este perfil
"""

RISK_PROFILES = {
    "conservador": {
        "risk_per_trade": 0.005,      # 0.5% del capital por operación
        "max_open_positions": 3,
        "stop_loss_atr_mult": 2.5,
        "take_profit_atr_mult": 3.0,
        "max_position_pct_of_capital": 0.20,
        "horizon": "largo",
    },
    "moderado": {
        "risk_per_trade": 0.01,       # 1% del capital por operación
        "max_open_positions": 5,
        "stop_loss_atr_mult": 2.0,
        "take_profit_atr_mult": 3.0,
        "max_position_pct_of_capital": 0.30,
        "horizon": "medio",
    },
    "agresivo": {
        "risk_per_trade": 0.02,       # 2% del capital por operación
        "max_open_positions": 8,
        "stop_loss_atr_mult": 1.5,
        "take_profit_atr_mult": 2.5,
        "max_position_pct_of_capital": 0.40,
        "horizon": "corto",
    },
}


def get_profile(name: str) -> dict:
    name = name.lower().strip()
    if name not in RISK_PROFILES:
        raise ValueError(
            f"Perfil '{name}' no existe. Opciones: {list(RISK_PROFILES.keys())}"
        )
    return RISK_PROFILES[name]
