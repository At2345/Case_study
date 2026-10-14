from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd


def valid_rows(df: pd.DataFrame, target: str = "target", prediction: str = "prediction") -> pd.DataFrame:
    """Rows scored by the official evaluation: both actual and prediction present.

    This filter reproduces `overall_error_metrics.parquet` exactly (n_rows=152,499). Exposure is
    NOT filtered, so stockout-censored months are included in the headline figure.
    """
    return df[df[target].notna() & df[prediction].notna()]


def compute_metrics(
    df: pd.DataFrame, target: str = "target", prediction: str = "prediction", filter_valid: bool = True
) -> Dict[str, float]:
    """FPE (volume-weighted WAPE, %), bias %, MAE, RMSE, n_rows, volume."""
    d = valid_rows(df, target, prediction) if filter_valid else df
    n = len(d)
    if n == 0:
        return {"fpe": np.nan, "bias_pct": np.nan, "mae": np.nan, "rmse": np.nan, "n_rows": 0, "volume": 0.0}
    err = d[prediction].to_numpy(dtype=float) - d[target].to_numpy(dtype=float)
    vol = float(d[target].sum())
    denom = vol if vol != 0 else np.nan
    return {
        "fpe": float(np.abs(err).sum() / denom * 100),
        "bias_pct": float(err.sum() / denom * 100),
        "mae": float(np.abs(err).mean()),
        "rmse": float(np.sqrt((err ** 2).mean())),
        "n_rows": int(n),
        "volume": vol,
    }


def fva_points(reference_fpe: float, candidate_fpe: float) -> float:
    """Forecast Value Added in percentage points: reference error minus candidate error.

    Positive means the candidate beats the reference. Planner scorecard (brief Section 3):
    FVA_Danny = fva_points(ML FPE, Danny FPE); negative means the override destroyed value.
    ML value added over the legacy baseline = fva_points(baseline FPE, ML FPE).
    """
    return float(reference_fpe - candidate_fpe)
