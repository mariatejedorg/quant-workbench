"""Parámetros de la simulación, separados del código para poder ajustarlos sin tocar la lógica."""

TICKER = "^IBEX"
TICKER_NAME = "IBEX 35"
HISTORICAL_PERIOD = "3y"  # histórico usado para estimar mu y sigma

N_DAYS = 252          # horizonte de simulación (~1 año de sesiones)
N_SIMULATIONS = 10_000
RANDOM_SEED = 42       # reproducibilidad: mismo resultado en cada ejecución

TARGET_PRICE_MULTIPLIER = 1.10  # precio objetivo = +10% sobre el precio actual
