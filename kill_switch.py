"""
Control manual del freno de emergencia. Uso:

    python3 kill_switch.py activar "vi algo raro en el mercado, frenar todo"
    python3 kill_switch.py desactivar
    python3 kill_switch.py estado
"""

import sys
from safety import ManualKillSwitch


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    action = sys.argv[1]
    ks = ManualKillSwitch()

    if action == "activar":
        reason = sys.argv[2] if len(sys.argv) > 2 else "Detenido manualmente"
        ks.activate(reason)
        print(f"[!] Kill-switch ACTIVADO. Motivo: {reason}")
        print("El motor no abrirá posiciones nuevas hasta que lo desactives.")
    elif action == "desactivar":
        ks.deactivate()
        print("Kill-switch desactivado. El motor puede volver a operar.")
    elif action == "estado":
        if ks.is_active():
            print(f"[!] Kill-switch ACTIVO. Motivo: {ks.reason()}")
        else:
            print("Kill-switch inactivo. El motor puede operar normalmente.")
    else:
        print(f"Acción desconocida: {action}")
        print(__doc__)


if __name__ == "__main__":
    main()
