import numpy as np
import pandas as pd

from app.triage.quality import incomplete_months, mask_incomplete, mask_level_series

T = pd.Timestamp


def _frame(n_items=25):
    rows = []
    items = []
    for i in range(n_items):
        items.append({"item_id": i, "channel": "amazon" if i < n_items // 2 else "D2C"})
        for d, imp_amz in [("2025-12-01", False), ("2026-01-01", False), ("2026-02-01", True)]:
            imputed = imp_amz if i < n_items // 2 else False
            rows.append({"item_id": i, "date": T(d), "target": 0.0 if imputed else 5.0, "imputed": imputed})
    return pd.DataFrame(rows), pd.DataFrame(items)


def test_flags_only_the_channel_month_that_is_fully_imputed():
    ts, items = _frame()
    flagged = incomplete_months(ts, items, min_items=5)
    assert flagged[["channel", "date"]].values.tolist() == [["amazon", T("2026-02-01")]]
    assert flagged["imputed_share"].iloc[0] == 1.0


def test_small_channels_are_not_flagged():
    ts, items = _frame(n_items=6)
    assert incomplete_months(ts, items, min_items=20).empty


def test_mask_sets_target_nan_for_flagged_channel_months_only():
    ts, items = _frame()
    flagged = incomplete_months(ts, items, min_items=5)
    out = mask_incomplete(ts, items, flagged)
    amz_feb = out.merge(items, on="item_id").query("channel == 'amazon' and date == '2026-02-01'")
    assert amz_feb["target"].isna().all()
    assert out["target"].notna().sum() == len(ts) - len(amz_feb)
    assert ts["target"].notna().all()  # input not mutated


def test_mask_level_series_blanks_levels_that_contain_a_flagged_channel_with_history():
    ts, items = _frame()
    mapping = pd.DataFrame({"level_id": [1, 1, 2], "item_id": [0, 20, 21]})  # level 1 has amazon item 0
    level_ts = pd.DataFrame({
        "level_id": [1, 1, 2, 2], "date": [T("2026-01-01"), T("2026-02-01")] * 2, "target": [10.0, 4.0, 3.0, 3.0],
    })
    flagged = incomplete_months(ts, items, min_items=5)
    out = mask_level_series(level_ts, mapping, items, flagged, ts)
    got = out.set_index(["level_id", "date"])["target"]
    assert np.isnan(got[(1, T("2026-02-01"))])
    assert got[(1, T("2026-01-01"))] == 10.0
    assert got[(2, T("2026-02-01"))] == 3.0
