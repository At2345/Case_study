import numpy as np
import pandas as pd
import pytest

from app.evaluation.reconcile import (
    build_summing_matrix,
    mint_reconcile,
    reconcile_two_level,
)

KEYS = ["level_id", "cutoff", "date"]
D = pd.Timestamp("2025-02-01")
C = pd.Timestamp("2025-01-01")


def test_summing_matrix_shape_and_content():
    S = build_summing_matrix(3)
    assert S.shape == (4, 3)
    assert S[0].tolist() == [1, 1, 1]
    assert np.array_equal(S[1:], np.eye(3))


@pytest.mark.parametrize("weights", ["ols", "wls_struct"])
def test_closed_form_matches_matrix_mint(weights):
    rng = np.random.default_rng(0)
    n = 4
    bottom = rng.uniform(5, 50, n)
    top = bottom.sum() + 17.0
    S = build_summing_matrix(n)
    w_top = 1.0 if weights == "ols" else float(n)
    W = np.diag([w_top] + [1.0] * n)
    expected = mint_reconcile(S, np.concatenate([[top], bottom]), W)

    items = pd.DataFrame({"level_id": 1, "cutoff": C, "date": D, "item_id": range(n), "prediction": bottom})
    base = pd.DataFrame({"level_id": [1], "cutoff": [C], "date": [D], "baseline": [top]})
    out = reconcile_two_level(items, base, weights=weights, clip_negative=False)
    assert out["reconciled"].to_numpy() == pytest.approx(expected[1:])


@pytest.mark.parametrize("weights", ["ols", "wls_struct"])
def test_bottom_moves_toward_top_without_overshoot(weights):
    items = pd.DataFrame({"level_id": 1, "cutoff": C, "date": D, "item_id": [1, 2, 3], "prediction": [10.0, 20.0, 30.0]})
    base = pd.DataFrame({"level_id": [1], "cutoff": [C], "date": [D], "baseline": [100.0]})
    out = reconcile_two_level(items, base, weights=weights, clip_negative=False)
    assert 60.0 < out["reconciled"].sum() < 100.0


def test_coherent_inputs_are_unchanged():
    items = pd.DataFrame({"level_id": 1, "cutoff": C, "date": D, "item_id": [1, 2], "prediction": [10.0, 5.0]})
    base = pd.DataFrame({"level_id": [1], "cutoff": [C], "date": [D], "baseline": [15.0]})
    out = reconcile_two_level(items, base, weights="ols")
    assert out["reconciled"].tolist() == pytest.approx([10.0, 5.0])


def test_non_negativity_clip():
    items = pd.DataFrame({"level_id": 1, "cutoff": C, "date": D, "item_id": [1, 2], "prediction": [1.0, 50.0]})
    base = pd.DataFrame({"level_id": [1], "cutoff": [C], "date": [D], "baseline": [0.0]})
    unclipped = reconcile_two_level(items, base, weights="ols", clip_negative=False)["reconciled"]
    clipped = reconcile_two_level(items, base, weights="ols")["reconciled"]
    assert unclipped.min() < 0
    assert clipped.min() >= 0


def test_levels_without_baseline_are_dropped():
    items = pd.DataFrame({"level_id": [1, 2], "cutoff": C, "date": D, "item_id": [1, 2], "prediction": [1.0, 2.0]})
    base = pd.DataFrame({"level_id": [1], "cutoff": [C], "date": [D], "baseline": [1.0]})
    assert reconcile_two_level(items, base)["level_id"].tolist() == [1]
