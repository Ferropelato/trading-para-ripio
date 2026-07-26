# Research de mercado: Ripio (para la propuesta de trading automatizado)

Notas de contexto para tener siempre a mano antes de la reunión. Fuente
principal: research pasado por el usuario (investigación propia), más
verificación puntual de competidores hecha durante el desarrollo de este
proyecto.

## Qué es Ripio

Fintech argentina de criptomonedas y blockchain, fundada en Buenos Aires
en 2013 (originalmente "BitPagos", procesador de pagos con Bitcoin).
Evolucionó a exchange + billetera + proveedor de infraestructura para
bancos y fintechs.

- **Fundadores**: Sebastián Serrano (CEO, cara visible de la empresa),
  Mugur Marculescu, Luciana Gruszeczka.
- **Empleados**: 200+
- **Usuarios alcanzados**: 25M+
- **Clientes institucionales**: 2.500+
- **Volumen operado**: USD 1.200M en el primer semestre de 2025.
- **Países**: Argentina, Brasil, México, Colombia, Chile, Uruguay,
  Estados Unidos. Expansión regulatoria en España para servicios puntuales.
- **Inversores**: Tim Draper, Digital Currency Group (DCG), Medici
  Ventures, otros fondos de VC internacionales.

## Producto (todo el ecosistema, no solo la wallet)

Compra/venta de cripto, stablecoins, wallet custodial, tarjeta Visa,
rendimientos sobre cripto, pagos de servicios, QR, PIX (Brasil), trading
profesional (Ripio Trade), OTC para grandes inversores, APIs para bancos,
tokenización de activos, Crypto-as-a-Service (CaaS), acceso a DeFi,
cuenta en dólares digitales según el país.

## El dato más importante para el pitch: Ripio ya es infraestructura B2B

Empresas que usan tecnología de Ripio por debajo del capó:
**Mercado Pago, Mercado Libre, OCA Blue (Grupo Itaú), IOL Inversiones**.

**Por qué esto importa para la propuesta**: el negocio de Ripio ya no es
solo captar usuarios finales -- es venderle infraestructura cripto a
bancos y fintechs (CaaS). Esto abre una segunda puerta de entrada además
de "agregarlo a la app de Ripio": este módulo también podría ofrecerse
como parte de su **oferta B2B/CaaS**, para que sus propios clientes
institucionales (otro banco, otra fintech) lo integren para SUS usuarios.
Vale la pena mencionar esto en la conversación como una segunda vía de
valor, no solo la wallet consumer.

## Fortalezas de Ripio (según el research)

1. Entiende la región (inflación, controles cambiarios, devaluación,
   acceso limitado al dólar) mejor que players de EE.UU.
2. Más de una década de trayectoria sobreviviendo ciclos de mercado y
   cambios regulatorios.
3. Reputación institucional: alianzas bancarias, SOC 1 Type II, SOC 2
   Type II, MFA, reservas completas.
4. Infraestructura B2B (probablemente su negocio más rentable hoy).
5. Una de las mayores mesas OTC de Latinoamérica (liquidez).
6. Innovación propia: Ripio Coin (RPC), stablecoins, tokenización,
   Launchpad, blockchain propia (LaChain).

## Debilidades de Ripio (según el research) -- y cómo esta propuesta conecta con cada una

1. **No es una billetera de uso diario** -- mucha gente la usa solo para
   comprar/vender/retirar, no como centro financiero (a diferencia de
   Mercado Pago). → Un módulo de trading automatizado da una razón real
   para mantener saldo asignado dentro de la app en vez de comprar y
   retirar enseguida -- ataca esta debilidad de forma directa.
2. **Marketing/reconocimiento de marca** fuera del mundo cripto, más
   débil que Mercado Pago. → No resuelto por este proyecto, pero un
   feature diferencial real es contenido de marketing en sí mismo.
3. **UX**: usuarios reportan demasiadas funciones, pantallas confusas,
   operaciones con muchos pasos. → El mockup de este proyecto está
   pensado deliberadamente simple (onboarding corto, lenguaje sin jerga,
   3 perfiles claros) -- vale la pena señalar esto como contraejemplo
   explícito en la conversación, no dar por sentado que lo van a notar solos.
