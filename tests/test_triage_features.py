import numpy as np
import pandas as pd
import pytest

from app.triage.features import (
    orderbook_cover,
    amazon_demand_gap,
    amazon_signals,
    derive_abc,
    disaggregate_baseline,
    ghost_candidates,
    npi_analogy,
    promo_activity,
)

T = pd.Timestamp
ASOF = T("2026-02-28")


def _daily(item_id, start, values, col):
    dates = pd.date_range(start, periods=len(values), freq="D")
    return pd.DataFrame({"item_id": item_id, "date": dates, col: values})


def test_glance_view_momentum_compares_last_14_days_with_the_previous_14():
    prev = _daily(1, "2026-02-01", [100] * 14, "glance_views")
    last = _daily(1, "2026-02-15", [134] * 14, "glance_views")
    traffic = pd.concat([prev, last])
    inv = pd.DataFrame({"item_id": [1], "date": [ASOF], "procurable_product_out_of_stock_rate": [0.02],
                        "open_purchase_order_units": [5.0], "sellable_on_hand_inventory_units": [182.0]})
    sell = _daily(1, "2026-02-01", [10] * 28, "ordered_units")
    out = amazon_signals(traffic, inv, sell, ASOF).set_index("item_id")
    assert out.loc[1, "glance_mom_pct"] == pytest.approx(0.34)
    assert out.loc[1, "on_hand"] == 182 and out.loc[1, "open_po"] == 5
    assert out.loc[1, "weeks_of_cover"] == pytest.approx(182 / 70)
    assert out.loc[1, "oos_rate_28d"] == pytest.approx(0.02)


def test_glance_view_momentum_is_nan_with_too_little_data():
    few = pd.concat([_daily(1, "2026-02-01", [100] * 3, "glance_views"), _daily(1, "2026-02-25", [200] * 3, "glance_views")])
    empty_inv = pd.DataFrame(columns=["item_id", "date", "procurable_product_out_of_stock_rate",
                                      "open_purchase_order_units", "sellable_on_hand_inventory_units"])
    empty_sell = pd.DataFrame(columns=["item_id", "date", "ordered_units"])
    out = amazon_signals(few, empty_inv, empty_sell, ASOF).set_index("item_id")
    assert np.isnan(out.loc[1, "glance_mom_pct"])


def test_stale_inventory_is_ignored():
    traffic = pd.concat([_daily(1, "2026-02-01", [50] * 28, "glance_views")])
    inv = pd.DataFrame({"item_id": [1], "date": [T("2026-02-01")], "procurable_product_out_of_stock_rate": [0.0],
                        "open_purchase_order_units": [0.0], "sellable_on_hand_inventory_units": [999.0]})
    sell = _daily(1, "2026-02-01", [5] * 28, "ordered_units")
    out = amazon_signals(traffic, inv, sell, ASOF).set_index("item_id")
    assert np.isnan(out.loc[1, "on_hand"])


def test_p70_gap_vs_recent_sell_out():
    fut = pd.DataFrame({"item_id": [1, 2], "date": [T("2026-03-01")] * 2, "amazon_forecast_p70": [150.0, np.nan]})
    past = pd.DataFrame({
        "item_id": [1] * 3, "date": pd.to_datetime(["2025-12-01", "2026-01-01", "2026-02-01"]),
        "amazon_total_ordered_units": [100.0, 100.0, 100.0],
    })
    out = amazon_demand_gap(fut, past, T("2026-03-01")).set_index("item_id")
    assert out.loc[1, "p70_gap_pct"] == pytest.approx(0.5)
    assert 2 not in out.index or np.isnan(out.loc[2, "p70_gap_pct"])


def test_disaggregation_follows_trailing_share_and_sums_to_level_baseline():
    ts = pd.concat([
        pd.DataFrame({"item_id": 1, "date": pd.date_range("2025-03-01", periods=12, freq="MS"), "target": 30.0}),
        pd.DataFrame({"item_id": 2, "date": pd.date_range("2025-03-01", periods=12, freq="MS"), "target": 10.0}),
        pd.DataFrame({"item_id": 3, "date": pd.date_range("2025-03-01", periods=12, freq="MS"), "target": 0.0}),
    ])
    mapping = pd.DataFrame({"level_id": [1, 1, 2], "item_id": [1, 2, 3]})
    base = pd.DataFrame({"level_id": [1, 2], "date": [T("2026-03-01")] * 2, "baseline_proxy": [400.0, 8.0]})
    out = disaggregate_baseline(base, mapping, ts).set_index("item_id")
    assert out.loc[1, "baseline_item"] == pytest.approx(300.0)
    assert out.loc[2, "baseline_item"] == pytest.approx(100.0)
    assert out.loc[3, "baseline_item"] == pytest.approx(8.0)  # no history: equal split of a one-item level
    assert out["baseline_item"].sum() == pytest.approx(408.0)


