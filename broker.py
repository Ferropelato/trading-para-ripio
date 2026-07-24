"""
Capa de abstracción de bróker: una interfaz común para que conectar a
Libertex, Binance, o cualquier otro bróker/exchange sea cuestión de
escribir un adaptador nuevo (una clase que implemente BrokerBase), sin
tocar el motor de estrategias ni el backtester.

Incluye:
- BrokerBase: la interfaz (contrato) que cualquier bróker debe cumplir.
- PaperBroker: una implementación funcional para simular operaciones
  sobre datos históricos o en memoria, sin plata real -- útil para
  probar el motor "como si" estuviera operando en vivo, pero sin riesgo.
- LibertexBrokerAdapter: un ESQUELETO documentado de cómo se vería un
  adaptador real. No está conectado a la API real de Libertex (acá no
  hay credenciales ni acceso de red a ese dominio) -- marca claramente
  dónde iría cada llamada real.
"""

from abc import ABC, abstractmethod
from datetime import datetime

from app_logger import get_logger

log = get_logger(__name__)


class BrokerBase(ABC):
    """Contrato que cualquier bróker (real o simulado) debe cumplir."""

    @abstractmethod
    def get_current_price(self, symbol: str) -> float:
        """Devuelve el último precio conocido para el símbolo."""
        raise NotImplementedError

    @abstractmethod
    def get_balance(self) -> float:
        """Devuelve el capital disponible en la cuenta."""
        raise NotImplementedError

    @abstractmethod
    def place_order(self, symbol: str, side: str, units: float) -> dict:
        """
        Coloca una orden de mercado. side es 'buy' o 'sell'. Devuelve un
        dict con al menos: {"status", "symbol", "side", "units", "price", "timestamp"}.
        """
        raise NotImplementedError

    @abstractmethod
    def get_open_positions(self) -> dict:
        """Devuelve las posiciones abiertas actuales: {symbol: {unidades, precio_entrada}}."""
        raise NotImplementedError


class PaperBroker(BrokerBase):
    """
    Bróker simulado ("paper trading"): mantiene un balance y posiciones en
    memoria, y ejecuta órdenes instantáneamente al precio que se le pasa
    (con slippage configurable), sin ninguna conexión externa. Sirve para
    probar el flujo completo motor -> decisión -> "ejecución" -> registro,
    de punta a punta, antes de conectar cualquier bróker real.
    """

    def __init__(self, initial_balance: float = 1000.0, slippage_pct: float = 0.0005,
                 commission_pct: float = 0.001):
        self.balance = initial_balance
        self.slippage_pct = slippage_pct
        self.commission_pct = commission_pct
        self.positions = {}  # symbol -> {"unidades": float, "precio_entrada": float}
        self._current_prices = {}  # symbol -> último precio conocido (se setea con set_price)
        self.order_history = []
        self._processed_client_order_ids = {}  # client_order_id -> resultado ya devuelto

    def set_price(self, symbol: str, price: float) -> None:
        """Actualiza el precio 'de mercado' conocido para un símbolo (simula el feed de datos)."""
        self._current_prices[symbol] = price

    def get_current_price(self, symbol: str) -> float:
        if symbol not in self._current_prices:
            raise ValueError(f"No hay precio cargado para '{symbol}'. Llamá a set_price() primero.")
        return self._current_prices[symbol]

    def get_balance(self) -> float:
        return self.balance

    def get_open_positions(self) -> dict:
        return dict(self.positions)

    def place_order(self, symbol: str, side: str, units: float, client_order_id: str = None) -> dict:
        """
        client_order_id: identificador que genera EL LLAMADOR (no el
        bróker), único por intento lógico de orden. Si se pasa y ya fue
        procesado antes, devuelve el resultado original en vez de
        ejecutar la orden de nuevo -- esto es lo que evita que un
        reintento de red (la orden se ejecutó pero la respuesta se
        perdió, y el código de arriba reintenta) dispare la misma compra
        o venta dos veces.
        """
        if client_order_id and client_order_id in self._processed_client_order_ids:
            log.info("client_order_id '%s' ya procesado antes -- devolviendo resultado "
                      "original, no se ejecuta de nuevo", client_order_id)
            return self._processed_client_order_ids[client_order_id]

        result = self._place_order_impl(symbol, side, units)

        if client_order_id:
            self._processed_client_order_ids[client_order_id] = result

        return result

    def _place_order_impl(self, symbol: str, side: str, units: float) -> dict:
        if side not in ("buy", "sell"):
            raise ValueError("side debe ser 'buy' o 'sell'")
        if units <= 0:
            raise ValueError("units debe ser positivo")

        price = self.get_current_price(symbol)
        exec_price = price * (1 + self.slippage_pct) if side == "buy" else price * (1 - self.slippage_pct)
        trade_value = units * exec_price
        commission = trade_value * self.commission_pct

        if side == "buy":
            total_cost = trade_value + commission
            if total_cost > self.balance:
                order = {
                    "status": "rejected", "motivo": "Saldo insuficiente",
                    "symbol": symbol, "side": side, "units": units,
                    "timestamp": datetime.utcnow().isoformat(),
                }
                self.order_history.append(order)
                log.warning("Orden rechazada por saldo insuficiente: %s %s %s", side, units, symbol)
                return order

            self.balance -= total_cost
            existing = self.positions.get(symbol)
            if existing:
                total_units = existing["unidades"] + units
                avg_price = (existing["unidades"] * existing["precio_entrada"] + units * exec_price) / total_units
                self.positions[symbol] = {"unidades": total_units, "precio_entrada": avg_price}
            else:
                self.positions[symbol] = {"unidades": units, "precio_entrada": exec_price}

        else:  # sell
            existing = self.positions.get(symbol)
            if not existing or existing["unidades"] < units:
                order = {
                    "status": "rejected", "motivo": "No hay suficientes unidades para vender",
                    "symbol": symbol, "side": side, "units": units,
                    "timestamp": datetime.utcnow().isoformat(),
                }
                self.order_history.append(order)
                log.warning("Orden de venta rechazada: no hay posición suficiente en %s", symbol)
                return order

            proceeds = trade_value - commission
            self.balance += proceeds
            remaining = existing["unidades"] - units
            if remaining <= 1e-9:
                del self.positions[symbol]
            else:
                self.positions[symbol]["unidades"] = remaining

        order = {
            "status": "filled", "symbol": symbol, "side": side, "units": units,
            "price": round(exec_price, 4), "commission": round(commission, 4),
            "timestamp": datetime.utcnow().isoformat(),
        }
        self.order_history.append(order)
        log.info("Orden ejecutada: %s %s %s @ %.4f", side, units, symbol, exec_price)
        return order


