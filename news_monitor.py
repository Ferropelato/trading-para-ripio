"""
Monitor de noticias de alto impacto, para pausar entradas nuevas de forma
híbrida (manual o automática según horario) cuando aparece una noticia
que podría invalidar la lectura técnica del mercado -- ver README.md,
sección "Módulo de noticias".

Deliberadamente simple y auditable:
- Fuentes: feeds RSS públicos (no requieren API key ni registro). Cubren
  dos capas a propósito: (1) medios de cripto globales (CoinDesk,
  Cointelegraph) para lo que afecta al mercado cripto en general, y
  (2) medios económicos LOCALES de los países donde opera Ripio
  (Argentina, Brasil, México, Colombia, Uruguay) más EEUU y China (las
  dos economías que más mueven el sentimiento cripto global) -- un
  anuncio local (ej. un cambio regulatorio o una devaluación en un solo
  país) puede no aparecer nunca en la prensa cripto global, pero sí
  importar mucho para un usuario de ESE país. Cada feed se verificó
  a mano contra la URL real (no se asume que existe -- varios medios
  tienen un `/rss` que en realidad es una página HTML, no un feed).
- Clasificación de impacto: coincidencia de palabras clave en el título
  (hackeo, quiebra, regulación, etc.), NO un modelo de sentimiento con IA.
  Un keyword match es fácil de auditar ("se disparó por la palabra X en
  este título") -- un score de sentimiento de una IA es una caja negra
  más, y ya hay bastante superficie de error en este sistema como para
  sumar una decisión no explicable.
- Las palabras clave para los medios LOCALES son deliberadamente más
  angostas que las de cripto: términos genuinamente agudos/puntuales
  (ej. "devaluación", "corralito", "default"), nunca palabras que la
  prensa económica local menciona todos los días como rutina (ej.
  "inflación", "dólar", "BCRA" solos) -- si se agregaran esas, la pausa
  automática terminaría activa casi todo el tiempo en un país con
  inflación crónica, y una alerta que suena siempre deja de servir de
  alerta.
- Nunca coloca ni cierra una orden por sí solo. Lo máximo que hace en modo
  automático es pausar la apertura de posiciones NUEVAS por un tiempo --
  el mismo tipo de freno conservador que ya usan el circuit breaker y el
  kill-switch manual (ver safety.py). Cerrar una posición ya abierta ante
  una noticia sigue siendo una decisión manual del humano.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import requests

from app_logger import get_logger

log = get_logger(__name__)

DEFAULT_FEEDS = [
    # --- Cripto global ---
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    # --- EEUU (economía/mercados en general -- mueve el sentimiento
    # cripto global más que casi cualquier otro país) ---
    "https://www.cnbc.com/id/20910258/device/rss/rss.html",  # CNBC Economy
    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",  # WSJ Markets
    # --- China (la otra economía que más mueve el sentimiento cripto
    # global -- regulación china de cripto en particular tiene historial
    # de mover el mercado entero) ---
    "https://www.scmp.com/rss/92/feed",  # South China Morning Post, Business
    # --- Argentina ---
    "https://www.ambito.com/rss/pages/economia.xml",  # Ámbito, Economía
    "https://www.cronista.com/arc/outboundfeeds/news/",  # El Cronista
    "https://www.infobae.com/arc/outboundfeeds/rss/",  # Infobae (general, incluye economía)
    # --- Brasil ---
    "https://g1.globo.com/rss/g1/economia/",  # G1, Economia
    "https://valor.globo.com/rss/valor",  # Valor Econômico
    # --- México ---
    "https://www.jornada.com.mx/rss/economia.xml",  # La Jornada, Economía
    # --- Colombia ---
    "https://www.larepublica.co/rss/economia",  # La República, Economía
    # --- Uruguay ---
    "https://www.elobservador.com.uy/rss/pages/economia.xml",  # El Observador, Economía
]

# Palabras clave de alto impacto en inglés (medios cripto/EEUU/China).
# Intencionalmente conservador: mejor una alerta de más que una señal real
# de crisis que pase desapercibida.
HIGH_IMPACT_KEYWORDS = [
    "hack", "hacked", "exploit", "exploited", "breach",
    "sec sues", "sec charges", "lawsuit", "indicted",
    "banned", "ban on", "bans ", "regulation", "regulator", "crackdown",
    "bankrupt", "bankruptcy", "insolvency", "insolvent",
    "halts withdrawals", "pauses withdrawals", "freeze", "frozen",
    "collapse", "collapses", "crashes", "plunge", "plunges",
    "delist", "delisting", "delisted",
    "etf approved", "etf rejected", "etf denied",
    # Macro EEUU/China -- términos de eventos puntuales (decisión de tasa,
    # medida arancelaria concreta), no menciones rutinarias de la economía.
    "rate hike", "rate cut", "interest rate decision", "fed hikes", "fed cuts",
    "recession", "trade war", "tariffs on", "capital controls", "sanctions on",
    "china crackdown", "china bans", "pboc",
]

# Palabras clave de alto impacto en español/portugués (medios locales de los
# países donde opera Ripio). Ver nota arriba: a propósito angostas, solo
# eventos puntuales y agudos -- nunca vocabulario económico de uso diario.
HIGH_IMPACT_KEYWORDS_LOCAL = [
    "devaluación", "devalúa", "devaluación del peso",
    "corralito", "corrida cambiaria", "corrida bancaria",
    "default", "cesación de pagos", "quiebra", "quiebras",
    "control de cambios", "control de capitales", "cepo cambiario",
    "congelamiento de depósitos", "feriado bancario",
    "suba de tasas", "baja de tasas", "sube la tasa", "baja la tasa",
    "prohíbe", "prohibición de", "prohibición del",
    # Portugués (Brasil)
    "desvalorização", "corrida bancária", "calote", "quebra do banco",
    "controle de capitais", "congelamento de depósitos",
]

# Lista combinada -- lo que usa NewsMonitor por defecto si no se le pasa
# una lista de palabras clave propia.
ALL_HIGH_IMPACT_KEYWORDS = HIGH_IMPACT_KEYWORDS + HIGH_IMPACT_KEYWORDS_LOCAL


@dataclass
class NewsItem:
    title: str
    link: str
    published: str
    source: str
    matched_keywords: list = field(default_factory=list)


def _parse_rss(xml_text: str, source: str) -> list:
    """Extrae (titulo, link, fecha) de un feed RSS estandar. Devuelve [] si el XML no es valido
    en vez de romper -- un feed caido no deberia tumbar el resto del monitoreo."""
    items = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        log.warning("No se pudo parsear el feed RSS de %s (%s)", source, e)
        return items

    for item in root.iter("item"):
        title_el = item.find("title")
        if title_el is None or not title_el.text:
            continue
        link_el = item.find("link")
        pub_el = item.find("pubDate")
        items.append(NewsItem(
            title=title_el.text.strip(),
            link=(link_el.text.strip() if link_el is not None and link_el.text else ""),
            published=(pub_el.text.strip() if pub_el is not None and pub_el.text else ""),
            source=source,
        ))
    return items


def classify_impact(title: str, keywords=None) -> list:
    """Devuelve las palabras clave de alto impacto encontradas en el titulo (vacio si ninguna)."""
    keywords = keywords if keywords is not None else ALL_HIGH_IMPACT_KEYWORDS
    title_lower = title.lower()
    return [kw for kw in keywords if kw in title_lower]


def _default_http_get(url: str, timeout: float) -> str:
    # User-Agent explícito -- bug real encontrado al agregar los feeds
    # locales (ver README, ronda de cobertura multi-país): varios medios
    # (Ámbito, La República, El Observador, uno de los feeds de CNBC)
    # devuelven 403 al User-Agent por defecto de `requests`
    # ("python-requests/x.x.x", identificable como bot) -- con eso, esos
    # feeds fallarían SIEMPRE en producción, en silencio (el error queda
    # atrapado y logueado como warning, nunca hace caer el proceso), sin
    # que nadie lo note salvo que se lo pruebe contra la red real.
    response = requests.get(
        url, timeout=timeout,
        headers={"User-Agent": "Mozilla/5.0 (compatible; TradingEngineNewsMonitor/1.0)"},
    )
    response.raise_for_status()
    return response.text


class NewsMonitor:
    """
    Sondea feeds RSS públicos y devuelve titulares NUEVOS (no vistos en
    llamadas anteriores) que matchean alguna palabra clave de alto
    impacto. `http_get` es inyectable para poder testear sin red real
    (por defecto hace un GET real).
    """

    def __init__(self, feeds=None, keywords=None, timeout=10.0, http_get=None):
        # OJO: usar "is not None", no "or" -- una lista vacia [] es un valor
        # valido y deliberado ("sin feeds"), no equivalente a "no se paso
        # nada". "feeds or DEFAULT_FEEDS" trataria [] como falsy y caeria
        # a los feeds reales por accidente (bug real encontrado en el test
        # de este mismo modulo: paso feeds=[] esperando cero red y termino
        # llamando a las URLs reales de CoinDesk/Cointelegraph).
        self.feeds = feeds if feeds is not None else DEFAULT_FEEDS
        self.keywords = keywords if keywords is not None else ALL_HIGH_IMPACT_KEYWORDS
        self.timeout = timeout
        self._http_get = http_get or _default_http_get
        self._seen_links = set()

    def get_seen_links(self) -> set:
        """Para persistir el deduplicado entre reinicios (ver NewsGuard /
        _LiveEngine) -- sin esto, cada reinicio del proceso arranca con
        este set vacío y vuelve a alertar (y, en modo automático, vuelve a
        PAUSAR entradas nuevas) sobre titulares que ya se habían visto y
        alertado antes del reinicio -- un feed RSS típico mantiene varios
        días de titulares, así que esto no es un caso raro."""
        return self._seen_links

    def restore_seen_links(self, links) -> None:
        self._seen_links = set(links or [])

    def fetch_high_impact_news(self) -> list:
        """Devuelve una lista de NewsItem nuevos y de alto impacto detectados en esta llamada."""
        new_high_impact = []
        for feed_url in self.feeds:
            try:
                xml_text = self._http_get(feed_url, self.timeout)
            except Exception as e:
                log.warning("No se pudo consultar el feed %s (%s)", feed_url, e)
                continue

            for item in _parse_rss(xml_text, source=feed_url):
                dedup_key = item.link or item.title
                if not dedup_key or dedup_key in self._seen_links:
                    continue
                self._seen_links.add(dedup_key)

                matched = classify_impact(item.title, self.keywords)
                if matched:
                    item.matched_keywords = matched
                    new_high_impact.append(item)
        return new_high_impact


@dataclass
class AutomationWindow:
    """Ventana horaria en UTC (start inclusive, end exclusive) donde la reacción a
    noticias pasa de 'solo alerta' a 'alerta + pausa automática'. Soporta
    ventanas que cruzan la medianoche UTC (ej. 22 a 6)."""
    start_hour_utc: int
    end_hour_utc: int

    def contains(self, when: datetime) -> bool:
        hour = when.hour
        if self.start_hour_utc == self.end_hour_utc:
            return True  # ventana de 24hs
        if self.start_hour_utc < self.end_hour_utc:
            return self.start_hour_utc <= hour < self.end_hour_utc
        return hour >= self.start_hour_utc or hour < self.end_hour_utc


class NewsAutomationSchedule:
    """
    Decide si, en un momento dado, la reacción a noticias de alto impacto
    debe ser MANUAL (solo alerta, el humano decide qué hacer) o AUTOMÁTICA
    (además de alertar, pausa la apertura de posiciones nuevas por
    `cooldown_minutes`). Sin ventanas configuradas, el modo es siempre
    manual -- hay que optar explícitamente por más automatismo, nunca es
    el default.
    """

    def __init__(self, windows=None, cooldown_minutes: float = 60.0):
        self.windows = windows or []
        self.cooldown_minutes = cooldown_minutes

    def is_automatic_now(self, when: datetime = None) -> bool:
        when = when or datetime.now(timezone.utc)
        return any(w.contains(when) for w in self.windows)


class NewsGuard:
    """
    Une NewsMonitor + NewsAutomationSchedule + un canal de alertas. En cada
    `check()`: busca noticias nuevas de alto impacto y SIEMPRE alerta
    (manual o automático). Si el momento cae dentro de una ventana
    automática, además activa una pausa temporal de entradas nuevas.

    Se auto-limita a consultar los feeds como mucho cada `min_interval_seconds`
    reales (no simulados) sin importar cuántas veces se llame `check()`,
    para no golpear los feeds públicos en cada tick de un loop rápido.
    """

    def __init__(self, monitor: NewsMonitor, schedule: NewsAutomationSchedule,
                 alert_channel, min_interval_seconds: float = 300.0, logger=None):
        self.monitor = monitor
        self.schedule = schedule
        self.alert_channel = alert_channel
        self.min_interval_seconds = min_interval_seconds
        self.log = logger or log
        self._paused_until = None
        self._last_checked_at = None

    def check(self, now: datetime = None) -> list:
        now = now or datetime.now(timezone.utc)

        if (self._last_checked_at is not None
                and (now - self._last_checked_at).total_seconds() < self.min_interval_seconds):
            return []
        self._last_checked_at = now

        news_items = self.monitor.fetch_high_impact_news()
        if not news_items:
            return []

        automatic = self.schedule.is_automatic_now(now)
        modo = "AUTOMATICO" if automatic else "MANUAL"
        for item in news_items:
            msg = (f"[NOTICIA ALTO IMPACTO - modo {modo}] {item.source}: {item.title} "
                   f"(palabras clave: {', '.join(item.matched_keywords)}) {item.link}")
            self.alert_channel.send(msg)
            self.log.warning(msg)

        if automatic:
            self._paused_until = now + timedelta(minutes=self.schedule.cooldown_minutes)
            self.log.warning(
                "Pausa automática de entradas nuevas activada hasta %s "
                "(modo automático + noticia de alto impacto)", self._paused_until,
            )
        return news_items

    def entries_paused(self, now: datetime = None) -> bool:
        if self._paused_until is None:
            return False
        now = now or datetime.now(timezone.utc)
        return now < self._paused_until

    def get_paused_until(self):
        """Para persistir la pausa entre reinicios del proceso (ver
        _LiveEngine en live_runner.py) -- sin esto, reiniciar el proceso
        durante una pausa automática activa la levantaba en silencio,
        igual que le pasaba al circuit breaker antes de corregirlo."""
        return self._paused_until

    def restore_paused_until(self, paused_until) -> None:
        self._paused_until = paused_until

    def get_seen_links(self) -> set:
        return self.monitor.get_seen_links()

    def restore_seen_links(self, links) -> None:
        self.monitor.restore_seen_links(links)
