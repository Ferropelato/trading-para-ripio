FROM python:3.12-slim

WORKDIR /app

# Copiar solo requirements primero para aprovechar el cache de capas de
# Docker -- si el código cambia pero no las dependencias, no hace falta
# reinstalar todo de nuevo.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Corre los tests de sanidad como parte del build -- si algo está roto,
# la imagen ni siquiera se termina de construir. Esto es intencional:
# preferible que falle acá a que falle en producción.
RUN python3 tests.py

# Por defecto corre un backtest de ejemplo con datos reales incluidos.
# Sobreescribir el comando al correr el container para otras estrategias:
#   docker run motor-trading python3 run_backtest.py --strategy tendencia --profile conservador --csv real_data/aapl_daily.csv
CMD ["python3", "run_backtest.py", "--strategy", "momentum", "--profile", "agresivo", "--csv", "real_data/btc_daily.csv"]
