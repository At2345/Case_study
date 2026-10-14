import math

import numpy as np
import pandas as pd
import pytest

from app.config import settings
from app.evaluation.metrics import compute_metrics, fva_points, valid_rows

needs_data = pytest.mark.skipif(
    not (settings.data_dir / "backtest_predictions.parquet").exists(), reason="data not available"
)


def test_fpe_and_bias_hand_computed():
    df = pd.DataFrame({"target": [10.0, 0.0, 5.0], "prediction": [12.0, 3.0, 2.0]})
    m = compute_metrics(df)
    assert m["fpe"] == pytest.approx(100 * (2 + 3 + 3) / 15)
    assert m["bias_pct"] == pytest.approx(100 * (2 + 3 - 3) / 15)
    assert m["mae"] == pytest.approx(8 / 3)
    assert m["rmse"] == pytest.approx(math.sqrt((4 + 9 + 9) / 3))
    assert m["n_rows"] == 3


def test_rows_with_missing_target_or_prediction_are_excluded():
    df = pd.DataFrame({"target": [10.0, np.nan, 5.0], "prediction": [10.0, 4.0, np.nan]})
    assert len(valid_rows(df)) == 1
    assert compute_metrics(df)["fpe"] == 0.0


def test_empty_and_zero_volume_do_not_raise():
    assert compute_metrics(pd.DataFrame({"target": [], "prediction": []}))["n_rows"] == 0
    zero = compute_metrics(pd.DataFrame({"target": [0.0], "prediction": [1.0]}))
    assert math.isnan(zero["fpe"])


def test_fva_sign_convention():
    assert fva_points(44.1, 26.4) == pytest.approx(17.7)
    assert fva_points(26.4, 40.0) < 0


@needs_data
def test_reproduces_official_overall_error_metrics():
    from app.data.loaders import load

    official = load("overall_error_metrics").iloc[0]
    ours = compute_metrics(load("backtest_predictions"))
    assert ours["n_rows"] == int(official.n_rows) == 152499
    assert ours["fpe"] == pytest.approx(official.metric_fpe, abs=1e-6)
    assert ours["bias_pct"] == pytest.approx(official.metric_bias_pct, abs=1e-6)
    assert ours["mae"] == pytest.approx(official.metric_mae, abs=1e-6)
    assert ours["rmse"] == pytest.approx(official.metric_rmse, abs=1e-6)


@needs_data
def test_reproduces_per_item_metrics():
    from app.data.loaders import load

    bt = load("backtest_predictions")
    item = load("item_error_metrics")
    scored = valid_rows(bt)
    assert scored.item_id.nunique() == len(item)
    assert scored.groupby("item_id").size().sum() == item.n_rows.sum()
    sample = item.sample(50, random_state=0)
    for row in sample.itertuples():
        m = compute_metrics(scored[scored.item_id == row.item_id], filter_valid=False)
        assert m["mae"] == pytest.approx(row.metric_mae, rel=1e-6)
        assert m["n_rows"] == row.n_rows
