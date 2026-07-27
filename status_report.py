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

from state_store import StateStore
from trade_history import TradeHistoryLog


def build_status_report(symbol: str, state_path: str, trades_path: str = None,
                         kill_switch_path: str = ".KILL_SWITCH", max_recent: int = 5) -> str:
    lines = [f"=== Estado de {symbol} ==="]

    state = StateStore(path=state_path).load()
    if not state["saved_at"]:
        lines.append(f"Sin estado guardado en {state_path} -- la sesión no llegó a persistir nada todavía.")
    else:
        lines.append(f"Última actualización: {state['saved_at']}")
        lines.append(f"Capital: {state['capital']:.2f}" if state["capital"] is not None else "Capital: (desconocido)")
        if state["positions"]:
            lines.append("Posiciones abiertas:")
            for sym, pos in state["positions"].items():
                lines.append(f"  - {sym}: {pos.get('unidades')} unidades @ {pos.get('precio_entrada')}")
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
    usuario tipee ninguna ruta."""
    sessions = []
    for state_path in sorted(glob.glob("live_state_*.json")):
        base = state_path[len("live_state_"):-len(".json")]
        symbol = base.upper()
        trades_path = f"trades_{base}.csv"
        sessions.append((symbol, state_path, trades_path if os.path.exists(trades_path) else None))
    return sessions


def main():
    parser = argparse.ArgumentParser(description="Reporte de estado de las sesiones de paper trading en vivo")
    parser.add_argument("--symbol", help="Si se pasa junto con --state, reporta solo esa sesión puntual")
    parser.add_argument("--state", help="Ruta del archivo de estado (--state-path del live_runner)")
    parser.add_argument("--trades", help="Ruta del historial de operaciones (--trade-history-path del live_runner)")
    parser.add_argument("--kill-switch", default=".KILL_SWITCH", help="Ruta del archivo de control del kill-switch")
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

    kill_switch_files = {path for _, path, _ in
                          [(s, args.kill_switch, None) for s in sessions]}
    reports = []
    for symbol, state_path, trades_path in sessions:
        reports.append(build_status_report(symbol, state_path, trades_path, args.kill_switch, args.recent))
    print(("\n" + "-" * 50 + "\n").join(reports))

    if len(sessions) > 1 and len(kill_switch_files) == 1:
        print("\n[!] Ojo: todas las sesiones de esta demo comparten el mismo archivo de "
              "kill-switch por defecto (.KILL_SWITCH) -- activar el freno manual pausaría "
              "TODAS a la vez, no una sola. En el motor multi-usuario real (multi_user.py) "
              "esto ya está resuelto (cada usuario tiene su propio archivo); acá, si querés "
              "frenos independientes por sesión, arrancá cada una con --kill-switch-file "
              "propio (ver nota en el README).")


if __name__ == "__main__":
    main()
