"""Descarga de precios históricos con yfinance."""

from pathlib import Path

import pandas as pd
import yfinance as yf

# Algunos equipos con antivirus que inspecciona el tráfico HTTPS (p. ej. Norton)
# rompen la verificación del certificado que usa yfinance por defecto. Si existe
# un bundle de certificados local (ver README), se usa aquí; si no, se usa la
# verificación estándar.
_CUSTOM_CA_BUNDLE = Path(__file__).resolve().parent.parent / ".certs" / "cacert.pem"


def _build_session():
    if not _CUSTOM_CA_BUNDLE.exists():
        return None
    from curl_cffi import requests as curl_requests

    return curl_requests.Session(impersonate="chrome", verify=str(_CUSTOM_CA_BUNDLE))

# Cesta de activos: 2 índices de referencia + 8 acciones europeas/españolas líquidas.
TICKERS = {
    "^IBEX": "IBEX 35",
    "^GSPC": "S&P 500",
    "SAN.MC": "Banco Santander",
    "ITX.MC": "Inditex",
    "IBE.MC": "Iberdrola",
    "REP.MC": "Repsol",
    "ASML.AS": "ASML",
    "SAP.DE": "SAP",
    "MC.PA": "LVMH",
    "AIR.PA": "Airbus",
}

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def download_prices(tickers: list[str], period: str = "3y") -> pd.DataFrame:
    """Descarga precios de cierre ajustado diarios para una lista de tickers.

    Devuelve un DataFrame con fechas como índice y un ticker por columna.
    """
    raw = yf.download(
        tickers, period=period, auto_adjust=True, progress=False, session=_build_session()
    )
    prices = raw["Close"]

    if isinstance(prices, pd.Series):
        prices = prices.to_frame(name=tickers[0])

    prices = prices.dropna(how="all").ffill().dropna()
    return prices


def load_or_download_prices(tickers: list[str], period: str = "3y") -> pd.DataFrame:
    """Usa la caché en data/prices.csv si existe; si no, descarga y la guarda."""
    DATA_DIR.mkdir(exist_ok=True)
    cache_path = DATA_DIR / "prices.csv"

    if cache_path.exists():
        prices = pd.read_csv(cache_path, index_col=0, parse_dates=True)
        if set(tickers).issubset(prices.columns):
            return prices[tickers]

    prices = download_prices(tickers, period=period)
    prices.to_csv(cache_path)
    return prices


if __name__ == "__main__":
    df = download_prices(list(TICKERS.keys()))
    print(df.tail())
