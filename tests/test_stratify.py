import pandas as pd
import pytest

from app.evaluation.stratify import horizon_months, product_level_comparison, stratified_metrics


def _backtest():
    cutoff = pd.Timestamp("2025-01-01")
    d1, d2 = pd.Timestamp("2025-02-01"), pd.Timestamp("2025-03-01")
    rows = [
        # level 1 has two items in different channels; errors cancel at level
        dict(item_id=1, level_id=1, channel="amazon", abc_category="A", cutoff=cutoff, date=d1, target=10.0, prediction=14.0),
        dict(item_id=2, level_id=1, channel="D2C", abc_category="A", cutoff=cutoff, date=d1, target=10.0, prediction=6.0),
        dict(item_id=1, level_id=1, channel="amazon", abc_category="A", cutoff=cutoff, date=d2, target=10.0, prediction=10.0),
        dict(item_id=3, level_id=2, channel="amazon", abc_category="B", cutoff=cutoff, date=d1, target=None, prediction=5.0),
    ]
    df = pd.DataFrame(rows)
    df["horizon"] = horizon_months(df["cutoff"], df["date"])
    return df


def test_horizon():
    df = _backtest()
    assert df["horizon"].tolist() == [1, 1, 2, 1]


def test_stratified_excludes_unscored_rows_and_shares_sum_to_100():
    out = stratified_metrics(_backtest(), ["channel"])
    assert set(out.channel) == {"amazon", "D2C"}
    assert out.volume_share_pct.sum() == pytest.approx(100)
    amazon = out[out.channel == "amazon"].iloc[0]
    assert amazon.n_rows == 2 and amazon.fpe == pytest.approx(100 * 4 / 20)


def test_product_level_error_cancels_across_channels():
    df = _backtest()
    base = pd.DataFrame({
        "level_id": [1, 1],
        "cutoff": [pd.Timestamp("2025-01-01")] * 2,
        "date": [pd.Timestamp("2025-02-01"), pd.Timestamp("2025-03-01")],
        "prediction": [30.0, 10.0],
    })
    out = product_level_comparison(df, baseline=base).iloc[0]
    assert out.ml_fpe == pytest.approx(0.0)
    assert out.baseline_fpe == pytest.approx(100 * 10 / 30)
    assert out.fva_ml_vs_baseline_pts == pytest.approx(100 * 10 / 30)


def test_complete_only_drops_levels_with_unpredicted_items():
    df = _backtest()
    extra = pd.DataFrame([dict(item_id=9, level_id=1, channel="B2B wo Amazon", abc_category="A",
                               cutoff=pd.Timestamp("2025-01-01"), date=pd.Timestamp("2025-02-01"),
                               target=7.0, prediction=None)])
    extra["horizon"] = 1
    df = pd.concat([df, extra], ignore_index=True)
    base = pd.DataFrame({"level_id": [1, 1], "cutoff": [pd.Timestamp("2025-01-01")] * 2,
                         "date": [pd.Timestamp("2025-02-01"), pd.Timestamp("2025-03-01")],
                         "prediction": [30.0, 10.0]})
    strict = product_level_comparison(df, baseline=base).iloc[0]
    loose = product_level_comparison(df, baseline=base, complete_only=False).iloc[0]
    assert strict.level_months == 1
    assert loose.level_months == 2