def test_disaggregation_is_seasonal_using_same_months_last_year():
    months = pd.date_range("2025-03-01", periods=12, freq="MS")
    # item 1 sells mostly in March-April; item 2 is flat
    a = [100.0, 100.0] + [10.0] * 10
    ts = pd.concat([
        pd.DataFrame({"item_id": 1, "date": months, "target": a}),
        pd.DataFrame({"item_id": 2, "date": months, "target": [20.0] * 12}),
    ])
    mapping = pd.DataFrame({"level_id": [1, 1], "item_id": [1, 2]})
    base = pd.DataFrame({"level_id": [1, 1], "date": [T("2026-03-01"), T("2026-09-01")], "baseline_proxy": [100.0, 100.0]})
    out = disaggregate_baseline(base, mapping, ts).set_index(["item_id", "date"])["baseline_item"]
    # March: window Feb-Apr 2025 sees item1 200 vs item2 40 -> share 0.833; September: 30 vs 60 -> 0.333
    assert out[(1, T("2026-03-01"))] == pytest.approx(100 * 200 / 240)
    assert out[(1, T("2026-09-01"))] == pytest.approx(100 * 30 / 90)


def test_ghost_candidate_needs_last_year_spike_and_no_campaign():
    months = pd.date_range("2025-03-01", periods=12, freq="MS")
    vals = [100.0] * 12
    vals[0] = 1500.0  # March 2025 spike
    ts = pd.concat([pd.DataFrame({"item_id": i, "date": months, "target": vals}) for i in (1, 2, 3)])
    items = pd.DataFrame({"item_id": [1, 2, 3], "channel": ["B2B wo Amazon", "B2B wo Amazon", "amazon"]})
    promos = pd.DataFrame({
        "item_id": [2], "promotion_type": ["retailer_campaign"],
        "date_from": [T("2026-03-05")], "date_to": [T("2026-03-20")],
    })
    out = ghost_candidates(ts, promos, items, T("2026-03-01")).set_index("item_id")
    assert out.loc[1, "ghost_candidate"]
    assert not out.loc[2, "ghost_candidate"]  # campaign confirmed
    assert not out.loc[3, "ghost_candidate"]  # not a B2B item


def test_orderbook_cover_returns_bookings_for_the_month_only():
    ph = pd.DataFrame({
        "item_id": [1, 1, 2], "month": [T("2026-03-01"), T("2026-04-01"), T("2026-04-01")],
        "booked_qty": [50.0, 70.0, 5.0],
    })
    out = orderbook_cover(ph, T("2026-03-01"))
    assert out.to_dict("records") == [{"item_id": 1, "booked_m1": 50.0}]


def test_promo_activity_flags_deals_and_campaigns_in_window():
    promos = pd.DataFrame({
        "item_id": [1, 2], "promotion_type": ["marketplace_deal", "retailer_campaign"],
        "date_from": [T("2026-04-10")] * 2, "date_to": [T("2026-04-12")] * 2,
    })
    fut = pd.DataFrame({"item_id": [3], "date": [T("2026-03-01")], "amazon_deal_days": [3.0]})
    out = promo_activity(promos, fut, [T("2026-03-01"), T("2026-04-01")]).set_index("item_id")
    assert out.loc[1, "amazon_deal_active"] and not out.loc[1, "b2b_campaign_active"]
    assert out.loc[2, "b2b_campaign_active"] and not out.loc[2, "amazon_deal_active"]
    assert out.loc[3, "amazon_deal_active"]


def test_npi_analogy_uses_predecessor_recent_run_rate():
    ts = pd.DataFrame({
        "item_id": [9] * 4, "date": pd.date_range("2025-11-01", periods=4, freq="MS"),
        "target": [100.0, 100.0, 100.0, np.nan],
    })
    pred = pd.DataFrame({"item_id": [1], "predecessor_item_id": [9], "split_weight": [np.nan]})
    final = pd.DataFrame({"item_id": [1], "final_m1": [160.0]})
    out = npi_analogy(ts, pred, final).set_index("item_id")
    assert out.loc[1, "ml_vs_pred_pct"] == pytest.approx(0.6)


def test_derived_abc_fills_only_missing_classes_by_value_share():
    items = pd.DataFrame({
        "item_id": [1, 2, 3, 4], "abc_category": ["A", None, None, None], "sales_price": [10.0] * 4,
    })
    ts = pd.concat([
        pd.DataFrame({"item_id": i, "date": pd.date_range("2025-03-01", periods=12, freq="MS"), "target": v})
        for i, v in [(1, 1000.0), (2, 100.0), (3, 10.0), (4, 1.0)]
    ])
    out = derive_abc(items, ts).set_index("item_id")
    assert out.loc[1, "abc_final"] == "A" and not out.loc[1, "abc_derived"]
    assert out.loc[2, "abc_final"] in ("A", "B") and out.loc[2, "abc_derived"]
    assert out.loc[4, "abc_final"] == "C"
