from __future__ import annotations

from typing import Iterable, List, Optional

import numpy as np
import pandas as pd

from app.data.loaders import load
from app.evaluation.metrics import compute_metrics, fva_points, valid_rows
from app.evaluation.segments import add_intermittency, add_lifecycle, add_promo_flag

UNCLASSIFIED = "UNCLASSIFIED"


def horizon_months(cutoff: pd.Series, date: pd.Series) -> pd.Series:
    return (date.dt.year - cutoff.dt.year) * 12 + (date.dt.month - cutoff.dt.month)


def prepare_backtest(data_dir=None) -> pd.DataFrame:
    """Backtest joined with item master and product-level (level_id) mapping."""
    bt = load("backtest_predictions", data_dir=data_dir)
    items = load(
        "items",
        columns=["item_id", "product_id", "channel", "abc_category", "product_status", "has_eol", "sales_price"],
        data_dir=data_dir,
    )
    mapping = load("baseline_mapping", data_dir=data_dir)
    df = bt.merge(items, on="item_id", how="left").merge(mapping, on="item_id", how="left")
    df["abc_category"] = df["abc_category"].fillna(UNCLASSIFIED)
    df["horizon"] = horizon_months(df["cutoff"], df["date"])
    return df


def prepare_enriched_backtest(data_dir=None) -> pd.DataFrame:
    """Backtest plus lifecycle phase, promo_active and intermittency bucket (all as known at the cutoff)."""
    df = prepare_backtest(data_dir)
    items = load("items", columns=["item_id", "launch_month", "end_of_life_month"], data_dir=data_dir)
    df = add_lifecycle(df, items)
    df = add_promo_flag(df, load("promotions", data_dir=data_dir))
    df = add_intermittency(df, load("target_time_series", columns=["item_id", "date", "imputed"], data_dir=data_dir))
    return df


def stratified_metrics(df: pd.DataFrame, by: Iterable[str]) -> pd.DataFrame:
    """Item-level FPE/bias per segment, with volume share and item counts (scored rows only)."""
    by = list(by)
    scored = valid_rows(df)
    total_volume = scored["target"].sum()
    rows = []
    for key, g in scored.groupby(by, observed=True):
        key = key if isinstance(key, tuple) else (key,)
        m = compute_metrics(g, filter_valid=False)
        rows.append({**dict(zip(by, key)), **m, "items": g["item_id"].nunique(),
                     "volume_share_pct": 100 * m["volume"] / total_volume})
    out = pd.DataFrame(rows)
    return out.sort_values("volume", ascending=False).reset_index(drop=True)


def product_level_comparison(
    df: pd.DataFrame,
    baseline: Optional[pd.DataFrame] = None,
    by: Optional[List[str]] = None,
    complete_only: bool = True,
    data_dir=None,
) -> pd.DataFrame:
    """ML (item predictions summed to level_id) vs legacy baseline at product level.

    The baseline forecasts the whole product, so comparing it with actuals restricted to items that
    have an ML prediction would inflate its error. With complete_only=True a level-month is kept only
    when every item that has an actual also has an ML prediction (like-for-like). With
    complete_only=False all scored item rows are summed (matches the official metric's row filter).
    """
    if baseline is None:
        baseline = load("baseline_forecast", data_dir=data_dir)
    scored = valid_rows(df)
    keys = ["level_id", "cutoff", "date", "horizon"]
    lvl = scored.groupby(keys, as_index=False)[["prediction", "target"]].sum()
    if complete_only:
        n_actual = df[df["target"].notna()].groupby(keys).size().rename("n_actual")
        n_scored = scored.groupby(keys).size().rename("n_scored")
        counts = pd.concat([n_actual, n_scored], axis=1).fillna(0).reset_index()
        complete = counts[counts["n_actual"] == counts["n_scored"]][keys]
        lvl = lvl.merge(complete, on=keys, how="inner")
    lvl = lvl.merge(
        baseline.rename(columns={"prediction": "baseline"}), on=["level_id", "cutoff", "date"], how="inner"
    )
    lvl["horizon_bucket"] = pd.cut(lvl["horizon"], [0, 1, 3, 6, 12], labels=["h1", "h2-3", "h4-6", "h7-12"])
    groups = by or []
    rows = []
    iterator = lvl.groupby(groups, observed=True) if groups else [((), lvl)]
    for key, g in iterator:
        key = key if isinstance(key, tuple) else (key,)
        ml = compute_metrics(g, prediction="prediction", filter_valid=False)
        bl = compute_metrics(g, prediction="baseline", filter_valid=False)
        rows.append({
            **dict(zip(groups, key)),
            "level_months": len(g), "levels": g["level_id"].nunique(), "volume": ml["volume"],
            "ml_fpe": ml["fpe"], "baseline_fpe": bl["fpe"],
            "ml_bias_pct": ml["bias_pct"], "baseline_bias_pct": bl["bias_pct"],
            "fva_ml_vs_baseline_pts": fva_points(bl["fpe"], ml["fpe"]),
        })
    return pd.DataFrame(rows)


def prediction_coverage(df: pd.DataFrame) -> dict:
    """Share of rows with an actual that have no prediction (coverage gap D6)."""
    with_target = df[df["target"].notna()]
    return {
        "rows_with_target": int(len(with_target)),
        "rows_missing_prediction": int(with_target["prediction"].isna().sum()),
        "pct_missing_prediction": float(100 * with_target["prediction"].isna().mean()) if len(with_target) else np.nan,
        "items_in_backtest": int(df["item_id"].nunique()),
    }
