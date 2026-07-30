"""
Informe consolidado de resultados reales de las sesiones de paper
trading en vivo -- pensado para volver a correr cuando haga falta un
snapshot fresco (ej. justo antes de una reunión), no para mirarlo una
sola vez. Mide dos cosas distintas a propósito:

1. Fiabilidad de ejecución: cuánto tiempo lleva corriendo cada sesión,
   cuántas fallas reales de red absorbió sin caerse (rate limits,
   errores de conexión), cuántas pausas automáticas por noticias reales
   se activaron, y si la reconciliación interna siempre coincidió con el
   bróker.
2. Resultado de las operaciones cerradas -- pero esto NO se presenta como
   evidencia de rentabilidad: unos días de paper trading no dicen nada
   sobre si una estrategia funciona a largo plazo. Se muestra igual, sin
   maquillar, porque ocultarlo sería peor que un número negativo.

Usa el mismo criterio de descubrimiento que status_report.py
(`live_state_*.json` en la carpeta) para no duplicar esa lógica.
"""

import glob
import os
from datetime import datetime, timezone

from app_logger import setup_logging

# Momento real en que se relanzaron las 4 sesiones en vivo con el fix de
# agregación de ticks (ver README, ronda de la causa raíz del bug de
# concentración). Antes de este momento, el ATR de CUALQUIER sesión que
# llevara más de ~10-15 minutos corriendo estaba corrompido -- medía el
# rango de precio entre polls de 45-90 segundos, no la volatilidad
# diaria real -- y eso producía stop loss/take profit artificialmente
# ajustados. No es una sospecha: se confirmó mirando la duración real de
# las operaciones (muchas cerraban en menos de 2 minutos, con perfiles
# pensados para un horizonte de días/semanas). El resultado neto de
# CUALQUIER operación cerrada antes de este momento no refleja la
# estrategia -- refleja el bug. Se deja como una fecha fija, no
# recalculada, porque es un hecho histórico de este pilot, no un
# parámetro que deba ajustarse.
ATR_FIX_DEPLOYED_AT = datetime(2026, 7, 30, 10, 21, 0, tzinfo=timezone.utc)

# state_store/trade_history/tax_export se importan DENTRO de
# generate_report(), no acá arriba -- get_logger() (que esos módulos
# llaman a nivel de módulo) auto-configura el logging la primera vez que
# se usa y se auto-bloquea después (ver app_logger._CONFIGURED). Importar
# acá arriba fijaría el nivel de logging del proceso entero apenas se
# importa este archivo, incluso para quien solo quiera reusar
# generate_report() como librería -- un efecto secundario que no
# corresponde tener solo por importar el módulo.


def _discover_sessions(directory: str = ".") -> list:
    sessions = []
    for state_path in sorted(glob.glob(os.path.join(directory, "live_state_*.json"))):
        base = os.path.basename(state_path)[len("live_state_"):-len(".json")]
        trades_path = os.path.join(directory, f"trades_{base}.csv")
        log_path = os.path.join(directory, f"live_log_{base}.txt")
        sessions.append({
            "label": base,
            "state_path": state_path,
            "trades_path": trades_path if os.path.exists(trades_path) else None,
            "log_path": log_path if os.path.exists(log_path) else None,
        })
    return sessions


