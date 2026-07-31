# Motor de estrategias + backtesting (prototipo)

Prototipo funcional para probar reglas de trading configurables por perfil
de riesgo, sobre datos históricos. Sirve como base técnica para validar
ideas ANTES de arriesgar capital real o de pensar en cualquier producto
comercial.

Este README documenta el desarrollo ronda por ronda (así quedó cada bug
encontrado y cada decisión de diseño con su motivo, no solo el estado
final). Para una lectura rápida: `## Estructura` de abajo tiene el mapa
de todos los archivos, y las últimas rondas (Catorceava/Quinceava) son
las más relevantes para evaluar qué tan listo está esto hoy.

<details>
<summary><b>Índice (click para expandir)</b></summary>

- [Estructura](#estructura)
- [Cómo correrlo](#cómo-correrlo)
- [Datasets reales incluidos](#datasets-reales-incluidos)
- [Funciones de seguridad agregadas](#funciones-de-seguridad-agregadas)
- [Conectar datos reales en vivo (siguiente paso)](#conectar-datos-reales-en-vivo-siguiente-paso)
- [Qué más agregaría antes de acercar esto a operar con capital real](#qué-más-agregaría-antes-de-acercar-esto-a-operar-con-capital-real)
- [Benchmark contra buy & hold (agregado — hallazgo importante)](#benchmark-contra-buy-hold-agregado-hallazgo-importante)
- [Métricas nuevas](#métricas-nuevas)
- [Pruebas fuertes agregadas (stress testing)](#pruebas-fuertes-agregadas-stress-testing)
- [Las 10 mejoras agregadas (segunda ronda)](#las-10-mejoras-agregadas-segunda-ronda)
- [Tercera ronda: infraestructura y producción](#tercera-ronda-infraestructura-y-producción)
- [Cuarta ronda: preparado para continuar desde otra PC (con git/API real)](#cuarta-ronda-preparado-para-continuar-desde-otra-pc-con-gitapi-real)
- [Quinta ronda: broker real identificado (Ripio)](#quinta-ronda-broker-real-identificado-ripio)
- [Sexta ronda: confiabilidad y robustez general del sistema](#sexta-ronda-confiabilidad-y-robustez-general-del-sistema)
- [RipioBrokerAdapter (implementado)](#ripiobrokeradapter-implementado)
- [Séptima ronda: entorno con red real, fixes de fecha/hora, y paper trading en vivo](#séptima-ronda-entorno-con-red-real-fixes-de-fechahora-y-paper-trading-en-vivo)
- [Próximos pasos reales (lo que todavía falta)](#próximos-pasos-reales-lo-que-todavía-falta)
- [Octava ronda: módulo de noticias de alto impacto](#octava-ronda-módulo-de-noticias-de-alto-impacto-news_monitorpy)
- [Novena ronda: datasets y backtests sobre pares reales de Ripio Argentina (ARS)](#novena-ronda-datasets-y-backtests-sobre-pares-reales-de-ripio-argentina-ars)
- [Décima ronda: optimización de parámetros con walk-forward obligatorio](#décima-ronda-optimización-de-parámetros-con-walk-forward-obligatorio)
- [Onceava ronda: capa de integración con la billetera + aislamiento multi-usuario](#onceava-ronda-capa-de-integración-con-la-billetera-aislamiento-multi-usuario)
- [Doceava ronda: build de Docker verificado de punta a punta (CI)](#doceava-ronda-build-de-docker-verificado-de-punta-a-punta-ci)
- [Treceava ronda: primera corrida real de paper trading en vivo](#treceava-ronda-primera-corrida-real-de-paper-trading-en-vivo-en-curso)
- [Visión: más allá de cripto](#visión-más-allá-de-cripto-prueba-de-concepto-no-una-promesa)
- [Catorceava ronda: qué haría falta para un lanzamiento real (Fases 2 y 3)](#catorceava-ronda-qué-haría-falta-para-un-lanzamiento-real-fases-2-y-3)
- [Quinceava ronda: Fase 4 -- producto](#quinceava-ronda-fase-4-producto-onboarding-historialajustes-herramienta-de-soporte)
- [Dieciseisava ronda: cerrando huecos de cobertura](#dieciseisava-ronda-cerrando-huecos-de-cobertura-de-rondas-anteriores)
- [Notas importantes (leer antes de avanzar)](#notas-importantes-leer-antes-de-avanzar)

</details>

## Estructura

- `risk_profiles.py` — perfiles conservador / moderado / agresivo (% de riesgo
  por operación, cuántas posiciones simultáneas, distancia del stop loss).
- `strategies.py` — 4 estilos de estrategia basados en reglas técnicas
  públicas (tendencia, momentum, contracción de volatilidad, valor en
  tendencia). **No son réplicas de ningún fondo o persona real.**
- `risk_manager.py` — calcula el tamaño de posición para que ninguna
  operación arriesgue más del % definido por el perfil.
- `backtester.py` — motor que simula la estrategia + gestión de riesgo
  sobre datos históricos y calcula métricas de resultado.
- `data_utils.py` — generador de datos sintéticos (para probar sin
  depender de una API en vivo) y cargador de CSV real.
- `run_backtest.py` — script para correr todo desde la terminal.

**Seguridad y validación**: `safety.py` (circuit breaker, kill-switch,
validación de datos), `health.py` (heartbeat / huecos de datos),
`resilience.py` (reintentos con backoff), `reconciliation.py` (estado
interno vs. bróker), `state_store.py` (persistencia JSON y `SQLiteStateStore`
compartida entre usuarios).

**Análisis y validación anti-sobreajuste**: `validation.py` (walk-forward),
`sensitivity.py` (sensibilidad de parámetros), `param_optimizer.py`
(optimización que solo rankea por desempeño fuera de muestra),
`monte_carlo.py` (riesgo de ruina), `stress_test.py` (crisis históricas),
`significance.py`, `regime.py`, `multi_timeframe.py`, `portfolio.py`
(multi-activo con correlación).

**Bróker y ejecución en vivo**: `broker.py` (`BrokerBase`, `PaperBroker`
con soporte de llenados parciales, `RipioBrokerAdapter`,
`AlpacaBrokerAdapter`, `LibertexBrokerAdapter` esqueleto), `live_runner.py`
(`_LiveEngine` compartido entre replay histórico y polling en vivo,
`run_live`/`run_live_polling`), `price_feed.py` (`SharedPriceFeed`, un
poller por símbolo compartido entre usuarios), `news_monitor.py`
(alertas híbridas manual/automáticas por noticias de alto impacto).

**Multi-usuario y operaciones**: `wallet_integration.py`
(`WalletBalanceProvider`, el contrato de integración con una billetera
real), `multi_user.py` (`UserSessionManager`/`UserTradingSession`,
aislamiento completo por usuario), `ops_monitor.py`
(`OperationsMonitor`, alertas individuales y sistémicas), `support_tools.py`
(reporte consolidado para soporte al cliente).

**Otros**: `alerts.py` (consola/Telegram/email), `app_logger.py`,
`experiment_log.py`, `report.py` (HTML autocontenido), `tax_export.py`,
`kill_switch.py` (CLI del freno manual), `ripio_smoke.py` (smoke test de
solo lectura contra la API real de Ripio), `build_ars_datasets.py`
(construye datasets reales en ARS combinando fuentes públicas),
`tests.py` (suite completa, +80 pruebas).

## Cómo correrlo

```bash
pip install pandas numpy matplotlib

# Con datos sintéticos (demo, sin conexión a ninguna fuente real):
python3 run_backtest.py --strategy momentum --profile agresivo

# Con tus propios datos históricos (CSV: date,open,high,low,close,volume):
python3 run_backtest.py --strategy contraccion_volatilidad --profile moderado --csv mi_data.csv
```

Estrategias disponibles: `tendencia`, `momentum`, `contraccion_volatilidad`, `valor_en_tendencia`
Perfiles disponibles: `conservador`, `moderado`, `agresivo`

El script imprime métricas (retorno, drawdown máximo, win rate, cantidad
de operaciones, Sharpe aproximado) y guarda un gráfico de la curva de
capital en `reporte.png`.

## Datasets reales incluidos

Ya vienen dos datasets reales listos para usar (columnas normalizadas a
`date,open,high,low,close,volume`):

- `real_data/btc_daily.csv` — BTC/USD diario, 2020-01-02 a 2024-09-17 (1.721 velas,
  incluye el ciclo alcista 2020-2021, el crash de 2022 y la recuperación 2023-2024).
  Fuente original: Investing.com, vía repositorio público de GitHub.
- `real_data/aapl_daily.csv` — AAPL diario, 2015-02-17 a 2017-02-16 (506 velas).
  Fuente: dataset de ejemplo de Plotly (`plotly/datasets`), basado en datos de mercado reales.

```bash
python3 run_backtest.py --strategy momentum --profile agresivo --csv real_data/btc_daily.csv
```

Con BTC 2020-2024, momentum/agresivo terminó con +59% de retorno y -16%
de drawdown máximo (activó el circuit breaker una vez). Con AAPL 2015-2017
(mercado mucho más lateral) los retornos son bastante más modestos, entre
0% y 6% según la estrategia — así se ve la diferencia real entre operar
en un mercado con tendencia fuerte vs uno lateral.

## Funciones de seguridad agregadas

- **`safety.py` — validación de datos**: antes de correr cualquier backtest,
  chequea que no haya precios negativos, `high < low`, fechas duplicadas o
  desordenadas, o huecos de datos. Si algo falla, el backtester tira error
  en vez de dar un resultado silenciosamente corrupto.
- **`safety.py` — circuit breaker**: corta la apertura de posiciones nuevas
  si el capital cae más de `--max-drawdown` % desde su pico, o más de
  `--max-daily-loss` % en un solo día. Esto es un límite estructural, no
  una sugerencia — existe específicamente para el escenario "la estrategia
  se rompió y sigue intentando operar sin parar".
- **Slippage**: además de la comisión, cada operación asume un pequeño
  deslizamiento de precio (`slippage_pct`, 0.05% por defecto) para que el
  backtest no sea artificialmente optimista.
- **`validation.py` — walk-forward**: divide los datos en un tramo de
  "entrenamiento" y uno de "prueba" que la estrategia no vio, y avisa si
  el rendimiento se degrada fuertemente entre ambos (señal de sobreajuste).
  Correr con `--walk-forward`.
- **`tests.py` — tests de sanidad**: corré `python3 tests.py` después de
  cualquier cambio al código. Verifican que el riesgo por operación nunca
  supere el % configurado, que el tamaño de posición nunca pida más
  capital del disponible, y que la validación de datos funcione.
- **Log de operaciones auditable**: `--export-trades archivo.csv` guarda
  cada operación (fecha, precio de entrada/salida, motivo del cierre) para
  poder revisar después qué hizo el motor exactamente.

## Conectar datos reales en vivo (siguiente paso)

Nota histórica: en rondas anteriores este proyecto se desarrolló en un
entorno sin salida de red hacia APIs de mercado, así que el prototipo
usaba datos sintéticos para demostrar que el motor funciona de punta a
punta. Eso ya no aplica en el entorno actual (tiene acceso real a
internet, ver sección "Séptima ronda" más abajo), pero los datos
sintéticos se mantienen como opción por defecto para no depender de red
en cada corrida rápida. Para pasar a datos reales, reemplazá
`generate_synthetic_data` por una función que traiga precios históricos de:
- Una API de datos (ej. Alpha Vantage, Twelve Data, o la que dé tu bróker).
- Exportaciones CSV manuales (ya soportado vía `load_csv`).

Para pasar a **ejecución en vivo**, cada bróker/exchange expone su propia
API (órdenes, posiciones, precios en streaming). El motor de estrategias
ya está separado del resto para que sea fácil conectarlo a esa capa el
día que corresponda.

## Qué más agregaría antes de acercar esto a operar con capital real

Esto ya es bastante más sólido que un backtest simple, pero para que sea
"operable" de verdad (no solo en teoría) todavía faltaría:

1. **Más períodos y más activos en el backtest.** Dos datasets no alcanzan
   para confiar en una estrategia. Habría que correr esto sobre docenas de
   pares/acciones y sobre distintos regímenes de mercado (alcista, bajista,
   lateral, alta volatilidad) antes de sacar cualquier conclusión.
2. **Optimización de parámetros con protección anti-overfitting real.**
   Ahora mismo los parámetros de cada estrategia (períodos de media móvil,
   umbrales de RSI, etc.) están fijos a mano. El paso siguiente sería una
   búsqueda de parámetros que SIEMPRE se valide con walk-forward, nunca
   optimizando sobre el 100% de los datos.
3. **Simulación de Monte Carlo del riesgo de ruina**: correr miles de
   simulaciones aleatorias sobre la secuencia de operaciones para ver la
   probabilidad real de quedarse sin capital, no solo el resultado de una
   única corrida histórica.
4. **Puente a paper trading real** (no backtest, sino ejecutar en una cuenta
   demo con datos en vivo) durante varias semanas antes de pensar en
   capital real — esto todavía no está construido acá.
5. **Manejo seguro de credenciales**: cuando esto se conecte a una API de
   verdad, las claves de API nunca van en el código ni en el repositorio
   — van en variables de entorno o un gestor de secretos, con permisos
   mínimos (solo lectura de datos + colocar órdenes, nunca retiro de fondos).
6. **Alertas en vez de autonomía total**: para la primera versión en vivo,
   lo más sensato es que el sistema te avise (Telegram/email/notificación)
   cuando encuentra una señal, y que vos confirmes la operación — no que
   dispare la orden sola sin supervisión, al menos hasta tener mucho más
   historial de que funciona.
7. **Manejo de errores de conexión/API**: reintentos con backoff, y que si
   se corta la conexión con el bróker a mitad de una operación, el sistema
   no quede en un estado ambiguo (por ejemplo, no sepa si la orden se
   ejecutó o no).
8. **Aislamiento multi-usuario** (si esto pasa a ofrecerse a más gente):
   cada usuario con su propio capital, su propio perfil de riesgo y su
   propio circuit breaker — nunca un solo pool de decisiones para todos.

## Benchmark contra buy & hold (agregado — hallazgo importante)

Cada backtest ahora compara el resultado de la estrategia contra simplemente
comprar el activo al inicio y no tocar nada más. Esto reveló algo importante:
con BTC 2020-2024, la estrategia de momentum/agresivo dio +59% de retorno,
pero **comprar y mantener dio +765%** en el mismo período. La estrategia activa
le perdió por mucho al benchmark más simple — algo que sin esta comparación
hubiera pasado desapercibido. En mercados con tendencias muy fuertes y
sostenidas, entrar/salir activamente (pagando comisión y slippage en cada
operación) suele rendir peor que no tocar nada. Esto no invalida la
estrategia para todo escenario (en mercados laterales o bajistas debería
comportarse mejor que buy & hold), pero es una alerta real que había que
mostrar, no esconder.

## Métricas nuevas

Además de retorno y drawdown, ahora el reporte incluye:
- **profit_factor**: ganancia bruta total / pérdida bruta total. Por debajo
  de 1 significa que la estrategia pierde plata en conjunto.
- **expectancy_por_operacion**: cuánto se espera ganar (o perder) en
  promedio en cada operación individual.
- **sortino_aprox**: como el Sharpe, pero solo penaliza la volatilidad
  negativa (las bajadas) — más representativo para trading que el Sharpe
  tradicional, que también penaliza subidas fuertes como si fueran "riesgo".

## Pruebas fuertes agregadas (stress testing)

Los 12 tests anteriores verifican que el código hace lo que se espera en
condiciones normales. Estos son distintos: someten al motor a condiciones
extremas o adversariales, que es donde un sistema de trading real suele
romperse sin avisar.

- **`monte_carlo.py` — riesgo de secuencia y de ruina**: en vez de mirar
  un solo backtest (un solo orden posible de ganancias/pérdidas), simula
  miles de escenarios alternativos remuestreando las mismas operaciones
  (bootstrap). Con las 54 operaciones reales de BTC/momentum/agresivo: el
  resultado "oficial" del backtest fue +59%, pero el Monte Carlo reveló
  que en el peor 5% de los escenarios posibles el resultado es una
  **pérdida** (capital final ~$966 sobre $1000 inicial), algo que un solo
  backtest nunca te muestra.
- **`stress_test.py` — períodos de crisis histórica real**: corre el
  backtest específicamente sobre ventanas de crisis conocidas (crash
  COVID 2020, colapso Terra/LUNA 2022, colapso FTX 2022, el mercado
  bajista completo 2021-2022) en vez de promediarlas con los períodos
  buenos. Resultado real: en los 4 períodos de crisis la estrategia le
  ganó por mucho al buy & hold (ej. en el bear market completo: -13% la
  estrategia vs. -73% buy & hold). Esto arma una imagen mucho más
  completa: la estrategia pierde contra el buy & hold en tendencias
  alcistas fuertes, pero protege mucho mejor en las caídas.
- **Tests con datos adversariales** (en `tests.py`): flash crash de -80%
  en una sola vela, datos completamente planos (volatilidad cero, riesgo
  de división por cero), datasets de una sola fila. El motor debe
  sobrevivir a todo esto sin crashear y sin devolver números imposibles
  (capital negativo, por ejemplo).
- **Test de reproducibilidad/determinismo**: correr el mismo backtest dos
  veces con los mismos datos debe dar exactamente el mismo resultado. Si
  no, hay algo no determinista escondido que rompe la confianza en
  cualquier número que el motor reporte.

**Un hallazgo real del propio proceso**: al construir estas pruebas más
exigentes, se encontró y corrigió un bug real donde una edición anterior
había roto silenciosamente la función `load_csv` (quedó huérfana sin su
declaración `def`, un error que Python no señala hasta que se intenta usar
la función). Ningún test anterior lo detectó porque ninguno la ejercitaba
end-to-end después de esa edición puntual -- la lección concreta: correr
la suite de tests completa después de CADA cambio, no solo probar la
función que se acaba de tocar.

## Las 10 mejoras agregadas (segunda ronda)

1. **Walk-forward rolling** (`validation.py` → `rolling_walk_forward_validate`):
   en vez de un solo corte in-sample/out-of-sample, corre la validación
   sobre varias ventanas consecutivas del historial y calcula un % de
   "consistencia" (en cuántas ventanas la estrategia fue rentable fuera de
   muestra). Con BTC 2020-2024 y momentum/agresivo: 60% de consistencia
   sobre 5 ventanas.

2. **Costos realistas para cuenta chica** (`risk_manager.py`): comisión
   mínima fija además del %, tamaño mínimo de lote, y un mínimo operable
   por debajo del cual la operación se rechaza. Con u$s50 de capital y
   mínimos realistas, 84 de 89 señales fueron rechazadas — la razón
   numérica de por qué cuentas muy chicas no pueden operar en serio.

3. **Detección de régimen de mercado** (`regime.py`): calcula ADX y
   clasifica cada vela como "tendencia" o "lateral". Las estrategias de
   tendencia/momentum solo operan cuando el régimen lo confirma. Mejoró el
   resultado de momentum/agresivo en BTC de +59% a +85%, con menos
   drawdown (-16% → -10%).

4. **Kill-switch manual** (`safety.py` → `ManualKillSwitch`, más
   `kill_switch.py` como CLI): un freno de emergencia manual, separado del
   circuit breaker automático, que se activa creando un archivo de
   control. `python3 kill_switch.py activar "motivo"` / `desactivar` / `estado`.

5. **Monitoreo de salud / heartbeat** (`health.py`): `check_data_gaps`
   detecta huecos sospechosos en el historial; `Heartbeat` (para uso en
   vivo) detecta cuándo el feed de precios dejó de mandar datos frescos.

6. **Log de experimentos** (`experiment_log.py`): cada corrida con
   `--log-experiment` se guarda en `experiments.csv`, con todos los
   parámetros y métricas. `best_experiments()` devuelve el ranking de las
   mejores corridas probadas hasta ahora.

7. **Reporte HTML automático** (`report.py`): `--html-report archivo.html`
   genera un reporte autocontenido (un solo archivo) con la tabla de
   métricas y el gráfico de la curva de capital embebido, incluyendo una
   alerta visual si la estrategia perdió contra buy & hold.

8. **Redundancia de fuente de datos** (`data_utils.py` →
   `load_csv_with_fallback`): intenta cargar de una lista de rutas en
   orden y usa la primera que funcione, en vez de romperse si la fuente
   principal falla. También soporta políticas de datos faltantes
   (`ffill`, `interpolate`, o `raise` por defecto).

9. **Exportación fiscal** (`tax_export.py`): genera un CSV operación por
   operación con fechas, resultado en USD (y su referencia en ARS si se
   pasa una cotización), más un resumen agregado — pensado para
   entregarle a un contador. **No es asesoramiento fiscal.**

10. **Límite de exposición por correlación** (`portfolio.py`): backtesting
    multi-activo (`PortfolioBacktester`) que calcula la correlación
    reciente entre activos y reduce el tamaño de una posición nueva si ya
    hay posiciones abiertas fuertemente correlacionadas con ella. Probado
    con activos sintéticos: con correlación real de 0.76, 11 operaciones
    se escalaron; con correlación real de 0.015, ninguna se escaló — el
    ajuste distingue bien el riesgo concentrado del diversificado.

## Tercera ronda: infraestructura y producción

Las 5 mejoras pendientes de la ronda anterior, ya implementadas y probadas
(21/21 tests pasando en total):

1. **`requirements.txt`**: versiones exactas fijadas (pandas 3.0.2, numpy
   2.4.4, matplotlib 3.10.8, pyyaml 6.0.3, requests 2.33.1) para que el
   entorno sea reproducible igual en cualquier máquina.

2. **Logging real** (`app_logger.py`): reemplaza los `print` internos por
   logs con timestamp, nivel de severidad y módulo de origen. Integrado en
   los puntos más críticos para auditar después (circuit breaker,
   kill-switch). Uso: `from app_logger import get_logger`.

3. **Archivo de configuración YAML** (`config_ejemplo.yaml`): alternativa
   a pasar todo por línea de comandos. `python3 run_backtest.py --config
   config_ejemplo.yaml`. Los flags de CLI pasados además del `--config`
   sobreescriben lo que diga el YAML (probado: cambiar `--profile` por
   CLI pisó correctamente lo que decía el archivo).

4. **Capa de broker abstracta** (`broker.py`): `BrokerBase` define el
   contrato común. `PaperBroker` es una implementación funcional que
   simula compras/ventas en memoria (probada de punta a punta: ejecuta
   órdenes, calcula comisión y slippage, y rechaza limpiamente saldo
   insuficiente o ventas sin posición). `LibertexBrokerAdapter` es un
   esqueleto documentado de cómo sería el adaptador real -- no conectado,
   porque este sandbox no tiene acceso a ese dominio ni credenciales.

5. **Bot de alertas** (`alerts.py`): `ConsoleAlertChannel` (probado en
   vivo), `TelegramAlertChannel` y `EmailAlertChannel` (código completo y
   correcto, pero no probados en vivo por no tener acceso de red a esos
   servicios ni credenciales reales -- se verificó que Telegram al menos
   falla de forma segura, sin crashear, cuando no hay conexión).

6. **CI** (`.github/workflows/tests.yml`): corre `tests.py` y dos
   backtests de punta a punta automáticamente en cada push/PR. Sintaxis
   YAML validada y cada paso simulado localmente con éxito -- no pude
   correr GitHub Actions real desde este sandbox, pero la lógica está
   verificada paso por paso.

7. **Dockerización** (`Dockerfile`, `.dockerignore`): empaqueta el
   entorno completo, instala dependencias fijas, corre los tests como
   parte del build (si algo está roto, la imagen ni se termina de
   construir), y por defecto corre un backtest de ejemplo. **No pude
   compilar la imagen en este sandbox** (no hay Docker instalado acá) --
   cada comando individual del Dockerfile sí se probó suelto y funcionó.

8. **Análisis multi-timeframe** (`multi_timeframe.py`): confirma la señal
   diaria con la tendencia del timeframe semanal antes de operar, usando
   forward-fill con shift para evitar look-ahead bias (verificado con un
   test específico: la señal filtrada está en 0 hasta que existe al menos
   una vela semanal ya cerrada). Combinado con el filtro de régimen: BTC
   momentum/agresivo pasó de +59%/-16% de drawdown a **+77%/-9.4%** de
   drawdown.

## Cuarta ronda: preparado para continuar desde otra PC (con git/API real)

- **`live_runner.py`**: junta todas las piezas (estrategia, gestor de
  riesgo, bróker, alertas, kill-switch, heartbeat, **circuit breaker**,
  **filtro de régimen** y **multi-timeframe**) en un solo loop operable.
  Corre hoy contra `PaperBroker`, reproduciendo datos históricos como si
  llegaran en vivo. El día que se conecte un bróker real, el cambio es
  una sola línea (qué clase de bróker se instancia) — el resto del loop
  no cambia. Probado de punta a punta con datos reales.
- **`.env.example`**: plantilla de variables de entorno para credenciales
  (bróker, Telegram, email). Copiar a `.env`, completar ahí, nunca subir
  ese archivo a git (ya está en `.gitignore`).
- **`LibertexBrokerAdapter` actualizado**: ahora lee las credenciales
  desde variables de entorno (`BROKER_API_KEY`, `BROKER_API_SECRET`) en
  vez de solo aceptarlas como parámetro. Si no las encuentra, avisa por
  log en vez de fallar silenciosamente.
- **Repositorio git inicializado** con historial de las 6 rondas.

### Pasos concretos para conectar un bróker real

1. `pip install -r requirements.txt`
2. Correr `python tests.py` — confirmar que las 30 pruebas pasan.
3. Copiar `.env.example` a `.env` y completar credenciales (Ripio:
   `RIPIO_API_TOKEN` + `RIPIO_API_SECRET`; opcionalmente Telegram).
4. En `broker.py`, completar los métodos de `RipioBrokerAdapter`
   reemplazando cada `raise NotImplementedError` por la llamada HTTP
   real, siguiendo la documentación oficial (`apidocs.ripiotrade.co`).
5. Probar el adaptador de forma aislada primero (solo lectura:
   `get_current_price` y `get_balance`) antes de `place_order`.
6. Cuando haya confianza, reemplazar `PaperBroker` por el adaptador real
   en `live_runner.py` y correrlo primero con montos mínimos.


## Quinta ronda: broker real identificado (Ripio)

A diferencia de Libertex, **Ripio sí tiene una API pública, real y
documentada** para exactamente esto (`apidocs.ripio.com` /
`apidocs.ripiotrade.co`):

- Cubre wallet + exchange: acceso a balances, órdenes de compra/venta,
  orderbooks, datos de mercado en tiempo real e historial de transacciones.
- Autenticación con API Token + Secret Key que generás vos mismo desde tu
  cuenta -- sin necesidad de partnership ni aprobación previa.
- **Permisos granulares por token**: Lectura, Compra/Venta, y Retiros de
  criptomonedas son permisos independientes. Para este motor, generar un
  token con Lectura + Compra/Venta únicamente -- **nunca** darle permiso
  de retiro a las credenciales que usa el bot.
- Ejemplos de código oficiales en `github.com/ripio/trade`.

Se implementó `RipioBrokerAdapter` en `broker.py` contra la API real
(firma HMAC oficial + ticker público verificado en vivo). Los endpoints
privados (balance/órdenes) quedan listos; `place_order` exige
`allow_trading=True` de forma explícita.

**Otro bug real encontrado y corregido en el proceso**: al insertar
`RipioBrokerAdapter` antes de `LibertexBrokerAdapter`, la edición borró la
línea `class LibertexBrokerAdapter(BrokerBase):`, dejando su `__init__`
huérfano dentro de la clase anterior -- Python lo interpretó como un
segundo constructor de `RipioBrokerAdapter` que pisaba al primero. Se
agregó un test específico (`test_broker_adapters_dont_leak_into_each_other`)
que hubiera atrapado esto automáticamente. Este es ya el tercer bug de este
mismo patrón encontrado durante el desarrollo -- la lección se repite: las
inserciones de texto justo antes de una declaración `class`/`def`
existente son el punto más frágil de cualquier edición, y merecen doble
chequeo o un test dedicado.

## Sexta ronda: confiabilidad y robustez general del sistema

Esta ronda no fue sobre agregar estrategias nuevas, sino sobre lo que
separa un motor que "funciona en la demo" de uno confiable de verdad
(30/30 tests pasando en total):

1. **Persistencia de estado** (`state_store.py`): guarda posiciones
   abiertas, capital, stop loss/take profit en disco con escritura
   atómica (write-then-rename). Probado: sobrevive a un reinicio
   simulado del proceso y a un archivo corrupto (arranca limpio en vez
   de crashear).

2. **Reconciliación** (`reconciliation.py`): compara lo que el motor cree
   tener abierto contra lo que el bróker realmente reporta. Detecta 4
   tipos de desfasaje: todo coincide, posición fantasma interna, posición
   no registrada (operación manual por fuera del bot), y diferencia de
   cantidad en el mismo símbolo.

3. **Resiliencia ante fallas de red** (`resilience.py`): decorador de
   reintentos con backoff exponencial, que distingue errores transitorios
   (reintenta) de errores permanentes como credenciales inválidas
   (falla inmediato, sin insistir en algo que no se va a arreglar solo).

4. **Idempotencia de órdenes**: `PaperBroker.place_order` ahora acepta
   `client_order_id` -- un reintento de red con el mismo id devuelve el
   resultado original en vez de ejecutar la compra/venta dos veces.

5. **Apagado ordenado** (`live_runner.py`): maneja señales SIGINT/SIGTERM
   del sistema operativo -- probado con una señal real (`kill -INT`), no
   solo simulada: termina el tick en curso, guarda el estado, y sale limpio.

6. **Significancia estadística** (`significance.py`): compara el
   resultado real contra cientos de estrategias con entradas aleatorias
   pero la misma gestión de riesgo. Hallazgo real: momentum/agresivo en
   BTC quedó en el **percentil 95** (ventaja de timing genuina, no solo
   gestión de riesgo), mientras que en EUR/USD quedó en el **percentil 24**
   (peor que la mayoría de las entradas al azar -- ahí no hay ventaja real).

7. **Sensibilidad de parámetros** (`sensitivity.py`): corre un grid de
   variaciones de parámetros y avisa si el resultado cambia de signo
   (ganancia↔pérdida) entre valores cercanos -- señal de sobreajuste.
   Tendencia en BTC resultó robusta (9/9 combinaciones rentables, sin
   cambio de signo); momentum en EUR/USD perdió de forma consistente en
   las 4 variantes probadas (mala ahí, pero no fue "mala suerte" con un
   valor puntual).

8. **Más datos reales**: se sumó `real_data/eurusd_daily.csv` (EUR/USD
   diario, 2012-2022, ~2.400 velas) para tener un mercado de forex real,
   muy distinto en comportamiento a las cripto/acciones que ya había.

**Otro bug real encontrado y corregido**: al integrar el apagado
ordenado, `import signal` (el módulo de Python) colisionó con la variable
local `signal` (la señal de la estrategia dentro de `live_runner.py`),
rompiendo el manejo de señales del sistema operativo. Se corrigió con un
alias (`import signal as system_signal`). Es un bug distinto en su origen
a los tres anteriores (no fue una edición que borró una declaración), pero
la misma lección de fondo: probar de punta a punta después de cada
integración nueva, no solo cada pieza por separado.

## RipioBrokerAdapter (implementado)

Adaptador real contra Ripio Trade (`https://api.ripio.com/trade/...`):

- Firma HMAC-SHA256 + Base64 según ejemplos oficiales (`github.com/ripio/api`).
- `get_current_price` — ticker público (sin credenciales).
- `get_balance` / `get_open_positions` — endpoints privados firmados.
- `place_order` — implementado, pero **bloqueado por defecto**
  (`allow_trading=False`) para no mandar órdenes reales por accidente.

```bash
pip install -r requirements.txt
cp .env.example .env   # completar RIPIO_API_TOKEN y RIPIO_API_SECRET

# Solo lectura (público):
python ripio_smoke.py --pair BTC_USDC

# Lectura privada (requiere .env):
python ripio_smoke.py --pair BTC_USDC --balances
```

Crear el token en https://trade.ripio.com/market/api/token con permisos
de **Lectura** (+ Compra/Venta solo cuando vayas a operar). Nunca retiro.

## Séptima ronda: entorno con red real, fixes de fecha/hora, y paper trading en vivo

Esta ronda se hizo ya en una máquina con acceso real a internet (a
diferencia de rondas anteriores). Eso permitió *verificar* cosas que
antes solo se podían dejar documentadas como "próximo paso", y encontró
tres bugs reales en el proceso:

1. **Bug de fin de semana en los datos sintéticos** (`data_utils.py`):
   `pd.bdate_range(end=hoy, periods=n)` devuelve un elemento menos que
   `n` cuando `hoy` cae sábado o domingo (comportamiento observado en
   pandas 3.0.2). Esto rompía `tests.py` y hasta la demo por defecto de
   `run_backtest.py` **cualquier fin de semana** — se reprodujo en vivo
   (era sábado) y se corrigió pidiendo un rango más largo y recortando al
   final para garantizar siempre exactamente `n` fechas.

2. **Ticker público de Ripio confirmado con precio real en vivo**
   (`ripio_smoke.py`, sin credenciales) — la nota de "sandbox sin salida
   de red" de secciones anteriores de este README ya no aplica al entorno
   actual.

3. **Lectura privada (`--balances`) sigue sin funcionar** — no es un bug
   de firma (se comparó byte a byte contra el ejemplo oficial de
   `github.com/ripio/api/authentication/python`, coincide exacto) ni de
   reloj (se probó con el timestamp exacto del servidor y falla igual).
   Se encontró y corrigió de paso un problema real de sincronización de
   hora (`broker.py`): el reloj local de esta máquina está ~16.7s
   atrasado, y el resync automático contra `/trade/public/server-time`
   puede devolver 429 (rate limit compartido con otros endpoints
   públicos) justo después de llamar al ticker. Ahora se aprovecha el
   campo `timestamp` que Ripio devuelve en TODAS sus respuestas (éxito o
   error) para mantener el offset al día sin depender solo de ese
   endpoint. Pero el error que queda (`401 / error_code 40105
   "Unauthorized"`) es distinto al de un token inventado
   (`"Invalid token"`) o un timestamp inválido (`"Invalid timestamp"`) —
   apunta a algo del lado de la cuenta (permiso de Lectura no confirmado,
   token no activado, o restricción de IP), no del código.

4. **Paper trading con precios reales en vivo, implementado**
   (`live_runner.py` → `run_live_polling`): hasta ahora el runner "en
   vivo" solo podía reproducir un CSV histórico. Ahora hay un segundo modo
   que en cada intervalo pide el precio real a un `price_source` (ej.
   `RipioBrokerAdapter`, solo lectura del ticker público) y lo procesa
   como un tick nuevo, mientras la ejecución sigue siendo 100% simulada
   con `PaperBroker` — **nunca coloca una orden real**, sin importar qué
   bróker se use como fuente de precio. Se precarga historial real
   (`--csv`) para que las medias móviles/ADX/filtro semanal tengan
   contexto desde el primer tick. La lógica de decisión por-tick se
   extrajo a una sola clase compartida (`_LiveEngine`) entre el replay de
   CSV y el polling en vivo, para no duplicarla (varios de los bugs
   reales de rondas anteriores vinieron justo de tener la misma lógica
   copiada en dos lugares).

   Limitación a tener en cuenta: un ticker da el último precio, no un
   candle OHLC real, así que cada tick en vivo se aproxima con
   open=último cierre conocido y high/low=envolvente de open/close. Anda
   bien para probar el flujo de decisión de punta a punta con precios
   reales, pero indicadores que dependen fuerte de high/low intradía
   (ADX) son menos precisos que con velas reales.

   Uso:
   ```bash
   python live_runner.py --csv real_data/btc_daily.csv --live-prices \
       --symbol BTC_USDC --strategy momentum --profile moderado --poll-interval 60
   ```

36/36 tests pasando (se agregó `test_live_polling_runs_with_stub_price_feed`,
que usa un feed de precios de prueba sin red para quedar determinista).

## Próximos pasos reales (lo que todavía falta)

1. **Resolver el 401 de lectura privada** (`python ripio_smoke.py
   --balances`) — revisar en el panel de Ripio que el token tenga
   permiso de Lectura confirmado/activado. Si se regenera el token (
   recomendado si en algún momento quedó expuesto fuera de este
   repositorio), volver a probar.
2. **Correr `--live-prices` varias semanas** (no solo un puñado de ticks
   de prueba) antes de pensar en capital real, e idealmente reemplazar la
   aproximación OHLC del ticker por un feed de velas real si Ripio lo
   ofrece.
3. **Activar `allow_trading=True` solo con montos mínimos** cuando la
   lectura privada ya esté verificada.
4. **Más datasets / más regímenes** para no confiar en un solo activo.
5. **Optimización de parámetros siempre con walk-forward**.
6. **Docker y CI nunca se probaron de punta a punta en una máquina real**
   (Docker no está instalado en este entorno; el workflow de GitHub
   Actions nunca corrió en GitHub de verdad, solo se validó localmente
   paso a paso).


## Octava ronda: módulo de noticias de alto impacto (`news_monitor.py`)

Motivación: el mercado cripto opera 24hs cruzando husos horarios, así que
una noticia de alto impacto (hackeo, quiebra de un exchange, un
regulador anunciando una restricción) puede aparecer en cualquier
momento, no solo en "horario de mercado". Se agregó un monitor híbrido:

- **Fuentes**: feeds RSS públicos de CoinDesk y Cointelegraph -- sin API
  key, sin registro.
- **Clasificación**: coincidencia de palabras clave de alto impacto
  (hackeo, quiebra, regulación, delisting, etc.) en el título.
  Deliberadamente simple y auditable en vez de un modelo de sentimiento
  con IA -- se puede ver exactamente qué palabra disparó cada alerta.
- **Modo híbrido** (`NewsAutomationSchedule` + `NewsGuard`): por defecto
  todo es **manual** -- el motor alerta por el mismo canal de alertas que
  ya existía (`alerts.py`) y el humano decide si frenar con el
  kill-switch. Se pueden configurar ventanas horarias UTC (ej. de
  madrugada, cuando es menos probable estar mirando el teléfono) donde,
  además de alertar, el sistema **pausa automáticamente la apertura de
  posiciones nuevas** por un tiempo configurable (`cooldown_minutes`).
  Nunca coloca ni cierra una orden por sí solo bajo ningún modo -- mismo
  principio conservador que el circuit breaker y el kill-switch manual.

Uso:
```bash
python live_runner.py --csv real_data/btc_daily.csv --live-prices \
    --symbol BTC_USDC --strategy momentum --profile moderado \
    --news-alerts --news-auto-window 22-6 --news-cooldown-minutes 60
```
Sin `--news-auto-window`, el modo es siempre manual (solo alerta, nunca
pausa solo). Se puede repetir `--news-auto-window` para varias ventanas.

**Bug real encontrado y corregido en el proceso**: `NewsMonitor` usaba
`feeds or DEFAULT_FEEDS`, y en Python una lista vacía `[]` es "falsy" --
eso hacía que pasar `feeds=[]` (un valor válido, "sin feeds") cayera
igual a los feeds reales por defecto. Un test que pretendía no tocar la
red terminaba llamando a CoinDesk/Cointelegraph de verdad. Corregido a
comparar contra `None` explícitamente, con un test de regresión dedicado.

42/42 tests pasando. Probado también manualmente contra los feeds RSS
reales: detectó correctamente noticias reales de alto impacto vigentes
al momento de la prueba (quiebra de un pool de minería, una cuenta de X
hackeada, un arresto por hackeo bancario).


## Novena ronda: datasets y backtests sobre pares reales de Ripio Argentina (ARS)

Se confirmó contra la documentación oficial (`apidocs.ripiotrade.co`) que
Ripio Trade **no publica un endpoint público de velas históricas** (solo
ticker de 24hs, orderbook y trades recientes). Se confirmó también contra
`/trade/public/pairs` cuáles son los pares habilitados para Argentina:
`BTC_USDC`, `ETH_USDC`, `USDC_ARS`, `USDT_ARS`, y varios más contra USDC
-- no hay pares directos tipo `BTC_ARS` en el book real de Ripio (el
camino real es cripto→USDC→ARS).

Para poder backtestear igual sobre lo que le importa a un usuario
argentino, se construyeron dos datasets combinando fuentes **reales y
públicas** (`build_ars_datasets.py`), no datos inventados:

- `real_data/usdc_ars_daily.csv`: tipo de cambio USD/ARS real (Yahoo
  Finance, ticker `ARS=X`, sin API key), usado como proxy de USDC_ARS ya
  que USDC está pegado 1:1 al dólar. 1301 velas reales, 2021-07-25 a
  2026-07-24 -- incluye la devaluación de diciembre de 2023.
- `real_data/btc_ars_daily.csv`: BTC re-denominado en pesos, aplicando el
  tipo de cambio ARS real del mismo día a la vela real de
  `real_data/btc_daily.csv` (BTC/USD). Es una aproximación (no hay book
  nativo BTC/ARS con ese historial) documentada explícitamente en el
  script -- preserva la volatilidad real de BTC, solo la re-denomina.

**Resultado real y honesto** (perfil moderado, comisión y slippage
incluidos, igual que el resto de los backtests de este proyecto): en
**ambos** pares ARS, durante 2021-2024, **comprar y mantener (dolarizarse)
le ganó a las cuatro estrategias activas** por un margen enorme (ej.
momentum en USDC_ARS: +485% la estrategia vs. **+1454%** de solo tener
USDC). Tiene sentido y es una conclusión valiosa para la propuesta: en un
escenario de devaluación sostenida del peso, cualquier tiempo que la
estrategia pasa "afuera" de la posición dolarizada cuesta carísimo -- el
valor real de este motor está en pares cripto-cripto genuinamente
volátiles y de dos vías (BTC_USDC, ETH_USDC), no en el par ARS de
entrada/salida, que para la mayoría de los usuarios ya funciona mejor
simplemente como reserva de valor. Mostrar este resultado tal cual (en
vez de ocultarlo) es parte de la misma política de honestidad del resto
del proyecto.


## Décima ronda: optimización de parámetros con walk-forward obligatorio

`param_optimizer.py` prueba una grilla de parámetros (ej. distintos
períodos de media móvil) y **rankea cada combinación exclusivamente por
su retorno promedio FUERA de muestra**, en varias ventanas walk-forward
-- nunca por el resultado sobre el 100% de los datos. Esto era un
pendiente marcado desde rondas anteriores ("la optimización de
parámetros siempre debe validarse con walk-forward, nunca sobre el 100%
de los datos"). Estructuralmente no existe en el módulo ninguna función
que rankee por el dataset completo -- no se puede pedir ese atajo por
accidente.

Ejemplo real (estrategia `tendencia` sobre BTC/USD real, grilla de
medias rápida/lenta, 5 ventanas walk-forward):

```
{'fast': 10, 'slow': 40} -> retorno_prom_oos:  0.92%, consistencia: 60.0%  <- recomendado
{'fast': 30, 'slow': 40} -> retorno_prom_oos:  0.66%, consistencia: 40.0%
{'fast': 30, 'slow': 60} -> retorno_prom_oos:  0.58%, consistencia: 40.0%
...
{'fast': 20, 'slow': 50} -> retorno_prom_oos: -0.27%, consistencia: 40.0%
```

Notar que ningún valor es espectacular -- es precisamente el punto: un
número modesto pero validado fuera de muestra es más confiable que un
número grande logrado optimizando sobre todo el histórico (que es
exactamente el tipo de resultado inflado que este módulo evita mostrar
como si fuera bueno).

44/44 tests pasando.


## Onceava ronda: capa de integración con la billetera + aislamiento multi-usuario

Pensado directamente para la propuesta de integración a Ripio (ver
sección de negocio de este README): dos módulos que definen cómo esto se
conectaría a una billetera real sin mover fondos fuera de la app, y cómo
se comportaría con más de un usuario a la vez.

- **`wallet_integration.py`**: `WalletBalanceProvider` es el contrato que
  el backend de una billetera (Ripio) implementaría contra su propio
  ledger -- `reserve_for_trading` / `release_from_trading` /
  `settle_trade_result` son reclasificaciones contables dentro de la
  MISMA cuenta del usuario, nunca una transferencia externa.
  `SimulatedWalletBalanceProvider` es una implementación de referencia en
  memoria para demos y tests.
- **`multi_user.py`**: `UserSessionManager` + `UserTradingSession` dan a
  cada usuario su propio bróker (fondeado desde su asignación reservada),
  su propio circuit breaker y su propio kill-switch -- nunca un solo pool
  de decisiones para todos.

**Bug real encontrado y corregido antes de que llegara a los tests**: al
diseñar `UserTradingSession`, instanciar `ManualKillSwitch()` sin
parámetros usa por defecto un archivo de control COMPARTIDO
(`.KILL_SWITCH`). Sin pasarle un `control_file` propio por usuario
(`.KILL_SWITCH_{user_id}`), activar el kill-switch de un usuario hubiera
activado el de todos -- exactamente el bug de aislamiento que este módulo
existe para evitar. Se detectó leyendo el código de `safety.py` antes de
escribir el test de integración, no después de que fallara. El test
`test_user_sessions_are_fully_isolated` es la prueba de regresión
dedicada a este caso.

49/49 tests pasando.


## Doceava ronda: build de Docker verificado de punta a punta (CI)

El Dockerfile nunca se había podido compilar en ningún entorno de
desarrollo de este proyecto (sin Docker instalado localmente) -- solo se
había revisado línea por línea a mano. Se agregó un job `docker-build` al
workflow de GitHub Actions (`.github/workflows/tests.yml`) que:

1. Construye la imagen (`docker build`) -- el propio Dockerfile corre los
   49 tests como parte del build, así que si algo está roto la imagen ni
   se termina de construir.
2. Corre el backtest de ejemplo dentro del container ya construido.

Los runners de `ubuntu-latest` de GitHub Actions ya traen Docker
instalado, así que esto valida de punta a punta -- por primera vez de
verdad -- que la imagen compila y corre, sin necesitar Docker en ninguna
máquina de desarrollo. YAML validado localmente antes de pushear.

Limitación conocida (preexistente, no introducida en esta ronda): el
Dockerfile corre `tests.py` durante el build, y uno de esos tests
(`test_ripio_public_ticker_live`) hace una llamada de red real al ticker
público de Ripio. Si esa llamada falla por un problema transitorio de red
justo durante el build, la imagen no compila -- un candidato a mejora
futura sería aislar ese test específico del build de Docker.


## Treceava ronda: primera corrida real de paper trading en vivo (en curso)

Arrancado `live_runner.py --live-prices` contra dos pares reales de
Ripio, corriendo en simultáneo en background:

- **BTC_USDC** / momentum / agresivo (semilla: `real_data/btc_daily.csv`)
- **ETH_USDC** / tendencia / moderado (semilla nueva: `real_data/eth_daily.csv`,
  histórico real de ETH/USD vía Yahoo Finance, mismo método que
  `usdc_ars_daily.csv`)

Ambos con `--news-alerts` activado en modo automático de 24hs. En el
primer chequeo (esperable: la primera vez que se consulta un feed, TODO
lo que ya estaba publicado se ve como "nuevo") encontraron noticias
reales de alto impacto vigentes al momento de arrancar -- el arresto de
una red de hackeo norcoreana, la quiebra de Poolin, una demanda a BitMEX
-- y activaron correctamente la pausa automática de entradas nuevas en
ambos. Comportamiento esperado, no un bug.

**Limitación honesta**: esto corre en la PC de desarrollo, no en un
servidor. "Paper trading en vivo durante semanas" (pendiente real,
sección de próximos pasos) necesita que la máquina se mantenga prendida
todo ese tiempo -- si se apaga o suspende, el proceso se corta. El paso
siguiente real sería migrar esto a una VPS chica y barata (o a un
scheduled job) para que corra sin depender de que una laptop personal
quede encendida.

**Actualización**: ambas sesiones llevan **17+ horas corriendo sin
interrupción**, sobreviviendo picos de tráfico real (varios 429 de rate
limit de Ripio durante desarrollo intensivo, manejados sin caerse -- el
comportamiento de resiliencia diseñado funcionando en la práctica, no
solo en un test) y siguen reaccionando en vivo a noticias reales del
sector cada vez que aparecen (el cierre de un exchange, prácticas de
seguridad de Binance, entre otras detectadas durante la noche). Sigue
siendo el arranque de la evidencia de "semanas", no la evidencia
completa, pero ya es más que un smoke test de unos minutos.

**Cero operaciones en 17 horas -- por qué esto es esperable, no un
síntoma de que algo esté roto**: revisando los logs, la pausa por
noticias solo estuvo activa ~1 hora en total (2 eventos de 30 minutos
cada uno) -- no explica las otras ~16 horas sin operar. La razón real es
otra: las estrategias de este proyecto están pensadas para velas
**diarias** (medias móviles de 20 a 50 "días"). En un solo día calendario
real, el precio se mueve relativamente poco comparado con el historial de
años que ya tienen esas medias incorporado -- no alcanza para generar un
cruce nuevo o una ruptura. Hace falta que pasen días reales, no solo
horas, para que estas estrategias en particular tengan motivo de generar
una señal nueva. Esto confirma en la práctica la limitación ya
documentada sobre el modo `--live-prices` (un ticker da un precio, no un
candle diario real) -- vale la pena tenerlo listo como respuesta si en la
demo preguntan "¿por qué no operó nada en tantas horas?".


## Visión: más allá de cripto (prueba de concepto, no una promesa)

Pregunta natural al pensar en esto como producto: ¿se podría ofrecer
también compra-venta de acciones o bonos (al estilo eToro), todo
integrado en la misma app? Vale la pena separar dos cosas antes de
mezclarlas en la misma propuesta:

**Por qué NO es lo mismo que integrar cripto.** En Argentina, cripto y
valores negociables (acciones, bonos) están bajo marcos regulatorios
distintos. Ripio ya opera cripto sin ser una sociedad de bolsa; vender
acciones de verdad requiere estar registrado como **Agente de
Negociación ante la CNV** -- capital mínimo, compliance, auditorías, un
trámite de meses o años, no una feature de producto. Ni eToro lo resuelve
solo: además de ser broker-dealer registrado (FINRA/SEC), usan a **Apex
Clearing** como socio de clearing para la liquidación real -- la parte
más pesada (custodia, ejecución, cumplimiento) la tercerizan con alguien
que ya tiene la licencia, no la construyen desde cero. El camino
realista, si esto se persigue algún día, es el mismo patrón: integrarse
vía API con un bróker ya licenciado ("brokerage-as-a-service"), no pedir
una licencia propia.

**Lo que SÍ se construyó y probó como prueba de concepto**: la
arquitectura de bróker de este proyecto (`BrokerBase` en `broker.py`) ya
estaba diseñada para ser agnóstica al activo. Se agregó
`AlpacaBrokerAdapter`, un adaptador real contra Alpaca Markets (acciones
de EE.UU., cuenta de *paper trading* gratuita con solo un email, sin
plata real) -- y **sin cambiar una sola línea** de `strategies.py`,
`risk_manager.py`, `safety.py`, `backtester.py` ni `live_runner.py`, el
mismo motor ya arma señales, calcula tamaño de posición según el perfil
de riesgo, y ejecuta (en modo simulado) sobre AAPL o KO exactamente
igual que sobre BTC_USDC. Probado en `tests.py`
(`test_live_polling_accepts_alpaca_as_price_source`): con un feed de
precios de Apple inyectado, el motor abrió una posición simulada real de
0.59 acciones a $228.11, con stop loss y take profit calculados
correctamente. `live_runner.py --live-prices --broker alpaca --symbol
AAPL` ya funciona en el código -- ejecutarlo en vivo de verdad requiere
crear una cuenta gratuita en alpaca.markets, algo que no se hizo en este
proyecto (no hay una cuenta real, así que a diferencia de Ripio esto no
se pudo validar contra la red real).

**Conclusión para la propuesta**: esto se muestra como evidencia de que
la arquitectura escala más allá de cripto sin reescritura -- no como un
pedido de que Ripio se convierta en agente de bolsa. Mantenerlo como una
sección de visión separada del pedido principal (que sí está en el mismo
terreno regulatorio que Ripio ya pisa hoy).


## Catorceava ronda: qué haría falta para un lanzamiento real (Fases 2 y 3)

Roadmap concreto de qué falta después de un "sí" de Ripio, más allá del
motor ya validado. Fase 1 (legal/negocio) y Fase 4 (producto/UX) quedan
fuera del alcance técnico de este repo. Acá se atacaron Fase 2 completa
y Fase 3 completa:

### Fase 2 -- feed de precios compartido (`price_feed.py`)

Cada sesión de usuario pidiéndole el precio a Ripio por su cuenta no
escala -- ya se había observado un 429 real con solo 2 sesiones
concurrentes (ver Séptima ronda). `SharedPriceFeed` mantiene UN poller de
fondo por símbolo (no por usuario): todos los usuarios que miran el mismo
par comparten una sola llamada de red. Prueba concreta
(`test_shared_price_feed_as_drop_in_reduces_calls_for_two_sessions`): 2
sesiones x 5 ticks generan 10 llamadas reales sin esto, 1 sola con esto.
Es un reemplazo directo de `price_source` en `run_live_polling`, sin
tocar `live_runner.py`.

**Bug de concurrencia propio, encontrado y corregido antes de escribir
los tests**: el primer diseño hacía que el poller de fondo disparara su
primer fetch inmediatamente al arrancar, compitiendo con el fetch
sincrónico del llamador que lo activó -- doble llamada de red en el
arranque de cada símbolo nuevo. Corregido con un lock por símbolo
(double-checked locking) y haciendo que el poller espere el intervalo
completo antes de su primer refresco.

### Fase 3a -- persistencia real (`SQLiteStateStore` en `state_store.py`)

Reemplaza un archivo JSON por usuario por una única base SQLite
compartida (todos los usuarios, una key cada uno). `all_keys()` lista
todos los usuarios con estado guardado -- la base para el monitoreo
centralizado de 3b. `multi_user.py` se actualizó para usar esto en vez de
archivos sueltos.

### Fase 3b -- monitoreo centralizado (`ops_monitor.py`)

`OperationsMonitor` agrega circuit breaker / kill-switch / heartbeat /
reconciliación de TODAS las sesiones activas, y alerta por el mismo canal
que ya usa el resto del sistema. Si el % de usuarios con el MISMO
problema cruza un umbral configurable, escala a una alerta "sistémica"
distinta de las individuales -- la diferencia real entre "a alguien le
fue mal" y "esto le está pasando a todos, hay que revisar si es un
movimiento de mercado real o un bug".

### Fase 3c -- órdenes parciales y estados reales del libro (`broker.py`, `live_runner.py`)

Hasta ahora el motor asumía que toda orden se llena entera al instante.
`PaperBroker` ahora soporta `fill_ratio` para simular llenados parciales
("partially_filled") y órdenes que quedan "open" (resting, sin llenar
todavía) -- con `get_order_status()` para consultarlas más tarde.
`_LiveEngine` se reescribió para: (1) registrar la posición con las
unidades REALMENTE llenadas, nunca las pedidas; (2) no mandar una segunda
orden mientras la primera sigue pendiente; (3) resolver una orden
pendiente en un tick posterior en vez de olvidarla.

**Dos bugs reales encontrados y corregidos en el proceso**:
- `AlpacaBrokerAdapter.place_order` devolvía la cantidad PEDIDA como
  `units` cuando no había `filled_qty` (orden "open") -- hubiera hecho
  que el motor registrara una posición que Alpaca todavía no ejecutó.
  Corregido a 0.0 en ese caso, con test de regresión dedicado.
- El propio `_LiveEngine` (antes de esta ronda) ya tenía el mismo patrón
  de bug latente: usaba la cantidad PEDIDA (`sizing["unidades"]`) para
  registrar la posición interna en vez de la cantidad REALMENTE llenada
  por el bróker -- invisible hasta ahora porque `PaperBroker` siempre
  llenaba entero. Corregido al mismo tiempo que se agregó el soporte de
  llenados parciales.

75/75 tests pasando.

### Fase 3d -- prueba de carga: circuit breaker simultáneo de muchos usuarios

`test_load_many_simultaneous_circuit_breakers_stay_isolated_and_detected`
simula 500 usuarios reales (sesiones completas vía `UserSessionManager`,
persistidas en la misma base SQLite compartida) sometidos al MISMO shock
de mercado (una caída de -15% de equity, igual para todos -- el
escenario real de un crash del activo que todos tienen). Resultado:

- Los 500 circuit breakers se activaron de forma completamente
  independiente -- verificado explícitamente que son instancias
  distintas, nunca una compartida, incluso a esta escala.
- `OperationsMonitor` detectó y escaló correctamente el evento como
  sistémico: 500/500 (100%) usuarios afectados.
- Tiempo total de procesamiento: **0.23 segundos** para las 500 sesiones
  (setup 0.15s + shock 0.01s + `check_all()` 0.07s) -- sin cuellos de
  botella a esta escala.

76/76 tests pasando.


## Quinceava ronda: Fase 4 -- producto (onboarding, historial/ajustes, herramienta de soporte)

- **Mockup interactivo ampliado** (compartido aparte, no en el repo):
  ahora incluye el flujo completo de onboarding (qué es esto, elegir
  perfil con educación en lenguaje simple, consentimiento explícito de
  riesgo con checkbox obligatorio), historial completo de operaciones con
  resumen y filtros, pantalla de ajustes (cambiar asignación/perfil, ver
  comisiones), y una pantalla de detalle que explica en criollo por qué
  el motor se pausó (circuit breaker o noticia de alto impacto), con
  demos de los 3 estados.

- **`support_tools.py`** (código real, no mockup):
  `generate_user_support_snapshot` arma un reporte consolidado para que
  un agente de soporte no tenga que ir a buscar cada dato a un lugar
  distinto -- saldo de billetera, balance del bróker, posiciones
  abiertas, estado de circuit breaker/kill-switch, reconciliación, y
  últimas operaciones. Marca advertencias explícitas para el agente
  cuando hay algo que no debería confirmarse al usuario sin escalar
  primero (ej. un desfasaje de reconciliación).

- **Corrección propia, no un bug del proyecto**: al escribir
  `support_tools.py` se vio que varios archivos (`live_runner.py`,
  `backtester.py`, `kill_switch.py`) llaman a `ManualKillSwitch.reason()`,
  y al leer `safety.py` de forma incompleta se concluyó erróneamente que
  el método no existía. Se agregó una implementación propia sin notar
  que ya había una al final de la clase -- Python se queda con la última
  definición de un método duplicado, así que la nueva quedó
  silenciosamente ignorada, y el primer test escrito falló al asumir el
  comportamiento equivocado (`""` en vez de `None` cuando está inactivo).
  Corregido eliminando el duplicado y ajustando el test al comportamiento
  real ya existente. Vale la pena dejarlo documentado como recordatorio
  de leer la clase entera antes de asumir que falta algo.

81/81 tests pasando.


## Dieciseisava ronda: cerrando huecos de cobertura de rondas anteriores

Auditoría de qué módulos no tenían NINGÚN test propio (solo se
ejercitaban indirectamente vía otros): `strategies.py` (las 4
estrategias en sí), `validation.py` (walk-forward -- el diferencial
anti-sobreajuste que más se cita en este README) y `stress_test.py`
(validación contra crisis históricas reales, otro punto fuerte del
pitch) no tenían ningún test dedicado. Se agregaron 10 tests nuevos que
verifican directamente: que las 4 estrategias devuelven señales binarias
válidas, que `trend_following` coincide exactamente con el cruce de
medias esperado, que `value_dip_in_uptrend` exige ambas condiciones (no
alcanza con una sola), que `walk_forward_validate` detecta y advierte un
caso de sobreajuste forzado deliberadamente (tendencia limpia in-sample +
flash crash out-of-sample), que `rolling_walk_forward_validate` calcula
la consistencia correctamente y rechaza pedir más ventanas de las que el
dataset soporta, y que `run_crisis_stress_test` sigue cubriendo las 4
crisis históricas conocidas con los datos reales de BTC ya incluidos.

**Bug propio encontrado y corregido al escribir estos tests** (no del
motor): el primer intento de probar `trend_following` construía el
DataFrame de prueba pasando una `Series` con su propio índice (0..79) y
forzando un índice de fechas distinto en el constructor de `pd.DataFrame`
-- pandas realinea por ETIQUETA en esos casos, no por posición, y como
los índices no compartían ninguna etiqueta el resultado quedó en `NaN`
silenciosamente en toda la columna. El test lo detectó solo (comparó
contra un cálculo hecho aparte, sobre la Series original sin ese
problema), sin necesitar debug manual. Corregido construyendo la Series
ya con el índice final desde el principio.

**Segundo bug real encontrado, este sí del proyecto** (no de un test):
al verificar manualmente `run_backtest.py` (el script de entrada
principal, también sin ningún test propio hasta esta ronda), `python
run_backtest.py --config config_ejemplo.yaml` crasheaba con
`UnicodeDecodeError` en esta máquina Windows. Causa: `open(args.config)`
sin `encoding` explícito abre con el codepage del sistema (cp1252 acá) en
vez de UTF-8, y `config_ejemplo.yaml` tiene tildes en sus comentarios.
**El CI nunca lo detectó porque corre en Linux** (UTF-8 por defecto) --
un recordatorio concreto de que "los tests pasan en CI" no es lo mismo
que "funciona en la máquina del usuario". Se corrigió agregando
`encoding="utf-8"` explícito ahí, y de paso en `safety.py`
(`ManualKillSwitch.activate`/`reason`, que tenía el mismo patrón al
guardar el motivo del kill-switch -- no crasheaba hoy porque escribe y
lee en la misma máquina, pero rompería si el archivo se crea en Windows
y se lee dentro de un container Linux, o al revés). Se agregó un test
para `run_backtest.py --config` (tampoco tenía ninguno) que reproduce
exactamente este escenario.

92/92 tests pasando.

## Diecisieteava ronda: bug real detectado por la corrida en vivo, no por un test

Con las sesiones de paper trading en vivo (`BTC_USDC`, `ETH_USDC`) corriendo
sin cortes desde hacía más de 23 horas, `ETH_USDC` generó su primera señal
de compra (`buy 0.522411 ETH_USDC`) -- y el bróker la rechazó dos veces
seguidas por **saldo insuficiente**, pese a que la cuenta tenía capital
disponible. Esto es exactamente el tipo de caso raro que una corrida corta
o un backtest de escritorio difícilmente expone, y que solo aparece
dejando el sistema corriendo contra precios reales el tiempo suficiente.

**Causa real:** `risk_manager.position_size()` calcula un tope de "no
invertir más capital del que hay disponible" (`max_units_by_capital =
capital / entry_price`) usando el precio limpio, sin slippage ni comisión.
Cuando ese tope es el que termina definiendo las unidades a comprar (típico
cuando el ATR es chico y el cálculo por riesgo pediría de más), el
`trade_value` resultante es *exactamente* igual al capital disponible. Pero
`PaperBroker` (y cualquier bróker real) ejecuta con `exec_price = precio *
(1 + slippage)` y suma una comisión encima -- así que el costo real
termina siendo *mayor* que el capital, y la orden se rechaza siempre que
este tope se activa. El tope, pensado como red de seguridad, terminaba
bloqueando la operación por completo en vez de protegerla.

Reproducido de forma aislada y confirmado (capital=$1000, precio=$2000,
ATR=1, perfil moderado -> unidades=0.5, costo limpio=$1000.00 exacto,
costo real con slippage+comisión=$1001.50 -> rechazado).

**Corrección:** se agregó un margen de seguridad chico (0.5% por defecto,
parámetro `capital_safety_margin_pct` en `position_size()`) al tope de
capital, para que quede lugar para el slippage y la comisión que se suman
en la ejecución real. No afecta el caso normal (donde el tope de capital no
es el que decide), solo el caso límite donde antes garantizaba el rechazo.
Test de regresión agregado (`test_position_size_capped_by_capital_survives_slippage_and_commission`)
que reproduce el escenario exacto visto en vivo y verifica, contra un
`PaperBroker` real con slippage y comisión configurados, que la orden ya
no se rechaza.

93/93 tests pasando.

## Dieciochoava ronda: feedback probando el mockup a mano + roadmap de producto

Se probó el mockup interactivo directamente (no solo revisión de código), lo
que sacó a la luz dos huecos de UX que un test automatizado no detecta:

- **Estética genérica en vez de la de Ripio.** Se extrajeron los colores
  reales de ripio.com (violeta eléctrico `#7908FF`, negro puro, rosa
  `#FF7FEB` como acento, botones tipo píldora, tipografía Geist) y se
  reemplazó la paleta anterior. Regla aprendida: para un mockup que se va
  a mostrar como "así se vería integrado", adivinar la estética no alcanza
  -- hay que verificarla contra la marca real.
- **Cambiar un monto o un perfil se aplicaba al toque, sin confirmación.**
  Riesgoso en un producto real (un toque de más reasigna capital). Se
  agregó un paso explícito de "Confirmar asignación" / "Confirmar cambio
  de perfil" en el dashboard y en Ajustes -- el valor mostrado durante el
  arrastre queda marcado como "sin confirmar" hasta que el usuario lo
  confirma a propósito.
- **No había evidencia visual de que el motor estuviera haciendo algo.**
  Se agregó una lista de posiciones abiertas que se mueve sola (precio
  simulado, P&L recalculado) cada pocos segundos, y el gráfico chico del
  estado ahora se redibuja según ese movimiento en vez de ser una imagen
  fija -- pausar "congela" visualmente las posiciones, reanudar las
  vuelve a mover.
- **El historial mostraba "23 operaciones, 14/9" pero la lista real tenía
  10 filas sin datos para expandir.** Se cargaron las 23 operaciones
  reales (14 ganadoras + 9 perdedoras, incluyendo detalle de precio y
  motivo por cada una) y el resumen ahora se calcula desde esos datos en
  vez de estar tipeado a mano -- no puede volver a desincronizarse. Las
  pestañas de filtro (Ganancias/Pérdidas) y el detalle por operación al
  tocarla ahora funcionan de verdad.
- **Pausar no modificaba el saldo mostrado.** Ahora, al pausar, el saldo y
  la asignación se actualizan con el resultado acumulado del lapso
  (ganancia o pérdida), y la pantalla de detalle de pausa muestra un
  resumen real (tiempo activo, posiciones vigiladas, resultado del lapso)
  en vez de solo un texto genérico.

**Pregunta de producto real que surgió probándolo**: al cambiar de perfil
de riesgo con posiciones ya abiertas, éstas siguieron exactamente igual.
Se confirmó que es el comportamiento correcto (no un bug): `position_size()`
se calcula una sola vez, al momento de abrir la operación, y ese cálculo
queda fijo para ella -- un cambio de perfil rige las operaciones
*siguientes*, nunca modifica retroactivamente una posición ya abierta.

**Tres ideas de producto quedaron documentadas como roadmap** (no
construidas todavía, por decisión explícita de priorizar cerrar la
propuesta antes de sumar alcance nuevo): un freno simétrico al circuit
breaker que asegure ganancias al llegar a una meta (hoy el circuit
breaker solo protege a la baja), un modo manual/asistido para el usuario
que prefiere elegir sus propias operaciones usando la misma
infraestructura de riesgo, y soporte real para que un mismo usuario opere
varios pares a la vez bajo un único cupo de posiciones compartido (hoy
cada sesión en vivo opera un solo par -- así corren, por separado,
BTC_USDC y ETH_USDC ahora mismo). Detalle de las tres en
`propuesta_ripio_trading_integrado.md`, sección "Ideas para próximas
iteraciones".

## Diecinueveava ronda: historial persistente entre reinicios + robustez del test runner

Surgió de una pregunta de producto directa: si el usuario (o, en esta
etapa, yo probando la demo) cierra la app o apaga la máquina y el motor se
para, ¿se pierde la continuidad? La respuesta correcta para un producto
real es que el motor de decisión vive en un servidor, no en el dispositivo
del usuario -- eso ya estaba resuelto por diseño (`UserSessionManager`,
`SharedPriceFeed`, etc. son conceptos de backend). Pero probar esa
respuesta reveló un hueco real: **el registro de operaciones cerradas no
sobrevivía a un reinicio del proceso.**

`state_store.py` (`StateStore`/`SQLiteStateStore`) persiste una FOTO del
momento -- posiciones abiertas y capital --, pero nunca guardó el detalle
de cada operación ya cerrada; eso solo vivía en la memoria del bróker
(`PaperBroker.orders`, que se resetea al reiniciar) y como texto libre en
el log. Para un uso intermitente real (activarlo hoy, pausar, retomar en
semanas, y en algún momento pedir un reporte de todo lo operado en el
medio para soporte) esto no alcanza -- un reinicio de proceso volvía
invisible todo lo operado antes de él.

**Se agregó `trade_history.py`** (`TradeHistoryLog`): un registro
append-only en CSV, separado del estado de posiciones a propósito, que
graba cada apertura y cierre (símbolo, motivo, unidades, precio,
resultado, saldo resultante) con marca de tiempo. Al ser append-only y
vivir en su propio archivo, sobrevive intacto a cualquier cantidad de
reinicios del proceso -- se probó abriendo una segunda instancia contra el
mismo archivo simulando un reinicio, y el historial acumulado sigue
completo. Conectado a `_LiveEngine` (activación opcional vía
`--trade-history-path`, sin romper compatibilidad con quien no lo pasa) en
los cuatro puntos donde se resuelve una compra o venta (inmediata o tras
quedar pendiente), sin duplicar la lógica de cálculo de resultado en cada
uno.

**De paso, se encontró y corrigió un problema real en el propio test
runner**: el loop de `tests.py` solo atrapaba `AssertionError` -- un test
que depende de red (el ticker público de Ripio) puede tirar un error de
conexión en vez de una aserción fallida, y como ese tipo de excepción no
se atrapaba, cortaba el script entero antes de llegar a los tests
siguientes, escondiendo su resultado por completo. Se generalizó a atrapar
cualquier excepción, así un test roto o con mala suerte de red nunca vuelve
a esconder a los demás.

95/95 tests pasando.

## Veinteava ronda: primeras operaciones reales cerradas + reporte de estado + kill-switch compartido

**El fix de la ronda anterior (margen de seguridad en `position_size()`)
ya se validó con operaciones reales**: tras reiniciar la sesión de
`ETH_USDC` con la corrección aplicada, el motor completó dos operaciones
de punta a punta (compra y venta) sin ningún rechazo por saldo
insuficiente -- las dos cerraron con una pérdida chica y controlada
(-2.01 y -2.15 sobre un capital de ~1000), exactamente el comportamiento
esperado de un stop loss haciendo su trabajo, no un error. Es la primera
vez que este prototipo ejecuta un ciclo completo de compra+venta contra
precios reales de punta a punta, y el historial persistente (ronda
anterior) lo registró correctamente.

**Se agregó `status_report.py`**: un comando de una sola línea
(`python status_report.py`, sin argumentos) que arma un reporte en texto
plano del estado de todas las sesiones en vivo que encuentra en la carpeta
-- capital, posiciones abiertas, si el kill-switch está activo, y un
resumen del historial de operaciones con las últimas N. Pensado para que
el usuario pueda chequear "¿cómo está esto ahora?" él mismo, sin tener que
revisar logs a mano ni pedirlo.

**Al construirlo apareció un bug real**: `run_live`/`run_live_polling` no
exponían forma de elegir el archivo de control del kill-switch manual --
todas las sesiones usaban el mismo por defecto (`.KILL_SWITCH`). Con dos
sesiones corriendo a la vez (`BTC_USDC` y `ETH_USDC`, como ahora mismo),
esto significa que pausar una a mano pausaría accidentalmente la otra
también -- justo el tipo de acoplamiento que el aislamiento multi-usuario
(`multi_user.py`) ya evita para el producto real, pero que las sesiones
de demo standalone no tenían resuelto. Se agregó `--kill-switch-file` a
la CLI de `live_runner.py` (con el mismo valor por defecto de siempre,
así no rompe nada existente) para que cada sesión pueda tener el suyo, y
se reiniciaron `BTC_USDC`/`ETH_USDC` con `.KILL_SWITCH_BTC_USDC` /
`.KILL_SWITCH_ETH_USDC` propios (el historial de operaciones sobrevivió
intacto al reinicio, como está pensado).

**Corrección sobre la marcha en `status_report.py`**: la primera versión
intentaba adivinar si una sesión tenía kill-switch propio chequeando si
`.KILL_SWITCH_<SÍMBOLO>` existía en disco -- pero ese archivo solo existe
mientras el freno está ACTIVO (es la bandera, no una ruta de config), así
que su ausencia no dice nada sobre qué archivo está mirando la sesión.
Corregido para asumir directamente la convención (siempre reporta sobre
`.KILL_SWITCH_<SÍMBOLO>`), con una nota aclarando la limitación en vez de
un heurístico silenciosamente equivocado.

96/96 tests pasando.

## Veintiunava ronda: auditoría dirigida a los módulos menos revisados

Con la propuesta y el mockup ya cerrados, se recorrieron los archivos que
hasta ahora solo se habían tocado de pasada: `kill_switch.py`,
`tax_export.py`, `state_store.py`, `resilience.py`, `health.py`,
`report.py`, `experiment_log.py`.

- **Bug real**: `kill_switch.py` (el comando para pausar/reanudar a mano)
  siempre apuntaba al archivo de control genérico `.KILL_SWITCH`, sin
  forma de elegir otro -- con el aislamiento agregado en la ronda
  anterior (`--kill-switch-file`), este comando había quedado inútil para
  pausar una sesión puntual: miraba un archivo que esa sesión ya ni
  siquiera consulta. Se agregó `--file` para que apunte al mismo archivo
  que la sesión que se quiere frenar, con test de regresión.
- **Hueco real de integración**: `tax_export.py` existía desde antes pero
  ningún otro módulo lo llamaba, y no tenía ningún test -- estaba
  completamente desconectado de cualquier fuente de datos real. Se agregó
  `pair_trades()` en `trade_history.py`, que convierte el registro plano
  del historial persistente (una fila por compra/venta) al formato de
  "operación completa" que `tax_export.py` necesita, y se verificó el
  camino de punta a punta (incluyendo con los mismos números reales que
  ya generó `ETH_USDC` en esta sesión).
- **Autocorrección**: se sospechó el mismo bug de encoding ya encontrado
  dos veces antes (Windows/cp1252) en `state_store.py`, que abre el
  archivo de estado sin `encoding="utf-8"` explícito. Se probó antes de
  afirmarlo -- `json.dump` escapa por defecto cualquier caracter fuera de
  ASCII a su secuencia `\uXXXX` (una tilde nunca llega a escribirse en el
  archivo tal cual), así que el contenido siempre termina siendo ASCII
  puro y el encoding de apertura no cambia nada en la práctica. **No era
  un bug real**, y se corrige acá para no
  repetir la afirmación sin haber verificado. Se agregó igual el
  `encoding="utf-8"` explícito como buena práctica preventiva (si algún
  día se cambia a `ensure_ascii=False` por legibilidad, ahí sí importaría),
  documentado como mejora de estilo, no como corrección de un bug.
- **Observación sin acción** (para dejar registrada, no urgente): el
  `Heartbeat` de `health.py` se actualiza en cada tick pero nadie llama a
  `is_stale()`/`status()` en el camino de las sesiones standalone de
  `live_runner.py` -- solo lo consulta `ops_monitor.py`, que es parte de
  la arquitectura multi-usuario, no de estos dos procesos de demo. En la
  práctica esto no deja ciego al operador porque cada fallo de red ya se
  loggea individualmente (se vio varias veces en los logs reales de esta
  sesión), así que no se priorizó -- pero si algún día el feed devolviera
  un precio "viejo" sin lanzar una excepción, no habría ninguna alerta
  explícita de eso hoy.
- `resilience.py`, `report.py`, `experiment_log.py` se revisaron sin
  encontrar problemas -- ya tenían manejo de encoding correcto y su
  cobertura de tests existente sigue siendo representativa.

98/98 tests pasando.

## Veintidosava ronda: bug real -- el capital no sobrevivía a un reinicio

Encontrado usando la propia herramienta recién construida (`status_report.py`)
en las sesiones reales: después de reiniciar `ETH_USDC` para aislar el
kill-switch (ronda anterior), el reporte mostró **capital: 1000.00** --
pero esa sesión había cerrado en **991.87** tras sus dos operaciones
reales. El historial de operaciones (`trade_history.py`) seguía
perfecto; el capital no.

**Causa**: `_LiveEngine.__init__` restaura desde el estado guardado las
posiciones abiertas, el stop loss/take profit y la orden pendiente -- pero
nunca el capital. El bróker siempre arrancaba con el `--capital` fijo de
la CLI (1000.0 por defecto), sin importar cuánto quedara realmente de
corridas anteriores. Como ahora mismo no había ninguna posición abierta,
el síntoma fue "solo" un capital incorrecto -- pero si hubiera habido una
posición abierta al reiniciar, el bug hubiera sido peor: el motor
terminaría pensando que tiene la posición Y el capital inicial completo
en efectivo al mismo tiempo, sobreestimando cuánto hay disponible para
la próxima operación.

Es, además, un buen ejemplo de un hueco que dos partes bien testeadas por
separado no garantizan: `state_store.py` ya tenía su propio test
verificando que el capital se guarda y se lee bien (`test_state_survives_simulated_restart`),
pero nadie verificaba que `_LiveEngine` lo tomara y lo aplicara al
bróker real -- el hueco estaba en la integración entre ambos, no en
ninguno de los dos por separado.

**Corrección**: `_LiveEngine.__init__` ahora sobrescribe `self.broker.balance`
con el capital guardado cuando existe un estado previo. Test de regresión
agregado que arma un estado con un capital distinto al `--capital` de
arranque y verifica que gana el guardado. El archivo de estado real de
`ETH_USDC` (que había quedado corrompido a 1000.00 por este bug) se
corrigió a mano a 991.87 -- el valor real, recuperado del historial de
operaciones persistente -- antes de reiniciar la sesión con el fix.

99/99 tests pasando.

## Veintitresava ronda: la misma pista, un bug peor -- posiciones abiertas tampoco sobrevivían al bróker

Siguiendo la misma línea que el bug del capital (ronda anterior), se
probó a propósito el caso que las dos sesiones reales no habían disparado
todavía por pura casualidad de timing: **reiniciar el proceso con una
posición abierta**. Reproducido de forma aislada antes de tocar nada:
`internal_positions` restauraba la posición correctamente, pero
`broker.get_open_positions()` quedaba vacío -- el bróker es una instancia
nueva en cada reinicio, sin memoria de nada anterior, y nada la llenaba
con lo que ya estaba abierto.

Esto es más serio que el bug del capital: `process_tick()` decide si hay
que evaluar la salida (`in_position = self.symbol in
self.broker.get_open_positions()`) mirando SOLO al bróker, no al registro
interno. Con este bug, tras un reinicio con una posición abierta, el
motor pensaría que no hay nada activo y podría intentar abrir una
posición nueva encima de la real, en vez de seguir vigilándola con su
stop loss/take profit -- perdiendo el control de una posición que sigue
existiendo. `reconcile()` lo hubiera detectado en el próximo `force_persist()`
(cada `reconcile_every` ticks) y lo hubiera logueado como error, pero no
lo corrige solo, y el daño (la posible apertura duplicada) ya podría
haber pasado antes de esa próxima persistencia.

**Corrección**: `_LiveEngine.__init__` ahora también carga las posiciones
restauradas directamente en `broker.positions`, no solo en
`internal_positions`. Test de regresión que reproduce exactamente el
escenario (estado guardado con una posición abierta, bróker nuevo tras el
"reinicio") y verifica que el bróker la conoce desde el primer momento.
Las dos sesiones reales no tenían ninguna posición abierta al momento de
este fix, así que no hizo falta ninguna corrección manual esta vez -- se
reiniciaron igual para que el fix quede activo de cara al próximo
reinicio real que sí encuentre algo abierto.

100/100 tests pasando.

## Veinticuatroava ronda: el hallazgo más serio -- un reinicio desactivaba el circuit breaker

Siguiendo la misma línea (¿qué más no sobrevive a un reinicio?), se probó
el caso más delicado de todos: **¿qué pasa si el circuit breaker está
activo -- protegiendo la cuenta -- justo cuando el proceso se reinicia?**
Reproducido antes de escribir ninguna corrección:

1. Se simula una caída del 20% de capital (supera el límite de 15%) -- el
   circuit breaker se activa correctamente (`tripped = True`).
2. Se persiste el estado y se crea una instancia nueva de `_LiveEngine`
   con un `CircuitBreaker` fresco (simulando el reinicio).
3. El circuit breaker nuevo arranca con `tripped = False` -- **el freno se
   había desactivado solo**, sin que ninguna condición real lo justificara.

Causa doble: `CircuitBreaker` se construye nuevo en cada arranque sin que
nada restaure si ya estaba disparado, y `equity_curve` (de donde sale el
pico histórico para calcular el drawdown) también arrancaba vacía en
cada reinicio -- así que el primer tick post-reinicio se convertía en el
nuevo "techo", borrando cualquier caída previa de la cuenta.

Esto es justo el freno de seguridad más destacado en toda la propuesta
("nunca opera fuera de las reglas de riesgo... frena si el capital cae
demasiado") -- que un reinicio del proceso (algo tan simple como el que
se hizo varias veces en esta misma sesión) lo resetee en silencio es el
hueco más serio encontrado hasta ahora, no uno cosmético.

**Corrección**: `force_persist()` ahora también guarda si el circuit
breaker está activo, su motivo, y el pico histórico de equity (un solo
número, no toda la curva -- es lo único que `CircuitBreaker.check()`
necesita). `_LiveEngine.__init__` restaura los tres. Test de regresión
que reproduce el escenario exacto (drawdown real, reinicio, freno debe
seguir activo). Las sesiones reales nunca tuvieron el freno activo, así
que no hizo falta ninguna corrección manual -- se reiniciaron para que el
fix quede activo de cara a cualquier freno real futuro.

101/101 tests pasando.

## Veinticincoava ronda: el mismo bug, un tercer freno afectado -- la pausa por noticias

Cerrando la línea de auditoría de "¿qué otro freno automático no
sobrevive a un reinicio?": `NewsGuard._paused_until` (la pausa temporal
que se activa cuando hay una noticia de alto impacto en ventana
automática) tenía exactamente el mismo problema que el circuit breaker
-- vivía solo en memoria. Reproducido antes de corregir: se simula una
pausa activa, se persiste el estado, y una instancia nueva de `NewsGuard`
tras el "reinicio" reporta `entries_paused() == False` pese a que la
pausa real seguía vigente.

Menos crítico que el circuit breaker en la práctica (la ventana de
vulnerabilidad es la duración del cooldown, 60 minutos por defecto, no
indefinida como un freno manual), pero el mismo hueco real -- y esta
sesión ya vio una pausa automática dispararse de verdad contra noticias
reales (ronda 20), así que no es un escenario hipotético.

**Corrección**: `NewsGuard` gana `get_paused_until()`/`restore_paused_until()`;
`force_persist()` guarda la pausa activa (si hay una) junto con todo lo
demás; `_LiveEngine.__init__` la restaura. Test de regresión que
reproduce el escenario exacto.

Con esto, los tres frenos automáticos del motor (circuit breaker,
kill-switch manual, pausa por noticias) sobreviven todos a un reinicio
del proceso -- el kill-switch manual ya lo hacía desde el principio (es
un archivo en disco, no un objeto en memoria), y ahora los otros dos
también.

102/102 tests pasando.

## Veintiseisava ronda: cierre de la línea -- la referencia de pérdida diaria

Último hueco de esta familia: el circuit breaker chequea DOS cosas
(drawdown desde el pico histórico, y pérdida dentro del día actual). Ya
se había corregido la primera (ronda 24); la referencia de la segunda
(`day_start_equity`/`current_day`) tenía el mismo problema -- vivía solo
en memoria. Un reinicio a mitad de un día que ya venía con pérdida
reseteaba la referencia al equity del momento del reinicio, ocultando esa
caída del chequeo de pérdida diaria (aunque el chequeo de drawdown desde
el pico, ya corregido, seguía funcionando como red de contención de
fondo).

Corregido igual que los anteriores: se persiste y se restaura. Test de
regresión. Con esto, **los cuatro puntos de memoria que definen si el
motor sigue operando con seguridad tras un reinicio** (capital,
posiciones abiertas, estado del circuit breaker completo -- pico +
pérdida diaria --, y pausa por noticias) están todos cubiertos.

103/103 tests pasando.

## Veintisieteava ronda: seguro de ganancias (primera pieza del roadmap, ya construida)

Primera de las tres ideas documentadas como roadmap (ronda 17) que pasa a
ser código real: un freno simétrico al circuit breaker, pero a la suba.
El circuit breaker solo protegía contra pérdidas (drawdown desde el pico,
o pérdida diaria); no había forma de decirle al motor "si llegás a tal
ganancia, hacé algo para asegurarla".

**Primera versión (descartada tras feedback)**: al llegar a la meta,
simplemente dejaba de abrir posiciones nuevas -- la posición ya abierta
seguía viva con su stop loss/take profit normal. El usuario señaló el
problema real: esa ganancia no estaba asegurada de verdad, solo estaba
"flotando" en una posición todavía abierta -- si el precio se daba vuelta
antes de tocar el stop loss propio de esa posición (que no tiene por qué
coincidir con la meta de ganancia), la ganancia se perdía igual. Además,
pausar del todo le pone techo a la ganancia total sin necesidad.

**Diseño final, con lógica de "trinquete" (ratchet)**:

- **`safety.ProfitLock`**: al llegar a la meta, el motor CIERRA la
  posición abierta en ese momento para realizar la ganancia de verdad (no
  solo pausa). `check()` avisa que se llegó a la meta; tras cerrar, se
  llama a `lock_in(nuevo_capital)`, que sube el piso de referencia al
  capital ya realizado y sigue vigilando la PRÓXIMA meta desde ahí. Nunca
  se queda pausado para siempre -- no hay techo para la ganancia total,
  solo un piso que sube en escalones cada vez que se asegura una porción.
- **Alcance**: fuerza el cierre de la posición abierta (a diferencia del
  circuit breaker, que nunca cierra nada solo, solo bloquea entradas
  nuevas) -- son mecanismos distintos a propósito: uno protege deteniendo,
  el otro asegura realizando.
- **Persistencia entre reinicios desde el primer día**: `reference_capital`
  (el piso, que sube con cada aseguramiento) y `times_locked` sobreviven a
  un reinicio del proceso, aplicando la lección de las rondas anteriores
  desde el diseño inicial en vez de como un fix posterior.
- **CLI**: `--profit-lock-pct` (ej. `--profit-lock-pct 25` asegura
  ganancias cada vez que se cruza +25% desde el último aseguramiento).
  Sin la opción, desactivado -- comportamiento idéntico al de antes de
  esta ronda. Activado en las dos sesiones reales (`BTC_USDC`/`ETH_USDC`)
  al 25%.

108/108 tests pasando.

## Veintiochoava ronda: multi-par real con cupo compartido (segunda pieza del roadmap, ya construida)

Segunda de las tres ideas del roadmap (ronda 17) que pasa a ser código
real: que un mismo usuario reparta su asignación entre varios pares a la
vez, compartiendo un único cupo de posiciones simultáneas -- en vez de
una sesión por par, como corrían hasta ahora `BTC_USDC` y `ETH_USDC`.

**Hallazgo de paso, antes de escribir nada nuevo**: `risk_profiles.py`
define `max_open_positions` para cada perfil (3/5/8 según
conservador/moderado/agresivo) desde las primeras rondas del proyecto --
pero **nunca se aplicaba en ningún lado del motor**, ni en `_LiveEngine`
ni en `portfolio.py` (el backtester multi-activo existente). No era un
bug visible porque una sesión de un solo símbolo nunca podía abrir más de
una posición de todos modos -- recién con multi-símbolo hacía falta que
ese número significara algo de verdad.

**Diseño**: en vez de duplicar la lógica de decisión por-tick en una
función nueva (la lección más citada en este README: la lógica copiada
en dos lugares es de donde salieron varios bugs reales), se generalizó
`_LiveEngine` para aceptar uno o varios símbolos con el mismo código:

- `stop_loss`/`take_profit`/`pending_order` pasan de un único valor a un
  diccionario `símbolo -> valor` -- lo único genuinamente por-símbolo.
- Capital, circuit breaker, seguro de ganancias, kill-switch y pausa por
  noticias siguen siendo UN solo objeto compartido -- protegen la cuenta
  completa, no un par en particular (ya lo eran así incluso en la versión
  de un solo símbolo).
- `_mark_to_market()` ahora suma el valor de TODAS las posiciones
  abiertas (cada una a su último precio conocido), no solo la del tick
  actual -- necesario para que el circuit breaker/seguro de ganancias
  midan la cuenta entera.
- Nuevo: `max_positions`, el cupo COMPARTIDO -- antes de abrir una
  posición nueva en cualquier símbolo, se chequea el total de posiciones
  abiertas entre TODOS los símbolos contra este límite, no un conteo por
  símbolo. Por defecto usa el `max_open_positions` del perfil elegido
  (finalmente conectado a algo real).
- `process_tick()` ahora recibe un `symbol` explícito (opcional si el
  motor solo maneja uno, para no romper ninguno de los ~15 tests
  existentes que lo llamaban sin ese argumento).
- `run_live_polling()` se generalizó de la misma forma (acepta un símbolo
  suelto o una lista, con `seed_csv` como dict `{símbolo: ruta}` en ese
  caso) -- un solo ciclo de polling pide el precio de cada símbolo por
  separado (son instrumentos distintos, no hay forma de compartir esa
  consulta) y procesa el tick de cada uno contra la misma cuenta.
- CLI: `--symbols BTC_USDC,ETH_USDC,...` + `--csvs ruta1,ruta2,...` (en
  el mismo orden) reemplazan a `--symbol`/`--csv` para este modo;
  `--max-positions` permite overridear el cupo del perfil.

`run_live` (replay histórico de un CSV) se dejó sin tocar -- el
backtesting multi-activo ya lo cubre `portfolio.py` desde antes, y
mezclar ambos casos en el mismo refactor no aportaba nada.

108/108 → **111/111** tests pasando (3 tests nuevos: cupo compartido
haciendo lo que promete, varios símbolos operando en simultáneo sin
límite artificial cuando no se pide uno, y una corrida de punta a punta
con dos símbolos bajo un feed de prueba).

También en esta etapa: las tres sesiones en vivo (`BTC_USDC`, `ETH_USDC`,
y una tercera combinando ambas bajo una sola cuenta con cupo compartido,
para demostrar multi-par de verdad) sobrevivieron un corte real de casi
dos días (la máquina se apagó/durmió sin ningún error de código antes del
corte) -- al reiniciarlas, todo el estado volvió exactamente como había
quedado (capital, historial, pausa por noticias ya vencida
correctamente). No fue una prueba planeada, pero es la validación más
realista posible de todo lo persistido en las últimas rondas.

## Veintinoveava ronda: modo manual/asistido (tercera y última pieza del roadmap, ya construida)

Tercera y última de las tres ideas del roadmap (ronda 17): para el
usuario que ya sabe operar y prefiere elegir él mismo cuándo entrar y
salir, en vez de dejarlo en manos de la estrategia automática -- sin
perder ninguna de las protecciones que ya tiene el modo automático.

- **`manual_trading.ManualOrderQueue`**: mismo patrón que `kill_switch.py`
  -- un archivo de control simple que `_LiveEngine` consulta en cada tick,
  sin necesitar ningún servicio nuevo corriendo aparte. Cada símbolo tiene
  a lo sumo una orden manual en cola; se consume (se borra) apenas el
  motor la procesa, sea que se haya ejecutado o rechazado.
- **Una compra manual respeta los MISMOS frenos que una automática**:
  circuit breaker, pausa automática por noticias, y el cupo compartido de
  posiciones -- si algo la bloquea, se descarta con el motivo explicado
  en el log, no queda reintentando sola en silencio. Si el ATR es
  inválido o el tamaño de posición resultante no es viable, también se
  rechaza con motivo explícito (a diferencia de una señal automática, que
  simplemente no hace nada ese tick -- una orden manual explícita merece
  una respuesta, no silencio).
- **Una venta manual (salir) SIEMPRE se deja pasar**, sin importar el
  circuit breaker ni si el precio todavía no tocó el stop loss/take
  profit propios -- salir nunca está bloqueado, el mismo principio que ya
  regía para los frenos automáticos.
- El historial persistente distingue `apertura_manual` de `apertura` (automática) y `manual` de
  `señal_estrategia`/`stop_loss`/`take_profit`/`seguro_de_ganancias` como
  motivo de cierre -- un reporte de soporte puede ver exactamente qué
  decidió la persona y qué decidió la estrategia.
- **CLI**: `manual_order.py comprar/vender SÍMBOLO --file RUTA` (mismo
  estilo que `kill_switch.py`) para dejar pedida una orden desde otro
  proceso, más `manual_order.py estado --file RUTA` para ver qué queda en
  cola. La sesión en vivo se activa con
  `--manual-orders-file RUTA` (sin esta opción, desactivado -- comportamiento
  de siempre).

Con esto, las tres ideas documentadas en la ronda 17 como roadmap
("seguro de ganancias", "multi-par con cupo compartido", "modo
manual/asistido") ya son las tres código real, no solo dirección.

115/115 tests pasando (4 nuevos: la cola persiste y consume una vez,
la CLI funciona de punta a punta, una compra manual respeta el circuit
breaker y queda etiquetada como manual en el historial, y una venta
manual cierra sin importar otros frenos).

## Trigésima ronda: informe consolidado de resultados reales + otro bug de encoding

Con las tres sesiones en vivo acumulando historia real, se agregó
`real_results_report.py`: un informe que junta todas las sesiones que
encuentre (mismo criterio que `status_report.py`) y muestra dos cosas por
separado a propósito -- **fiabilidad de ejecución** (cuánto tiempo lleva
corriendo cada sesión, cuántos rate limits/errores de conexión reales
absorbió sin caerse, cuántas pausas por noticias reales se activaron, si
la reconciliación interna siempre coincidió) y **resultado de las
operaciones cerradas**, aclarando explícitamente que esto último mide
fiabilidad, no rentabilidad -- unos días de paper trading no dicen nada
sobre si una estrategia funciona a largo plazo.

**Bug real encontrado escribiendo el propio informe**: los primeros
números salieron en cero para "pausas por noticias" y "reconciliaciones
OK" pese a que sabíamos que hubo muchas -- las palabras de búsqueda
("automática", "Reconciliación") tienen tilde, y los logs de las
sesiones en vivo se escriben redirigiendo stdout desde la consola de
Windows (cp1252), no con UTF-8 explícito -- el mismo patrón de encoding
que ya rompió dos veces antes en este proyecto, esta vez mordiendo la
propia herramienta que se armó para reportar sobre las otras. Corregido
buscando solo el tramo sin tilde de cada frase.

**Segundo bug real, mismo patrón, en el propio script**: al guardar el
informe con `python real_results_report.py > archivo.md` (la forma obvia
de usarlo), el archivo resultante quedaba con los acentos codificados en
cp1252, no UTF-8 -- fallaba al leerlo como UTF-8 estricto. Corregido
agregando `--output archivo.md`, que el script mismo escribe con
`encoding="utf-8"` explícito, sin depender de cómo la consola de Windows
redirija stdout.

**Resultado real acumulado a la fecha** (ver el informe generado para el
detalle completo): 8 operaciones cerradas en total (2 ganadoras, 6
perdedoras), neto levemente negativo -- pero cero desfasajes de
reconciliación en cientos de chequeos, y más de 300 rate limits/errores
de conexión reales absorbidos sin que ninguna sesión se cayera.

117/117 tests pasando.

**De paso, se actualizó el mockup** (`ripio_trading_mockup.html`, fuera de
este repo) para que refleje lo que el motor real ya hace: un panel de
"Operar manualmente" en el dashboard (comprar un par nuevo respetando el
cupo compartido, vender cualquier posición en cualquier momento), y un
card de "Seguro de ganancias" en Ajustes (activar, ver el piso actual y
cuántas veces se aseguró, y una simulación del cierre+ratchet). Hasta
esta ronda el mockup mostraba visualmente 3 posiciones simultáneas pero
no explicaba estas dos piezas del motor real -- ahora sí.


## Trigésima primera ronda: auditoría del código más nuevo -- dos bugs reales en modo manual y seguro de ganancias multi-par

Pasada de auditoría dirigida específicamente al código más nuevo del
proyecto (seguro de ganancias, multi-par, modo manual), por ser el menos
probado en el tiempo. Se encontró un bug real en `process_tick()`
(`live_runner.py`): `manual_orders.pop_order(symbol)` consume la orden
manual de la cola apenas se lee, sin importar si después hay algo que
hacer con ella. Cuando la orden es una venta y NO hay posición abierta
para ese símbolo (ej. el stop loss ya la cerró en un tick anterior, o el
usuario pide vender algo que nunca llegó a comprarse), la rama de salida
no se ejecuta (`if in_position:` es falso) y la rama de entrada tampoco la
contempla (solo mira `manual_side == "buy"`) -- la orden se pierde sin que
ningún log explique qué pasó. Es una inconsistencia real: **todos** los
demás rechazos manuales (breaker activo, pausa por noticias, ATR
inválido, cupo de posiciones lleno, tamaño no viable) sí quedan
explicados en el log; este caso, el único que involucraba una venta, no.

Corregido agregando una rama explícita (`elif manual_side == "sell":` justo
después del bloque de salida) que loguea el rechazo con motivo. Test de
regresión (`test_live_engine_manual_sell_without_position_is_discarded_not_stuck`):
pide una venta manual sin haber abierto nunca una posición, corre un tick,
y verifica que no se abre nada y que la orden se consume de la cola (no
queda reintentando sola).

118/118 tests pasando.

**Segundo bug, mismo pasada de auditoría, más serio -- el seguro de
ganancias podía bajar su propio piso en multi-par.** El atajo de
`process_tick()` para cuando la meta ya se alcanzó y el símbolo del tick
actual no tiene posición propia ("se banca directo, sin pasar por el
bróker") usaba `self.broker.get_balance()` (solo efectivo) como nuevo
piso de referencia. Eso es correcto en modo un-solo-símbolo (ahí, sin
posición abierta, efectivo == equity total), pero en multi-par es
incorrecto: la meta puede haberse alcanzado por la ganancia NO realizada
de OTRO símbolo que sigue abierto, y el efectivo solo no la incluye.
Bancar solo el efectivo banca un piso más bajo que el equity real que
disparó el gatillo -- y como `ProfitLock.lock_in()` no valida que el
nuevo piso sea mayor al anterior (no tenía por qué hacerlo: nunca antes
podía pasarle un valor menor), en el peor caso el "seguro de ganancias"
terminaba *bajando* el piso del trinquete en vez de subirlo, violando su
garantía central ("nunca hay techo, pero tampoco debería haber retrocesos").

Corregido bancando `equity` (la variable ya calculada como efectivo + TODAS
las posiciones, la misma que `check()` usó para decidir que la meta se
alcanzó) en vez de `self.broker.get_balance()`. Test de regresión
(`test_live_engine_profit_lock_multi_symbol_banks_full_equity_not_just_cash`):
abre una posición grande en SYM_B (ganancia no realizada que por sí sola
supera la meta), procesa un tick de SYM_A (que nunca tuvo posición propia,
y cuyo efectivo solo queda por debajo del piso original), y verifica que
el nuevo piso sea el equity total, no el efectivo.

119/119 tests pasando.

**El mismo bug tenía una segunda instancia**, en el otro lugar donde
`lock_in()` se llama: `_apply_sell_fill()`, justo después de ejecutar la
venta que SÍ cierra la posición que disparó el seguro de ganancias. Ahí
también se bancaba `self.broker.get_balance()` -- correcto si ese es el
único símbolo con actividad, pero si OTRO símbolo sigue con una posición
abierta y valiosa en paralelo, esa venta puntual no la incluye. Mismo
riesgo: bancar un piso más bajo que el equity real, e incluso menor al
anterior. Corregido con `self._mark_to_market()` (ya reflejaba
correctamente la posición recién cerrada, porque el bróker actualiza su
estado antes de que se llame a este método). Test de regresión
(`test_live_engine_profit_lock_sell_close_banks_full_equity_not_just_cash`):
SYM_B queda abierta con una ganancia enorme mientras se cierra SYM_A por
seguro de ganancias, y se verifica que el nuevo piso incluya el valor de
SYM_B, no solo el efectivo resultante de esa venta puntual.

120/120 tests pasando.

**Se revisó el mockup contra ambos hallazgos.** El botón "Vender" del
modo manual solo existe en las filas de posiciones que ya están
renderizadas -- no hay forma de pedir desde la UI una venta de algo que
no está abierto, y el mockup tampoco cierra posiciones solo en segundo
plano (su `tick()` solo mueve precios), así que el primer bug no tenía
equivalente ahí. El segundo sí: la simulación de "Simular: se alcanzó la
meta" calculaba el nuevo piso como `piso anterior + ganancia de la
posición cerrada`, ignorando la ganancia no realizada de cualquier otra
posición que quedara abierta en simultáneo -- el mismo patrón de bug
recién corregido en el motor real, esta vez en la demo. Corregido para
sumar también la ganancia no realizada de las posiciones que siguen
abiertas, y republicado el Artifact.


## Trigésima segunda ronda: rotación por rentabilidad + 6 pares nuevos en vivo

Pregunta real que motivó esta ronda: ¿cuántos pares están operando hoy y
cuántos más se pueden sumar? Se consultó `/trade/public/pairs` de Ripio
en vivo (no research viejo): **15 pares habilitados para Argentina**, de
los cuales 10 son cripto-cripto genuinamente volátiles (aptos para las
estrategias) y 5 son pares de "estacionamiento" (USDC_ARS, USDT_ARS,
USDC_USDT, EURC_USDC, PYUSD_USDC) donde el research de la Novena ronda ya
mostró que comprar y mantener le gana al trading activo. De los 10
volátiles, solo 2 (BTC_USDC, ETH_USDC) tenían sesión en vivo -- quedaban
8 sin usar.

Esto expuso una brecha real de diseño: `max_positions` era un cupo
"primero que llega, ocupa el lugar", no un ranking real por rentabilidad
-- con más pares candidatos que cupo, el orden de llegada decidía, no qué
tan bueno era cada uno. Se construyó `position_ranking.py`: expectancy
(ganancia promedio por operación cerrada) por símbolo, a partir del
historial REAL de `trade_history.py` -- no de la fuerza de la señal del
momento, que mide momentum, no rentabilidad comprobada. Con el cupo
lleno, una señal automática con mejor expectancy probada que el símbolo
más débil abierto ahora puede ROTAR (cerrar el débil, abrir la
candidata). Deliberadamente conservador en dos sentidos: (1) solo aplica
a entradas automáticas, nunca a una compra manual -- una decisión
explícita de una persona no necesita "ganarse" el cupo compitiendo; (2)
la candidata también necesita historial propio suficiente y mejor -- un
símbolo nuevo se gana su lugar por la vía normal (cupo libre), nunca
desplazando a uno probado por conjetura. `MIN_TRADES_FOR_SCORE = 3`: con
menos operaciones cerradas, el score es "desconocido", no "malo". 5 tests
nuevos (unitarios del scoring + integración con el motor, incluyendo el
caso "candidata sin historial no rota" y "compra manual nunca rota").
125/125 tests pasando.

**Datasets reales para los pares nuevos.** De los 8 pares sin usar, se
verificaron contra Yahoo Finance (mismo criterio sin API key que
`build_ars_datasets.py`, y con el nombre completo de cada activo
confirmado contra la respuesta -- no asumido por el ticker): 6 tienen
historial real (`build_new_pairs_datasets.py`): LINK_USDC, LTC_USDC,
UNI_USDC, WLD_USDC, RPC_USDC, RIF_USDC. **HYPE_USDC y LAC_USDC quedan
afuera de esta ronda** -- Yahoo Finance devuelve 0 velas para esos dos
tickers, y no se inventó un dataset para no romper la política de datos
reales del proyecto. Se pueden operar igual en modo manual (no necesita
historial para calentar indicadores).

Se lanzó una cuarta sesión en vivo (`new_pairs`, cuenta y capital propios,
separada de `btc_usdc`/`eth_usdc`/`multi` para no tocar su historial ya
acumulado) con los 6 pares nuevos bajo un cupo compartido de 5 -- primera
prueba real de la rotación por rentabilidad con más candidatos que cupo.

**Hallazgo operativo real (no un bug del motor): stdout bufferizado
ocultaba actividad real.** Al lanzar la sesión nueva redirigiendo la
salida a un archivo (`> live_log_new_pairs.txt`), el log pareció
"congelado" más de 3 horas -- pero el proceso seguía vivo y consumiendo
CPU (confirmado por su estado real, no supuesto). La causa: el
`StreamHandler` de `app_logger.py` escribe a `sys.stdout`, que Python
bufferiza por bloques cuando la salida no es una terminal (el caso de
cualquier redirección `>` a archivo) -- con poco volumen de log
generado, el buffer todavía no se había llenado lo suficiente como para
volcarse a disco. El motor nunca estuvo colgado; el archivo que se
estaba mirando sí mentía sobre cuán reciente era la actividad real. Esto
importa porque `status_report.py` y `real_results_report.py` miden salud
por el timestamp del log -- un log bufferizado puede hacer parecer
muerta una sesión que está perfectamente viva. Se resolvió relanzando con
`python -u` (salida sin buffer); vale para cualquier sesión nueva que se
lance redirigiendo a archivo de acá en adelante.


## Trigésima tercera ronda: bug real de concentración -- un ATR chico convertía "arriesgar 1%" en "apostar el 97% del capital"

Chequeo de rutina de las 4 sesiones en vivo tras otra suspensión de
máquina (mismo patrón ya visto antes: procesos vivos hasta que la máquina
se suspende, ninguno crashea por su cuenta). Al revisar el estado de
`new_pairs` con `status_report.py`, algo saltó a la vista: capital en
efectivo de apenas USDC 3.41, con **el 97% del capital metido en una
sola posición** de LINK_USDC (114.77 unidades a $8.4271).

Antes de asumir que era un bug, se verificó con los números reales del
propio log: stop loss a $8.3879 contra una entrada de $8.3969 -- una
distancia de apenas $0.009, es decir un ATR de práctica ~$0.0045 (0.05%
del precio). Con el perfil "moderado" (arriesgar 1% del capital por
operación), `risk_amount / stop_distance` pedía sizing para ~1111
unidades -- muy por encima de lo que el capital disponible permitía. El
único freno que terminaba actuando era el tope de capital (pensado para
evitar rechazo por slippage/comisión, no para controlar concentración),
que redondeaba hacia abajo hasta "todo lo que el bolsillo permite", no
hasta "el 1% que el perfil dice que hay que arriesgar".

La matemática de "arriesgar 1%" sigue siendo correcta *si el stop se
toca limpio*: unidades × distancia_al_stop = 1% del capital, sin importar
cuántas unidades sean. El problema real es otro: con una posición que
concentra ~97% del capital, cualquier salto de precio que NO respete el
stop de forma prolija (un gap, una noticia, un tick perdido -- todas
cosas que ya se vieron pasar en las sesiones reales de este proyecto)
puede dejar una pérdida muchísimo mayor al 1% pensado, porque la
exposición nominal real no tiene nada que ver con el riesgo teórico.

**Fix**: nuevo tope de concentración en `risk_manager.position_size()`
(`max_position_pct_of_capital`), independiente del tope de capital
existente -- ninguna posición puede pesar más que este % del capital, sin
importar qué tan chico sea el ATR. Valores por perfil (`risk_profiles.py`):
20% conservador, 30% moderado, 40% agresivo. Test de regresión
reproduce el escenario real de LINK_USDC (entrada ~$8.40, ATR ~0.0045) y
confirma que ahora la posición queda topeada al % del perfil, no al
límite de capital disponible. 126/126 tests pasando.

**Ojo con esto al reiniciar `new_pairs`**: el fix aplica a partir de la
próxima entrada -- no resize una posición ya abierta. La posición de
LINK_USDC que quedó abierta con el sizing viejo sigue como está (con su
propio stop/take-profit ya fijados) hasta que se cierre sola por señal,
stop, take-profit o el seguro de ganancias; no hace falta (ni conviene)
tocarla manualmente solo por esto.


## Trigésima cuarta ronda: la causa raíz del bug de concentración -- ticks en vivo corrompían el ATR

Se pidió medir con un backtest qué tan seguido el tope de concentración
nuevo (ronda anterior) importa de verdad -- para eso se armó
`backtest_concentration_report.py`, corriendo la misma estrategia/perfil
de la sesión `new_pairs` sobre los 6 pares nuevos con datos diarios
reales. Resultado: **0 de 148 operaciones** activaron el tope. Un
contraste total contra el ~97% de concentración que se vio en vivo con
LINK_USDC -- suficiente para desconfiar del backtest, no del hallazgo
anterior.

La causa: en `run_live_polling` (`live_runner.py`), cada tick en vivo se
agregaba como una vela NUEVA a la misma serie sembrada con velas diarias
reales (`df_sym.loc[now] = {...}`, con `now` la marca de tiempo exacta
del poll). El ATR se calcula con una ventana rodante de 14 períodos --
con `poll-interval` de 45-90 segundos, en apenas 10-15 minutos esas 14
"velas" dejan de ser 14 días reales y pasan a ser 14 ticks de segundos
entre sí. El ATR termina midiendo el rango de precio típico de 45
segundos, no de un día -- y ese ATR artificialmente diminuto es
exactamente lo que disparó el bug de concentración con LINK_USDC. El
backtest nunca lo vio porque ahí el ATR siempre se calculó sobre días
reales, de punta a punta -- este bug es *exclusivo* del modo en vivo.

**Fix de raíz**: nueva función `_append_live_tick()` -- los ticks del
MISMO día calendario actualizan la vela de hoy en curso in-place (high =
máximo de todos los ticks del día, low = mínimo, close = último precio),
en vez de agregar una fila por tick. Un tick de un día calendario nuevo
sí abre una vela propia, con el open = último cierre conocido -- mismo
criterio que un dataset diario real. La ventana rodante del ATR vuelve a
estar dominada por días reales, con a lo sumo una vela "en formación"
(la de hoy), igual que cualquier sistema de trading que mide un ATR
diario contra un día todavía no cerrado.

3 tests nuevos: dos unitarios sobre `_append_live_tick` (varios ticks del
mismo día no agregan filas; un día nuevo sí abre una vela propia) y uno
de punta a punta que simula 40 ticks del mismo día sobre datos reales de
BTC y confirma que el ATR no colapsa (bajo el bug viejo, esos mismos 40
ticks hubieran reemplazado toda la ventana de 14 y colapsado el ATR a
casi cero). 129/129 tests pasando. Se reiniciaron las 4 sesiones en vivo
con el fix aplicado.

**Por qué el backtest de concentración igual valió la pena, aunque diera
0%**: encontró un bug más importante que el que se estaba buscando medir.
Es la misma disciplina de todo este proyecto -- medir antes de asumir,
y seguir la discrepancia hasta la causa real en vez de conformarse con
"el número no es lo que esperaba, pero bueno".


## Trigésima quinta ronda: alertas de concentración en los reportes

Cierre del ciclo de la auditoría de concentración: hasta ahora, notar que
una posición concentraba demasiado capital requería mirar el JSON de
estado a mano (así se encontró el bug de LINK_USDC en primer lugar). Se
agregó `risk_manager.concentration_warnings()` -- compara el costo de
entrada de cada posición abierta (unidades × precio de entrada) contra el
capital total aproximado (efectivo + costo de entrada de todas las
posiciones), y avisa si alguna supera 45% -- a propósito por encima del
tope más permisivo de cualquier perfil (agresivo, 40%), así que en
operación normal nunca debería dispararse. Usa el precio de ENTRADA, no
mark-to-market en vivo -- alcanza para una alerta de atención sin que
estos reportes (pensados para leerse sin depender de la red) tengan que
pegarle a la API por un precio actual.

Integrado en los dos lugares donde ya se mira el estado de las sesiones:
`status_report.py` (una línea `[!] Concentración: ...` junto a la
posición) y `real_results_report.py` (misma línea, en el informe
consolidado). 4 tests nuevos: el cálculo en sí (con el escenario real de
LINK_USDC como caso positivo, y una posición normal como caso negativo),
más uno de integración por cada reporte. 132/132 tests pasando.
Verificado contra la sesión `new_pairs` real (posición de UNI_USDC al
~30%, dentro de lo esperado): no aparece ningún aviso, cero falsos
positivos.


## Trigésima sexta ronda: el bug de ATR también corrompió resultados YA reportados -- corregido en el informe

Revisando la duración real de cada operación cerrada (ver ronda
anterior) para confirmar el alcance del bug de agregación de ticks,
apareció algo que no era solo técnico: **btc_usdc, eth_usdc y multi**
llevaban corriendo mucho más tiempo que `new_pairs` cuando se aplicó el
fix -- así que el bug de ATR corrompido no fue un incidente aislado de
un par nuevo, fue el estado normal de las 3 sesiones más viejas durante
casi toda su historia. Separando las operaciones cerradas por fecha
contra el momento exacto del fix (2026-07-30T10:21:00 UTC):

- **eth_usdc**: 24/24 operaciones, neto -32.88, TODAS antes del fix.
- **multi**: 13/13 operaciones, neto -33.92, TODAS antes del fix.
- **new_pairs**: 13/15 antes del fix (neto -6.47), 2/15 después (neto +4.83).
- **Total**: de -68.44 USDC de resultado neto consolidado, -73.27
  corresponden a operaciones antes del fix -- las 2 operaciones después
  del fix ya dieron positivo (+4.83, muestra todavía muy chica para
  sacar conclusiones, pero la dirección es la correcta).

Es decir: **el número negativo que ya estaba en
`informe_resultados_reales.pdf`** (documento ya preparado como evidencia
para la propuesta) **no medía la estrategia -- medía el bug**. No
corregirlo hubiera sido presentar como "resultado real de la estrategia"
algo que en realidad era el efecto de datos corrompidos.

`real_results_report.py` ahora separa esto automáticamente: cada sesión
muestra cuántas de sus operaciones cerraron antes del fix (con su neto,
marcado explícitamente como no representativo) y el total consolidado
hace lo mismo, agregando un bloque "Después del fix" con el resultado
que sí refleja el motor corregido. La fecha de corte
(`ATR_FIX_DEPLOYED_AT`) queda fija en el código como un hecho histórico
de este pilot, no como un parámetro a ajustar. Test de regresión
reproduce el escenario con una operación sintética antes y otra después
del corte, verificando que el informe las separe y explique la causa.
133/133 tests pasando. Informe regenerado
(`informe_resultados_reales.md/.pdf`) con la separación real aplicada.

**Cierre de la investigación -- auditoría de los sistemas relacionados.**
Con el alcance real del bug de ATR ya confirmado, se revisó qué más
podía haber quedado afectado por el mismo problema de fondo (ver ronda
anterior):

- **`regime.py` (filtro ADX)** usa una media exponencial (`.ewm()`), no
  una ventana rodante de filas fijas -- misma familia de riesgo que el
  ATR (le da más peso a lo reciente, y "lo reciente" durante el bug
  también eran ticks de segundos), pero se corrige con el mismo fix ya
  aplicado, sin necesitar un cambio propio.
- **`multi_timeframe.py`** resulta estructuralmente inmune: usa
  `df.resample("W")`, que agrupa por tiempo CALENDARIO real, no por
  cantidad de filas -- así que aunque hubiera cientos de ticks de
  segundos mezclados con velas diarias reales, cada semana calendario
  seguía agregándose correctamente. No hacía falta tocar nada acá.
- **El circuit breaker funcionó como corresponde incluso durante el
  bug**: se encontró un solo evento real de activación
  (`eth_usdc`, 29-jul, "Pérdida diaria máxima alcanzada: 5.1%") --
  exactamente la respuesta esperada ante una seguidilla de stop-loss
  rápidos como los que causaba el ATR corrompido. El freno de seguridad
  no falló; hizo su trabajo mientras otra pieza del sistema estaba rota,
  y frenó el día antes de que el daño fuera mayor. Vale la pena
  señalarlo como evidencia de que las capas de seguridad son
  independientes entre sí -- una falla en una no tira abajo a las demás.

**De paso, se actualizó el mockup** con una card informativa de "Gestión
de riesgo automática" en Ajustes (tope de concentración por posición +
priorización por historial probado al elegir entre varios pares) --
hasta esta ronda el mockup no reflejaba ninguna de las dos piezas nuevas
de gestión de riesgo construidas hoy. También se aclaró que el cupo de
posiciones es un techo, no un objetivo -- el sistema nunca "llena" el
cupo por tener saldo asignado sin usar, solo abre cuando hay una señal
real (duda real del usuario mirando el mockup: solo 3 de 5 posiciones
abiertas en el ejemplo, comportamiento esperado, no un bug).


## Trigésima séptima ronda: el deduplicado de noticias no sobrevivía a un reinicio

Auditando `news_monitor.py`/`reconciliation.py` a pedido, por no
haberlos revisado en detalle esta sesión. `reconciliation.py` y la
lógica de emparejamiento de operaciones (`pair_trades`, usada en el
informe impositivo) resultaron sólidas -- sin hallazgos. Pero
`NewsMonitor._seen_links` (el set que evita re-alertar el mismo
titular) solo vivía en memoria del proceso, sin persistir -- mismo
patrón de bug ya visto varias veces este proyecto (circuit breaker,
posiciones, capital, referencia de pérdida diaria, seguro de ganancias),
esta vez en una cuarta pieza de estado que se había pasado por alto.

**Confirmado con evidencia real, no solo con lectura de código**: el
mismo titular de noticia apareció repetido 6-8 veces en un mismo archivo
de log -- una vez por cada reinicio de esa sesión durante el día. Con
`--news-auto-window 0-24` (modo automático permanente, como corren las
4 sesiones en vivo), cada reinicio no solo re-alertaba de forma
redundante: también volvía a **pausar la apertura de posiciones
nuevas** durante otra hora entera, aunque no hubiera pasado nada nuevo
de verdad. Con la cantidad de reinicios de hoy, esto le costó tiempo de
operación real a las sesiones sin ningún motivo genuino.

**Fix**: `NewsMonitor.get_seen_links()`/`restore_seen_links()`, expuesto
a través de `NewsGuard`, persistido en `force_persist()` (nuevo campo
`news_seen_links`) y restaurado en `_LiveEngine.__init__` -- mismo
patrón que ya se usa para la pausa por noticias, el circuit breaker, etc.
2 tests nuevos: el mecanismo de persistencia en sí, y un reinicio
completo de `_LiveEngine` con un feed simulado que sigue devolviendo el
mismo titular, confirmando que no se re-alerta ni se re-pausa. 135/135
tests pasando. Se reiniciaron las 4 sesiones en vivo con el fix.


## Trigésima octava ronda: OperationsMonitor no chequeaba concentración

Auditando `ops_monitor.py` (el monitoreo centralizado para miles de
usuarios, Fase 3b): chequeaba circuit breaker, kill-switch, heartbeat y
reconciliación, pero no concentración -- justo el único de los avisos ya
agregados a `status_report.py`/`real_results_report.py` que faltaba acá.
Es una laguna real y no menor: `OperationsMonitor` es exactamente el
lugar donde un problema de concentración generalizado (el mismo tipo de
bug que causó el caso de LINK_USDC, si volviera a pasar por cualquier
otro motivo en el futuro) se vería como un patrón agregado entre muchos
usuarios -- sin este chequeo, quedaría invisible ahí, aunque los otros
dos reportes sí lo mostraran individualmente.

Agregado con el mismo `concentration_warnings()` ya existente, dentro
del mismo chequeo que ya cargaba el estado para reconciliación (sin
duplicar la lectura). Un usuario sobre-concentrado ahora también puede
disparar la alerta sistémica agregada si supera el umbral configurado,
igual que circuit breaker o kill-switch. Test de regresión con el mismo
escenario real de LINK_USDC. 136/136 tests pasando.


## Trigésima novena ronda: la simulación de significancia estadística permitía posiciones superpuestas

Auditando `significance.py` (el módulo que compara la estrategia real
contra cientos de estrategias "de mentira" con entradas al azar, para
saber si el resultado es una ventaja real o pudo salir por casualidad):
`_run_random_strategy` procesaba cada fecha de entrada elegida al azar
como si se resolviera al instante -- calculaba de una su precio de
salida futuro y sumaba la ganancia/pérdida ANTES de considerar la
siguiente entrada elegida. Eso le permitía a la línea de base aleatoria
"reutilizar" capital que, en una cronología real, todavía seguiría atado
a una posición sin cerrar -- algo que la estrategia real nunca puede
hacer (`Backtester.run()` sostiene una sola posición a la vez, de
punta a punta). Comparar contra una línea de base que puede hacer trampa
no es una comparación justa, y sesga sistemáticamente el percentil
reportado.

**Fix**: la simulación aleatoria ahora también sostiene una sola
posición a la vez -- un candidato de entrada que caería dentro de una
posición todavía abierta (incluyendo el mismo día que esa posición se
resuelve, para calzar exactamente con las ramas excluyentes de
entrada/salida del día en `Backtester.run()`) se descarta, y se prueba
con el siguiente candidato del orden aleatorio hasta juntar la cantidad
de operaciones pedida. Test de regresión: con precio constante y un ATR
que nunca toca stop/take profit, cualquier posición se sostiene hasta el
último día del dataset -- así que sin importar el seed ni cuántas
operaciones se pidan, con el freno correcto SOLO la primera puede
ejecutarse de verdad, un resultado matemáticamente fijo y verificable a
mano. 137/137 tests pasando.


## Cuadragésima ronda: el sistema de reintentos con backoff nunca estuvo activo en vivo

El hallazgo más importante de toda esta pasada de auditoría continua.
Auditando `broker.py` contra `resilience.py` (el módulo de reintentos
con backoff exponencial -- construido, testeado en aislamiento, y
documentado desde la "Sexta ronda: confiabilidad y robustez"): el
decorador `@retry_with_backoff` **nunca se había aplicado a ningún
método real del bróker**. Ni `RipioBrokerAdapter._request` ni
`AlpacaBrokerAdapter._request` lo tenían -- el único mecanismo que
absorbía errores de red en las sesiones en vivo era el catch-and-skip
del loop de polling en `live_runner.py` (esperar hasta el próximo tick
completo, 45-90 segundos), no el backoff rápido (1s, 2s, 4s dentro del
mismo tick) que este módulo estaba pensado para dar. El módulo existía,
tenía su propio test, y el README lo mencionaba como parte de la
resiliencia del sistema -- pero jamás estuvo conectado a nada real.

**Segundo hallazgo, peor todavía**: incluso si el decorador hubiera
estado aplicado, no habría ayudado con el error más común de todas las
sesiones en vivo de este proyecto. Un 429 (rate limit) caía en el
catch-all genérico "código de respuesta >= 400 -> `PermanentBrokerError`"
-- y `retry_with_backoff` deliberadamente NO reintenta errores
permanentes (por diseño: no tiene sentido reintentar credenciales
inválidas o fondos insuficientes). Un 429 es exactamente el caso de
libro de "esperá un poco y volvé a intentar", no "esto nunca va a
funcionar" -- estaba mal clasificado desde el día en que se escribió
`_request`.

**Fix**: `@retry_with_backoff(max_attempts=3, base_delay_seconds=1.0,
max_delay_seconds=10.0)` aplicado a `_request` en ambos adaptadores, y
un chequeo explícito de `status_code == 429` ANTES del catch-all
genérico, clasificándolo como `TransientBrokerError` en los dos. De
paso, se limpiaron varios imports locales de `resilience` que quedaron
redundantes al importar todo a nivel de módulo. 2 tests nuevos: uno de
punta a punta (un 429 seguido de una respuesta exitosa, verificando que
`_request` reintenta sola y el llamador nunca ve el error) y uno de
introspección (confirma que el decorador sigue aplicado en los dos
adaptadores reales, para no volver a perderlo en silencio en un
refactor futuro). 139/139 tests pasando. Se reiniciaron las 4 sesiones
en vivo con el fix -- dado lo seguido que este proyecto absorbió 429 en
sus propias sesiones (cientos de veces, documentado en rondas
anteriores), este cambio debería reducir cuántos ticks se saltean
esperando el próximo poll en vez de reintentar en el momento.


## Cuadragésima primera ronda: cierre de la auditoría continua -- cuarto lugar sin chequeo de concentración, resto sólido

Última pasada de la auditoría continua de esta sesión, revisando lo que
quedaba: `strategies.py` (las 4 estrategias, línea por línea, buscando
look-ahead bias -- `momentum_breakout` y `volatility_contraction` usan
correctamente `.iloc[i-1]` para comparar contra el máximo/mínimo de
AYER, no de hoy), `safety.py` (CircuitBreaker, ManualKillSwitch,
validate_ohlcv), `validation.py`, `monte_carlo.py`, `portfolio.py`
(hereda automáticamente el tope de concentración por compartir
`risk_manager.position_size()` -- buena señal de que centralizar ese fix
ahí fue la decisión correcta), `kill_switch.py`, `ripio_smoke.py`: todo
sólido, sin hallazgos.

`support_tools.py` sí tenía la misma laguna ya encontrada tres veces
esta sesión: `generate_user_support_snapshot` arma advertencias para un
agente de soporte (reconciliación, circuit breaker, kill-switch) pero le
faltaba concentración -- el cuarto lugar, después de
`status_report.py`/`real_results_report.py`/`ops_monitor.py`, que
necesitaba el mismo aviso. Agregado con el mismo `concentration_warnings()`
ya existente. Test de regresión con el mismo escenario real de
LINK_USDC. 140/140 tests pasando.

Con esto, la auditoría continua de esta sesión cubrió la totalidad del
código del proyecto.


## Cuadragésima segunda ronda: cobertura de noticias multi-país (Argentina, Brasil, México, Colombia, Uruguay, EEUU, China)

Se preguntó específicamente si un anuncio económico del gobierno
argentino había quedado registrado -- la respuesta honesta fue que no, y
por diseño: `news_monitor.py` solo leía CoinDesk y Cointelegraph (medios
cripto globales en inglés), con palabras clave específicas de crisis
cripto (hack, quiebra, regulación) -- un anuncio macro local, por
importante que sea, queda estructuralmente fuera de ese radar.

Se amplió a propósito, no solo para Argentina: Ripio opera en Argentina,
Brasil, México, Colombia, Uruguay, Chile y EEUU -- un evento LOCAL en
cualquiera de esos países puede no aparecer nunca en la prensa cripto
global, pero sí importar mucho para un usuario de ese país en particular.
Se sumó EEUU (mueve el sentimiento cripto global más que casi cualquier
otro país) y China (misma razón, con historial concreto de que su
regulación cripto mueve el mercado entero).

**Cada feed se verificó a mano con curl antes de agregarlo** -- varios
candidatos obvios resultaron ser páginas HTML disfrazadas de RSS, no
feeds reales (ej. la URL "genérica" de RSS de Ámbito). Fuentes finales,
todas confirmadas reales: Ámbito, Cronista, Infobae (Argentina); G1,
Valor Econômico (Brasil); La Jornada (México); La República (Colombia);
El Observador (Uruguay); CNBC Economy, WSJ Markets (EEUU); South China
Morning Post Business (China) -- 11 fuentes nuevas sobre las 2
existentes.

**Palabras clave locales, deliberadamente angostas.** Agregar términos
como "inflación" o "dólar" a secas hubiera hecho que la pausa automática
quedara activa casi todo el tiempo en un país con inflación crónica -- una
alerta que suena siempre deja de servir de alerta. Se usaron solo
términos de eventos agudos y puntuales: devaluación, corralito, corrida
bancaria/cambiaria, default, control de capitales (español y sus
equivalentes en portugués para los medios de Brasil). Test de regresión
específico: confirma que SÍ detecta un titular de devaluación real, y que
NO se dispara con una nota de rutina sobre inflación, el dólar blue, o
una licitación del BCRA.

**Bug real encontrado por el propio test de humo agregado**: 4 de los 13
feeds (Ámbito, La República, El Observador, y uno de los feeds de CNBC)
devolvían 403 con el User-Agent por defecto de `requests`
("python-requests/x.x.x", identificable como bot) -- en producción esos
4 feeds hubieran fallado SIEMPRE, en silencio (el error queda atrapado y
logueado como warning, sin tirar abajo el proceso), sin que nadie lo
notara salvo que se probara contra la red real. Corregido agregando un
User-Agent explícito en `_default_http_get`. Con el fix: 13/13 feeds
responden con contenido real.

2 tests nuevos (palabras clave locales sin sobre-disparo, feeds
alcanzables de verdad) más el fix de User-Agent. 142/142 tests pasando.
Se reiniciarán las 4 sesiones en vivo con la cobertura ampliada.


## Cuadragésima tercera ronda: modo "solo monitoreo" -- USDC_ARS sin apostar contra el propio research

Pedido de seguimiento directo: sumar USDC_ARS "junto con los otros"
pares en vivo. Antes de hacerlo, se corrió de nuevo el backtest de las 4
estrategias sobre el dataset actualizado (hasta hoy): comprar y mantener
dólares dio **+1439%** en el período 2021-2026, contra apenas **+115%**
de la mejor estrategia activa (momentum/moderado) -- **12 veces peor que
no hacer nada**. Reconfirma con datos frescos el hallazgo de la Novena
ronda. Lanzar esto como sesión activa hubiera sido apostar capital real
(aunque sea de paper trading) contra la propia evidencia del proyecto.

Se presentó la disyuntiva directamente en vez de decidir en silencio, y
se optó por **modo "solo monitoreo"**: nueva opción `monitor_only` en
`_LiveEngine`/`run_live_polling` (y `--monitor-only` en el CLI) -- la
sesión sigue el precio, las noticias, y persiste el estado con total
normalidad, pero nunca abre una posición, ni automática ni manual (la
compra manual se rechaza explícitamente, con el mismo tipo de log que
los demás rechazos). Una posición que ya estuviera abierta ANTES de
activar este modo se sigue pudiendo cerrar con normalidad -- este modo
bloquea aperturas nuevas, nunca salidas.

Es una pieza genérica, no un parche de una sola vez: sirve para cualquier
par donde valga la pena tener el pulso real antes de comprometer capital,
no solo para USDC_ARS. 2 tests nuevos: nunca abre con una señal
automática fuerte, y rechaza una compra manual pero deja cerrar una
posición que ya estaba abierta de antes. 144/144 tests pasando. Se lanzó
una quinta sesión en vivo, USDC_ARS en modo solo-monitoreo.


## Cuadragésima cuarta ronda: Brasil y Colombia en vivo, y un bug de datos de dos años

Pedido de seguimiento: además de USDC_ARS, activar los pares de Ripio en
Brasil (BTC_BRL, ETH_BRL, SOL_BRL, USDC_BRL, USDT_BRL) y Colombia
(USDC_COP), verificados antes contra el ticker público real de Ripio
(`GET /trade/public/tickers/{pair}`) para confirmar que existen y cotizan
(SOL_BRL parece un par recién listado -- su ticker trae `first`/`high`/
`low` en 0 y `price_change_percent_24h: 100.00`, probablemente sin
historial de 24hs todavía; se dejó igual porque el ticker actual es real).

Al construir los datasets con la misma técnica real ya usada para ARS
(`build_ars_datasets.py`: tipo de cambio real de Yahoo Finance + BTC/ETH
reales re-denominados) apareció un bug de datos que venía de antes: **
`real_data/btc_daily.csv` estaba parado en 2024-09-17** -- casi dos años
desactualizado -- mientras que `eth_daily.csv` sí se había estado
refrescando (llega hasta hoy). Como `build_ars_datasets.py` reusa
`btc_daily.csv` tal cual, **`btc_ars_daily.csv` también venía cortado en
esa fecha desde que se creó**, sin que ninguna ronda anterior lo hubiera
notado (el conteo de filas parecía razonable, pero el rango de fechas
nunca se verificó explícitamente). Se corrigió con un script nuevo,
`refresh_btc_daily.py`, que baja de Yahoo Finance (BTC-USD) solo las
velas reales que faltan y las agrega sin tocar el historial 2020-2024 ya
guardado -- 682 velas nuevas, 2024-09-17 a hoy. Se corrieron de nuevo
`build_ars_datasets.py` (btc_ars_daily.csv pasó de estar cortado a 1300
velas reales hasta hoy) y el script nuevo de Brasil/Colombia con el dato
ya corregido.

A diferencia de USDC_ARS, acá el backtest (momentum/moderado) le gana a
comprar-y-mantener en los 6 pares -- tiene sentido: BRL y COP se
apreciaron contra el dólar en el período, así que "mantener dólares"
fue la peor opción, no la mejor. No hay tensión con el research propio
esta vez, así que se lanzaron **activos** (sin `--monitor-only`): una
sesión multi-símbolo para los 5 pares de Brasil y otra para USDC_COP,
mismo patrón que las sesiones existentes (`--live-prices`,
`--news-alerts`, `profit-lock-pct 25`). Al arrancar, el monitor de
noticias multi-país (ronda anterior) ya activó una pausa automática de
entradas nuevas por una noticia de alto impacto real -- confirma que la
ampliación de cobertura efectivamente está influyendo en sesiones reales,
no solo en los tests. 144/144 tests pasando.

Nota pendiente: no se encontró un feed de noticias de Chile que
funcionara (La Tercera, Diario Financiero, Emol y BioBioChile fallaron
las verificaciones) -- Ripio opera en Chile pero por ahora ese país no
tiene cobertura de noticias local, solo la cobertura global/regional que
ya existe.


## Cuadragésima quinta ronda: los reintentos de 429 chocaban sincronizados entre sesiones

Al pasar a 7 sesiones en vivo en paralelo (con Brasil y Colombia), los
logs mostraron 500+ líneas de 429 en un solo día, varias terminando en
"falló tras 3 intentos" = tick perdido. La causa no era solo el volumen
de requests: el backoff era **determinístico e igual para todo** -- un
429 esperaba los mismos 1s y 2s exactos que un timeout de red, así que
todos los procesos que chocaban con el rate limit al mismo tiempo
reintentaban también al mismo tiempo, y volvían a chocar contra la misma
ventana.

Tres cambios en `resilience.py`/`broker.py`:

1. **`RateLimitBrokerError`**, subclase de `TransientBrokerError` (nada
   que ya tratara a los transitorios cambia de contrato): un 429 no es
   un error de red cualquiera, es el servidor pidiendo explícitamente
   bajar el ritmo. El retry usa una escala de espera propia y más larga
   (5s base exponencial, tope 45s) en vez de la común (1s base).
2. **Retry-After**: si el servidor manda ese header en el 429, se
   respeta como espera mínima (parseo tolerante: si no vino, no es
   numérico, o la respuesta ni tiene headers -- caso de los transportes
   falsos de los tests -- simplemente se ignora).
3. **Jitter de ±30% en TODOS los reintentos**: cada espera se multiplica
   por un factor aleatorio, lo que descorrelaciona los procesos entre sí
   (`jitter=False` disponible solo para tests determinísticos).

Verificado en producción: tras reiniciar las sesiones (solo las 6 sin
posiciones abiertas -- `new_pairs` tenía UNI_USDC abierta y se dejó
corriendo con el código anterior, la regla de nunca matar una sesión
con posición abierta se mantuvo), los logs muestran esperas variadas de
3.6s a 11.9s en vez de los 1.0s/2.0s fijos. 2 tests nuevos y 1
endurecido (el de 429 de punta a punta ahora también verifica que la
espera use la escala larga, capturando el sleep en vez de dormir de
verdad). 146/146 tests pasando.


## Cuadragésima sexta ronda: refresco unificado de datasets -- el problema de BTC no era solo de BTC

La ronda 44 encontró `btc_daily.csv` parado hacía dos años, pero al
revisar el resto: `eth_daily.csv` estaba 6 días viejo y los 6 pares
nuevos 2 días. Refrescar "a mano un archivo cuando se nota" no escala, y
un dataset base viejo corta silenciosamente a los derivados que se
construyen encima (btc_ars, btc_brl, eth_brl -- eth_brl terminaba el
07-23 solo porque eth_daily estaba viejo).

`refresh_datasets.py` reemplaza al `refresh_btc_daily.py` puntual de la
ronda 44: refresca TODAS las bases de Yahoo (BTC, ETH y los 6 pares
nuevos, reusando el mapa de tickers ya verificado de
`build_new_pairs_datasets.py`) agregando solo las velas reales que
falten sin pisar historial, y después corre los dos scripts de derivados
para que ARS/BRL/COP queden consistentes. Una sola puerta de entrada
para mantener todo real_data/ al día.

Tras el refresco (18 velas nuevas entre las 8 bases, eth_brl ahora hasta
hoy) se reiniciaron solo las sesiones afectadas Y sin posiciones
(eth_usdc y multi); la sesión BRL no se tocó porque ya había abierto su
primera posición real de paper trading (ETH_BRL) minutos después de
arrancar -- tomará el dataset al día en su próximo reinicio natural.
146/146 tests pasando.


## Notas importantes (leer antes de avanzar)

1. **Este backtest usa datos sintéticos por defecto.** Los resultados que
   viste recién no dicen nada sobre si la estrategia funciona en el
   mercado real — son solo para confirmar que el motor calcula bien. El
   próximo paso serio es correrlo sobre datos históricos reales de los
   instrumentos que te interesen, y sobre varios períodos distintos
   (incluyendo crisis, mercados laterales, etc).
2. **Ninguna estrategia acá garantiza rentabilidad.** El objetivo del
   backtesting es descartar lo que no funciona y cuantificar el riesgo,
   no encontrar una fórmula segura — no existe tal cosa.
3. **Este motor no ejecuta operaciones reales.** Es solo el cerebro de
   decisión + simulación. Conectarlo a una cuenta real y que opere sin
   supervisión humana es una decisión aparte, con riesgos aparte.
4. **Si en algún momento esto se ofrece a otras personas** (no solo para
   vos), pasa a ser un servicio financiero regulado en la mayoría de las
   jurisdicciones (gestión de carteras / asesoría de inversión), con
   requisitos de registro ante el organismo correspondiente (en Argentina,
   la CNV para valores, o registro como Proveedor de Servicios de Activos
   Virtuales para cripto). Este prototipo es una base técnica, no un
   producto habilitado para operar la plata de terceros.
