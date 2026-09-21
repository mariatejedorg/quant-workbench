"""Parámetros del Proyecto 10 (capstone): universo, benchmark, ventanas de
señal y de estimación, y frecuencia de rebalanceo.
"""

# Mismo universo invertible que el Proyecto 9 (que a su vez reutiliza el
# Proyecto 1): 8 acciones europeas/españolas líquidas.
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

# Benchmark de referencia (no invertible directamente, solo vara de medir).
BENCHMARK_TICKER = "^GSPC"
BENCHMARK_NAME = "S&P 500"

# 4 años de histórico: el primer año se usa únicamente como ventana de
# arranque para poder calcular la SMA larga (200 sesiones) y la primera
# estimación de mu/Sigma antes del primer rebalanceo -- el backtest
# "efectivo" corre sobre los ~3 años restantes.
HISTORICAL_PERIOD = "4y"

TRADING_DAYS_PER_YEAR = 252

# Medias móviles de la señal de momentum: mismos valores que el Proyecto 2.
SMA_SHORT = 50
SMA_LONG = 200

# Ventana móvil (en sesiones) usada para estimar mu/Sigma en cada
# rebalanceo -- siempre con datos ANTERIORES a la fecha de rebalanceo,
# nunca futuros.
LOOKBACK_DAYS = 252

RISK_FREE_RATE = 0.045

# Frecuencia de rebalanceo: "M" = mensual (primer día de cotización de cada mes).
REBALANCE_FREQUENCY = "M"
