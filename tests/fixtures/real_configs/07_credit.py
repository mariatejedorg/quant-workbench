"""Parámetros del Proyecto 7: empresa a analizar, horizonte del modelo de
Merton, y tolerancias del procedimiento iterativo (KMV) que recupera el
valor y la volatilidad de los activos a partir del equity observado.
"""

# Empresa a analizar. Se elige Ford (apalancamiento real y significativo)
# en vez de un ticker con muy poca deuda: con deuda casi nula la distancia
# al impago del modelo es trivialmente alta y el ejercicio pierde interés.
TICKER = "F"

# Tipo de interés libre de riesgo anualizado (misma cifra que Proyecto 6,
# aproximación de la letra del Tesoro US a corto plazo, asumida constante).
RISK_FREE_RATE = 0.045

# Horizonte temporal del modelo de Merton: 1 año es la convención estándar
# de la metodología KMV (probabilidad de impago "a 1 año vista").
TIME_HORIZON_YEARS = 1.0

# Histórico de precios usado para estimar la volatilidad del equity
# (sigma_E), que alimenta el arranque del procedimiento iterativo.
HISTORICAL_PERIOD = "2y"

# --- Procedimiento iterativo KMV (src/merton.py) ---

# Se considera que sigma_V ha convergido cuando el cambio relativo entre
# una iteración y la siguiente es menor que esta tolerancia.
SIGMA_V_TOLERANCE = 1e-4

# Número máximo de iteraciones del bucle sigma_V -> V_t -> sigma_V
MAX_ITERATIONS = 50

# Dentro de cada iteración, Newton-Raphson invierte E_t = BS(V_t) para
# recuperar V_t un día a la vez: mismas tolerancias que implied_vol.py
# del Proyecto 6, pero aplicadas al subyacente V en vez de a sigma.
NEWTON_TOLERANCE = 1e-6
NEWTON_MAX_ITERATIONS = 100
