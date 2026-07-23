"""
Exportación de ganancias/pérdidas realizadas para uso impositivo.

IMPORTANTE: esto NO es asesoramiento fiscal. Es un registro ordenado de
lo que hizo el motor, pensado para entregarle a un contador -- no
reemplaza su criterio profesional sobre cómo declarar cada caso ante
AFIP u otro organismo. Las reglas impositivas cambian y dependen de la
situación de cada persona (residencia fiscal, tipo de instrumento,
si hay convenio de doble imposición, etc.).
"""

import pandas as pd


def export_tax_report(trades: list, output_path: str = "reporte_impositivo.csv",
                       ars_per_usd: float = None) -> pd.DataFrame:
    """
    Genera un CSV con cada operación cerrada: fecha de entrada, fecha de
    salida, resultado en USD (o la moneda de los datos originales), y si
    se pasa `ars_per_usd`, también el equivalente en ARS al tipo de cambio
    indicado (útil como referencia, no como conversión oficial válida para
    la declaración -- eso lo define la normativa vigente al momento).
    """
    if not trades:
        df = pd.DataFrame(columns=[
            "fecha_entrada", "fecha_salida", "dias_en_posicion",
            "precio_entrada", "precio_salida", "unidades", "resultado_usd",
            "tipo", "motivo_cierre",
        ])
        df.to_csv(output_path, index=False)
        return df

    rows = []
    for t in trades:
        entrada = pd.to_datetime(t["fecha_entrada"]) if t["fecha_entrada"] else None
        salida = pd.to_datetime(t["fecha_salida"])
        dias = (salida - entrada).days if entrada is not None else None

        row = {
            "fecha_entrada": entrada.date() if entrada is not None else "",
            "fecha_salida": salida.date(),
            "dias_en_posicion": dias,
            "precio_entrada": t["precio_entrada"],
            "precio_salida": t["precio_salida"],
            "unidades": t["unidades"],
            "resultado_usd": t["pnl"],
            "tipo": "ganancia" if t["pnl"] > 0 else "pérdida",
            "motivo_cierre": t["motivo"],
        }
        if ars_per_usd:
            row["resultado_ars_referencial"] = round(t["pnl"] * ars_per_usd, 2)
        rows.append(row)

    df = pd.DataFrame(rows)
    df.to_csv(output_path, index=False)
    return df


def tax_summary(trades: list, ars_per_usd: float = None) -> dict:
    """Resumen agregado del año/período para llevarle directo al contador."""
    if not trades:
        return {"ganancia_total_usd": 0, "perdida_total_usd": 0, "resultado_neto_usd": 0,
                "cantidad_operaciones_ganadoras": 0, "cantidad_operaciones_perdedoras": 0}

    ganancias = [t["pnl"] for t in trades if t["pnl"] > 0]
    perdidas = [t["pnl"] for t in trades if t["pnl"] <= 0]

    summary = {
        "ganancia_total_usd": round(sum(ganancias), 2),
        "perdida_total_usd": round(sum(perdidas), 2),
        "resultado_neto_usd": round(sum(t["pnl"] for t in trades), 2),
        "cantidad_operaciones_ganadoras": len(ganancias),
        "cantidad_operaciones_perdedoras": len(perdidas),
    }
    if ars_per_usd:
        summary["resultado_neto_ars_referencial"] = round(summary["resultado_neto_usd"] * ars_per_usd, 2)

    return summary
