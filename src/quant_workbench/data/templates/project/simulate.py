"""Monte Carlo simulation of a price with geometric Brownian motion (standard library only).

The model: the price changes each day by a random factor

    S(t+1) = S(t) * exp((mu - sigma^2 / 2) * dt + sigma * sqrt(dt) * Z),   Z ~ N(0, 1)

where mu is the drift, sigma the volatility and dt one trading day as a fraction of a year.
The ``- sigma^2 / 2`` term is the Ito correction: without it the average price would grow
faster than mu, because a positive and a negative move of the same size do not cancel out in
a multiplicative process.
"""

import math
import random


def simulate_paths(
    *,
    initial_price: float,
    drift: float,
    volatility: float,
    n_steps: int,
    n_paths: int,
    seed: int,
    trading_days: int,
) -> list[list[float]]:
    """Simulate ``n_paths`` price paths of ``n_steps`` days each (every path includes day 0)."""
    rng = random.Random(seed)  # a private generator: does not disturb anybody else's randomness
    dt = 1.0 / trading_days
    step_drift = (drift - 0.5 * volatility**2) * dt
    step_scale = volatility * math.sqrt(dt)

    paths = []
    for _ in range(n_paths):
        price = initial_price
        path = [price]
        for _ in range(n_steps):
            price *= math.exp(step_drift + step_scale * rng.gauss(0.0, 1.0))
            path.append(price)
        paths.append(path)
    return paths


def daily_returns(path: list[float]) -> list[float]:
    """Simple daily returns of a price path: ``p[t] / p[t-1] - 1``."""
    return [after / before - 1.0 for before, after in zip(path, path[1:], strict=False)]


def sharpe_ratio(returns: list[float], risk_free_rate: float, trading_days: int) -> float:
    """Annualised Sharpe ratio: mean excess daily return over its volatility, times sqrt(days).

    Multiplying by the square root of the number of trading days annualises the ratio because
    the mean grows linearly with time and the standard deviation with its square root.
    """
    excess = [r - risk_free_rate / trading_days for r in returns]
    mean = sum(excess) / len(excess)
    variance = sum((r - mean) ** 2 for r in excess) / (len(excess) - 1)
    if variance == 0:
        return 0.0
    return mean / math.sqrt(variance) * math.sqrt(trading_days)


def max_drawdown(path: list[float]) -> float:
    """Largest peak-to-trough fall of the path, as a negative fraction (-0.25 = -25 %)."""
    peak = path[0]
    worst = 0.0
    for price in path:
        peak = max(peak, price)
        worst = min(worst, price / peak - 1.0)
    return worst


def percentile(values: list[float], q: float) -> float:
    """The ``q``-th quantile (0 to 1) of ``values`` by linear interpolation between neighbours."""
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
