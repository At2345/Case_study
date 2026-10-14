"""Fallback and stand-in baseline report.

Usage (repo root):  $env:PYTHONPATH="backend"; python -m app.forecasting.report
Writes CSVs and two parquet artifacts to REPORTS_DIR.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.config import settings
from app.data.loaders import load
from app.evaluation.metrics import compute_metrics
from app.evaluation.segments import add_intermittency, add_lifecycle
from app.evaluation.stratify import prepare_enriched_backtest
from app.forecasting.fallback import (
    apply_fallback,
    decay_method,
    fit_eol_decay,
    leave_one_cutoff_out,
    monthly_wide,
    select_champions,
    stat_forecasts,
)
from app.forecasting.stat_methods import METHODS
from app.triage.quality import incomplete_months, mask_incomplete, mask_level_series

LIVE_CUTOFF = pd.Timestamp("2026-02-01")  # last observed month; first live forecast month is 2026-03
FIT_LAST_EOL = pd.Timestamp("2024-12-01")


def _metric_row(name, frame, pred):
    m = compute_metrics(frame.assign(prediction=frame[pred]))
    return {"method": name, "n_rows": m["n_rows"], "fpe": m["fpe"], "bias_pct": m["bias_pct"]}


def method_table(frame: pd.DataFrame, method_cols) -> pd.DataFrame:
    rows = [_metric_row("ml", frame, "prediction")] + [_metric_row(m, frame, m) for m in method_cols]
    return pd.DataFrame(rows).sort_values("fpe").reset_index(drop=True)


def fpe_by_segment(frame: pd.DataFrame, seg: str, cols) -> pd.DataFrame:
    out = {}
    for s, g in frame.groupby(seg):
        out[s] = {c: compute_metrics(g.assign(prediction=g[c]))["fpe"] for c in cols}
        out[s]["n_rows"] = len(g)
        out[s]["volume"] = g["target"].sum()
    return pd.DataFrame(out).T


def build_backtest_frame(methods):
    df = prepare_enriched_backtest()
    ts = load("target_time_series")
    wide = monthly_wide(ts, "item_id")
    stat = stat_forecasts(wide, sorted(df["cutoff"].unique()), methods, id_col="item_id").drop(columns=["horizon"])
    merged = df.merge(stat, on=["item_id", "cutoff", "date"], how="left")
    assert len(merged) == len(df)
    merged["fb_segment"] = np.where(merged["phase"] == "POST_EOL", "POST_EOL", merged["intermittency"])
    merged["cal_segment"] = merged["phase"] + "|" + merged["intermittency"]
    return merged


def legacy_proxy_table(methods) -> pd.DataFrame:
    ts = load("baseline_target_time_series")
    wide = monthly_wide(ts, "level_id")
    legacy = load("baseline_forecast").rename(columns={"prediction": "legacy"})
    cutoffs = sorted(legacy["cutoff"].unique())
    stat = stat_forecasts(wide, cutoffs, methods, id_col="level_id")
    actual = ts[["level_id", "date", "target"]]
    m = stat.merge(legacy, on=["level_id", "cutoff", "date"], how="inner").merge(actual, on=["level_id", "date"], how="inner")
    m = m.dropna(subset=["legacy", "target"])
    rows = [{"method": "legacy (actual tool)", "n_rows": len(m), "fpe_vs_actual": compute_metrics(m.assign(prediction=m["legacy"]))["fpe"],
             "bias_vs_actual": compute_metrics(m.assign(prediction=m["legacy"]))["bias_pct"],
             "gap_to_legacy_pct": 0.0, "gap_bias_pct": 0.0}]
    for name in methods:
        s = m.dropna(subset=[name])
        r = compute_metrics(s.assign(prediction=s[name]))
        gap = np.abs(s[name] - s["legacy"]).sum() / s["legacy"].sum() * 100
        gap_bias = (s[name] - s["legacy"]).sum() / s["legacy"].sum() * 100
        rows.append({"method": name, "n_rows": len(s), "fpe_vs_actual": r["fpe"], "bias_vs_actual": r["bias_pct"],
                     "gap_to_legacy_pct": gap, "gap_bias_pct": gap_bias})
    return pd.DataFrame(rows).sort_values("gap_to_legacy_pct").reset_index(drop=True)


def build_live(methods, champions, proxy_method, route_segments=()):
    items = load("items", columns=["item_id", "product_id", "channel", "product_status", "launch_month", "end_of_life_month"])
    ts = load("target_time_series")
    live = load("live_predictions").rename(columns={"prediction": "ml_prediction"})
    flagged = incomplete_months(ts, items)
    if not flagged.empty:
        print("\n!! Incomplete latest actuals (masked as missing, not zero):")
        print(flagged.to_string(index=False))
    ts_masked = mask_incomplete(ts, items, flagged)
    wide = monthly_wide(ts_masked, "item_id")
    stat = stat_forecasts(wide, [LIVE_CUTOFF], methods, id_col="item_id").drop(columns=["horizon"])
    base = stat[["item_id", "cutoff", "date"]].copy()
    base = add_lifecycle(base, items[["item_id", "launch_month", "end_of_life_month"]])
    base = add_intermittency(base, ts[["item_id", "date", "imputed"]])
    base["fb_segment"] = np.where(base["phase"] == "POST_EOL", "POST_EOL", base["intermittency"])
    frame = base.merge(stat, on=["item_id", "cutoff", "date"]).merge(live, on=["item_id", "date"], how="left")
    frame = frame.rename(columns={"ml_prediction": "prediction"})
    frame = apply_fallback(frame, champions, route_segments=route_segments)
    frame = frame.merge(items[["item_id", "product_id", "channel", "product_status"]], on="item_id", how="left")

    levels = mask_level_series(load("baseline_target_time_series"), load("baseline_mapping"), items, flagged, ts)
    lw = monthly_wide(levels, "level_id")
    lstat = stat_forecasts(lw, [LIVE_CUTOFF], {proxy_method: methods[proxy_method]}, id_col="level_id")
    lstat = lstat.rename(columns={proxy_method: "baseline_proxy"})[["level_id", "date", "baseline_proxy"]]
    return frame, lstat


def main() -> None:
    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 30)
    settings.reports_dir.mkdir(parents=True, exist_ok=True)

    ts = load("target_time_series")
    items = load("items", columns=["item_id", "end_of_life_month"])
    lam, decay_tbl = fit_eol_decay(ts, items, FIT_LAST_EOL)
    methods = {**METHODS, "ma3_decay": decay_method(lam)}
    print(f"== EOL run-out decay (items with EOL <= {FIT_LAST_EOL.date()}) == lambda per month = {lam:.3f}")
    print(decay_tbl.round(3).T.to_string(header=False))

    bt = build_backtest_frame(methods)
    mcols = list(methods)
    common = bt[bt["target"].notna() & bt["prediction"].notna() & bt[mcols].notna().all(axis=1)]
    t_common = method_table(common, mcols)
    t_missing = method_table(bt[bt["target"].notna() & bt["prediction"].isna() & bt[mcols].notna().all(axis=1)], mcols)
    print(f"\n== Methods vs ML on rows ML scored AND all methods available (n={len(common)}) ==")
    print(t_common.round(1).to_string(index=False))
    print("\n== Methods on rows with an actual but NO ML prediction ==")
    print(t_missing.round(1).to_string(index=False))

    seg_tables = {s: fpe_by_segment(common, s, ["prediction"] + mcols) for s in ["fb_segment", "channel", "phase"]}
    for s, t in seg_tables.items():
        print(f"\n== FPE by {s} (common rows) ==")
        print(t.round(1).to_string())

    champ_rows = bt[bt["target"].notna() & bt[mcols].notna().all(axis=1)]
    champions = select_champions(champ_rows, "fb_segment", mcols)
    print("\n== Champion fallback per segment (selected in-sample on all 5 cutoffs) ==", champions)

    loco = leave_one_cutoff_out(champ_rows, mcols, cal_col="cal_segment")
    print("\n== Out-of-cutoff check (champions, routing and calibration learned on the other 4 cutoffs) ==")
    loco_rows = [("ML else fallback", loco["ml_with_fallback"]), ("ML routed to champion where it loses", loco["routed"]),
                 ("ML bias-calibrated per phase|intermittency", loco["calibrated"])]
    loco_tbl = pd.DataFrame([{"strategy": n, "n_rows": m["n_rows"], "fpe": m["fpe"], "bias_pct": m["bias_pct"]} for n, m in loco_rows])
    zero = compute_metrics(champ_rows.assign(prediction=0.0))
    loco_tbl = pd.concat([loco_tbl, pd.DataFrame([{"strategy": "Zero forecast (reference)", "n_rows": zero["n_rows"], "fpe": zero["fpe"], "bias_pct": zero["bias_pct"]}])])
    print(loco_tbl.round(1).to_string(index=False))
    print("Segments routed to champion (count of 5 held-out folds):", loco["routed_segments"])
    seg_cmp = {}
    for name, key in [("ml", "ml_frame"), ("routed", "routed_frame"), ("calibrated", "calibrated_frame")]:
        fr = loco[key]
        seg_cmp[name] = {s: compute_metrics(g)["fpe"] for s, g in fr.groupby("fb_segment")}
    seg_tbl = pd.DataFrame(seg_cmp)
    print("\n== Out-of-cutoff FPE by segment ==")
    print(seg_tbl.round(1).to_string())
    route_segments = [s for s, k in loco["routed_segments"].items() if k >= 4]
    print("Segments routed in live (chosen in >= 4 of 5 folds):", route_segments)

    with_fb = apply_fallback(bt, champions)
    scored = with_fb[with_fb["target"].notna() & with_fb["final"].notna()]
    rows = []
    for name, f in [("ML only (official filter)", scored[scored["source"] == "ml"]),
                    ("Fallback rows only", scored[scored["source"].str.startswith("fallback")]),
                    ("ML + fallback (all rows with actual)", scored)]:
        m = compute_metrics(f.assign(prediction=f["final"]))
        rows.append({"set": name, "n_rows": m["n_rows"], "fpe": m["fpe"], "bias_pct": m["bias_pct"], "volume": m["volume"]})
    coverage = pd.DataFrame(rows)
    n_actual = int(with_fb["target"].notna().sum())
    print(f"\n== Coverage after fallback (rows with an actual: {n_actual}) ==")
    print(coverage.round(1).to_string(index=False))

    proxy = legacy_proxy_table(methods)
    print("\n== Stand-in for legacy baseline at level_id (closest to the legacy forecast first) ==")
    print(proxy.round(1).to_string(index=False))
    proxy_method = proxy[proxy["method"] != "legacy (actual tool)"].iloc[0]["method"]
    print("Proxy method chosen:", proxy_method)

    live, lbase = build_live(methods, champions, proxy_method, route_segments)
    summary = live.groupby("source").agg(items=("item_id", "nunique"), rows=("item_id", "size"), units=("final", "sum"))
    print("\n== Live forecast completeness (2026-03 .. 2027-02) ==")
    print(summary.round(0).to_string())
    by_status = live.assign(has_final=live["final"].notna()).groupby("product_status")["has_final"].mean().round(3)
    print("Share of live rows with a forecast by status:", by_status.to_dict())

    monthly = live.groupby("date").agg(final=("final", "sum"), ml=("prediction", "sum")).reset_index()
    ly = ts.assign(date=ts["date"] + pd.DateOffset(years=1)).groupby("date")["target"].sum().rename("same_month_last_year")
    monthly = monthly.merge(ly, on="date", how="left")
    print("\n== Live total vs same month last year (all items) ==")
    print(monthly.round(0).to_string(index=False))

    out = settings.reports_dir
    t_common.to_csv(out / "fallback_methods_common_rows.csv", index=False)
    t_missing.to_csv(out / "fallback_methods_missing_rows.csv", index=False)
    coverage.to_csv(out / "fallback_coverage.csv", index=False)
    loco_tbl.to_csv(out / "fallback_out_of_cutoff.csv", index=False)
    seg_tbl.to_csv(out / "fallback_out_of_cutoff_by_segment.csv")
    proxy.to_csv(out / "legacy_proxy_candidates.csv", index=False)
    for s, t in seg_tables.items():
        t.to_csv(out / f"fallback_fpe_by_{s}.csv")
    keep = ["item_id", "product_id", "channel", "product_status", "date", "prediction", "final", "source", "fb_segment"]
    live[keep].rename(columns={"prediction": "ml_prediction"}).to_parquet(out / "live_forecast_complete.parquet", index=False)
    lbase.to_parquet(out / "live_baseline_proxy.parquet", index=False)
    print(f"\nWrote artifacts to {out}")


if __name__ == "__main__":
    main()
