"""
Punto de entrada. Ejemplo de uso:

    python3 run_backtest.py --strategy momentum --profile agresivo
    python3 run_backtest.py --strategy contraccion_volatilidad --profile moderado --csv mi_data.csv
"""

import argparse
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import yaml

from data_utils import generate_synthetic_data, load_csv
from backtester import Backtester
from risk_profiles import RISK_PROFILES
from strategies import STRATEGIES
from validation import walk_forward_validate
from health import check_data_gaps
from experiment_log import log_experiment
from report import generate_html_report
from app_logger import setup_logging, get_logger

log = get_logger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Motor de backtesting de estrategias")
    parser.add_argument("--config", type=str, default=None,
                         help="Ruta a un archivo YAML con los parámetros (ver config_ejemplo.yaml). "
                              "Los flags de línea de comandos pasados además de este sobreescriben lo que diga el YAML.")
    parser.add_argument("--strategy", choices=list(STRATEGIES.keys()), default=None)
    parser.add_argument("--profile", choices=list(RISK_PROFILES.keys()), default=None)
    parser.add_argument("--capital", type=float, default=None)
    parser.add_argument("--csv", type=str, default=None,
                         help="Ruta a CSV con columnas date,open,high,low,close,volume")
    parser.add_argument("--out", type=str, default=None)
    parser.add_argument("--export-trades", type=str, default=None,
                         help="Si se pasa, guarda el log completo de operaciones en este CSV")
    parser.add_argument("--walk-forward", action="store_true", default=None,
                         help="Corre además una validación in-sample/out-of-sample")
    parser.add_argument("--max-drawdown", type=float, default=None,
                         help="%% de drawdown desde el pico que activa el circuit breaker")
    parser.add_argument("--max-daily-loss", type=float, default=None,
                         help="%% de pérdida en un día que activa el circuit breaker")
    parser.add_argument("--log-experiment", action="store_true", default=None,
                         help="Guarda este resultado en experiments.csv para comparar corridas")
    parser.add_argument("--notas", type=str, default=None,
                         help="Nota libre para identificar este experimento en el log")
    parser.add_argument("--html-report", type=str, default=None,
                         help="Si se pasa, genera un reporte HTML autocontenido en esta ruta")
    parser.add_argument("--log-level", type=str, default="INFO",
                         help="Nivel de logging: DEBUG, INFO, WARNING, ERROR")
    args = parser.parse_args()

    setup_logging(level=args.log_level)

    # Defaults base
    params = {
        "strategy": "momentum", "profile": "moderado", "capital": 1000.0,
        "csv": None, "out": "reporte.png", "export_trades": None,
        "walk_forward": False, "max_drawdown": 15.0, "max_daily_loss": 5.0,
        "log_experiment": False, "notas": "", "html_report": None,
    }

    # El YAML pisa los defaults
    if args.config:
        with open(args.config) as f:
            yaml_params = yaml.safe_load(f) or {}
        params.update(yaml_params)
        log.info("Configuración cargada desde %s", args.config)

    # Los flags explícitos de CLI pisan tanto los defaults como el YAML
    cli_overrides = {
        "strategy": args.strategy, "profile": args.profile, "capital": args.capital,
        "csv": args.csv, "out": args.out, "export_trades": args.export_trades,
        "walk_forward": args.walk_forward, "max_drawdown": args.max_drawdown,
        "max_daily_loss": args.max_daily_loss, "log_experiment": args.log_experiment,
        "notas": args.notas, "html_report": args.html_report,
    }
    for key, value in cli_overrides.items():
        if value is not None:
            params[key] = value

    if params["csv"]:
        df = load_csv(params["csv"])
    else:
        log.warning("No se pasó CSV: usando datos SINTÉTICOS de demostración.")
        df = generate_synthetic_data()

    gaps = check_data_gaps(df, max_gap_days=5)
    if gaps:
        print(f"[!] Se encontraron {len(gaps)} huecos sospechosos en los datos (>5 días sin velas):")
        for g in gaps[:5]:
            print(f"  - entre {g['fecha_antes_del_hueco'].date()} y {g['fecha_despues_del_hueco'].date()} ({g['dias_de_hueco']} días)")

    bt = Backtester(df, strategy_name=params["strategy"], profile_name=params["profile"],
                     initial_capital=params["capital"], max_drawdown_pct=params["max_drawdown"],
                     max_daily_loss_pct=params["max_daily_loss"])
    result = bt.run()

    log.info("Backtest completo: %s / %s -> retorno %s%%", params["strategy"], params["profile"],
              result["retorno_total_pct"])
    print("\n=== RESULTADO DEL BACKTEST ===")
    for key in ["estrategia", "perfil_riesgo", "capital_inicial", "capital_final",
                "retorno_total_pct", "retorno_buy_and_hold_pct", "le_gano_al_buy_and_hold",
                "max_drawdown_pct", "num_operaciones", "win_rate_pct", "profit_factor",
                "expectancy_por_operacion", "sharpe_aprox", "sortino_aprox",
                "circuit_breaker_activado"]:
        print(f"{key}: {result[key]}")

    if result["circuit_breaker_activado"]:
        print("\n[!] El circuit breaker se activó durante el backtest:")
        for ev in result["eventos_circuit_breaker"]:
            print(f"  - {ev['fecha']}: {ev['motivo']}")

    if params["export_trades"] and result["trades"]:
        pd.DataFrame(result["trades"]).to_csv(params["export_trades"], index=False)
        print(f"\nLog de operaciones guardado en: {params['export_trades']}")

    if params["walk_forward"]:
        print("\n=== VALIDACIÓN WALK-FORWARD (in-sample vs out-of-sample) ===")
        wf = walk_forward_validate(df, params["strategy"], params["profile"], params["capital"])
        print(f"Retorno in-sample:  {wf['in_sample']['retorno_total_pct']}%")
        print(f"Retorno out-sample: {wf['out_sample']['retorno_total_pct']}%")
        if wf["advertencia"]:
            print(f"[!] {wf['advertencia']}")

    if params["log_experiment"]:
        log_experiment(result, fuente_datos=(params["csv"] or "sintético"), notas=params["notas"])
        print("\nExperimento guardado en experiments.csv")

    if params["html_report"]:
        generate_html_report(result, output_path=params["html_report"], fuente_datos=(params["csv"] or "sintético"))
        print(f"Reporte HTML guardado en: {params['html_report']}")

    equity = result["equity_curve"]
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(equity.index, equity.values, linewidth=1.5)
    ax.set_title(f"Curva de capital — {params['strategy']} / {params['profile']}")
    ax.set_xlabel("Fecha")
    ax.set_ylabel("Capital")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(params["out"], dpi=150)
    print(f"\nGráfico guardado en: {params['out']}")


if __name__ == "__main__":
    main()
