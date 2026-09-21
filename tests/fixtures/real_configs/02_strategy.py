"""Parámetros de la estrategia, separados del código para poder ajustarlos sin tocar la lógica."""

TICKER = "^IBEX"
TICKER_NAME = "IBEX 35"
PERIOD = "3y"

SMA_SHORT = 50
SMA_LONG = 200

INITIAL_CAPITAL = 10_000.0
RISK_FREE_RATE = 0.0  # tasa libre de riesgo anual, para el Sharpe Ratio
