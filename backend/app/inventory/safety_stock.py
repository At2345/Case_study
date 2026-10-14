"""Phase 3: dynamic safety stock from the forecast quantile spread plus order-book lead time.

SS_dynamic = z * sqrt(LT * sigma_f^2 + d^2 * sigma_LT^2), sigma_f from the P10-P90 band of month 1.
Compared against the classic static rule SS_static = z * sigma_demand(last 12 months) * sqrt(LT).
Holding cost = SS * purchase_price * holding rate. The reduction is measured, not assumed.
Run: PYTHONPATH=backend python -m app.inventory.safety_stock  (after app.forecasting.quantiles)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Dict

import numpy as np
import pandas as pd

from app.config import settings
from app.data.loaders import load

SERVICE_Z = {"A": 2.054, "B": 1.645, "C": 1.282}  # 98% / 95% / 90%
DEFAULT_Z = 1.282
HOLDING_RATE = 0.25  # annual, share of purchase price (assumption to confirm)
TARGET_REDUCTION = 0.12
REVIEW_MONTHS = 1.0  # monthly S&OP replenishment review


def lead_times(ob: pd.DataFrame) -> pd.DataFrame:
    d = ob[ob["delivered_at"].notna() & ob["ordered_at"].notna() & (ob["cancellation_status"] != "cancelled_completely")]
    days = (d["delivered_at"] - d["ordered_at"]).dt.days
    d = d.assign(lt=days)[(days >= 0) & (days <= 365)]
    g = d.groupby("channel")["lt"]
    return pd.DataFrame({"lt_days_mean": g.mean(), "lt_days_std": g.std(), "lines": g.size()}).reset_index()


def compute(triage: pd.DataFrame, bands: pd.DataFrame, hist: pd.DataFrame, lt: pd.DataFrame, first_month) -> pd.DataFrame:
    t = triage[triage["in_scope"]][["item_id", "product_id", "channel", "abc_category", "purchase_price", "final_m1"]]
    b1 = bands[bands["date"] == pd.Timestamp(first_month)][["item_id", "p10", "p90", "sigma"]]
    sd = hist.groupby("item_id")["target"].std().rename("sigma_hist")
    d = t.merge(b1, on="item_id", how="left").merge(sd, on="item_id", how="left").merge(lt, on="channel", how="left")
    d["z"] = d["abc_category"].map(SERVICE_Z).fillna(DEFAULT_Z)
    d["lt_m"] = REVIEW_MONTHS + d["lt_days_mean"].fillna(0) / 30.0  # protection interval = review period + lead time
    d["lt_sd_m"] = d["lt_days_std"].fillna(0) / 30.0
    dem, sf = d["final_m1"].fillna(0), d["sigma"].fillna(0)
    d["ss_dynamic"] = d["z"] * np.sqrt(d["lt_m"] * sf ** 2 + dem ** 2 * d["lt_sd_m"] ** 2)
    d["ss_static"] = d["z"] * d["sigma_hist"].fillna(0) * np.sqrt(d["lt_m"]) + d["z"] * dem * d["lt_sd_m"]
    price = d["purchase_price"].fillna(0)
    d["holding_dynamic_gbp"] = d["ss_dynamic"] * price * HOLDING_RATE
    d["holding_static_gbp"] = d["ss_static"] * price * HOLDING_RATE
    return d


def summarise(d: pd.DataFrame) -> Dict:
    def block(x: pd.DataFrame) -> Dict:
        s, n = float(x["holding_static_gbp"].sum()), float(x["holding_dynamic_gbp"].sum())
        return {"items": int(len(x)), "ss_static_units": float(x["ss_static"].sum()), "ss_dynamic_units": float(x["ss_dynamic"].sum()),
                "holding_static_gbp": s, "holding_dynamic_gbp": n, "reduction_pct": (1 - n / s) if s > 0 else None}
    a = block(d[d["abc_category"] == "A"])
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "holding_rate": HOLDING_RATE,
            "target_reduction_pct": TARGET_REDUCTION, "a_items": a, "all_items": block(d),
            "by_channel": {c: block(g) for c, g in d.groupby("channel")},
            "target_met_on_a_items": bool(a["reduction_pct"] is not None and a["reduction_pct"] >= TARGET_REDUCTION)}


def service_backtest(bt: pd.DataFrame, ts: pd.DataFrame, q: pd.DataFrame, lt: pd.DataFrame) -> Dict:
    """Achieved cycle service level of both policies at horizon 1 on the latest backtest cutoff (A-items)."""
    from app.forecasting.quantiles import Z_BAND, _frame
    d = _frame(bt)
    cut = d["cutoff"].max()
    d = d[(d["cutoff"] == cut) & (d["horizon"] == 1)]
    d = d.merge(bt.drop_duplicates("item_id")[["item_id", "abc_category"]], on="item_id", how="left")
    d = d[d["abc_category"] == "A"].merge(q, on=["channel", "hb"], how="left").merge(lt, on="channel", how="left")
    win = ts[(ts["date"] <= cut) & (ts["date"] > cut - pd.DateOffset(months=12))]
    d = d.merge(win.groupby("item_id")["target"].std().rename("sigma_hist"), on="item_id", how="left")
    z = SERVICE_Z["A"]
    lt_m, lt_sd = REVIEW_MONTHS + d["lt_days_mean"].fillna(0) / 30.0, d["lt_days_std"].fillna(0) / 30.0
    sf = d["base"] * (d["q90"] - d["q10"]) / Z_BAND
    ss_dyn = z * np.sqrt(lt_m * sf ** 2 + d["prediction"] ** 2 * lt_sd ** 2)
    ss_sta = z * d["sigma_hist"].fillna(0) * np.sqrt(lt_m) + z * d["prediction"] * lt_sd
    return {"cutoff": str(pd.Timestamp(cut).date()), "rows": int(len(d)), "target_service": 0.98,
            "service_dynamic": float((d["target"] <= d["prediction"] + ss_dyn).mean()),
            "service_static": float((d["target"] <= d["prediction"] + ss_sta).mean()),
            "ss_dynamic_units": float(ss_dyn.sum()), "ss_static_units": float(ss_sta.sum())}


def main() -> Dict:
    out = settings.reports_dir
    meta = json.loads((out / "triage_meta.json").read_text())
    triage = pd.read_parquet(out / "triage_live.parquet")
    bands = pd.read_parquet(out / "quantile_bands.parquet")
    start = pd.Timestamp(meta["cutoff"]) - pd.DateOffset(months=11)
    hist = load("target_time_series", columns=["item_id", "date", "target"], filters=[("date", ">=", start)])
    ob = load("orderbook", columns=["channel", "ordered_at", "delivered_at", "cancellation_status"])
    lt = lead_times(ob)
    d = compute(triage, bands, hist, lt, meta["first_month"])
    keep = ["item_id", "product_id", "channel", "abc_category", "final_m1", "p10", "p90", "sigma", "sigma_hist", "z",
            "lt_m", "ss_dynamic", "ss_static", "holding_dynamic_gbp", "holding_static_gbp"]
    d[keep].to_parquet(out / "safety_stock.parquet", index=False)
    lt.to_csv(out / "lead_times.csv", index=False)
    s = summarise(d)
    s["lead_times"] = lt.to_dict("records")
    from app.evaluation.stratify import prepare_backtest
    calib = pd.read_csv(out / "quantile_calibration.csv")
    full = load("target_time_series", columns=["item_id", "date", "target"])
    s["service_backtest"] = service_backtest(prepare_backtest(), full, calib, lt)
    (out / "safety_stock_meta.json").write_text(json.dumps(s, indent=2, default=float))
    print(json.dumps(s, indent=2, default=float))
    return s


if __name__ == "__main__":
    main()
