"""Forecast Value Added of planner overrides against subsequent actuals."""
from __future__ import annotations

from typing import List, Sequence

import numpy as np
import pandas as pd

ACCEPTED = "ML Output Formally Accepted"
COLUMNS = ["n_overrides", "n_accepted", "volume", "fpe_ml", "fpe_planner", "fpe_baseline", "fva_pts"]


def _fpe(err: np.ndarray, vol: float) -> float:
    return float(np.abs(err).sum() / vol * 100) if vol > 0 else float("nan")


def override_fva(
    overrides: pd.DataFrame, actuals: pd.DataFrame, group_by: Sequence[str] = ("planner_id",)
) -> pd.DataFrame:
    """FVA_planner = FPE(ML) - FPE(planner) in points; positive means the overrides added value.

    Only overrides whose target month has an observed actual count. Formal acceptances of the ML number
    are counted in `n_accepted` but excluded from the error comparison. If an item-month has several
    ledger rows the latest one wins.
    """
    group_by = list(group_by)
    empty = pd.DataFrame(columns=group_by + COLUMNS)
    if overrides.empty:
        return empty
    ov = overrides.copy()
    ov["date"] = pd.to_datetime(ov["target_month"])
    ov = ov.drop_duplicates(["item_id", "cutoff", "date"], keep="last")
    act = actuals.dropna(subset=["target"])[["item_id", "date", "target"]]
    j = ov.merge(act, on=["item_id", "date"], how="inner")
    if j.empty:
        return empty
    j["accepted"] = j["reason_code"] == ACCEPTED
    rows = []
    for key, g in j.groupby(group_by):
        key = key if isinstance(key, tuple) else (key,)
        real = g[~g["accepted"]]
        if real.empty:
            continue
        vol = float(real["target"].sum())
        rows.append({
            **dict(zip(group_by, key)),
            "n_overrides": len(real), "n_accepted": int(g["accepted"].sum()), "volume": vol,
            "fpe_ml": _fpe((real["ml_recommended_quantity"] - real["target"]).to_numpy(), vol),
            "fpe_planner": _fpe((real["submitted_quantity"] - real["target"]).to_numpy(), vol),
            "fpe_baseline": _fpe((real["baseline_quantity"] - real["target"]).to_numpy(), vol),
        })
    if not rows:
        return empty
    out = pd.DataFrame(rows)
    out["fva_pts"] = out["fpe_ml"] - out["fpe_planner"]
    return out


def negative_fva_streaks(per_cycle: pd.DataFrame, min_cycles: int = 3) -> List[str]:
    """Segments whose last `min_cycles` cycles all have negative FVA (candidates for the autopilot tier)."""
    flagged = []
    for seg, g in per_cycle.sort_values("cutoff").groupby("segment"):
        recent = g.tail(min_cycles)
        if len(recent) >= min_cycles and (recent["fva_pts"] < 0).all():
            flagged.append(seg)
    return sorted(flagged)
