"""Order-book floor: bookings already in the order book are a lower bound on a month's demand.

`booked_by_cutoff` reproduces what the planner could have seen at a past cutoff (lines ordered by the end of the
cutoff month and not yet cancelled then). `evaluate_floor` checks on the backtest whether raising the ML forecast
to the bookings would have reduced the error.
"""
from __future__ import annotations

import pandas as pd

from app.evaluation.metrics import compute_metrics
from app.phasing.lag import QTY


def booked_by_cutoff(lines: pd.DataFrame, cutoff: pd.Timestamp, target_month: pd.Timestamp) -> pd.Series:
    """Units per item booked for `target_month` as known at the end of the cutoff month."""
    cutoff_end = pd.Timestamp(cutoff) + pd.offsets.MonthEnd(0)
    known = lines[(lines["mm"] == pd.Timestamp(target_month)) & (lines["ordered_at"] <= cutoff_end)]
    cancelled_before = known["cancelled"] & (known["cancellation_date"].notna()) & (known["cancellation_date"] <= cutoff_end)
    # a completely cancelled line with no cancellation date cannot be dated: treat it as cancelled (conservative)
    undated = known["cancelled"] & known["cancellation_date"].isna()
    alive = known[~(cancelled_before | undated)]
    return alive.groupby("item_id")[QTY].sum()


def evaluate_floor(lines: pd.DataFrame, backtest: pd.DataFrame, max_horizon: int = 2) -> pd.DataFrame:
    """FPE of ML vs ML floored at the bookings known at the cutoff, on scored backtest rows with horizon <= max_horizon.

    `backtest` needs item_id, cutoff, date, prediction, target, horizon, channel.
    """
    bt = backtest[(backtest["horizon"] <= max_horizon) & backtest["target"].notna() & backtest["prediction"].notna()].copy()
    booked_parts = []
    for (cutoff, month), g in bt.groupby(["cutoff", "date"]):
        b = booked_by_cutoff(lines, cutoff, month)
        booked_parts.append(pd.Series(g["item_id"].map(b).fillna(0.0).to_numpy(), index=g.index))
    bt["booked"] = pd.concat(booked_parts) if booked_parts else 0.0
    bt["floored"] = bt[["prediction", "booked"]].max(axis=1)
    rows = []
    scopes = [("all", bt)] + [(ch, g) for ch, g in bt.groupby("channel")]
    for name, g in scopes:
        ml = compute_metrics(g, filter_valid=False)
        fl = compute_metrics(g.assign(prediction=g["floored"]), filter_valid=False)
        hit = g["booked"] > g["prediction"]
        rows.append({
            "scope": name, "rows": len(g), "rows_floored": int(hit.sum()),
            "fpe_ml": ml["fpe"], "fpe_floored": fl["fpe"], "bias_ml": ml["bias_pct"], "bias_floored": fl["bias_pct"],
            "fpe_ml_on_floored_rows": compute_metrics(g[hit], filter_valid=False)["fpe"] if hit.any() else float("nan"),
            "fpe_floor_on_floored_rows": compute_metrics(g[hit].assign(prediction=g.loc[hit, "floored"]), filter_valid=False)["fpe"] if hit.any() else float("nan"),
        })
    return pd.DataFrame(rows)
