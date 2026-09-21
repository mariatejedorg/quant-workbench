"""Parámetros del Proyecto 9: universo invertible, benchmarks de referencia,
tipo libre de riesgo, y resolución de la frontera eficiente.
"""

# Universo invertible: las mismas 8 acciones europeas/españolas líquidas
# del Proyecto 1 (proyecto-1-analisis-mercado/src/data.py), sin los dos
# índices -- un índice no es un activo en el que se pueda invertir
# directamente, así que no participa en la optimización (ver BENCHMARK_TICKERS).
TICKERS = {
    "SAN.MC": "Banco Santander",
    "ITX.MC": "Inditex",
    "IBE.MC": "Iberdrola",
    "REP.MC": "Repsol",
    "ASML.AS": "ASML",
    "SAP.DE": "SAP",
    "MC.PA": "LVMH",
    "AIR.PA": "Airbus",
}

# Índices de referencia: se pintan en el gráfico riesgo/retorno para
# comparar, pero no entran en la optimización.
BENCHMARK_TICKERS = {
    "^IBEX": "IBEX 35",
    "^GSPC": "S&P 500",
}

HISTORICAL_PERIOD = "3y"
TRADING_DAYS_PER_YEAR = 252

# Tipo de interés libre de riesgo anualizado (misma cifra que Proyectos 6-8).
RISK_FREE_RATE = 0.045

# Número de puntos con los que se traza la curva de la frontera eficiente
# (uno por cada retorno objetivo entre el de mínima varianza y el del
# activo con mayor retorno esperado).
N_FRONTIER_POINTS = 50
