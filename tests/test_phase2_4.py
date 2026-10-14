import json

import numpy as np
import pandas as pd
import pytest

from app.evaluation.disaggregate import disaggregate
from app.forecasting.quantiles import apply_bands, calibrate, horizon_bucket
from app.inventory.safety_stock import compute, lead_times, summarise
from app.orchestration.graph import GuardError, run_cycle
from app.rules import otif


# ---------------------------------------------------------------- F-SOP-01 disaggregation
@pytest.mark.parametrize("method", ["mint_diag", "proportional", "ols"])
def test_disaggregation_is_coherent_and_non_negative(method):
    out = disaggregate([100.0, 10.0, 1.0], 50.0, sigma=[30.0, 5.0, 1.0], method=method)
    assert out.sum() == pytest.approx(50.0) and (out >= 0).all()


def test_mint_diag_moves_uncertain_items_most():
    out = disaggregate([100.0, 100.0], 260.0, sigma=[30.0, 10.0], method="mint_diag")
    assert out[0] - 100 == pytest.approx(9 * (out[1] - 100))


def test_proportional_and_ols_follow_their_weights():
    assert disaggregate([300.0, 100.0], 800.0, method="proportional") == pytest.approx([600.0, 200.0])
    assert disaggregate([300.0, 100.0], 600.0, method="ols") == pytest.approx([400.0, 200.0])


def test_negative_results_are_clipped_and_redistributed():
    out = disaggregate([10.0, 100.0], 20.0, method="ols")
    assert out == pytest.approx([0.0, 20.0])
    with pytest.raises(ValueError):
        disaggregate([1.0], -1.0)
    with pytest.raises(ValueError):
        disaggregate([1.0], 1.0, method="magic")


# ---------------------------------------------------------------- F-B2B-03 OTIF
def test_otif_flags_only_underfilled_bookings_in_covered_channels(tmp_path):
    cfg = otif.load_terms(tmp_path)
    rows = [
        {"item_id": 1, "channel": "B2B wo Amazon", "month": "2026-03-01", "booked": 1000.0, "plan": 500.0, "sales_price": 10.0},
        {"item_id": 2, "channel": "B2B wo Amazon", "month": "2026-03-01", "booked": 1000.0, "plan": 960.0, "sales_price": 10.0},
        {"item_id": 3, "channel": "amazon", "month": "2026-03-01", "booked": 1000.0, "plan": 0.0, "sales_price": 10.0},
    ]
    risk = otif.check(rows, cfg)
    assert [r["item_id"] for r in risk] == [1]
    assert risk[0]["fill_rate"] == 0.5 and risk[0]["penalty_gbp"] == pytest.approx(500 * 10 * 0.05)


def test_otif_terms_file_overrides_defaults(tmp_path):
    (tmp_path / "otif_terms.json").write_text(json.dumps({"channels": {"B2B wo Amazon": {"min_fill": 0.5, "penalty_pct": 0.1}}}))
    cfg = otif.load_terms(tmp_path)
    rows = [{"item_id": 1, "channel": "B2B wo Amazon", "month": "m", "booked": 100.0, "plan": 40.0, "sales_price": 2.0}]
    assert otif.check(rows, cfg)[0]["penalty_gbp"] == pytest.approx(60 * 2 * 0.1)


# ---------------------------------------------------------------- Phase 3 bands and safety stock
def _bt():
    rng = np.random.default_rng(0)
    rows = []
    for c, cut in enumerate(pd.to_datetime(["2025-01-01", "2025-04-01", "2025-07-01"])):
        for i in range(200):
            for h in (1, 2, 5):
                pred = 100.0
                rows.append({"item_id": i, "cutoff": cut, "date": cut + pd.DateOffset(months=h), "horizon": h,
                             "channel": "amazon" if i % 2 else "D2C", "prediction": pred, "target": pred * rng.uniform(0.5, 1.5)})
    return pd.DataFrame(rows)


def test_conformal_bands_cover_about_80_percent_out_of_sample():
    q, check = calibrate(_bt())
    overall = check.loc[check["channel"] == "ALL", "coverage"].iloc[0]
    assert 0.7 <= overall <= 0.9
    assert (q["q10"] < q["q50"]).all() and (q["q50"] < q["q90"]).all()


def test_bands_are_ordered_and_zero_for_zero_plans():
    q, _ = calibrate(_bt())
    monthly = pd.DataFrame({"item_id": [1, 1, 2], "date": pd.to_datetime(["2026-03-01", "2026-08-01", "2026-03-01"]),
                            "final_qty": [100.0, 50.0, 0.0]})
    b = apply_bands(monthly, pd.Series({1: "amazon", 2: "D2C"}), q, pd.Timestamp("2026-02-01"))
    assert (b["p10"] <= b["p50"]).all() and (b["p50"] <= b["p90"]).all()
    assert b.loc[b["item_id"] == 2, ["p10", "p90", "sigma"]].sum().sum() == 0
    assert list(horizon_bucket(pd.Series([1, 4, 12]))) == ["h1-3", "h4-6", "h7-12"]


def test_safety_stock_uses_band_sigma_and_lead_time():
    ob = pd.DataFrame({"channel": ["amazon"] * 3, "ordered_at": pd.to_datetime(["2025-01-01"] * 3),
                       "delivered_at": pd.to_datetime(["2025-01-31", "2025-01-31", "2025-01-31"]),
                       "cancellation_status": ["not_cancelled"] * 3})
    lt = lead_times(ob)
    assert lt.loc[0, "lt_days_mean"] == 30
    triage = pd.DataFrame({"item_id": [1], "product_id": ["P"], "channel": ["amazon"], "abc_category": ["A"],
                           "purchase_price": [10.0], "final_m1": [100.0], "in_scope": [True]})
    bands = pd.DataFrame({"item_id": [1], "date": [pd.Timestamp("2026-03-01")], "p10": [50.0], "p90": [150.0], "sigma": [40.0]})
    hist = pd.DataFrame({"item_id": [1] * 3, "target": [0.0, 100.0, 200.0]})
    d = compute(triage, bands, hist, lt.fillna(0), "2026-03-01")
    assert d.loc[0, "lt_m"] == pytest.approx(2.0)
    assert d.loc[0, "ss_dynamic"] == pytest.approx(2.054 * np.sqrt(2.0) * 40.0)
    assert d.loc[0, "ss_static"] == pytest.approx(2.054 * 100.0 * np.sqrt(2.0))
    s = summarise(d)
    assert s["a_items"]["reduction_pct"] == pytest.approx(0.6) and s["target_met_on_a_items"]


# ---------------------------------------------------------------- Phase 4 orchestration
def test_cycle_graph_stops_at_human_review():
    seen = []
    out = run_cycle(on_step=seen.append, nodes=[("a", lambda: {"x": 1}), ("b", lambda: {"y": 2})])
    assert out["status"] == "AWAITING_REVIEW" and [s["node"] for s in seen] == ["a", "b"]


def test_failed_guard_halts_the_graph():
    def bad():
        raise GuardError("queue size 0 outside sanity bounds")
    out = run_cycle(nodes=[("a", lambda: {}), ("b", bad), ("c", lambda: {})])
    assert out["status"] == "FAILED" and [s["node"] for s in out["steps"]] == ["a", "b"]
    assert "sanity bounds" in out["steps"][-1]["error"]
