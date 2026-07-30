"""
Evidencia sobre el tope de concentración (ver README, ronda de auditoría
de concentración -- encontrado en vivo con LINK_USDC): corre un backtest
real sobre los 6 pares nuevos (LINK_USDC, LTC_USDC, UNI_USDC, WLD_USDC,
RPC_USDC, RIF_USDC -- los mismos que la sesión `new_pairs` en vivo, mismo
`strategy`/`profile`) y reporta cuántas veces `max_position_pct_of_capital`
terminó siendo el freno que definió una entrada, y qué tan grande hubiera
quedado esa posición sin el tope (el freno de respaldo, capital
disponible, deja pasar hasta ~99.5% del capital).

No es una comparación "antes vs después" simulada -- se corre UNA sola
vez, con el código actual (el tope ya está activo), y se lee directo del
campo `limitado_por` que `risk_manager.position_size()` devuelve por cada
operación. Esto mide qué tan seguido este freno importa de verdad sobre
datos reales, no solo que "no rompe nada" (eso ya lo cubren los tests).

Correr: python backtest_concentration_report.py
"""

from backtester import Backtester
from data_utils import load_csv
from app_logger import setup_logging, get_logger

log = get_logger(__name__)

PARES = {
    "LINK_USDC": "real_data/link_usdc_daily.csv",
    "LTC_USDC": "real_data/ltc_usdc_daily.csv",
    "UNI_USDC": "real_data/uni_usdc_daily.csv",
    "WLD_USDC": "real_data/wld_usdc_daily.csv",
    "RPC_USDC": "real_data/rpc_usdc_daily.csv",
    "RIF_USDC": "real_data/rif_usdc_daily.csv",
}

# Mismo strategy/profile que la sesión new_pairs en vivo -- para que este
# reporte sea evidencia sobre el mismo comportamiento que está corriendo
# de verdad, no un escenario aparte.
ESTRATEGIA = "momentum"
PERFIL = "moderado"


def generate_report() -> str:
    lines = ["# Evidencia del tope de concentración -- backtest sobre los 6 pares nuevos", ""]
    lines.append(f"Estrategia: {ESTRATEGIA} / Perfil: {PERFIL} (mismo que la sesión `new_pairs` en vivo)")
    lines.append("")

    total_ops, total_limitadas, pct_max_global = 0, 0, 0.0

    for symbol, csv_path in PARES.items():
        df = load_csv(csv_path)
        result = Backtester(df, ESTRATEGIA, PERFIL, initial_capital=1000.0).run()

        n_ops = result["num_operaciones"]
        n_limitadas = result["operaciones_limitadas_por_concentracion"]
        pct_max = result["pct_capital_maximo_concentrado"]

        total_ops += n_ops
        total_limitadas += n_limitadas
        pct_max_global = max(pct_max_global, pct_max)

        lines.append(f"## {symbol}")
        lines.append("")
        lines.append(f"- Operaciones totales: {n_ops}")
        if n_ops == 0:
            lines.append("- Sin operaciones en este período -- nada que medir.")
        else:
            pct_limitadas = n_limitadas / n_ops * 100
            lines.append(f"- Limitadas por el tope de concentración: {n_limitadas} ({pct_limitadas:.1f}%)")
            if n_limitadas > 0:
                lines.append(
                    f"- Sin el tope, la más grande de esas hubiera llegado a ~{pct_max:.1f}% del "
                    f"capital (el freno de respaldo, capital disponible, deja pasar hasta ~99.5%)."
                )
        lines.append("")

    lines.append("## Total consolidado")
    lines.append("")
    lines.append(f"- Operaciones totales (los 6 pares): {total_ops}")
    if total_ops > 0:
        lines.append(
            f"- Limitadas por el tope de concentración: {total_limitadas} "
            f"({total_limitadas / total_ops * 100:.1f}%)"
        )
    lines.append(f"- Concentración máxima que se hubiera visto sin el tope: ~{pct_max_global:.1f}% del capital")
    lines.append("")
    lines.append(
        "**Nota:** esto mide sobre datos históricos (2024-2026, Yahoo Finance), no sobre lo que "
        "efectivamente operó la sesión en vivo -- sirve para dimensionar qué tan seguido este freno "
        "importa en la práctica para estos 6 activos, no como resultado de trading real."
    )
    return "\n".join(lines)


def main():
    setup_logging(level="WARNING")
    print(generate_report())


if __name__ == "__main__":
    main()
