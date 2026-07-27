"""
Control manual del freno de emergencia. Uso:

    python3 kill_switch.py activar "vi algo raro en el mercado, frenar todo"
    python3 kill_switch.py desactivar
    python3 kill_switch.py estado

Si corrés varias sesiones en paralelo, cada una con su propio archivo de
control (ver `--kill-switch-file` en live_runner.py), pasale el mismo acá
con `--file` para frenar SOLO esa sesión -- sin `--file`, este comando
actúa sobre el genérico `.KILL_SWITCH`, que una sesión arrancada con uno
propio ya no está mirando:

    python3 kill_switch.py activar "..." --file .KILL_SWITCH_ETH_USDC
"""

import argparse
from safety import ManualKillSwitch


def main():
    parser = argparse.ArgumentParser(description="Control manual del kill-switch")
    parser.add_argument("action", choices=["activar", "desactivar", "estado"])
    parser.add_argument("reason", nargs="?", default="Detenido manualmente",
                         help="[activar] Motivo del freno (ponelo entre comillas si tiene espacios)")
    parser.add_argument("--file", default=".KILL_SWITCH",
                         help="Archivo de control a usar -- debe coincidir con el --kill-switch-file "
                              "de la sesión que querés frenar (por defecto: .KILL_SWITCH)")
    args = parser.parse_args()

    ks = ManualKillSwitch(control_file=args.file)

    if args.action == "activar":
        ks.activate(args.reason)
        print(f"[!] Kill-switch ACTIVADO ({args.file}). Motivo: {args.reason}")
        print("El motor no abrirá posiciones nuevas hasta que lo desactives.")
    elif args.action == "desactivar":
        ks.deactivate()
        print(f"Kill-switch desactivado ({args.file}). El motor puede volver a operar.")
    elif args.action == "estado":
        if ks.is_active():
            print(f"[!] Kill-switch ACTIVO ({args.file}). Motivo: {ks.reason()}")
        else:
            print(f"Kill-switch inactivo ({args.file}). El motor puede operar normalmente.")


if __name__ == "__main__":
    main()
