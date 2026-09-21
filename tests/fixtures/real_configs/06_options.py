"""Parámetros del Proyecto 6: ticker, tipo libre de riesgo, y tolerancias
del solver de volatilidad implícita. Centralizados aquí para poder cambiar
el activo o los parámetros sin tocar el código de src/.
"""

# Activo subyacente sobre el que se descarga la cadena de opciones real
TICKER = "AAPL"

# Tipo de interés libre de riesgo anualizado (aprox. letra del Tesoro US a
# corto plazo). Simplificación: se asume constante, en vez de descargar una
# curva de tipos real — razonable para el objetivo pedagógico del proyecto.
RISK_FREE_RATE = 0.045

# Rendimiento por dividendo anualizado del subyacente. 0.0 si se ignora
# (simplificación estándar del Black-Scholes "clásico", sin el ajuste de
# Merton por dividendos continuos).
DIVIDEND_YIELD = 0.0

# --- Solver de volatilidad implícita (src/implied_vol.py) ---

# Volatilidad inicial para arrancar Newton-Raphson
INITIAL_VOL_GUESS = 0.30

# Número máximo de iteraciones antes de rendirse (Newton-Raphson o bisección)
MAX_ITERATIONS = 100

# Se considera que el solver ha convergido cuando |BS(sigma) - precio_mercado|
# es menor que esta tolerancia (en las mismas unidades que el precio, USD)
PRICE_TOLERANCE = 1e-6

# Cota inferior y superior de sigma para el fallback por bisección
VOL_MIN = 1e-4
VOL_MAX = 5.0

# --- Selección de contratos válidos (src/data.py) ---

# Se descartan contratos sin volumen/interés abierto mínimo o demasiado
# alejados del dinero, para evitar precios poco fiables (bid-ask muy ancho)
MIN_OPEN_INTEREST = 10
MONEYNESS_RANGE = (0.7, 1.3)  # K/S entre 0.7 y 1.3 del precio spot

# Se descartan también los vencimientos a muy pocos días: con tan poco
# tiempo hasta el vencimiento, un spread bid-ask normal ya representa una
# fracción enorme del precio de la opción, y la volatilidad implícita
# recuperada se dispara a valores absurdos (cientos de %) que no reflejan
# volatilidad real sino ruido de cotización. Se comprobó al construir la
# superficie 3D: sin este filtro, unos pocos contratos a 1 día de vencer
# dominaban la escala del eje Z y aplanaban visualmente el resto.
MIN_DAYS_TO_EXPIRY = 7

# Número de trayectorias para la validación Monte Carlo (src/monte_carlo_check.py)
MC_N_SIMULATIONS = 50_000
MC_RANDOM_SEED = 42
