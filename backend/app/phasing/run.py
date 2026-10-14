"""Order-book phasing run.

Usage (repo root):  $env:PYTHONPATH="backend"; python -m app.phasing.run
Writes reports/phasing_profile.csv, phasing_profile_eom.csv, phasing_backtest.csv, phasing_items.parquet.
Run it before `python -m app.triage.run` (the triage uses the booked quantities).
"""
from __future__ import annotations

import pandas as pd

from app.config import settings
from app.data.loaders import load
from app.evaluation.stratify import prepare_backtest
from app.phasing.floor import evaluate_floor
from app.phasing.lag import (
    QTY,
    backtest_profile,
    backtest_profile_items,
    booked_orders,
    lag_profile,
    overall_profile,
    prepare_lines,
)

FIRST_MONTH = pd.Timestamp("2026-03-01")
MATURITY_MONTHS = 5      # lines older than this are assumed fully delivered/cancelled
BACKTEST_TRAIN_END = pd.Timestamp("2024-12-01")
BACKTEST_START = pd.Timestamp("2025-01-01")
BACKTEST_END = pd.Timestamp("2025-09-01")


def main() -> None:
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 30)
    out = settings.reports_dir
    out.mkdir(parents=True, exist_ok=True)

    cols = ["order_id", "item_id", "channel", "customer_id", "ordered_at", "material_availability_at", "delivered_at",
            QTY, "cancellation_status", "cancellation_date"]
    lines = prepare_lines(load("orderbook", columns=cols))
    mature_before = FIRST_MONTH - pd.DateOffset(months=MATURITY_MONTHS + 1)

    overall = overall_profile(lines, mature_before)
    by_eom = lag_profile(lines, mature_before)
    overall.to_csv(out / "phasing_profile.csv", index=False)
    by_eom.to_csv(out / "phasing_profile_eom.csv", index=False)
    print(f"== Delivery lag profile (lines with material month <= {mature_before.date()}) ==")
    print(overall.round(3).to_string(index=False))
    print("\n== Same, by material date in the last 5 days of the month ==")
    print(by_eom.round(3).to_string(index=False))

    rows = []
    for ch in sorted(lines["channel"].unique()):
        bt = backtest_profile(lines, ch, BACKTEST_TRAIN_END, BACKTEST_START, BACKTEST_END)
        rows.append({"channel": ch, **bt})
    bt = pd.DataFrame(rows)
    item_rows = []
    for ch in sorted(lines["channel"].unique()):
        item_rows.append({"channel": ch, **{f"item_{k}": v for k, v in backtest_profile_items(
            lines, ch, BACKTEST_TRAIN_END, BACKTEST_START, BACKTEST_END).items()}})
    bt = bt.merge(pd.DataFrame(item_rows), on="channel")
    bt.to_csv(out / "phasing_backtest.csv", index=False)
    print(f"\n== Backtest: channel-month delivered units, profile fitted to {BACKTEST_TRAIN_END.date()}, "
          f"tested {BACKTEST_START.date()}..{BACKTEST_END.date()} (WAPE, lower is better) ==")
    print(bt[["channel", "months", "units", "wape_naive", "wape_profile"]].round(3).to_string(index=False))
    print("\n== Same at item-month grain ==")
    print(bt[["channel", "item_item_months", "item_wape_naive", "item_wape_profile"]].round(3).to_string(index=False))

    booked = booked_orders(lines, FIRST_MONTH)
    booked.to_parquet(out / "phasing_items.parquet", index=False)
    ch = lines.drop_duplicates("item_id").set_index("item_id")["channel"]
    booked["channel"] = booked["item_id"].map(ch)
    print("\n== What the order book holds for the planning window (units) ==")
    s = booked.groupby(["month", "channel"])[["booked_qty", "booked_shifted_qty", "carry_in_qty"]].sum().round(0)
    print(s.head(12).to_string())

    floor = evaluate_floor(lines, prepare_backtest())
    floor.to_csv(out / "phasing_floor_backtest.csv", index=False)
    print("\n== Order-book floor on the backtest (horizon 1-2): ML vs ML raised to the bookings known at the cutoff ==")
    print(floor.round(2).to_string(index=False))

    # consistency: profile applied to the last actual B2B month vs the deliveries already scheduled in the book
    last = FIRST_MONTH - pd.DateOffset(months=1)
    b2b = lines[(lines["channel"] == "B2B wo Amazon") & ~lines["cancelled"]]
    feb_demand = b2b.loc[b2b["mm"] == last, QTY].sum()
    p1 = float(overall.set_index("channel").loc["B2B wo Amazon", "p1"])
    sched = booked[(booked["channel"] == "B2B wo Amazon") & (booked["month"] == FIRST_MONTH)]["carry_in_qty"].sum()
    print(f"\nConsistency check, B2B {last:%b %Y} demand {feb_demand:,.0f} units: profile expects {feb_demand * p1:,.0f} "
          f"to be delivered in {FIRST_MONTH:%b %Y}; the order book already schedules {sched:,.0f}.")
    print(f"Wrote artifacts to {out}")


if __name__ == "__main__":
    main()
