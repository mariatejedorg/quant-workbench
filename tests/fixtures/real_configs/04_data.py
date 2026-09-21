"""Descarga de datos fundamentales y precios recientes con yfinance."""

from pathlib import Path

import pandas as pd
import yfinance as yf

# 18 empresas repartidas en 8 sectores, para que las consultas GROUP BY sector
# tengan sentido. Nombre legible aparte porque yfinance no siempre lo trae limpio.
COMPANIES = {
    "SAN.MC": "Banco Santander",
    "BBVA.MC": "BBVA",
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "ASML.AS": "ASML",
    "SAP.DE": "SAP",
    "ITX.MC": "Inditex",
    "MC.PA": "LVMH",
    "REP.MC": "Repsol",
    "IBE.MC": "Iberdrola",
    "AIR.PA": "Airbus",
    "SIE.DE": "Siemens",
    "NOVN.SW": "Novartis",
    "RHHBY": "Roche",
    "NESN.SW": "Nestlé",
    "ULVR.L": "Unilever",
    "TEF.MC": "Telefónica",
    "DTE.DE": "Deutsche Telekom",
}

PRICE_HISTORY_PERIOD = "6mo"

# Algunos equipos con antivirus que inspecciona el tráfico HTTPS (p. ej. Norton)
# rompen la verificación del certificado que usa yfinance por defecto. Si existe
# un bundle de certificados local (ver README), se usa aquí; si no, se usa la
# verificación estándar. Mismo mecanismo que en Proyectos 1-3.
_CUSTOM_CA_BUNDLE = Path(__file__).resolve().parent.parent / ".certs" / "cacert.pem"

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _build_session():
    if not _CUSTOM_CA_BUNDLE.exists():
        return None
    from curl_cffi import requests as curl_requests

    return curl_requests.Session(impersonate="chrome", verify=str(_CUSTOM_CA_BUNDLE))


def download_fundamentals(companies: dict[str, str] = COMPANIES) -> pd.DataFrame:
    """Descarga un snapshot de métricas fundamentales por empresa vía yfinance."""
    session = _build_session()
    rows = []
    for ticker, nombre in companies.items():
        info = yf.Ticker(ticker, session=session).info
        rows.append(
            {
                "ticker": ticker,
                "nombre": nombre,
                "sector": info.get("sector") or "Sin clasificar",
                "market_cap": info.get("marketCap"),
                "per": info.get("trailingPE"),
                "margen_beneficio": info.get("profitMargins"),
                "crecimiento_ingresos": info.get("revenueGrowth"),
            }
        )
    return pd.DataFrame(rows)


def load_or_download_fundamentals(companies: dict[str, str] = COMPANIES) -> pd.DataFrame:
    """Usa la caché en data/fundamentales.csv si existe; si no, descarga y la guarda.

    Los fundamentales son un snapshot puntual (no una serie temporal), así que
    aquí basta con cachear el archivo completo una vez, a diferencia de P1-P3.
    """
    DATA_DIR.mkdir(exist_ok=True)
    cache_path = DATA_DIR / "fundamentales.csv"

    if cache_path.exists():
        return pd.read_csv(cache_path)

    df = download_fundamentals(companies)
    df.to_csv(cache_path, index=False)
    return df


def download_recent_prices(companies: dict[str, str] = COMPANIES, period: str = PRICE_HISTORY_PERIOD) -> pd.DataFrame:
    """Precios de cierre de los últimos meses, en formato largo (ticker, fecha, precio_cierre)."""
    raw = yf.download(list(companies.keys()), period=period, auto_adjust=True, progress=False, session=_build_session())
    prices = raw["Close"]

    long_format = prices.reset_index().melt(id_vars="Date", var_name="ticker", value_name="precio_cierre")
    long_format = long_format.rename(columns={"Date": "fecha"}).dropna()
    long_format["fecha"] = long_format["fecha"].dt.strftime("%Y-%m-%d")
    return long_format


def load_or_download_recent_prices(companies: dict[str, str] = COMPANIES, period: str = PRICE_HISTORY_PERIOD) -> pd.DataFrame:
    DATA_DIR.mkdir(exist_ok=True)
    cache_path = DATA_DIR / "precios_recientes.csv"

    if cache_path.exists():
        return pd.read_csv(cache_path)

    df = download_recent_prices(companies, period)
    df.to_csv(cache_path, index=False)
    return df
