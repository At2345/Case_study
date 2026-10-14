import numpy as np
import pandas as pd
import pytest

from app.phasing.lag import (
    backtest_profile,
    backtest_profile_items,
    booked_orders,
    expected_deliveries,
    lag_profile,
    overall_profile,
    prepare_lines,
)

T = pd.Timestamp


def lines(rows):
    cols = ["order_id", "item_id", "channel", "customer_id", "ordered_at", "material_availability_at",
            "delivered_at", "ordered_quantity_plan_relevant", "cancellation_status"]
    df = pd.DataFrame(rows, columns=cols)
    for c in ("ordered_at", "material_availability_at", "delivered_at"):
        df[c] = pd.to_datetime(df[c])
    return prepare_lines(df)


def row(i, mat, dlv, qty=10.0, channel="B2B wo Amazon", item=1, status="no_cancellation", ordered="2025-01-01"):
    return (f"o{i}", item, channel, "C1", ordered, mat, dlv, qty, status)


def test_prepare_lines_flags_month_end_and_drops_zero_quantity():
    df = lines([
        row(1, "2025-01-10", "2025-01-20"),
        row(2, "2025-01-28", "2025-02-03"),      # last 5 days of January (28..31)
        row(3, "2025-01-26", "2025-01-30"),      # 26th is not within the last 5 days
        row(4, "2025-02-24", "2025-03-02", qty=0.0),
    ])
    assert len(df) == 3
    assert df.set_index("order_id")["eom"].to_dict() == {"o1": False, "o2": True, "o3": False}
    assert df.set_index("order_id").loc["o2", "lag"] == 1


def test_lag_profile_is_quantity_weighted_and_uses_only_delivered_mature_lines():
    df = lines([
        row(1, "2025-01-05", "2025-01-20", qty=30.0),   # lag 0
        row(2, "2025-01-06", "2025-02-02", qty=10.0),   # lag 1
        row(3, "2025-01-07", "2025-03-02", qty=10.0),   # lag 2
        row(4, "2025-01-08", None, qty=500.0),          # undelivered: excluded
        row(5, "2025-01-09", "2025-01-12", qty=500.0, status="cancelled_completely"),  # cancelled: excluded
        row(6, "2025-06-05", "2025-07-02", qty=500.0),  # not mature: excluded
    ])
    p = overall_profile(df, mature_before=T("2025-03-01")).set_index("channel").loc["B2B wo Amazon"]
    assert (p["p0"], p["p1"], p["p2plus"]) == pytest.approx((0.6, 0.2, 0.2))
    assert p["p0"] + p["p1"] + p["p2plus"] == pytest.approx(1.0)


def test_month_end_lines_have_a_much_later_profile():
    rows = [row(i, "2025-01-10", "2025-01-25", qty=10.0) for i in range(8)]
    rows += [row(100 + i, "2025-01-29", "2025-02-04", qty=10.0) for i in range(8)]
    prof = lag_profile(lines(rows), mature_before=T("2025-03-01")).set_index(["channel", "eom"])
    assert prof.loc[("B2B wo Amazon", False), "p0"] == pytest.approx(1.0)
    assert prof.loc[("B2B wo Amazon", True), "p1"] == pytest.approx(1.0)


def test_expected_deliveries_split_demand_over_months_and_add_carry_in():
    months = pd.date_range("2026-03-01", periods=4, freq="MS")
    demand = pd.Series([100.0, 200.0, 0.0, 0.0], index=months)
    carry = pd.Series([50.0, 10.0, 0.0, 0.0], index=months)
    out = expected_deliveries(demand, (0.6, 0.3, 0.1), carry)
    assert out.iloc[0] == pytest.approx(50 + 60)
    assert out.iloc[1] == pytest.approx(10 + 30 + 120)
    assert out.iloc[2] == pytest.approx(10 + 60)
    assert out.iloc[3] == pytest.approx(20)
    assert out.sum() == pytest.approx(demand.sum() + carry.sum())


def test_profile_beats_the_naive_assumption_in_the_backtest():
    rows = []
    n = 0
    for m in pd.date_range("2024-01-01", periods=24, freq="MS"):
        qty = 50.0 + 250.0 * (m.month % 2)  # volatile demand, so a one-month lag changes monthly totals
        for share, lag in ((0.7, 0), (0.3, 1)):
            n += 1
            deliver = m + pd.DateOffset(months=lag) + pd.Timedelta(days=14)
            rows.append(row(n, (m + pd.Timedelta(days=4)).strftime("%Y-%m-%d"), deliver.strftime("%Y-%m-%d"), qty=qty * share))
    bt = backtest_profile(lines(rows), "B2B wo Amazon", train_end=T("2024-12-01"), test_start=T("2025-02-01"), test_end=T("2025-10-01"))
    assert bt["wape_profile"] < 0.02
    assert bt["wape_naive"] > 0.2
    assert bt["months"] == 9


def test_item_grain_backtest_scores_the_profile_per_item_month():
    rows, n = [], 0
    for item in (1, 2):
        for m in pd.date_range("2024-01-01", periods=24, freq="MS"):
            qty = 50.0 + 250.0 * ((m.month + item) % 2)
            for share, lag in ((0.7, 0), (0.3, 1)):
                n += 1
                deliver = m + pd.DateOffset(months=lag) + pd.Timedelta(days=14)
                rows.append(row(n, (m + pd.Timedelta(days=4)).strftime("%Y-%m-%d"), deliver.strftime("%Y-%m-%d"), qty=qty * share, item=item))
    bt = backtest_profile_items(lines(rows), "B2B wo Amazon", T("2024-12-01"), T("2025-02-01"), T("2025-10-01"))
    assert bt["wape_profile"] < 0.02 < 0.2 < bt["wape_naive"]
    assert bt["item_months"] == 18


def test_booked_orders_split_into_current_shifted_and_carry_in():
    df = lines([
        row(1, "2026-03-05", "2026-03-20", qty=10.0, item=1, ordered="2026-02-20"),   # booked for March, delivered March
        row(2, "2026-03-28", "2026-04-04", qty=20.0, item=1, ordered="2026-02-25"),   # booked March, delivered April: shifted
        row(3, "2026-02-27", "2026-03-03", qty=40.0, item=1, ordered="2026-02-20"),   # February material, March delivery: carry-in
        row(4, "2026-03-10", "2026-03-15", qty=99.0, item=1, status="cancelled_completely", ordered="2026-02-20"),
    ])
    b = booked_orders(df, first_month=T("2026-03-01")).set_index(["item_id", "month"])
    assert b.loc[(1, T("2026-03-01")), "booked_qty"] == pytest.approx(30.0)
    assert b.loc[(1, T("2026-03-01")), "booked_shifted_qty"] == pytest.approx(20.0)
    assert b.loc[(1, T("2026-03-01")), "carry_in_qty"] == pytest.approx(40.0)
    assert b.loc[(1, T("2026-03-01")), "shifted_in_qty"] == pytest.approx(0.0)
    assert b.loc[(1, T("2026-04-01")), "shifted_in_qty"] == pytest.approx(20.0)  # the shifted order lands in April
    assert b.loc[(1, T("2026-04-01")), "carry_in_qty"] == pytest.approx(0.0)  # it is not prior-cycle work
