"""
Reporte de estado en texto plano de las sesiones de paper trading en
vivo -- pensado para que el propio usuario lo corra cuando quiera ("¿cómo
está esto ahora?") sin tener que ir a revisar logs a mano.

No inventa nada: lee directamente el archivo de estado persistente
(state_store.py) y el historial de operaciones (trade_history.py) de cada
sesión -- los mismos archivos que ya arma live_runner.py mientras corre.

Uso sin argumentos: busca en la carpeta actual todos los `live_state_*.json`
y muestra el estado de cada uno (más su `trades_*.csv` si existe con el
mismo nombre base):

    python status_report.py

Para una sesión puntual con rutas propias:

    python status_report.py --symbol BTC_USDC --state live_state_btc_usdc.json --trades trades_btc_usdc.csv
"""

import argparse
import glob
import os
from datetime import datetime

from state_store import StateStore
from trade_history import TradeHistoryLog
from risk_manager import concentration_warnings

# Si el estado no se actualizó hace más de esto, la sesión probablemente
# está muerta (proceso caído, máquina reiniciada) y nadie lo notó. El
# umbral es generoso a propósito: las sesiones de poll lento persisten
# cada varios minutos, y una falsa alarma constante entrena a ignorar
# la alerta (que es peor que no tenerla).
STALE_SESSION_MINUTES = 30


def _session_staleness_minutes(saved_at: str) -> float | None:
    """Minutos desde la última persistencia (saved_at es UTC naive,
    formato isoformat de state_store). None si no se puede parsear."""
    try:
        saved = datetime.fromisoformat(saved_at)
    except (TypeError, ValueError):
        return None
    return (datetime.utcnow() - saved).total_seconds() / 60.0


def build_status_report(symbol: str, state_path: str, trades_path: str = None,
                         kill_switch_path: str = ".KILL_SWITCH", max_recent: int = 5) -> str:
    lines = [f"=== Estado de {symbol} ==="]

    state = StateStore(path=state_path).load()
    if not state["saved_at"]:
        lines.append(f"Sin estado guardado en {state_path} -- la sesión no llegó a persistir nada todavía.")
    else:
        lines.append(f"Última actualización: {state['saved_at']}")
        staleness = _session_staleness_minutes(state["saved_at"])
        if staleness is not None and staleness > STALE_SESSION_MINUTES:
            lines.append(f"  [!] SESIÓN POSIBLEMENTE MUERTA: el estado no se actualiza hace "
                          f"{staleness:.0f} minutos (umbral: {STALE_SESSION_MINUTES}). Verificá que el "
                          f"proceso siga corriendo y reinicialo si hace falta.")
        lines.append(f"Capital: {state['capital']:.2f}" if state["capital"] is not None else "Capital: (desconocido)")
        if state["positions"]:
            lines.append("Posiciones abiertas:")
            for sym, pos in state["positions"].items():
                lines.append(f"  - {sym}: {pos.get('unidades')} unidades @ {pos.get('precio_entrada')}")
            for aviso in concentration_warnings(state["capital"], state["positions"]):
                lines.append(f"  [!] Concentración: {aviso}")
        else:
            lines.append("Posiciones abiertas: ninguna")
        pending = (state.get("extra") or {}).get("pending_order")
        if pending:
            lines.append(f"Orden pendiente sin resolver: {pending}")

    lines.append(f"Kill-switch ({kill_switch_path}): {'ACTIVO' if os.path.exists(kill_switch_path) else 'inactivo'}")

    if trades_path and os.path.exists(trades_path):
        history = TradeHistoryLog(trades_path).load_all()
        closed = [r for r in history if r["side"] == "sell"]
        pnls = [float(r["pnl"]) for r in closed if r["pnl"] not in ("", None)]
        wins = [p for p in pnls if p >= 0]
        losses = [p for p in pnls if p < 0]
        net = sum(pnls)
        lines.append(f"Historial: {len(closed)} operaciones cerradas "
                      f"({len(wins)} ganadoras, {len(losses)} perdedoras), neto {net:+.2f}")
        if history:
            recientes = history[-max_recent:]
            lines.append(f"Últimas {len(recientes)} operaciones:")
            for row in recientes:
                pnl_txt = f", pnl {float(row['pnl']):+.2f}" if row["pnl"] not in ("", None) else ""
                lines.append(f"  {row['timestamp']}: {row['side']} {row['units']} @ {row['price']} "
                              f"({row['motivo']}{pnl_txt})")
        else:
            lines.append("Historial: todavía no hay operaciones registradas.")
    else:
        lines.append("Historial: no se indicó (o no existe todavía) un archivo de historial para esta sesión.")

    return "\n".join(lines)


