# Motor de estrategias + backtesting (prototipo)

Prototipo funcional para probar reglas de trading configurables por perfil
de riesgo, sobre datos históricos. Sirve como base técnica para validar
ideas ANTES de arriesgar capital real o de pensar en cualquier producto
comercial.

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

Este sandbox no tiene salida de red hacia APIs de mercado, así que el
prototipo usa datos sintéticos para demostrar que el motor funciona de
punta a punta. Para pasar a datos reales, reemplazá `generate_synthetic_data`
por una función que traiga precios históricos de:
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
  riesgo, bróker, alertas, kill-switch, heartbeat) en un solo loop
  operable. Corre hoy contra `PaperBroker`, reproduciendo datos
  históricos como si llegaran en vivo. El día que se conecte un bróker
  real, el cambio es una sola línea (qué clase de bróker se instancia) —
  el resto del loop no cambia. Probado de punta a punta con datos reales.
  **Nota**: el balance que da `live_runner` difiere un poco del
  backtester (`$1856` vs `$1590` con los mismos parámetros) porque
  todavía no tiene integrado el circuit breaker ni los filtros de
  régimen/multi-timeframe — es la lógica de entrada/salida "base".
- **`.env.example`**: plantilla de variables de entorno para credenciales
  (bróker, Telegram, email). Copiar a `.env`, completar ahí, nunca subir
  ese archivo a git (ya está en `.gitignore`).
- **`LibertexBrokerAdapter` actualizado**: ahora lee las credenciales
  desde variables de entorno (`BROKER_API_KEY`, `BROKER_API_SECRET`) en
  vez de solo aceptarlas como parámetro. Si no las encuentra, avisa por
  log en vez de fallar silenciosamente.
- **Repositorio git inicializado** con un commit inicial limpio de todo
  el proyecto -- en la otra PC alcanza con descomprimir y ya tenés
  historial para seguir trabajando (`git log`, branches, etc.).

### Pasos concretos para cuando estés en la otra PC

1. Descomprimir `trading_engine_completo.zip` — ya viene con `git init`
   hecho y el primer commit cargado.
2. `pip install -r requirements.txt` (o `pip install -r requirements.txt
   --break-system-packages` según tu entorno).
3. Correr `python3 tests.py` primero — confirmá que las 22 pruebas pasan
   en tu máquina antes de tocar nada (si tu Python es una versión
   distinta a 3.11/3.12, puede haber alguna diferencia menor a revisar).
4. Copiar `.env.example` a `.env` y completar lo que tengas: credenciales
   de la API del bróker que consigas (revisar primero si el bróker en
   cuestión ofrece una API pública para desarrolladores — no todos los
   brokers minoristas la tienen), y opcionalmente el bot de Telegram.
5. En `broker.py`, completar los métodos de `LibertexBrokerAdapter` (o
   como se llame el adaptador del bróker que uses) reemplazando cada
   `raise NotImplementedError` por la llamada HTTP real, siguiendo la
   documentación de API de ese bróker específico.
6. Probar el adaptador nuevo de forma aislada primero (ej. solo
   `get_current_price` y `get_balance`, que son de solo lectura) antes de
   probar `place_order` con plata real.
7. Cuando tengas confianza en el adaptador, reemplazar `PaperBroker` por
   el adaptador real en `live_runner.py` y correrlo primero con montos
   mínimos.

## Próximos pasos sugeridos (no implementados todavía)

- **Capa de broker abstracta**: una interfaz común (clase base) para que
  conectar a Libertex, Binance, o cualquier otro bróker/exchange sea
  cuestión de escribir un adaptador nuevo, no de reescribir el motor.
- **Bot de alertas (Telegram/email)**: pasar de "correr el script a mano"
  a "avisa solo cuando hay una señal nueva", sin todavía autorizar
  ejecución automática -- el paso intermedio antes de cualquier autonomía.
- **CI (integración continua)**: que `tests.py` corra automáticamente en
  cada cambio de código (ej. GitHub Actions). El bug de `load_csv`
  encontrado durante este mismo proceso (ver sección de stress testing
  arriba) es la prueba concreta de por qué hace falta esto.
- **Dockerización**: empaquetar el entorno completo (versiones exactas de
  Python y librerías) para que corra idéntico en cualquier máquina.
- **Análisis multi-timeframe**: confirmar la señal en un timeframe mayor
  (ej. semanal) antes de operar en el diario.
- **`requirements.txt` con versiones fijas**, logging real (módulo
  `logging` en vez de `print`), y archivo de configuración (YAML/JSON)
  en vez de solo argumentos de línea de comandos.

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
