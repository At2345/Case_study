"""Hierarchical reconciliation (level_id -> item_id) and its backtest evaluation.

The hierarchy has two levels: product (`level_id`) and channel item (`item_id`). The only upper-level
base forecast available in the data is the legacy baseline, so reconciliation here combines the ML
item forecasts with the legacy product forecast. Variants implemented: OLS (W = I) and structural WLS
(W = diag(S 1)). MinT-shrink needs an error covariance that 5 backtest cutoffs cannot support yet.
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd

from app.data.loaders import load
from app.evaluation.metrics import compute_metrics, fva_points, valid_rows

KEYS = ["level_id", "cutoff", "date"]


def build_summing_matrix(n_bottom: int) -> np.ndarray:
    """S for one product with n_bottom items: first row is the total, then the identity."""
    return np.vstack([np.ones((1, n_bottom)), np.eye(n_bottom)])


def mint_reconcile(S: np.ndarray, y_hat: np.ndarray, W: np.ndarray) -> np.ndarray:
    """y_tilde = S (S' W^-1 S)^-1 S' W^-1 y_hat."""
    W_inv = np.linalg.inv(W)
    G = np.linalg.solve(S.T @ W_inv @ S, S.T @ W_inv)
    return S @ G @ y_hat


def reconcile_two_level(
    items: pd.DataFrame,
    baseline: pd.DataFrame,
    weights: str = "wls_struct",
    clip_negative: bool = True,
) -> pd.DataFrame:
    """Reconcile item forecasts (`prediction`) with the product forecast (`baseline`).

    Closed form of MinT with diagonal W for a two-level hierarchy: with item variances 1 and product
    variance w_top, every item moves by d / (w_top + n) where d = baseline - sum(items) and n is the
    number of items in the product-month. Product-months without a baseline are dropped.
    Returns the items frame with a `reconciled` column (clipped at zero by default).
    """
    if weights not in ("ols", "wls_struct"):
        raise ValueError("weights must be 'ols' or 'wls_struct'")
    out = items.merge(baseline[KEYS + ["baseline"]], on=KEYS, how="inner")
    grp = out.groupby(KEYS)["prediction"]
    n = grp.transform("size")
    gap = out["baseline"] - grp.transform("sum")
    w_top = 1.0 if weights == "ols" else n.astype(float)
    out["reconciled"] = out["prediction"] + gap / (w_top + n)
    if clip_negative:
        out["reconciled"] = out["reconciled"].clip(lower=0)
    return out


def complete_scored_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Scored rows restricted to level-months where every item that has an actual has a prediction."""
    scored = valid_rows(df)
    keys = KEYS
    n_actual = df[df["target"].notna()].groupby(keys).size().rename("n_actual")
    n_scored = scored.groupby(keys).size().rename("n_scored")
    counts = pd.concat([n_actual, n_scored], axis=1).fillna(0).reset_index()
    complete = counts[counts["n_actual"] == counts["n_scored"]][keys]
    return scored.merge(complete, on=keys, how="inner")


def evaluate_reconciliation(df: pd.DataFrame, baseline: Optional[pd.DataFrame] = None, data_dir=None) -> pd.DataFrame:
    """Item-level and product-level error for ML, legacy baseline and reconciled ML+baseline."""
    if baseline is None:
        baseline = load("baseline_forecast", data_dir=data_dir).rename(columns={"prediction": "baseline"})
    items = complete_scored_rows(df)[KEYS + ["item_id", "target", "prediction", "horizon"]]

    rows = []

    def summarise(name: str, frame: pd.DataFrame, pred_col: str) -> Dict[str, float]:
        item_m = compute_metrics(frame, prediction=pred_col, filter_valid=False)
        lvl = frame.groupby(KEYS, as_index=False).agg(target=("target", "sum"), p=(pred_col, "sum"))
        lvl_m = compute_metrics(lvl.rename(columns={"p": "prediction"}), filter_valid=False)
        return {
            "method": name, "level_months": len(lvl),
            "item_fpe": item_m["fpe"], "item_bias_pct": item_m["bias_pct"],
            "product_fpe": lvl_m["fpe"], "product_bias_pct": lvl_m["bias_pct"],
        }

    base_items = reconcile_two_level(items, baseline, weights="ols", clip_negative=False)
    base_items["ml"] = base_items["prediction"]
    rows.append(summarise("ML (bottom-up sum of items)", base_items, "ml"))
    # legacy baseline only exists at product level: spread it over items pro rata to ML for item-level scoring
    share = base_items["prediction"] / base_items.groupby(KEYS)["prediction"].transform("sum").replace(0, np.nan)
    base_items["baseline_spread"] = (base_items["baseline"] * share).fillna(
        base_items["baseline"] / base_items.groupby(KEYS)["prediction"].transform("size")
    )
    rows.append(summarise("Legacy baseline (spread pro rata to ML)", base_items, "baseline_spread"))
    for w in ("ols", "wls_struct"):
        rec = reconcile_two_level(items, baseline, weights=w)
        rows.append(summarise(f"Reconciled ML+baseline ({w})", rec, "reconciled"))

    out = pd.DataFrame(rows)
    ml_fpe = out.loc[0, "product_fpe"]
    out["product_fva_vs_ml_pts"] = [fva_points(ml_fpe, f) for f in out["product_fpe"]]
    return out
