import numpy as np
import pandas as pd
import pytest

from app.evaluation.fva import negative_fva_streaks, override_fva

T = pd.Timestamp


def _overrides(rows):
    cols = ["item_id", "cutoff", "target_month", "baseline_quantity", "ml_recommended_quantity",
            "submitted_quantity", "planner_id", "reason_code"]
    return pd.DataFrame(rows, columns=cols)


def _actuals(rows):
    return pd.DataFrame(rows, columns=["item_id", "date", "target"])


def test_fva_is_ml_error_minus_planner_error_and_positive_when_planner_is_closer():
    ov = _overrides([
        (1, "2026-02-01", "2026-03-01", 150.0, 100.0, 130.0, "danny", "Commercial Intelligence (Account Feedback)"),
        (2, "2026-02-01", "2026-03-01", 150.0, 100.0, 60.0, "danny", "Commercial Intelligence (Account Feedback)"),
    ])
    act = _actuals([(1, T("2026-03-01"), 130.0), (2, T("2026-03-01"), 100.0)])
    out = override_fva(ov, act).iloc[0]
    # ML errors 30 + 0 = 30 over 230 volume; planner errors 0 + 40 = 40
    assert out["fpe_ml"] == pytest.approx(100 * 30 / 230)
    assert out["fpe_planner"] == pytest.approx(100 * 40 / 230)
    assert out["fva_pts"] == pytest.approx(100 * (30 - 40) / 230)
    assert out["n_overrides"] == 2


def test_only_matured_overrides_count_and_accepts_are_excluded():
    ov = _overrides([
        (1, "2026-02-01", "2026-03-01", 100.0, 100.0, 100.0, "danny", "ML Output Formally Accepted"),
        (2, "2026-02-01", "2026-03-01", 100.0, 100.0, 90.0, "danny", "Supply Chain Constrained Run"),
        (3, "2026-02-01", "2026-04-01", 100.0, 100.0, 90.0, "danny", "Supply Chain Constrained Run"),
    ])
    act = _actuals([(1, T("2026-03-01"), 100.0), (2, T("2026-03-01"), 90.0)])  # item 3: April not yet observed
    out = override_fva(ov, act).iloc[0]
    assert out["n_overrides"] == 1 and out["n_accepted"] == 1


def test_no_matured_data_returns_empty_frame_with_columns():
    ov = _overrides([(3, "2026-02-01", "2026-04-01", 1.0, 1.0, 2.0, "danny", "x")])
    out = override_fva(ov, _actuals([(3, T("2026-03-01"), 5.0)]))
    assert out.empty and "fva_pts" in out.columns


def test_latest_override_per_item_month_is_used():
    ov = _overrides([
        (1, "2026-02-01", "2026-03-01", 100.0, 100.0, 50.0, "danny", "Supply Chain Constrained Run"),
        (1, "2026-02-01", "2026-03-01", 100.0, 100.0, 95.0, "danny", "Supply Chain Constrained Run"),
    ])
    act = _actuals([(1, T("2026-03-01"), 95.0)])
    out = override_fva(ov, act).iloc[0]
    assert out["n_overrides"] == 1 and out["fpe_planner"] == pytest.approx(0.0)


def test_grouping_by_reason_code():
    ov = _overrides([
        (1, "2026-02-01", "2026-03-01", 1.0, 100.0, 120.0, "danny", "A"),
        (2, "2026-02-01", "2026-03-01", 1.0, 100.0, 80.0, "danny", "B"),
    ])
    act = _actuals([(1, T("2026-03-01"), 120.0), (2, T("2026-03-01"), 100.0)])
    out = override_fva(ov, act, group_by=("reason_code",)).set_index("reason_code")
    assert out.loc["A", "fva_pts"] > 0 > out.loc["B", "fva_pts"]


def test_negative_streaks_need_consecutive_recent_cycles():
    df = pd.DataFrame({
        "segment": ["x"] * 4 + ["y"] * 4 + ["z"] * 2,
        "cutoff": list(pd.date_range("2025-10-01", periods=4, freq="MS")) * 2 + list(pd.date_range("2025-12-01", periods=2, freq="MS")),
        "fva_pts": [1.0, -2.0, -1.0, -3.0] + [-1.0, -1.0, 2.0, -1.0] + [-5.0, -5.0],
    })
    assert negative_fva_streaks(df, min_cycles=3) == ["x"]
    assert negative_fva_streaks(df, min_cycles=2) == ["x", "z"]
