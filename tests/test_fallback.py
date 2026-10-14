import numpy as np
import pandas as pd
import pytest

from app.forecasting.fallback import (
    apply_fallback,
    decay_method,
    fit_eol_decay,
    monthly_wide,
    stat_forecasts,
)
from app.forecasting.stat_methods import HORIZON, moving_average

T = pd.Timestamp


def _ts(values, item_id=1, start="2023-01-01"):
    dates = pd.date_range(start, periods=len(values), freq="MS")
    return pd.DataFrame({"item_id": item_id, "date": dates, "target": values})


def test_monthly_wide_is_chronological_and_complete():
    ts = pd.concat([_ts([1.0, 2.0, 3.0], 1), _ts([5.0], 2, start="2023-03-01")])
    w = monthly_wide(ts, "item_id")
    assert list(w.columns) == list(pd.date_range("2023-01-01", "2023-03-01", freq="MS"))
    assert np.isnan(w.loc[2, T("2023-01-01")])
    assert w.loc[2, T("2023-03-01")] == 5.0


def test_stat_forecasts_dates_horizon_and_no_lookahead():
    ts = _ts(list(range(1, 25)))
    w = monthly_wide(ts, "item_id")
    cutoff = T("2024-06-01")
    out = stat_forecasts(w, [cutoff], {"ma3": lambda H: moving_average(H, 3)})
    assert len(out) == HORIZON
    assert out["date"].min() == T("2024-07-01") and out["date"].max() == T("2025-06-01")
    assert out["horizon"].tolist() == list(range(1, 13))
    # history up to and including the cutoff month only: months 16, 17, 18 -> mean 17
    assert out["ma3"].iloc[0] == pytest.approx(17.0)


def test_fit_eol_decay_recovers_known_rate():
    lam = 0.8
    rows = []
    for item in range(20):
        eol = T("2024-01-01")
        pre = pd.date_range("2023-10-01", periods=3, freq="MS")
        rows += [(item, d, 100.0) for d in pre]
        post = pd.date_range("2024-01-01", periods=13, freq="MS")
        rows += [(item, d, 100.0 * lam ** (k + 1)) for k, d in enumerate(post)]
    ts = pd.DataFrame(rows, columns=["item_id", "date", "target"])
    items = pd.DataFrame({"item_id": range(20), "end_of_life_month": ["2024-01-01"] * 20})
    fitted, _ = fit_eol_decay(ts, items, last_eol=T("2024-06-01"))
    assert fitted == pytest.approx(lam, abs=0.01)


def test_decay_method_scales_by_lambda_per_horizon():
    H = np.full((1, 6), 10.0)
    out = decay_method(0.9)(H)
    assert out[0, 0] == pytest.approx(9.0)
    assert out[0, 1] == pytest.approx(8.1)


def test_leave_one_cutoff_out_routes_only_where_champion_beats_ml_in_training_cutoffs():
    from app.forecasting.fallback import leave_one_cutoff_out

    rows = []
    for c in pd.date_range("2024-01-01", periods=4, freq="MS"):
        # segment A: ML is accurate; segment B: ML is terrible, 'alt' is accurate
        rows += [dict(cutoff=c, fb_segment="A", target=10.0, prediction=10.0, alt=20.0),
                 dict(cutoff=c, fb_segment="B", target=10.0, prediction=50.0, alt=10.0)]
    df = pd.DataFrame(rows)
    res = leave_one_cutoff_out(df, ["alt"], min_gain_pts=5.0, min_route_rows=1)
    assert res["ml_with_fallback"]["fpe"] == pytest.approx(100 * (0 + 40) / 20)
    assert res["routed"]["fpe"] == pytest.approx(0.0)
    assert res["routed_segments"] == {"B": 4}


def test_leave_one_cutoff_out_does_not_route_tiny_segments():
    from app.forecasting.fallback import leave_one_cutoff_out

    rows = [dict(cutoff=c, fb_segment="B", target=10.0, prediction=50.0, alt=10.0)
            for c in pd.date_range("2024-01-01", periods=4, freq="MS")]
    res = leave_one_cutoff_out(pd.DataFrame(rows), ["alt"], min_route_rows=10)
    assert res["routed_segments"] == {}
    assert res["routed"]["fpe"] == pytest.approx(res["ml_with_fallback"]["fpe"])


def test_calibration_factors_clip_and_min_rows():
    from app.forecasting.fallback import calibration_factors

    df = pd.DataFrame({
        "seg": ["a"] * 4 + ["b"] * 4 + ["c"] * 2,
        "target": [5.0] * 4 + [1.0] * 4 + [5.0] * 2,
        "prediction": [10.0] * 4 + [10.0] * 4 + [10.0] * 2,
    })
    f = calibration_factors(df, "seg", lo=0.3, hi=1.5, min_rows=4)
    assert f["a"] == pytest.approx(0.5)
    assert f["b"] == pytest.approx(0.3)  # clipped from 0.1
    assert f["c"] == 1.0  # too few rows


def test_leave_one_cutoff_out_calibration_uses_other_cutoffs_only():
    from app.forecasting.fallback import leave_one_cutoff_out

    rows = [dict(cutoff=c, fb_segment="A", target=10.0, prediction=20.0, alt=10.0)
            for c in pd.date_range("2024-01-01", periods=4, freq="MS")]
    res = leave_one_cutoff_out(pd.DataFrame(rows), ["alt"], cal_col="fb_segment", min_cal_rows=1)
    assert res["calibrated"]["fpe"] == pytest.approx(0.0)
    assert res["ml_with_fallback"]["fpe"] == pytest.approx(100.0)


def test_apply_fallback_route_segments_override_ml():
    df = pd.DataFrame({"prediction": [5.0, 5.0], "fb_segment": ["x", "y"], "ma6": [1.0, 2.0]})
    out = apply_fallback(df, {"x": "ma6", "y": "ma6"}, route_segments=["x"])
    assert out["final"].tolist() == [1.0, 5.0]
    assert out["source"].tolist() == ["routed:ma6", "ml"]


def test_apply_fallback_uses_ml_first_then_champion_and_tags_source():
    df = pd.DataFrame({
        "prediction": [5.0, np.nan, np.nan, np.nan],
        "fb_segment": ["smooth", "smooth", "intermittent", "no_history"],
        "ma6": [1.0, 2.0, 3.0, np.nan],
        "croston_sba": [9.0, 9.0, 4.0, np.nan],
    })
    champions = {"smooth": "ma6", "intermittent": "croston_sba", "no_history": "ma6"}
    out = apply_fallback(df, champions)
    assert out["final"].iloc[:3].tolist() == [5.0, 2.0, 4.0]
    assert np.isnan(out["final"].iloc[3])
    assert out["source"].tolist() == ["ml", "fallback:ma6", "fallback:croston_sba", "none"]