def _discover_sessions():
    """Busca `live_state_<algo>.json` en la carpeta actual y empareja cada
    uno con `trades_<algo>.csv` si existe -- sin necesidad de que el
    usuario tipee ninguna ruta.

    Para el kill-switch asume la convención `.KILL_SWITCH_<SIMBOLO>` (la
    que usa `--kill-switch-file` en live_runner.py) -- OJO: el archivo de
    control solo existe en disco mientras el freno está ACTIVO, así que su
    ausencia no dice nada sobre qué ruta está mirando la sesión, solo que
    no está pausada. Si una sesión se arrancó SIN pasar --kill-switch-file
    (con el genérico .KILL_SWITCH de antes), este reporte igual va a
    consultar la ruta por convención y puede no coincidir con la real --
    para confirmarlo con certeza, mirá el comando con el que arrancó esa
    sesión."""
    sessions = []
    for state_path in sorted(glob.glob("live_state_*.json")):
        base = state_path[len("live_state_"):-len(".json")]
        symbol = base.upper()
        trades_path = f"trades_{base}.csv"
        kill_switch = f".KILL_SWITCH_{symbol}"
        sessions.append((symbol, state_path, trades_path if os.path.exists(trades_path) else None, kill_switch))
    return sessions


def main():
    parser = argparse.ArgumentParser(description="Reporte de estado de las sesiones de paper trading en vivo")
    parser.add_argument("--symbol", help="Si se pasa junto con --state, reporta solo esa sesión puntual")
    parser.add_argument("--state", help="Ruta del archivo de estado (--state-path del live_runner)")
    parser.add_argument("--trades", help="Ruta del historial de operaciones (--trade-history-path del live_runner)")
    parser.add_argument("--kill-switch", default=".KILL_SWITCH",
                         help="Ruta del archivo de control del kill-switch (solo se usa si además pasás --state)")
    parser.add_argument("--recent", type=int, default=5, help="Cuántas operaciones recientes mostrar")
    args = parser.parse_args()

    if args.state:
        print(build_status_report(args.symbol or "sesión", args.state, args.trades,
                                   args.kill_switch, args.recent))
        return

    sessions = _discover_sessions()
    if not sessions:
        print("No se encontró ningún live_state_*.json en esta carpeta -- "
              "¿estás parado en el directorio del proyecto, con alguna sesión corrida al menos una vez?")
        return

    reports = []
    for symbol, state_path, trades_path, kill_switch_path in sessions:
        reports.append(build_status_report(symbol, state_path, trades_path, kill_switch_path, args.recent))
    print(("\n" + "-" * 50 + "\n").join(reports))

    if len(sessions) > 1:
        print("\nNota: el kill-switch de arriba asume que cada sesión arrancó con "
              "--kill-switch-file .KILL_SWITCH_<SÍMBOLO> (la convención actual). Si alguna "
              "sesión sigue corriendo con el genérico .KILL_SWITCH de antes de esta ronda, "
              "reiniciala con --kill-switch-file para que quede aislada de las demás.")


if __name__ == "__main__":
    main()
