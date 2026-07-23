"""
Sistema de alertas: cuando el motor encuentra una señal nueva, avisa por
el canal configurado, en vez de operar solo. Este es el paso intermedio
recomendado antes de cualquier autonomía real: "avisame" en vez de
"hacelo vos".

Incluye:
- AlertChannel: interfaz común.
- ConsoleAlertChannel: imprime la alerta (para pruebas, sin credenciales).
- TelegramAlertChannel: envía por bot de Telegram -- código correcto y
  completo, pero este sandbox no tiene acceso de red a api.telegram.org
  ni un bot token real, así que no se pudo probar en vivo. Documentado
  abajo cómo activarlo.
- EmailAlertChannel: envía por SMTP -- mismo caso, código completo pero
  no probado en vivo por no tener credenciales de un servidor real.
"""

from abc import ABC, abstractmethod
from datetime import datetime

from app_logger import get_logger

log = get_logger(__name__)


class AlertChannel(ABC):
    @abstractmethod
    def send(self, message: str) -> bool:
        """Envía la alerta. Devuelve True si se envió con éxito."""
        raise NotImplementedError


class ConsoleAlertChannel(AlertChannel):
    """Imprime la alerta en la consola/log. Sin dependencias externas -- útil para pruebas."""

    def send(self, message: str) -> bool:
        log.info("ALERTA: %s", message)
        return True


class TelegramAlertChannel(AlertChannel):
    """
    Envía la alerta a un chat de Telegram vía bot.

    Para activar esto de verdad:
      1. Crear un bot con @BotFather en Telegram y obtener el token.
      2. Mandarle un mensaje al bot y obtener tu chat_id (hay varias
         formas documentadas en la API de Telegram para esto).
      3. Instanciar: TelegramAlertChannel(bot_token="...", chat_id="...")

    NUNCA hardcodear el token en el código -- pasarlo por variable de
    entorno (ej. os.environ["TELEGRAM_BOT_TOKEN"]).
    """

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    def send(self, message: str) -> bool:
        import requests
        try:
            response = requests.post(
                self.api_url,
                json={"chat_id": self.chat_id, "text": message},
                timeout=10,
            )
            if response.status_code == 200:
                log.info("Alerta enviada a Telegram")
                return True
            log.error("Telegram devolvió error %s: %s", response.status_code, response.text)
            return False
        except requests.RequestException as e:
            log.error("No se pudo enviar la alerta a Telegram: %s", e)
            return False


class EmailAlertChannel(AlertChannel):
    """
    Envía la alerta por email vía SMTP. Requiere credenciales de un
    servidor SMTP real (ej. una cuenta de Gmail con contraseña de
    aplicación, o cualquier proveedor SMTP). No probado en vivo en este
    sandbox por no tener acceso de red ni credenciales.
    """

    def __init__(self, smtp_host: str, smtp_port: int, username: str, password: str,
                 to_address: str, from_address: str = None):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.username = username
        self.password = password
        self.to_address = to_address
        self.from_address = from_address or username

    def send(self, message: str) -> bool:
        import smtplib
        from email.mime.text import MIMEText
        try:
            msg = MIMEText(message)
            msg["Subject"] = "Alerta del motor de trading"
            msg["From"] = self.from_address
            msg["To"] = self.to_address

            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10) as server:
                server.starttls()
                server.login(self.username, self.password)
                server.send_message(msg)
            log.info("Alerta enviada por email")
            return True
        except Exception as e:
            log.error("No se pudo enviar la alerta por email: %s", e)
            return False


def format_signal_alert(asset: str, strategy_name: str, profile_name: str,
                         signal_date, price: float, sizing: dict) -> str:
    """Arma el texto de la alerta a partir de una señal nueva detectada."""
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"[{timestamp}] Nueva señal de compra\n"
        f"Activo: {asset}\n"
        f"Estrategia: {strategy_name} | Perfil: {profile_name}\n"
        f"Fecha de la señal: {signal_date}\n"
        f"Precio: {price}\n"
        f"Tamaño sugerido: {sizing.get('unidades')} unidades\n"
        f"Stop loss: {sizing.get('stop_loss')} | Take profit: {sizing.get('take_profit')}\n"
        f"-- Esto es una alerta, no una orden ejecutada. Confirmá manualmente antes de operar. --"
    )
