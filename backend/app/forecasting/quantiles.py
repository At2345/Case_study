"""Phase 3: conformal P10/P50/P90 bands from backtest residual ratios.

Ratio r = actual / max(prediction, 1) per (channel, horizon bucket). Quantiles of r are fitted on all
cutoffs but the last and coverage is checked on the last one (honest out-of-sample number); the bands
used live are refitted on every cutoff.
Run: PYTHONPATH=backend python -m app.forecasting.quantiles
"""
from __future__ import annotations

import json
from typing import Dict, Tuple

import numpy as np
import pandas as pd

from app.config import settings
from app.evaluation.metrics import valid_rows

LO, HI = 0.10, 0.90
Z_BAND = 2 * 1.2816  # P10-P90 width in standard deviations


def horizon_bucket(h: pd.Series) -> pd.Series:
    return pd.cut(h, [0, 3, 6, 12], labels=["h1-3", "h4-6", "h7-12"]).astype(str)


def _frame(bt: pd.DataFrame) -> pd.DataFrame:
    d = valid_rows(bt)[["item_id", "cutoff", "date", "target", "prediction", "channel", "horizon"]].copy()
    d = d[d["horizon"].between(1, 12)]
    d["hb"] = horizon_bucket(d["horizon"])
    d["base"] = d["prediction"].clip(lower=1.0)
    d["ratio"] = d["target"] / d["base"]
    return d


def fit(d: pd.DataFrame) -> pd.DataFrame:
    q = d.groupby(["channel", "hb"])["ratio"].quantile([LO, 0.5, HI]).unstack()
    q.columns = ["q10", "q50", "q90"]
    return q.reset_index()


def coverage(d: pd.DataFrame, q: pd.DataFrame) -> pd.DataFrame:
    j = d.merge(q, on=["channel", "hb"], how="inner")
    j["inside"] = (j["target"] >= j["base"] * j["q10"]) & (j["target"] <= j["base"] * j["q90"])
    out = j.groupby(["channel", "hb"]).agg(rows=("inside", "size"), coverage=("inside", "mean")).reset_index()
    tot = pd.DataFrame([{"channel": "ALL", "hb": "ALL", "rows": len(j), "coverage": float(j["inside"].mean())}])
    return pd.concat([out, tot], ignore_index=True)


def calibrate(bt: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    d = _frame(bt)
    hold = d["cutoff"].max()
    check = coverage(d[d["cutoff"] == hold], fit(d[d["cutoff"] < hold]))
    return fit(d), check


def apply_bands(monthly: pd.DataFrame, channels: pd.Series, q: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    m = monthly[["item_id", "date", "final_qty"]].copy()
    m["channel"] = m["item_id"].map(channels)
    h = (m["date"].dt.year - cutoff.year) * 12 + (m["date"].dt.month - cutoff.month)
    m["hb"] = horizon_bucket(h.clip(1, 12))
    m = m.merge(q, on=["channel", "hb"], how="left")
    base = np.where(m["final_qty"] > 0, m["final_qty"].clip(lower=1.0), 0.0)
    m["p10"] = base * m["q10"].fillna(1.0)
    m["p50"] = m["final_qty"]
    m["p90"] = np.maximum(base * m["q90"].fillna(1.0), m["p50"])
    m["p10"] = np.minimum(m["p10"], m["p50"])
    m["sigma"] = (m["p90"] - m["p10"]) / Z_BAND
    return m[["item_id", "date", "p10", "p50", "p90", "sigma"]]


def main() -> Dict:
    from app.evaluation.stratify import prepare_backtest

    out = settings.reports_dir
    q, check = calibrate(prepare_backtest())
    q.to_csv(out / "quantile_calibration.csv", index=False)
    check.to_csv(out / "quantile_coverage.csv", index=False)
    meta = json.loads((out / "triage_meta.json").read_text())
    monthly = pd.read_parquet(out / "triage_monthly.parquet")
    triage = pd.read_parquet(out / "triage_live.parquet", columns=["item_id", "channel"])
    bands = apply_bands(monthly, triage.set_index("item_id")["channel"], q, pd.Timestamp(meta["cutoff"]))
    bands.to_parquet(out / "quantile_bands.parquet", index=False)
    overall = float(check.loc[check["channel"] == "ALL", "coverage"].iloc[0])
    print(q.round(3).to_string(index=False))
    print(check.round(3).to_string(index=False))
    print(f"Out-of-sample P10-P90 coverage on the last cutoff: {overall:.1%} (target 80%)")
    return {"coverage": overall}


if __name__ == "__main__":
    main()
