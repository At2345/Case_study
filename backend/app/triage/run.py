"""Live-cycle triage run.

Usage (repo root, after `python -m app.forecasting.report` has produced the live artifacts):
    $env:PYTHONPATH="backend"; python -m app.triage.run
Writes reports/triage_live.parquet and reports/triage_queue.csv.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional, Tuple

import numpy as np
import pandas as pd

from app.config import settings
from app.data.loaders import load
from app.evaluation.segments import add_lifecycle
from app.triage import features as F
from app.triage.quality import incomplete_months, mask_incomplete
from app.triage.rules import TriageConfig, assign_tiers, evaluate_flags

CUTOFF = pd.Timestamp("2026-02-01")  # last observed month
FIRST_MONTH = pd.Timestamp("2026-03-01")


def build_features(cfg: TriageConfig, reports_dir=None, data_dir=None) -> pd.DataFrame:
    return build_features_full(cfg, reports_dir, data_dir)[0]


def build_features_full(cfg: TriageConfig, reports_dir=None, data_dir=None) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Returns (item features, monthly forecast/baseline table, incomplete-month findings)."""
    reports_dir = reports_dir or settings.reports_dir
    live = pd.read_parquet(reports_dir / "live_forecast_complete.parquet")
    base_level = pd.read_parquet(reports_dir / "live_baseline_proxy.parquet")

    items = load("items", data_dir=data_dir)
    ts = load("target_time_series", data_dir=data_dir)
    flagged = incomplete_months(ts, items)
    tsm = mask_incomplete(ts, items, flagged)
    mapping = load("baseline_mapping", data_dir=data_dir)

    base_item = F.disaggregate_baseline(base_level, mapping, tsm)
    months = sorted(live["date"].unique())
    h_months, six_months = months[: cfg.var_months], months[:6]
    d = live.merge(base_item[["item_id", "date", "baseline_item"]], on=["item_id", "date"], how="left")
    d["baseline_item"] = d["baseline_item"].fillna(0.0)
    d["final"] = d["final"].fillna(0.0)

    def msum(col, ms):
        return d[d["date"].isin(ms)].groupby("item_id")[col].sum()

    m1 = d[d["date"] == months[0]].set_index("item_id")
    feat = pd.DataFrame({
        "final_h": msum("final", h_months), "baseline_h": msum("baseline_item", h_months),
        "final_6m": msum("final", six_months), "baseline_6m": msum("baseline_item", six_months),
        "final_12m": d.groupby("item_id")["final"].sum(),
        "final_m1": m1["final"], "baseline_m1": m1["baseline_item"],
        "ml_m1": m1["ml_prediction"], "source": m1["source"],
    }).reset_index()

    feat = feat.merge(items[["item_id", "product_id", "channel", "product_status", "sales_price", "purchase_price", "abc_category",
                             "launch_month", "end_of_life_month"]], on="item_id", how="left")
    abc = F.derive_abc(items, tsm)
    feat = feat.merge(abc, on="item_id", how="left")
    feat["abc_category"] = feat["abc_final"]

    last = tsm["date"].max()
    hist12 = tsm[tsm["date"] > last - pd.DateOffset(months=12)].groupby("item_id")["target"].sum()
    feat["hist_12m"] = feat["item_id"].map(hist12).fillna(0.0)
    feat["in_scope"] = (feat["final_12m"] > 0) | (feat["hist_12m"] > 0)

    lc = add_lifecycle(feat[["item_id"]].assign(cutoff=CUTOFF, date=FIRST_MONTH), items[["item_id", "launch_month", "end_of_life_month"]])
    feat = feat.merge(lc[["item_id", "phase", "months_since_launch", "months_to_eol"]], on="item_id", how="left")

    amazon_ids = items.loc[items["channel"] == "amazon", "item_id"]
    asof = ts["date"].max() + pd.offsets.MonthEnd(0)
    day0 = asof - pd.Timedelta(days=60)
    traffic = load("amazon_traffic", data_dir=data_dir, filters=[("date", ">=", day0)])
    inventory = load("amazon_inventory", data_dir=data_dir, filters=[("date", ">=", day0)])
    sell = load("amazon_sell_out", data_dir=data_dir, filters=[("date", ">=", day0)])
    sig = F.amazon_signals(traffic[traffic["item_id"].isin(amazon_ids)], inventory[inventory["item_id"].isin(amazon_ids)],
                           sell[sell["item_id"].isin(amazon_ids)], asof)
    feat = feat.merge(sig, on="item_id", how="left")

    gap = F.amazon_demand_gap(load("future_covariates", data_dir=data_dir),
                              load("past_covariates", data_dir=data_dir), FIRST_MONTH)
    feat = feat.merge(gap[["item_id", "p70_gap_pct"]], on="item_id", how="left")

    promos = load("promotions", data_dir=data_dir)
    fut = load("future_covariates", data_dir=data_dir)
    act = F.promo_activity(promos, fut, months[:3])
    feat = feat.merge(act, on="item_id", how="left")
    for c in ("amazon_deal_active", "b2b_campaign_active"):
        feat[c] = feat[c].fillna(False).astype(bool)

    ghost = F.ghost_candidates(tsm, promos, items, FIRST_MONTH)
    feat = feat.merge(ghost[["item_id", "ly_spike_ratio", "ghost_candidate"]], on="item_id", how="left")
    feat["ghost_candidate"] = feat["ghost_candidate"].fillna(False).astype(bool)

    npi = F.npi_analogy(tsm, load("predecessors", data_dir=data_dir), feat)
    feat = feat.merge(npi, on="item_id", how="left")

    phasing_path = reports_dir / "phasing_items.parquet"
    if phasing_path.exists():
        cover = F.orderbook_cover(pd.read_parquet(phasing_path), FIRST_MONTH)
        feat = feat.merge(cover, on="item_id", how="left")
        feat["booked_m1"] = feat["booked_m1"].fillna(0.0)
    else:
        print("!! phasing_items.parquet not found (run python -m app.phasing.run): order-book cover is not evaluated")
        feat["booked_m1"] = np.nan
    monthly = d[["item_id", "date", "final", "ml_prediction", "source", "baseline_item"]].rename(
        columns={"final": "final_qty", "baseline_item": "baseline_qty"})
    return feat, monthly, flagged


