"""
Perfiles de riesgo configurables.

Cada perfil define:
- risk_per_trade: % del capital que se arriesga como máximo en una sola operación
- max_open_positions: cuántas posiciones simultáneas permite
- stop_loss_atr_mult: qué tan lejos (en múltiplos de ATR) se ubica el stop loss
- take_profit_atr_mult: qué tan lejos se ubica el take profit
- horizon: "corto" (días), "medio" (semanas), "largo" (meses) -> afecta qué estrategias
  tienen sentido combinar con este perfil
"""

RISK_PROFILES = {
    "conservador": {
        "risk_per_trade": 0.005,      # 0.5% del capital por operación
        "max_open_positions": 3,
        "stop_loss_atr_mult": 2.5,
        "take_profit_atr_mult": 3.0,
        "horizon": "largo",
    },
    "moderado": {
        "risk_per_trade": 0.01,       # 1% del capital por operación
        "max_open_positions": 5,
        "stop_loss_atr_mult": 2.0,
        "take_profit_atr_mult": 3.0,
        "horizon": "medio",
    },
    "agresivo": {
        "risk_per_trade": 0.02,       # 2% del capital por operación
        "max_open_positions": 8,
        "stop_loss_atr_mult": 1.5,
        "take_profit_atr_mult": 2.5,
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
