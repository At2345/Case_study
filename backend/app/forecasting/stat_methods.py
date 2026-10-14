"""Vectorised statistical forecasting methods.

Every method takes a history matrix H of shape (n_series, T), columns chronological monthly,
with NaN for months before a series was observed, and returns forecasts of shape (n_series, 12)
for horizons 1..12. Series with no observations return NaN.
"""
from __future__ import annotations

import warnings
from typing import Callable, Dict

import numpy as np

HORIZON = 12


def _tile(level: np.ndarray) -> np.ndarray:
    return np.repeat(level[:, None], HORIZON, axis=1)


def moving_average(H: np.ndarray, k: int) -> np.ndarray:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return _tile(np.nanmean(H[:, -k:], axis=1))


def moving_median(H: np.ndarray, k: int) -> np.ndarray:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return _tile(np.nanmedian(H[:, -k:], axis=1))


def seasonal_naive(H: np.ndarray) -> np.ndarray:
    """Value of the same calendar month one year earlier; recent 6-month mean if that is unobserved."""
    n, T = H.shape
    fallback = moving_average(H, 6)
    if T < 12:
        return fallback
    out = H[:, T - 12:T].copy()
    return np.where(np.isnan(out), fallback, out)


def ses(H: np.ndarray, alpha: float = 0.3) -> np.ndarray:
    level = np.full(H.shape[0], np.nan)
    for t in range(H.shape[1]):
        x = H[:, t]
        seen = ~np.isnan(x)
        first = seen & np.isnan(level)
        upd = seen & ~np.isnan(level)
        level[first] = x[first]
        level[upd] = level[upd] + alpha * (x[upd] - level[upd])
    return _tile(level)


def croston_sba(H: np.ndarray, alpha: float = 0.1) -> np.ndarray:
    """Croston with the Syntetos-Boylan bias correction (1 - alpha/2)."""
    n, T = H.shape
    z = np.full(n, np.nan)  # smoothed demand size
    p = np.full(n, np.nan)  # smoothed inter-demand interval
    q = np.zeros(n)  # periods since last demand (including the current one)
    observed = np.zeros(n, dtype=bool)
    for t in range(T):
        x = H[:, t]
        seen = ~np.isnan(x)
        observed |= seen
        q[seen] += 1
        dem = seen & (x > 0)
        first = dem & np.isnan(z)
        upd = dem & ~np.isnan(z)
        z[first], p[first] = x[first], q[first]
        z[upd] = z[upd] + alpha * (x[upd] - z[upd])
        p[upd] = p[upd] + alpha * (q[upd] - p[upd])
        q[dem] = 0
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        rate = (1 - alpha / 2) * z / p
    rate = np.where(observed & np.isnan(z), 0.0, rate)
    rate[~observed] = np.nan
    return _tile(rate)


def combo_sn_ma12(H: np.ndarray) -> np.ndarray:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmean(np.stack([seasonal_naive(H), moving_average(H, 12)]), axis=0)


METHODS: Dict[str, Callable[[np.ndarray], np.ndarray]] = {
    "seasonal_naive": seasonal_naive,
    "ma3": lambda H: moving_average(H, 3),
    "ma6": lambda H: moving_average(H, 6),
    "ma12": lambda H: moving_average(H, 12),
    "median6": lambda H: moving_median(H, 6),
    "ses": ses,
    "croston_sba": croston_sba,
    "combo_sn_ma12": combo_sn_ma12,
}