def run(cfg: Optional[TriageConfig] = None, reports_dir=None) -> pd.DataFrame:
    return run_full(cfg, reports_dir)[0]


def run_full(cfg: Optional[TriageConfig] = None, reports_dir=None):
    cfg = cfg or TriageConfig()
    feat, monthly, flagged = build_features_full(cfg, reports_dir)
    return assign_tiers(evaluate_flags(feat, cfg), cfg), monthly, flagged, cfg


def summarise(res: pd.DataFrame) -> None:
    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 30)
    scope = res[res["in_scope"]]
    tot_items, tot_vol = len(scope), scope["final_12m"].sum()
    by = scope.groupby("tier").agg(items=("item_id", "size"), vol12=("final_12m", "sum"), value_gbp=("value_delta_gbp", lambda s: s.abs().sum()))
    by["items_pct"] = 100 * by["items"] / tot_items
    by["volume_pct"] = 100 * by["vol12"] / tot_vol
    print(f"== Tiers (in-scope items {tot_items}, of {len(res)}) ==")
    print(by.round(1).to_string())
    q = res[res["requires_review"]]
    print(f"\n== Exception queue: {len(q)} items; planner-led: {(res['state'] == 'PLANNER_LED').sum()} ==")
    print(q.groupby(["channel", "risk_type"]).size().unstack(fill_value=0).to_string())
    print("\nFlag counts among in-scope items:")
    flags = scope["risk_flags"].str.split(",").explode()
    print(flags[flags != ""].value_counts().to_string())
    print("\nVaR distribution (in scope, |value delta| GBP): ", scope["var_gbp"].quantile([.5, .9, .99]).round(0).to_dict(),
          " >=5000:", int((scope["var_gbp"] >= 5000).sum()), " >=500:", int((scope["var_gbp"] >= 500).sum()))
    cols = ["item_id", "channel", "abc_category", "baseline_h", "final_h", "value_delta_gbp", "risk_type", "risk_flags", "source"]
    print("\n== Top 15 exceptions by |value delta| ==")
    print(q.sort_values("var_gbp", ascending=False)[cols].head(15).round(1).to_string(index=False))


def main() -> None:
    res, monthly, flagged, cfg = run_full()
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    keep = [c for c in res.columns if c not in ("launch_month", "end_of_life_month")]
    res[keep].to_parquet(settings.reports_dir / "triage_live.parquet", index=False)
    monthly.to_parquet(settings.reports_dir / "triage_monthly.parquet", index=False)
    meta = {
        "cutoff": CUTOFF.strftime("%Y-%m-%d"), "first_month": FIRST_MONTH.strftime("%Y-%m-%d"),
        "var_months": cfg.var_months, "var_threshold_gbp": cfg.var_threshold_gbp, "var_low_gbp": cfg.var_low_gbp,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "quality_warnings": [
            {"channel": r.channel, "month": r.date.strftime("%Y-%m-%d"), "imputed_share": float(r.imputed_share), "items": int(r.items)}
            for r in flagged.itertuples()
        ],
    }
    (settings.reports_dir / "triage_meta.json").write_text(json.dumps(meta, indent=2))
    q = res[res["requires_review"]].sort_values("var_gbp", ascending=False)
    q[["item_id", "product_id", "channel", "abc_category", "baseline_h", "final_h", "value_delta_gbp", "risk_type", "risk_flags", "source", "narrative"]].to_csv(
        settings.reports_dir / "triage_queue.csv", index=False)
    summarise(res)


if __name__ == "__main__":
    main()
