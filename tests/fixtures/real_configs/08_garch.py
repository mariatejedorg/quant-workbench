"""Parámetros del Proyecto 8: ticker, histórico usado para el ajuste por
máxima verosimilitud, horizonte del pronóstico, y valores iniciales del
optimizador.
"""

# Mismo ticker que el Proyecto 6, para que la comparación con su volatilidad
# implícita (src/market_iv.py) sea directa, sobre el mismo activo.
TICKER = "AAPL"

# Histórico usado para estimar el modelo GARCH: cuanta más historia, más
# precisa la estimación por máxima verosimilitud (más observaciones).
HISTORICAL_PERIOD = "3y"

TRADING_DAYS_PER_YEAR = 252

# Horizonte del pronóstico de volatilidad, en días de calendario -- se
# elige para que sea comparable con un vencimiento de opciones real (mismo
# orden de magnitud que la sonrisa del Proyecto 6, que usa ~30-45 días).
FORECAST_HORIZON_DAYS = 30

# --- Optimizador (scipy.optimize.minimize sobre la log-verosimilitud negativa) ---

# Valores iniciales de (omega, alpha, beta). alpha0 + beta0 < 1 (estacionario);
# beta0 alto y alpha0 bajo es la región típica donde convergen la mayoría de
# series financieras reales (alta persistencia, reacción moderada al shock).
OMEGA_INIT = 1e-6
ALPHA_INIT = 0.08
BETA_INIT = 0.90

# Cota superior de alpha+beta impuesta al optimizador: estrictamente menor
# que 1 para garantizar un proceso estacionario (varianza incondicional finita).
STATIONARITY_MAX = 0.999
