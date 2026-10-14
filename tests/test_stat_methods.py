import numpy as np
import pytest

from app.forecasting.stat_methods import (
    HORIZON,
    METHODS,
    croston_sba,
    moving_average,
    seasonal_naive,
    ses,
)

nan = np.nan


def test_output_shape_for_all_methods():
    H = np.random.default_rng(0).poisson(3, size=(5, 30)).astype(float)
    for name, fn in METHODS.items():
        assert fn(H).shape == (5, HORIZON), name


def test_moving_average_ignores_leading_unobserved_months():
    H = np.array([[nan, nan, 2.0, 4.0], [1.0, 1.0, 1.0, 7.0]])
    out = moving_average(H, 3)
    assert out[0].tolist() == pytest.approx([3.0] * HORIZON)
    assert out[1].tolist() == pytest.approx([3.0] * HORIZON)


def test_moving_median_is_robust_to_a_spike():
    from app.forecasting.stat_methods import moving_median

    H = np.array([[0.0, 0.0, 0.0, 100.0, 0.0, 0.0]])
    assert moving_median(H, 6)[0, 0] == 0.0


def test_all_unobserved_series_gives_nan():
    out = moving_average(np.array([[nan, nan, nan]]), 3)
    assert np.isnan(out).all()


def test_seasonal_naive_repeats_last_year_by_horizon():
    H = np.arange(1.0, 25.0).reshape(1, 24)  # months 1..24
    out = seasonal_naive(H)
    assert out[0].tolist() == list(range(13, 25))


def test_seasonal_naive_falls_back_to_recent_mean_where_last_year_unobserved():
    H = np.array([[nan] * 8 + [2.0, 4.0, 6.0, 8.0]])
    out = seasonal_naive(H)
    assert out[0].tolist() == pytest.approx([5.0] * 8 + [2.0, 4.0, 6.0, 8.0])


def test_ses_constant_series_is_constant():
    out = ses(np.full((1, 20), 7.0), alpha=0.3)
    assert out[0].tolist() == pytest.approx([7.0] * HORIZON)


def test_croston_sba_constant_interval_demand():
    # demand of 10 every 2nd month -> z=10, p=2 -> SBA = (1 - a/2) * 5
    H = np.array([[0.0, 10.0] * 15])
    out = croston_sba(H, alpha=0.1)
    assert out[0, 0] == pytest.approx((1 - 0.05) * 10 / 2, rel=1e-6)


def test_croston_never_demanded_is_zero_and_nonnegative():
    out = croston_sba(np.zeros((1, 24)))
    assert out.tolist() == [[0.0] * HORIZON]
    H = np.random.default_rng(1).poisson(0.5, size=(50, 40)).astype(float)
    assert (croston_sba(H) >= 0).all()