def _log_stats(log_path: str) -> dict:
    # encoding="utf-8", errors="replace" -- los logs de las sesiones en vivo
    # se escriben redirigiendo stdout desde la consola de Windows (cp1252),
    # no con un encoding explícito, así que un caracter con tilde puede
    # llegar corrompido acá. Los patrones de búsqueda evitan a propósito
    # cualquier caracter con tilde (comparan solo el tramo ASCII antes de
    # la tilde) para no fallar por lo mismo que ya rompió dos veces antes
    # en este proyecto (ver README, rondas de encoding).
    with open(log_path, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    if not lines:
        return None
    return {
        "first_ts": lines[0].split(" | ")[0].strip(),
        "last_ts": lines[-1].split(" | ")[0].strip(),
        "rate_limits": sum(1 for l in lines if "429" in l),
        "conn_errors": sum(1 for l in lines if "NameResolutionError" in l or "Error de conexi" in l),
        "news_pauses": sum(1 for l in lines if "Pausa autom" in l and "de entradas nuevas activada" in l),
        "reconciliations_ok": sum(1 for l in lines if "Reconciliaci" in l and "OK" in l and "coincide" in l),
        "reconciliations_bad": sum(1 for l in lines if "Desfasaje detectado" in l),
    }


def _parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def generate_report(directory: str = ".") -> str:
    from state_store import StateStore
    from trade_history import TradeHistoryLog, pair_trades
    from tax_export import tax_summary
    from risk_manager import concentration_warnings

    sessions = _discover_sessions(directory)
    lines = ["# Informe de resultados reales -- paper trading en vivo", ""]
    lines.append(f"Generado: {datetime.now(timezone.utc).isoformat(timespec='seconds')} UTC")
    lines.append("")

    if not sessions:
        lines.append("No se encontró ninguna sesión (`live_state_*.json`) en esta carpeta.")
        return "\n".join(lines)

    total_trades, total_wins, total_losses, total_net = 0, 0, 0, 0.0
    pre_fix_trades, pre_fix_net = 0, 0.0
    hubo_operaciones_afectadas = False

    for s in sessions:
        state = StateStore(path=s["state_path"]).load()
        history = TradeHistoryLog(s["trades_path"]).load_all() if s["trades_path"] else []
        trades = pair_trades(history)
        summary = tax_summary(trades) if trades else None
        log_stats = _log_stats(s["log_path"]) if s["log_path"] else None

        lines.append(f"## Sesión: {s['label']}")
        lines.append("")
        if state["saved_at"] and state.get("capital") is not None:
            lines.append(f"- Capital actual: USDC {state['capital']:.2f}")
        for aviso in concentration_warnings(state.get("capital"), state.get("positions") or {}):
            lines.append(f"- [!] Concentración: {aviso}")
        if summary:
            wins = summary["cantidad_operaciones_ganadoras"]
            losses = summary["cantidad_operaciones_perdedoras"]
            net = summary["resultado_neto_usd"]
            lines.append(f"- Operaciones cerradas: {len(trades)} ({wins} ganadoras, {losses} perdedoras)")
            lines.append(f"- Resultado neto: USDC {net:+.2f}")
            total_trades += len(trades)
            total_wins += wins
            total_losses += losses
            total_net += net

            pre = [t for t in trades if t["fecha_salida"] and _parse_ts(t["fecha_salida"]) < ATR_FIX_DEPLOYED_AT]
            if pre:
                hubo_operaciones_afectadas = True
                pre_net_sesion = sum(t["pnl"] for t in pre)
                pre_fix_trades += len(pre)
                pre_fix_net += pre_net_sesion
                lines.append(
                    f"  - De esas, {len(pre)} se cerraron ANTES del fix del bug de agregación de "
                    f"ticks (ver nota al final) -- neto de esas: USDC {pre_net_sesion:+.2f}, "
                    f"no representativo de la estrategia."
                )
        else:
            lines.append("- Sin operaciones cerradas todavía.")
        if log_stats:
            lines.append(f"- Corriendo desde: {log_stats['first_ts']} (última actividad: {log_stats['last_ts']})")
            lines.append(f"- Rate limits (429) absorbidos sin caerse: {log_stats['rate_limits']}")
            lines.append(f"- Errores de conexión absorbidos sin caerse: {log_stats['conn_errors']}")
            lines.append(f"- Pausas automáticas por noticias reales activadas: {log_stats['news_pauses']}")
            lines.append(f"- Reconciliaciones internas OK: {log_stats['reconciliations_ok']} "
                          f"(desfasajes detectados: {log_stats['reconciliations_bad']})")
        lines.append("")

    lines.append("## Total consolidado")
    lines.append("")
    lines.append(f"- Operaciones cerradas en total: {total_trades} ({total_wins} ganadoras, {total_losses} perdedoras)")
    lines.append(f"- Resultado neto consolidado: USDC {total_net:+.2f}")
    if hubo_operaciones_afectadas:
        post_fix_trades = total_trades - pre_fix_trades
        post_fix_net = total_net - pre_fix_net
        lines.append(
            f"  - De ese total, {pre_fix_trades} operaciones (neto USDC {pre_fix_net:+.2f}) se cerraron "
            f"ANTES del fix del bug de agregación de ticks -- ver nota abajo, no representativas."
        )
        lines.append(
            f"  - Después del fix: {post_fix_trades} operaciones, neto USDC {post_fix_net:+.2f} "
            f"-- esto sí refleja la estrategia con el motor corregido (muestra todavía chica)."
        )
    lines.append("")
    lines.append(
        "**Nota:** este informe mide fiabilidad de ejecución (el motor corre, "
        "persiste entre reinicios, se recupera de fallas reales de red, respeta "
        "sus propios frenos de seguridad) -- no rentabilidad. Unos pocos días "
        "de paper trading no dicen nada sobre si una estrategia funciona a "
        "largo plazo, y nunca se presentó como tal."
    )
    if hubo_operaciones_afectadas:
        lines.append("")
        lines.append(
            f"**Nota sobre el bug de agregación de ticks (corregido {ATR_FIX_DEPLOYED_AT.isoformat()}):** "
            "hasta ese momento, los ticks en vivo se agregaban como una vela diaria nueva cada "
            "45-90 segundos -- con la ventana rodante de 14 períodos del ATR, en 10-15 minutos de "
            "sesión corrida el ATR terminaba midiendo el rango de precio típico entre polls, no de un "
            "día real, y eso producía stop loss/take profit artificialmente ajustados (confirmado "
            "mirando la duración real de las operaciones: muchas cerraban en menos de 2 minutos, con "
            "perfiles pensados para un horizonte de días/semanas). El resultado neto de las operaciones "
            "marcadas arriba como \"antes del fix\" refleja ese bug, no la estrategia -- se muestra "
            "igual, sin ocultarlo, por la misma política de honestidad del resto de este informe."
        )
    return "\n".join(lines)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Informe consolidado de resultados reales de paper trading")
    parser.add_argument("--dir", default=".", help="Carpeta donde buscar las sesiones (por defecto, la actual)")
    parser.add_argument("--output", "-o", default=None,
                         help="Si se pasa, escribe el informe en este archivo con UTF-8 explícito, en vez de "
                              "solo imprimirlo -- redirigir la salida con '>' en Windows puede guardar los "
                              "acentos con el codepage de la consola en vez de UTF-8, dejando el archivo mal "
                              "codificado. Con --output no hace falta redirigir nada.")
    args = parser.parse_args()

    # WARNING, no INFO: este script imprime un documento pensado para
    # guardarse tal cual -- los "Estado restaurado desde..." que
    # StateStore loguea en INFO son ruido acá, no señal (a diferencia de
    # status_report.py, pensado para mirarse en la terminal, donde ese
    # mismo mensaje sí es información útil).
    setup_logging(level="WARNING")
    report = generate_report(args.dir)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report + "\n")
        print(f"Informe guardado en {args.output} (UTF-8)")
    else:
        print(report)


if __name__ == "__main__":
    main()
