"""
Capa de abstracción de bróker: una interfaz común para que conectar a
Ripio, Libertex, Binance, o cualquier otro bróker/exchange sea cuestión
de escribir un adaptador nuevo (una clase que implemente BrokerBase), sin
tocar el motor de estrategias ni el backtester.

Incluye:
- BrokerBase: la interfaz (contrato) que cualquier bróker debe cumplir.
- PaperBroker: simulación en memoria para paper trading / tests.
- RipioBrokerAdapter: adaptador real contra la API de Ripio Trade
  (lectura + órdenes protegidas por allow_trading).
- LibertexBrokerAdapter: esqueleto documentado (sin API pública confirmada).
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
    Adaptador real para Ripio Trade (API v4 / gateway retail).

    Autenticación (oficial: github.com/ripio/api/authentication/python):
      message = Timestamp + HTTP_METHOD + PathSinQuery + JSONBody
      Signature = Base64(HMAC-SHA256(secret, message))

    Endpoints usados (gateway retail, mismos datos que api.ripiotrade.co/v4):
      GET  /trade/public/tickers/{pair}   -- público
      GET  /trade/user/balances          -- privado (lectura)
      GET  /trade/orders?pair=...&status=open
      POST /trade/orders                 -- privado (compra/venta)

    Seguridad:
      - Generar el token en https://trade.ripio.com/market/api/token con
        SOLO Lectura (+ Compra/Venta si vas a operar). Nunca permiso de retiro.
      - place_order exige allow_trading=True (por defecto False) para no
        disparar órdenes reales por accidente mientras se prueba lectura.
      - Credenciales SOLO por env / .env (RIPIO_API_TOKEN, RIPIO_API_SECRET).

    Pares típicos AR: BTC_USDC, ETH_USDC, USDC_ARS, USDT_ARS.
    """

    BASE_URL = "https://api.ripio.com"
    DEFAULT_TIMEOUT = 15

    def __init__(self, api_token: str = None, api_secret: str = None,
                 quote_currency: str = "USDC", allow_trading: bool = False,
                 timeout: float = None):
        import os
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass

        self.api_token = api_token or os.environ.get("RIPIO_API_TOKEN") or os.environ.get("API_KEY")
        self.api_secret = api_secret or os.environ.get("RIPIO_API_SECRET") or os.environ.get("SECRET_KEY")
        self.quote_currency = quote_currency.upper()
        self.allow_trading = allow_trading
        self.timeout = timeout if timeout is not None else self.DEFAULT_TIMEOUT
        self._processed_client_order_ids = {}
        self._server_time_offset_ms = None  # server_ms - local_ms

        if not self.api_token or not self.api_secret:
            log.warning(
                "RipioBrokerAdapter inicializado SIN credenciales -- "
                "completar RIPIO_API_TOKEN y RIPIO_API_SECRET en el archivo .env. "
                "Los endpoints públicos (precio) igual funcionan."
            )
        else:
            log.info(
                "RipioBrokerAdapter listo (quote=%s, allow_trading=%s)",
                self.quote_currency, self.allow_trading,
            )

    # --- firma / HTTP -----------------------------------------------------

    @staticmethod
    def normalize_pair(symbol: str) -> str:
        """Acepta BTC_USDC, BTC-USDC, btc/usdc y lo normaliza a BTC_USDC."""
        s = symbol.strip().upper().replace("-", "_").replace("/", "_")
        if "_" in s:
            return s
        # Heurística corta para pares comunes sin guion bajo
        for quote in ("USDC", "USDT", "BRL", "ARS", "COP"):
            if s.endswith(quote) and len(s) > len(quote):
                return f"{s[:-len(quote)]}_{quote}"
        raise ValueError(
            f"No pude interpretar el par '{symbol}'. Usá el formato Ripio, ej. BTC_USDC."
        )

    @staticmethod
    def build_signature(secret: str, timestamp: str, method: str, path: str, body: str = "") -> str:
        """Firma HMAC-SHA256 en Base64, igual que los ejemplos oficiales de Ripio."""
        import hmac
        import hashlib
        import base64
        from urllib.parse import urlparse

        pathname = urlparse(path).path  # descarta ?query=...
        message = f"{timestamp}{method.upper()}{pathname}{body}"
        digest = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).digest()
        return base64.b64encode(digest).decode("utf-8")

    def _require_credentials(self):
        from resilience import PermanentBrokerError
        if not self.api_token or not self.api_secret:
            raise PermanentBrokerError(
                "Faltan RIPIO_API_TOKEN / RIPIO_API_SECRET -- no se puede llamar a endpoints privados."
            )

    def _sync_server_time(self, force: bool = False) -> None:
        """
        Ripio rechaza requests si el Timestamp local se desvía >5s del servidor
        (default Timestamp-tolerance). Muchas PCs Windows están desfasadas;
        calibramos contra GET /trade/public/server-time.
        """
        import time
        import requests
        if self._server_time_offset_ms is not None and not force:
            return
        try:
            response = requests.get(
                f"{self.BASE_URL}/trade/public/server-time", timeout=self.timeout,
            )
            response.raise_for_status()
            server_ms = int(response.json()["data"]["timestamp"])
            local_ms = int(time.time() * 1000)
            self._server_time_offset_ms = server_ms - local_ms
            if abs(self._server_time_offset_ms) > 2000:
                log.info(
                    "Reloj local desfasado %d ms vs Ripio -- usando hora del servidor para firmar",
                    self._server_time_offset_ms,
                )
        except Exception as e:
            if self._server_time_offset_ms is None:
                # Sin ningun offset previo (ni siquiera capturado oportunistamente
                # de otra respuesta): reloj local como ultimo recurso.
                log.warning("No pude sincronizar hora con Ripio (%s) -- uso reloj local sin ajuste", e)
                self._server_time_offset_ms = 0
            else:
                # Ya teniamos un offset (por ejemplo, capturado del timestamp que
                # trae CUALQUIER respuesta de Ripio, exitosa o de error). No lo
                # pisamos con 0 solo porque este resync puntual fallo.
                log.warning(
                    "No pude re-sincronizar hora con Ripio (%s) -- sigo usando el ultimo offset conocido (%d ms)",
                    e, self._server_time_offset_ms,
                )

    def _capture_server_time_from_payload(self, payload: dict) -> None:
        """
        Ripio incluye 'timestamp' (ms, hora del servidor) en TODAS las
        respuestas, exitosas o de error. Aprovechar esto para mantener el
        offset de reloj al dia sin depender solo de /server-time, que tiene
        un rate limit propio: se observo que llamar al ticker publico justo
        antes puede hacer que /server-time devuelva 429, y sin este fallback
        el codigo asumia reloj local (aca desfasado ~16s), lo que invalidaba
        la firma de cualquier request privada inmediatamente despues.
        """
        import time
        try:
            server_ms = int(payload.get("timestamp"))
        except (TypeError, ValueError):
            return
        self._server_time_offset_ms = server_ms - int(time.time() * 1000)

    def _timestamp_ms(self) -> str:
        import time
        self._sync_server_time()
        offset = self._server_time_offset_ms or 0
        return str(int(time.time() * 1000) + offset)

    def _signed_headers(self, method: str, path: str, body: str = "") -> dict:
        self._require_credentials()
        timestamp = self._timestamp_ms()
        signature = self.build_signature(self.api_secret, timestamp, method, path, body)
        return {
            "Content-Type": "application/json",
            "Authorization": self.api_token,
            "Timestamp": timestamp,
            # Holgura extra por si el offset se desvía un poco entre requests
            "Timestamp-tolerance": "30000",
            "Signature": signature,
        }

    def _request(self, method: str, path: str, body_obj: dict = None, signed: bool = True) -> dict:
        """
        path debe empezar con /trade/... (como en los ejemplos oficiales).
        body_obj se serializa con separators compactos para que el body firmado
        coincida byte-a-byte con el enviado.
        """
        import json
        import requests
        from resilience import TransientBrokerError, PermanentBrokerError

        body = ""
        if body_obj is not None:
            body = json.dumps(body_obj, separators=(",", ":"))

        url = f"{self.BASE_URL}{path}"
        headers = {"Content-Type": "application/json"}
        if signed:
            headers = self._signed_headers(method, path, body)

        try:
            response = requests.request(
                method.upper(), url, headers=headers,
                data=body if body else None, timeout=self.timeout,
            )
        except requests.Timeout as e:
            raise TransientBrokerError(f"Timeout hablando con Ripio: {e}") from e
        except requests.ConnectionError as e:
            raise TransientBrokerError(f"Error de conexión con Ripio: {e}") from e

        try:
            payload = response.json()
        except ValueError:
            payload = None

        # Capturar la hora del servidor de CUALQUIER respuesta (exitosa o de
        # error) antes de decidir si esta request en particular fallo -- ver
        # _capture_server_time_from_payload.
        if isinstance(payload, dict) and "timestamp" in payload:
            self._capture_server_time_from_payload(payload)

        if response.status_code >= 500:
            raise TransientBrokerError(
                f"Ripio respondió {response.status_code}: {response.text[:200]}"
            )
        if response.status_code in (401, 403):
            raise PermanentBrokerError(
                f"Auth Ripio falló ({response.status_code}): {response.text[:300]}"
            )
        if response.status_code >= 400:
            raise PermanentBrokerError(
                f"Ripio rechazó la request ({response.status_code}): {response.text[:300]}"
            )

        if payload is None:
            raise TransientBrokerError("Respuesta no-JSON de Ripio")

        if payload.get("error_code") not in (None, 0):
            raise PermanentBrokerError(
                f"Ripio error_code={payload.get('error_code')}: {payload.get('message')}"
            )
        return payload

    # --- BrokerBase -------------------------------------------------------

    def get_current_price(self, symbol: str) -> float:
        """Ticker público (no requiere credenciales)."""
        pair = self.normalize_pair(symbol)
        payload = self._request("GET", f"/trade/public/tickers/{pair}", signed=False)
        data = payload.get("data") or {}
        last = data.get("last")
        if last is None:
            from resilience import PermanentBrokerError
            raise PermanentBrokerError(f"Ticker de {pair} sin campo 'last': {payload}")
        return float(last)

    def get_balances(self) -> list:
        """Lista cruda de balances de la cuenta (privado)."""
        payload = self._request("GET", "/trade/user/balances", signed=True)
        data = payload.get("data") or []
        # La doc a veces muestra data como lista anidada [[{...}]]; aplanar.
        if data and isinstance(data[0], list):
            data = data[0]
        return data

    def get_balance(self) -> float:
        """Capital disponible en la moneda quote (USDC por defecto)."""
        balances = self.get_balances()
        for row in balances:
            code = (row.get("currency_code") or row.get("currency") or "").upper()
            if code == self.quote_currency:
                return float(row.get("available_amount") or 0.0)
        return 0.0

    def get_open_positions(self) -> dict:
        """
        En un exchange spot no hay 'posiciones' apalancadas: tratamos como
        posición abierta cualquier balance > 0 de un activo que no sea la
        moneda quote.
        """
        balances = self.get_balances()
        positions = {}
        for row in balances:
            code = (row.get("currency_code") or row.get("currency") or "").upper()
            available = float(row.get("available_amount") or 0.0)
            if code and code != self.quote_currency and available > 0:
                positions[code] = {"unidades": available, "precio_entrada": None}
        return positions

    def place_order(self, symbol: str, side: str, units: float,
                    client_order_id: str = None, order_type: str = "market",
                    price: float = None) -> dict:
        """
        Coloca una orden. Por defecto allow_trading=False → rechaza sin
        enviar nada (modo seguro para probar el cableado).
        client_order_id se mapea a external_id de Ripio (idempotencia).
        """
        from datetime import datetime

        if side not in ("buy", "sell"):
            raise ValueError("side debe ser 'buy' o 'sell'")
        if units <= 0:
            raise ValueError("units debe ser positivo")

        if client_order_id and client_order_id in self._processed_client_order_ids:
            log.info("client_order_id '%s' ya procesado -- no se reenvía a Ripio", client_order_id)
            return self._processed_client_order_ids[client_order_id]

        if not self.allow_trading:
            order = {
                "status": "rejected",
                "motivo": "allow_trading=False (modo lectura). Pasá allow_trading=True para operar de verdad.",
                "symbol": self.normalize_pair(symbol),
                "side": side,
                "units": units,
                "timestamp": datetime.utcnow().isoformat(),
            }
            log.warning("Orden NO enviada a Ripio: %s", order["motivo"])
            return order

        pair = self.normalize_pair(symbol)
        body = {
            "pair": pair,
            "side": side,
            "type": order_type,
            # Strings como recomienda Ripio para evitar desajustes de firma por decimales
            "amount": format(units, ".10f").rstrip("0").rstrip(".") or "0",
        }
        if order_type == "limit":
            if price is None:
                raise ValueError("Las órdenes limit requieren price=")
            body["price"] = format(float(price), ".10f").rstrip("0").rstrip(".") or "0"
        if client_order_id:
            body["external_id"] = str(client_order_id)[:36]

        payload = self._request("POST", "/trade/orders", body_obj=body, signed=True)
        data = payload.get("data") or {}
        status_raw = (data.get("status") or "").lower()
        if status_raw in ("executed_completely", "executed_partially"):
            status = "filled"
        elif status_raw in ("open",):
            status = "open"
        elif status_raw in ("canceled", "cancelled"):
            status = "canceled"
        else:
            status = status_raw or "submitted"

        executed = data.get("executed_amount")
        avg_price = None
        if executed and data.get("total_value") and float(executed) > 0:
            avg_price = float(data["total_value"]) / float(executed)

        order = {
            "status": status,
            "symbol": pair,
            "side": side,
            "units": float(executed) if executed is not None else units,
            "price": avg_price if avg_price is not None else price,
            "broker_order_id": data.get("id"),
            "raw": data,
            "timestamp": datetime.utcnow().isoformat(),
        }
        if client_order_id:
            self._processed_client_order_ids[client_order_id] = order
        log.info("Orden Ripio %s: %s %s %s -> %s", order.get("broker_order_id"), side, units, pair, status)
        return order


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
