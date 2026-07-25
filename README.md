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
todo ese tiempo -- si se apaga o suspende, el proceso se corta. Esto es
el arranque de esa evidencia, no la evidencia completa. El paso
siguiente real sería migrar esto a una VPS chica y barata (o a un
scheduled job) para que corra sin depender de que una laptop personal
quede encendida.


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

### Fase 3d -- pendiente

Prueba de carga con muchos usuarios bajo un shock de mercado compartido
(circuit breakers disparándose en simultáneo) -- ver próxima ronda.


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
