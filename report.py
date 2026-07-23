"""
Genera un reporte HTML autocontenido (un solo archivo, sin dependencias
externas) con las métricas del backtest y el gráfico de la curva de
capital embebido como imagen. Sirve para archivar o compartir un
resultado sin tener que volver a correr el script.
"""

import base64
import io
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def _equity_chart_base64(equity_curve, strategy_name: str, profile_name: str) -> str:
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.plot(equity_curve.index, equity_curve.values, linewidth=1.5, color="#1d9e75")
    ax.set_title(f"Curva de capital — {strategy_name} / {profile_name}")
    ax.set_xlabel("Fecha")
    ax.set_ylabel("Capital")
    ax.grid(alpha=0.3)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140)
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")


def generate_html_report(result: dict, output_path: str = "reporte.html",
                          fuente_datos: str = "") -> None:
    img_b64 = _equity_chart_base64(result["equity_curve"], result["estrategia"], result["perfil_riesgo"])

    metric_rows = ""
    metrics_display = [
        ("Capital inicial", f"${result['capital_inicial']:.2f}"),
        ("Capital final", f"${result['capital_final']:.2f}"),
        ("Retorno de la estrategia", f"{result['retorno_total_pct']}%"),
        ("Retorno buy & hold (referencia)", f"{result['retorno_buy_and_hold_pct']}%"),
        ("¿Le ganó al buy & hold?", "Sí" if result["le_gano_al_buy_and_hold"] else "No"),
        ("Drawdown máximo", f"{result['max_drawdown_pct']}%"),
        ("Cantidad de operaciones", result["num_operaciones"]),
        ("Win rate", f"{result['win_rate_pct']}%"),
        ("Profit factor", result["profit_factor"]),
        ("Expectancy por operación", f"${result['expectancy_por_operacion']}"),
        ("Sharpe aprox.", result["sharpe_aprox"]),
        ("Sortino aprox.", result["sortino_aprox"]),
        ("¿Se activó el circuit breaker?", "Sí" if result["circuit_breaker_activado"] else "No"),
        ("Operaciones rechazadas por mínimo operable", result.get("operaciones_rechazadas_por_minimo", 0)),
    ]
    for label, value in metrics_display:
        metric_rows += f"<tr><td>{label}</td><td><b>{value}</b></td></tr>\n"

    warning_html = ""
    if not result["le_gano_al_buy_and_hold"]:
        warning_html = (
            '<div style="background:#FAECE7;border-left:4px solid #D85A30;padding:12px 16px;'
            'margin:16px 0;color:#4A1B0C;">'
            '⚠ Esta estrategia rindió menos que simplemente comprar y mantener en este período. '
            'No implica que sea inútil en todo contexto, pero no la uses con capital real sin '
            'entender por qué perdió contra la alternativa más simple.</div>'
        )

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Reporte de backtest — {result['estrategia']} / {result['perfil_riesgo']}</title>
<style>
  body {{ font-family: -apple-system, Arial, sans-serif; max-width: 800px; margin: 40px auto; color: #2C2C2A; }}
  h1 {{ font-size: 22px; font-weight: 500; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 16px; }}
  td {{ padding: 8px 12px; border-bottom: 1px solid #D3D1C7; }}
  td:first-child {{ color: #5F5E5A; }}
  img {{ max-width: 100%; margin-top: 20px; border: 1px solid #D3D1C7; border-radius: 8px; }}
  .footer {{ color: #888780; font-size: 12px; margin-top: 24px; }}
</style>
</head>
<body>
  <h1>Reporte de backtest: {result['estrategia']} / {result['perfil_riesgo']}</h1>
  <p>Fuente de datos: {fuente_datos or 'no especificada'}</p>
  {warning_html}
  <table>{metric_rows}</table>
  <img src="data:image/png;base64,{img_b64}" alt="Curva de capital">
  <p class="footer">
    Generado el {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}.
    Este reporte es un resultado de backtest histórico, no una garantía de
    rendimiento futuro.
  </p>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