4. **Competencia fuerte** (ver más abajo).
5. **Dependencia del ciclo de mercado cripto**: cuando baja Bitcoin, baja
   el volumen y la actividad, y con eso los ingresos por comisión. → Un
   motor que sigue operando activamente en mercados laterales/bajistas
   (algunas de las estrategias de este proyecto están pensadas para
   proteger mejor en caídas que buy & hold, ver README del motor)
   sostiene volumen operado incluso cuando el sentimiento general es bajista.

## Competidores

- **Argentina**: Lemon, Belo, Buenbit, Satoshi Tango.
- **Latinoamérica**: Bitso (el más grande de la región), Mercado Pago.
- **Globales**: Binance, Coinbase, Kraken, OKX, Bybit.

### Verificación propia: ¿alguno ya ofrece trading automatizado nativo?

Hecha durante este proyecto (búsquedas de julio 2026), para saber si el
ángulo del pitch es "sean los primeros" o "no se queden atrás":

- **Binance sí tiene** "Trading Bots" nativo (grid trading, DCA bot,
  integrado en su propia plataforma) -- confirma que la categoría de
  producto es real y ya validada a escala global, no una idea rara.
- **Bitso, Lemon y Belo: sin evidencia de un bot de trading nativo.**
  Lo que existe para Bitso son bots de TERCEROS (ej. CryptoRobotics) que
  se conectan por API externa -- exactamente el mismo patrón de
  integración que ya construimos y probamos contra la API de Ripio, solo
  que ellos lo hacen un tercero ajeno a la plataforma, no la plataforma misma.

**Conclusión honesta para el pitch**: no es "inventar una categoría que
no existe" (Binance ya probó que funciona a escala global) -- es "cerrar
la brecha con Binance, y ser el primero en ofrecerlo NATIVO (dentro de la
propia app, no un bot de tercero) entre los competidores directos de la
región". Ese es el ángulo defendible, ni exagerado ni tímido.

## Cómo gana dinero Ripio

Comisión por compra/venta, spreads, OTC, servicios institucionales, APIs,
tarjetas, staking, tokenización, alianzas comerciales. El negocio B2B
parece tener cada vez más peso relativo.

## Posible canal de contacto (a verificar, no asumir vigente)

Durante este proyecto se encontró que Sebastián Serrano (CEO) tiene
actividad pública en X/Twitter compartiendo enlaces a la documentación
de la API de Ripio (`github.com/ripio/api`) -- indica que al menos en
algún momento fue una persona técnica y accesible públicamente sobre
estos temas. **No asumir que esto sigue vigente hoy** sin confirmarlo --
revisar su actividad reciente antes de considerar un contacto directo por
esa vía. Puede ser más apropiado igual buscar al equipo de
desarrollo/producto o de alianzas/BD antes que ir directo al CEO en frío.

## Qué le falta a Ripio para ser "top mundial" (según el research, contexto de dónde podría encajar esto)

- Convertirse en una "super app" financiera (pagos + inversiones +
  préstamos + seguros + ahorro + comercio en un solo lugar).
- Expandirse con fuerza fuera de Latinoamérica.
- Marca global tan reconocida como Binance/Coinbase.
- Más productos financieros tradicionales integrados, no solo cripto.
- Ecosistema de desarrolladores/apps sobre su infraestructura con más
  adopción.

Esta propuesta encaja directamente con el primer punto ("super app" --
sumar inversión activa a lo que ya hacen) y, vía el ángulo B2B/CaaS,
también con el último (más adopción sobre su infraestructura).

## Notas para uso interno (no citar textual en el pitch)

- Puntajes del research original (autoevaluación subjetiva de quien lo
  escribió, no una fuente oficial de Ripio): Solidez 9/10, Seguridad
  9/10, Innovación 9.5/10, Facilidad de uso 8/10, Reconocimiento de marca
  7.5/10, Infraestructura 9.5/10, Potencial de crecimiento 9/10.
- Este archivo mezcla datos verificables (fundación, países, clientes
  B2B nombrados) con opiniones de quien hizo el research original (qué
  le "falta" para ser top mundial, debilidades de UX). Tratar la
  segunda categoría como hipótesis a confirmar en la conversación, no
  como hechos.
