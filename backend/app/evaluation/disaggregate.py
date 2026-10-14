"""F-SOP-01: disaggregate a committed product total to its channel items (top-down MinT with diagonal W).

With the product total fixed, MinT with W = diag(w) moves each item by gap * w_i / sum(w):
`ols` (w = 1), `proportional` (w = plan, structural scaling) and `mint_diag` (w = forecast error
variance from the P10-P90 band). Negative results are clipped and the rest redistributed so the
items always sum to the total.
"""
from __future__ import annotations

from typing import List

import numpy as np

METHODS = ("mint_diag", "proportional", "ols")


def _weights(plan: np.ndarray, sigma: np.ndarray, method: str) -> np.ndarray:
    if method == "ols":
        return np.ones_like(plan)
    if method == "proportional":
        return plan.copy()
    return sigma ** 2


def disaggregate(plan: List[float], total: float, sigma: List[float] = None, method: str = "mint_diag") -> np.ndarray:
    if method not in METHODS:
        raise ValueError(f"method must be one of {METHODS}")
    if total < 0:
        raise ValueError("total must be non-negative")
    p = np.asarray(plan, dtype=float)
    s = np.asarray(sigma if sigma is not None else np.zeros_like(p), dtype=float)
    w = _weights(p, np.nan_to_num(s), method)
    if w.sum() <= 0:
        w = p.copy() if p.sum() > 0 else np.ones_like(p)
    out = p.copy()
    active = np.ones(len(p), dtype=bool)
    for _ in range(len(p) + 1):
        gap = total - out.sum()
        if abs(gap) < 1e-9:
            break
        wa = np.where(active, w, 0.0)
        if wa.sum() <= 0:
            wa = active.astype(float)
        out = out + gap * wa / wa.sum()
        neg = out < 0
        if not neg.any():
            break
        out[neg] = 0.0
        active &= ~neg
    return out
