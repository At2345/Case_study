"""Phase 2 daily Amazon feature pipeline (DuckDB over the daily Parquet drops).

Rebuilds the monthly Amazon covariates from the ~29M daily rows (regression check against
`past_covariates`) and recalculates the weekly risk signals without manual steps.
Run: PYTHONPATH=backend python -m app.pipeline.daily_amazon
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

from app.config import settings

# rebuilt column -> past_covariates column
CHECKS = {
    "total_glance_views": "amazon_total_glance_views",
    "max_daily_glance_views": "amazon_max_daily_glance_views",
    "total_ordered_units": "amazon_total_ordered_units",
    "max_daily_ordered_units": "amazon_max_daily_ordered_units",
    "avg_oos_rate": "amazon_avg_oos_rate",
    "max_oos_rate": "amazon_max_oos_rate",
}
SURGE_PCT = 0.20
SLA_SECONDS = 300


def _p(data_dir: Path, name: str) -> str:
    return str(Path(data_dir) / f"{name}.parquet")


MONTHLY_SQL = """
WITH tr AS (
  SELECT item_id, date_trunc('month', date) AS month, sum(glance_views) AS total_glance_views,
         max(glance_views) AS max_daily_glance_views
  FROM read_parquet('{traffic}') GROUP BY 1, 2),
so AS (
  SELECT item_id, date_trunc('month', date) AS month, sum(ordered_units) AS total_ordered_units,
         max(ordered_units) AS max_daily_ordered_units
  FROM read_parquet('{sell}') GROUP BY 1, 2),
inv AS (
  SELECT item_id, date_trunc('month', date) AS month, avg(procurable_product_out_of_stock_rate) AS avg_oos_rate,
         max(procurable_product_out_of_stock_rate) AS max_oos_rate,
         avg(sellable_on_hand_inventory_units) AS avg_inventory_units
  FROM read_parquet('{inv}') GROUP BY 1, 2)
SELECT coalesce(tr.item_id, so.item_id, inv.item_id) AS item_id, coalesce(tr.month, so.month, inv.month) AS month,
       total_glance_views, max_daily_glance_views, total_ordered_units, max_daily_ordered_units,
       avg_oos_rate, max_oos_rate, avg_inventory_units
FROM tr FULL OUTER JOIN so USING (item_id, month) FULL OUTER JOIN inv USING (item_id, month)
"""

WEEKLY_SQL = """
WITH ref_day AS (SELECT max(date) AS d FROM read_parquet('{traffic}')),
tr AS (
  SELECT item_id,
         sum(CASE WHEN date > (SELECT d FROM ref_day) - INTERVAL 14 DAY THEN glance_views ELSE 0 END) AS gv_last14,
         sum(CASE WHEN date <= (SELECT d FROM ref_day) - INTERVAL 14 DAY THEN glance_views ELSE 0 END) AS gv_prev14
  FROM read_parquet('{traffic}') WHERE date > (SELECT d FROM ref_day) - INTERVAL 28 DAY GROUP BY 1),
so AS (
  SELECT item_id, sum(ordered_units) AS units_28d FROM read_parquet('{sell}')
  WHERE date > (SELECT d FROM ref_day) - INTERVAL 28 DAY GROUP BY 1),
inv AS (
  SELECT item_id, avg(procurable_product_out_of_stock_rate) AS oos_rate_28d,
         arg_max(sellable_on_hand_inventory_units, date) AS on_hand
  FROM read_parquet('{inv}') WHERE date > (SELECT d FROM ref_day) - INTERVAL 28 DAY GROUP BY 1)
SELECT tr.item_id, (SELECT d FROM ref_day) AS as_of, gv_last14, gv_prev14,
       CASE WHEN gv_prev14 > 0 THEN gv_last14 / gv_prev14 - 1 END AS glance_change_14d,
       units_28d, oos_rate_28d, on_hand
FROM tr LEFT JOIN so USING (item_id) LEFT JOIN inv USING (item_id)
"""


def _regression(rebuilt: pd.DataFrame, past: pd.DataFrame) -> Dict[str, Any]:
    j = rebuilt.merge(past.rename(columns={"date": "month"}), on=["item_id", "month"], how="inner")
    out = {"matched_item_months": int(len(j)), "columns": {}}
    for mine, ref in CHECKS.items():
        both = j[[mine, ref]].dropna()
        if both.empty:
            out["columns"][ref] = {"rows": 0, "match_rate": None}
            continue
        tol = 1e-6 * both[ref].abs().clip(lower=1.0)
        out["columns"][ref] = {"rows": int(len(both)), "match_rate": float(((both[mine] - both[ref]).abs() <= tol).mean()),
                               "max_abs_diff": float((both[mine] - both[ref]).abs().max())}
    return out


def run(data_dir: Optional[Path] = None, reports_dir: Optional[Path] = None) -> Dict[str, Any]:
    import duckdb

    data_dir = Path(data_dir or settings.data_dir)
    reports_dir = Path(reports_dir or settings.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    paths = {"traffic": _p(data_dir, "amazon_traffic"), "sell": _p(data_dir, "amazon_sell_out"), "inv": _p(data_dir, "amazon_inventory")}
    t0 = time.perf_counter()
    con = duckdb.connect()
    rows_in = sum(con.execute(f"SELECT count(*) FROM read_parquet('{p}')").fetchone()[0] for p in paths.values())
    monthly = con.execute(MONTHLY_SQL.format(**paths)).df()
    weekly = con.execute(WEEKLY_SQL.format(**paths)).df()
    t_features = time.perf_counter() - t0
    monthly["month"] = pd.to_datetime(monthly["month"]).dt.tz_localize(None)
    past = pd.read_parquet(_p(data_dir, "past_covariates"), columns=["item_id", "date"] + list(CHECKS.values()))
    reg = _regression(monthly, past)
    weekly["surge"] = weekly["glance_change_14d"] > SURGE_PCT
    monthly.to_parquet(reports_dir / "amazon_daily_monthly.parquet", index=False)
    weekly.to_parquet(reports_dir / "amazon_weekly_risk.parquet", index=False)
    elapsed = time.perf_counter() - t0
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows_in": int(rows_in), "item_months_out": int(len(monthly)), "items_weekly": int(len(weekly)),
        "as_of": str(pd.Timestamp(weekly["as_of"].max()).date()) if len(weekly) else None,
        "surge_items": int(weekly["surge"].sum()),
        "feature_seconds": round(t_features, 2), "total_seconds": round(elapsed, 2),
        "sla_seconds": SLA_SECONDS, "sla_ok": elapsed < SLA_SECONDS,
        "regression": reg,
    }
    (reports_dir / "amazon_pipeline_meta.json").write_text(json.dumps(result, indent=2))
    return result


def main() -> None:
    print(json.dumps(run(), indent=2))


if __name__ == "__main__":
    main()