class RipioBrokerAdapter(BrokerBase):
    """
    ESQUELETO de adaptador para Ripio Trade -- a diferencia de Libertex,
    Ripio SÍ tiene una API pública, real y documentada para esto
    (apidocs.ripio.com / apidocs.ripiotrade.co). Lo que sigue está basado
    en su documentación pública, pero no se pudo probar en vivo desde
    este sandbox por no tener acceso de red a su dominio.

    Datos confirmados de su documentación oficial:
      - Base URL: https://api.ripio.com/trade/... (rutas públicas y privadas)
      - Autenticación: API Token + Secret Key, generados desde tu cuenta
        en Ripio Trade (sección API de tu perfil) -- NUNCA hardcodear acá,
        usar variables de entorno (ver .env.example).
      - Las rutas privadas requieren un header "Timestamp" (milisegundos)
        y una firma HMAC calculada con el Secret Key -- el detalle EXACTO
        del algoritmo de firma (qué campos se concatenan, qué header lleva
        la firma) hay que confirmarlo mirando los ejemplos de código
        oficiales en github.com/ripio/trade antes de implementar el envío
        real, para no adivinar mal un detalle criptográfico.
      - Los tokens de API tienen permisos separados: Lectura, Compra/Venta,
        Retiros de criptomonedas. Para este bot, generar un token con SOLO
        Lectura + Compra/Venta -- NUNCA darle permiso de retiro.
      - Endpoint público de ejemplo (sin autenticación) mencionado en su
        documentación: GET https://api.ripio.com/trade/public/server-time

    Pasos para completar esto de verdad (desde la otra PC, con internet):
      1. Crear/usar tu cuenta de Ripio, generar el API Token + Secret Key
         con permisos de Lectura + Compra/Venta únicamente.
      2. Guardar esas credenciales en .env (RIPIO_API_TOKEN, RIPIO_API_SECRET).
      3. Revisar github.com/ripio/trade para el ejemplo exacto de cómo
         armar la firma HMAC de las requests privadas.
      4. Implementar primero get_current_price y get_balance (son de solo
         lectura, el lugar más seguro para probar que la autenticación
         funciona antes de tocar place_order).
      5. Recién después, implementar place_order -- y probarlo primero con
         montos mínimos reales.
    """

    BASE_URL = "https://api.ripio.com/trade"

    def __init__(self, api_token: str = None, api_secret: str = None):
        import os
        self.api_token = api_token or os.environ.get("RIPIO_API_TOKEN")
        self.api_secret = api_secret or os.environ.get("RIPIO_API_SECRET")

        if not self.api_token or not self.api_secret:
            log.warning(
                "RipioBrokerAdapter inicializado SIN credenciales -- "
                "completar RIPIO_API_TOKEN y RIPIO_API_SECRET en el archivo .env."
            )
        else:
            log.info("RipioBrokerAdapter inicializado con credenciales cargadas (esqueleto, sin conexión real)")

    def _signed_headers(self, method: str, path: str, body: str = "") -> dict:
        # Placeholder del esquema de firma -- CONFIRMAR el algoritmo exacto
        # contra github.com/ripio/trade antes de usar esto contra la API real.
        # El patrón típico de este tipo de APIs es HMAC-SHA256 sobre una
        # concatenación de timestamp + method + path + body, usando el
        # Secret Key, convertido a hexadecimal.
        raise NotImplementedError(
            "Confirmar el esquema exacto de firma HMAC en github.com/ripio/trade "
            "antes de implementar esto -- no adivinar el detalle criptográfico."
        )

    def get_current_price(self, symbol: str) -> float:
        # Endpoint público, no requiere firma: GET {BASE_URL}/public/ticker/{symbol} (confirmar ruta exacta en la doc)
        raise NotImplementedError("Implementar el GET real al endpoint público de ticker de Ripio Trade.")

    def get_balance(self) -> float:
        # Endpoint privado, requiere token + firma
        raise NotImplementedError("Implementar el GET real al endpoint de balance, firmado con el Secret Key.")

    def place_order(self, symbol: str, side: str, units: float) -> dict:
        # Endpoint privado, requiere token con permiso de Compra/Venta + firma
        raise NotImplementedError("Implementar el POST real al endpoint de órdenes de Ripio Trade.")

    def get_open_positions(self) -> dict:
        raise NotImplementedError("Implementar el GET real al endpoint de balances/posiciones de Ripio Trade.")


