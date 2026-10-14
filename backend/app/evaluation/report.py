"""Gate G0 evaluation report.

Usage (from repo root):  python -m app.evaluation.report   (with PYTHONPATH=backend)
Writes CSVs to REPORTS_DIR and prints the key tables.
"""
from __future__ import annotations

import pandas as pd

from app.config import settings
from app.data.loaders import load, validate_all
from app.evaluation.metrics import compute_metrics
from app.evaluation.reconcile import evaluate_reconciliation
from app.evaluation.stratify import (
    prepare_enriched_backtest,
    prediction_coverage,
    product_level_comparison,
    stratified_metrics,
)


def build_report() -> dict:
    issues = {k: v for k, v in validate_all().items() if v}
    if issues:
        raise SystemExit(f"Data contract violations: {issues}")

    df = prepare_enriched_backtest()
    official = load("overall_error_metrics").iloc[0]
    ours = compute_metrics(df)

    tables = {
        "by_channel": stratified_metrics(df, ["channel"]),
        "by_abc": stratified_metrics(df, ["abc_category"]),
        "by_channel_abc": stratified_metrics(df, ["channel", "abc_category"]),
        "by_horizon": stratified_metrics(df, ["horizon"]),
        "by_cutoff": stratified_metrics(df, ["cutoff"]),
        "by_status": stratified_metrics(df, ["product_status"]),
        "by_phase": stratified_metrics(df, ["phase"]),
        "by_phase_channel": stratified_metrics(df, ["phase", "channel"]),
        "by_after_eol": stratified_metrics(df, ["after_eol_at_target"]),
        "by_promo": stratified_metrics(df, ["channel", "promo_active"]),
        "by_intermittency": stratified_metrics(df, ["intermittency"]),
        "by_intermittency_channel": stratified_metrics(df, ["channel", "intermittency"]),
        "product_overall": product_level_comparison(df),
        "product_by_horizon": product_level_comparison(df, by=["horizon_bucket"]),
        "product_by_cutoff": product_level_comparison(df, by=["cutoff"]),
        "product_overall_all_scored_rows": product_level_comparison(df, complete_only=False),
        "reconciliation": evaluate_reconciliation(df),
    }
    return {"official": official, "ours": ours, "coverage": prediction_coverage(df), "tables": tables}


def main() -> None:
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 30)
    rep = build_report()
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    for name, t in rep["tables"].items():
        t.to_csv(settings.reports_dir / f"{name}.csv", index=False)

    o, m = rep["official"], rep["ours"]
    print("== Reproduction of overall_error_metrics ==")
    print(f"official: FPE={o.metric_fpe:.3f} bias={o.metric_bias_pct:.3f} MAE={o.metric_mae:.3f} "
          f"RMSE={o.metric_rmse:.3f} n={int(o.n_rows)}")
    print(f"ours:     FPE={m['fpe']:.3f} bias={m['bias_pct']:.3f} MAE={m['mae']:.3f} "
          f"RMSE={m['rmse']:.3f} n={m['n_rows']}")
    print("\n== Coverage ==", rep["coverage"])
    cols = ["n_rows", "items", "volume_share_pct", "fpe", "bias_pct"]
    for name in ["by_channel", "by_abc", "by_channel_abc", "by_horizon", "by_cutoff", "by_status",
                 "by_phase", "by_phase_channel", "by_after_eol", "by_promo", "by_intermittency",
                 "by_intermittency_channel"]:
        print(f"\n== {name} ==")
        print(rep["tables"][name].drop(columns=["mae", "rmse", "volume"]).round(1).to_string(index=False))
    for name in ["product_overall", "product_by_horizon", "product_by_cutoff", "product_overall_all_scored_rows"]:
        print(f"\n== {name} (ML vs legacy baseline at level_id) ==")
        print(rep["tables"][name].round(1).to_string(index=False))
    print("\n== reconciliation (matched level-months, ML item forecasts + legacy product baseline) ==")
    print(rep["tables"]["reconciliation"].round(1).to_string(index=False))


if __name__ == "__main__":
    main()
