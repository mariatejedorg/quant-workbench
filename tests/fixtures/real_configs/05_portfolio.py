"""Parámetros de la cartera, separados del código para poder ajustarlos sin tocar la lógica."""

# Cartera de ejemplo: los 8 activos individuales del Proyecto 1, igual ponderados.
# Peso arbitrario y deliberado -- el foco de este proyecto es la metodología de
# riesgo, no la selección/convicción de activos.
HOLDINGS = {
    "SAN.MC": {"nombre": "Banco Santander", "peso": 0.125},
    "ITX.MC": {"nombre": "Inditex", "peso": 0.125},
    "IBE.MC": {"nombre": "Iberdrola", "peso": 0.125},
    "REP.MC": {"nombre": "Repsol", "peso": 0.125},
    "ASML.AS": {"nombre": "ASML", "peso": 0.125},
    "SAP.DE": {"nombre": "SAP", "peso": 0.125},
    "MC.PA": {"nombre": "LVMH", "peso": 0.125},
    "AIR.PA": {"nombre": "Airbus", "peso": 0.125},
}

BENCHMARK_TICKER = "^IBEX"
BENCHMARK_NAME = "IBEX 35"

HISTORICAL_PERIOD = "3y"
INITIAL_CAPITAL = 100_000.0
RISK_FREE_RATE = 0.0

VAR_CONFIDENCE = 0.95
VAR_ROLLING_WINDOW = 60  # sesiones, para el VaR histórico rolling en el tiempo

PORTFOLIO_NAME = "Cartera Europa Diversificada"