class LibertexBrokerAdapter(BrokerBase):
    """
    ESQUELETO de adaptador real -- NO está conectado a la API de Libertex.
    Este sandbox no tiene acceso de red a ese dominio ni credenciales, así
    que cada método marca con un comentario dónde iría la llamada real.

    Para conectar esto de verdad (desde la otra PC, con acceso a internet
    y git):
      1. Conseguir credenciales de API de Libertex (revisar su panel de
         desarrollador -- no todos los brokers minoristas exponen una API
         pública, confirmar esto primero con su documentación o soporte).
      2. Copiar .env.example a .env y completar BROKER_API_KEY /
         BROKER_API_SECRET ahí (NUNCA en el código ni en git).
      3. Instalar python-dotenv (`pip install python-dotenv`) y cargar el
         .env al arrancar (ver el bloque comentado más abajo).
      4. Reemplazar cada `raise NotImplementedError(...)` por la llamada
         HTTP real (requests) al endpoint correspondiente de su API.
    """

    def __init__(self, api_key: str = None, api_secret: str = None):
        import os
        # Si no se pasan explícitamente, buscar en variables de entorno.
        # Para cargar un archivo .env automáticamente, descomentar:
        #   from dotenv import load_dotenv
        #   load_dotenv()
        self.api_key = api_key or os.environ.get("BROKER_API_KEY")
        self.api_secret = api_secret or os.environ.get("BROKER_API_SECRET")

        if not self.api_key or not self.api_secret:
            log.warning(
                "LibertexBrokerAdapter inicializado SIN credenciales -- "
                "solo va a poder instanciarse, cualquier llamada real va a fallar. "
                "Completar BROKER_API_KEY y BROKER_API_SECRET en el archivo .env."
            )
        else:
            log.info("LibertexBrokerAdapter inicializado con credenciales cargadas (esqueleto, sin conexión real)")

    def get_current_price(self, symbol: str) -> float:
        # Acá iría: GET a su endpoint de precios/quotes para `symbol`
        raise NotImplementedError(
            "Conectar al endpoint real de precios de Libertex. Ver su documentación de API."
        )

    def get_balance(self) -> float:
        # Acá iría: GET a su endpoint de balance de cuenta
        raise NotImplementedError("Conectar al endpoint real de balance de Libertex.")

    def place_order(self, symbol: str, side: str, units: float) -> dict:
        # Acá iría: POST a su endpoint de órdenes, con manejo de reintentos,
        # confirmación de ejecución, y manejo de errores de conexión (ver
        # health.py para el heartbeat que debería acompañar esto en vivo).
        raise NotImplementedError("Conectar al endpoint real de órdenes de Libertex.")

    def get_open_positions(self) -> dict:
        # Acá iría: GET a su endpoint de posiciones abiertas
        raise NotImplementedError("Conectar al endpoint real de posiciones de Libertex.")
