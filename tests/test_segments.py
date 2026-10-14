import numpy as np
import pandas as pd
import pytest

from app.evaluation.segments import (
    add_intermittency,
    add_lifecycle,
    add_promo_flag,
    lifecycle_phase,
    months_between,
)

T = pd.Timestamp


def test_months_between_handles_year_boundaries_and_nulls():
    a = pd.Series([T("2024-11-01"), T("2025-01-01"), pd.NaT])
    b = pd.Series([T("2025-02-01"), T("2025-01-01"), T("2025-01-01")])
    out = months_between(a, b)
    assert out.iloc[0] == 3 and out.iloc[1] == 0 and np.isnan(out.iloc[2])


def test_lifecycle_phase_rules():
    msl = pd.Series([2, 40, 40, 40, 40, np.nan])
    mte = pd.Series([np.nan, 4, -3, 20, np.nan, np.nan])
    phases = lifecycle_phase(msl, mte).tolist()
    assert phases == ["NPI", "EOL_APPROACHING", "POST_EOL", "MATURE", "MATURE", "UNKNOWN"]


def test_npi_takes_precedence_over_eol_when_both_apply():
    assert lifecycle_phase(pd.Series([1]), pd.Series([2])).iloc[0] == "NPI"


def test_add_lifecycle_flags_target_after_eol():
    df = pd.DataFrame({
        "item_id": [1, 1], "cutoff": [T("2025-01-01")] * 2, "date": [T("2025-03-01"), T("2025-08-01")],
    })
    items = pd.DataFrame({"item_id": [1], "launch_month": ["2020-01-01"], "end_of_life_month": ["2025-05-01"]})
    out = add_lifecycle(df, items)
    assert (out["phase"] == "EOL_APPROACHING").all()
    assert out["after_eol_at_target"].tolist() == [False, True]


def test_promo_flag_covers_every_month_in_window():
    df = pd.DataFrame({
        "item_id": [1, 1, 1, 2], "date": [T("2025-01-01"), T("2025-02-01"), T("2025-04-01"), T("2025-02-01")],
    })
    promos = pd.DataFrame({
        "item_id": [1], "date_from": [T("2025-01-20")], "date_to": [T("2025-03-05")],
        "promotion_type": ["marketplace_deal"],
    })
    out = add_promo_flag(df, promos)
    assert out["promo_active"].tolist() == [True, True, False, False]


def test_intermittency_uses_only_history_before_cutoff():
    tts = pd.DataFrame({
        "item_id": [1] * 5,
        "date": pd.to_datetime(["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01", "2024-05-01"]),
        "imputed": [True, True, False, False, False],
    })
    df = pd.DataFrame({"item_id": [1, 1, 2], "cutoff": [T("2024-03-01"), T("2024-06-01"), T("2024-03-01")]})
    out = add_intermittency(df, tts)
    assert out["intermittency"].tolist() == ["intermittent", "mixed", "no_history"]
    assert out["imputed_share"].iloc[0] == pytest.approx(1.0)
    assert out["imputed_share"].iloc[1] == pytest.approx(0.4)
