"""Statistical forecast generation, EOL run-out decay and the ML-with-fallback composition."""
from __future__ import annotations

from typing import Callable, Dict, Iterable, Optional, Tuple

import numpy as np
import pandas as pd

from app.evaluation.metrics import compute_metrics
from app.forecasting.stat_methods import HORIZON, moving_average

Method = Callable[[np.ndarray], np.ndarray]


def monthly_wide(ts: pd.DataFrame, id_col: str, value_col: str = "target") -> pd.DataFrame:
    """Pivot a long monthly series to ids x months (NaN before a series' first observation)."""
    wide = ts.pivot(index=id_col, columns="date", values=value_col)
    full = pd.date_range(wide.columns.min(), wide.columns.max(), freq="MS")
    return wide.reindex(columns=full)


def stat_forecasts(
    wide: pd.DataFrame, cutoffs: Iterable[pd.Timestamp], methods: Dict[str, Method], id_col: Optional[str] = None
) -> pd.DataFrame:
    """12-month forecasts for every series and cutoff, one column per method.

    History used for cutoff c is every month <= c (the backtest labels the last observed month as
    the cutoff and forecasts c+1..c+12). No later data is touched.
    """
    id_col = id_col or wide.index.name or "id"
    ids = wide.index.to_numpy()
    n = len(ids)
    frames = []
    for c in cutoffs:
        c = pd.Timestamp(c)
        H = wide.loc[:, :c].to_numpy(dtype=float)
        months = pd.date_range(c + pd.offsets.MonthBegin(1), periods=HORIZON, freq="MS")
        frame = {
            id_col: np.repeat(ids, HORIZON),
            "cutoff": c,
            "date": np.tile(months.to_numpy(), n),
            "horizon": np.tile(np.arange(1, HORIZON + 1), n),
        }
        for name, fn in methods.items():
            frame[name] = fn(H).ravel()
        frames.append(pd.DataFrame(frame))
    return pd.concat(frames, ignore_index=True)


def decay_method(lam: float) -> Method:
    """Last-3-month mean tapered by lam per forecast month (run-out of discontinued items)."""

    def _fn(H: np.ndarray) -> np.ndarray:
        base = moving_average(H, 3)
        return base * (lam ** np.arange(1, HORIZON + 1))[None, :]

    return _fn


def fit_eol_decay(
    ts: pd.DataFrame, items: pd.DataFrame, last_eol: pd.Timestamp, pre_months: int = 3
) -> Tuple[float, pd.DataFrame]:
    """Monthly decay factor of sales after the recorded end_of_life_month.

    For items whose EOL month is <= last_eol (so 13 post-EOL months are observed), ratio_k is total sales
    k months after EOL divided by total mean sales of the `pre_months` months before EOL. The decay
    factor is exp(slope) of log(ratio_k) on k, k = 0..12.
    """
    it = items[["item_id", "end_of_life_month"]].dropna().copy()
    it["eol"] = pd.to_datetime(it["end_of_life_month"])
    it = it[it["eol"] <= pd.Timestamp(last_eol)]
    wide = monthly_wide(ts, "item_id")
    pre_total = 0.0
    post_total = np.zeros(13)
    for item_id, eol in zip(it["item_id"], it["eol"]):
        if item_id not in wide.index:
            continue
        row = wide.loc[item_id]
        pre = row.reindex(pd.date_range(eol - pd.DateOffset(months=pre_months), periods=pre_months, freq="MS"))
        post = row.reindex(pd.date_range(eol, periods=13, freq="MS"))
        if pre.isna().any() or post.isna().any() or pre.mean() <= 0:
            continue
        pre_total += pre.mean()
        post_total += post.to_numpy()
    if pre_total <= 0:
        return float("nan"), pd.DataFrame(columns=["k", "ratio"])
    table = pd.DataFrame({"k": np.arange(13), "ratio": post_total / pre_total})
    valid = table[table["ratio"] > 0]
    slope = np.polyfit(valid["k"], np.log(valid["ratio"]), 1)[0]
    return float(np.exp(slope)), table


def select_champions(df: pd.DataFrame, segment_col: str, methods: Iterable[str]) -> Dict[str, str]:
    """For each segment, the method with the lowest FPE on rows that have an actual."""
    champions: Dict[str, str] = {}
    rows = df[df["target"].notna()]
    for seg, g in rows.groupby(segment_col):
        scores = {}
        for m in methods:
            r = compute_metrics(g.assign(prediction=g[m]))
            if r["n_rows"] > 0 and not np.isnan(r["fpe"]):
                scores[m] = r["fpe"]
        if scores:
            champions[seg] = min(scores, key=scores.get)
    return champions


def calibration_factors(
    df: pd.DataFrame, seg_col: str, lo: float = 0.3, hi: float = 1.5, min_rows: int = 200
) -> Dict[str, float]:
    """Per-segment ratio sum(target)/sum(ML prediction), clipped; 1.0 when a segment has too few rows."""
    rows = df[df["target"].notna() & df["prediction"].notna()]
    factors: Dict[str, float] = {}
    for seg, g in rows.groupby(seg_col):
        denom = g["prediction"].sum()
        factors[seg] = float(np.clip(g["target"].sum() / denom, lo, hi)) if len(g) >= min_rows and denom > 0 else 1.0
    return factors


def leave_one_cutoff_out(
    df: pd.DataFrame,
    methods: Iterable[str],
    segment_col: str = "fb_segment",
    min_gain_pts: float = 5.0,
    cal_col: Optional[str] = None,
    min_cal_rows: int = 200,
    min_route_rows: int = 1000,
) -> Dict[str, object]:
    """Out-of-cutoff check of routing: ML everywhere vs ML except segments where a simple method wins.

    For each held-out cutoff, champions and the routed segments are chosen from the OTHER cutoffs only:
    a segment is routed when it has at least `min_route_rows` training rows and its champion's FPE is
    lower than ML's by more than `min_gain_pts` on rows where both exist. Held-out rows are scored with
    (a) ML else fallback champion, (b) routed, (c) calibrated: ML multiplied by the per-`cal_col` bias
    factor learned on the other cutoffs.
    """
    methods = list(methods)
    pieces_ml, pieces_routed, pieces_cal = [], [], []
    route_counts: Dict[str, int] = {}
    rows = df[df["target"].notna()]
    for held in sorted(rows["cutoff"].unique()):
        train = rows[(rows["cutoff"] != held) & rows[methods].notna().all(axis=1)]
        test = rows[rows["cutoff"] == held].copy()
        champions = select_champions(train, segment_col, methods)
        both = train[train["prediction"].notna()]
        routed = set()
        for seg, g in both.groupby(segment_col):
            if seg not in champions or len(g) < min_route_rows:
                continue
            ml = compute_metrics(g)["fpe"]
            ch = compute_metrics(g.assign(prediction=g[champions[seg]]))["fpe"]
            if ml - ch > min_gain_pts:
                routed.add(seg)
        for seg in routed:
            route_counts[seg] = route_counts.get(seg, 0) + 1
        fb = apply_fallback(test, champions, segment_col)
        champ_pred = pd.Series(np.nan, index=test.index)
        for seg, m in champions.items():
            mask = test[segment_col] == seg
            champ_pred[mask] = test.loc[mask, m]
        use_champ = test[segment_col].isin(routed) & champ_pred.notna()
        routed_final = np.where(use_champ, champ_pred, fb["final"])
        pieces_ml.append(test.assign(prediction=fb["final"]))
        pieces_routed.append(test.assign(prediction=routed_final))
        if cal_col is not None:
            f = calibration_factors(train, cal_col, min_rows=min_cal_rows)
            scaled = test["prediction"] * test[cal_col].map(f).fillna(1.0)
            pieces_cal.append(test.assign(prediction=np.where(test["prediction"].notna(), scaled, fb["final"])))
    out = {
        "ml_with_fallback": compute_metrics(pd.concat(pieces_ml)),
        "routed": compute_metrics(pd.concat(pieces_routed)),
        "routed_segments": route_counts,
        "routed_frame": pd.concat(pieces_routed),
        "ml_frame": pd.concat(pieces_ml),
    }
    if cal_col is not None:
        out["calibrated"] = compute_metrics(pd.concat(pieces_cal))
        out["calibrated_frame"] = pd.concat(pieces_cal)
    return out


def apply_fallback(
    df: pd.DataFrame,
    champions: Dict[str, str],
    segment_col: str = "fb_segment",
    route_segments: Iterable[str] = (),
) -> pd.DataFrame:
    """final = ML prediction where present, else the segment champion's statistical forecast.

    Segments listed in `route_segments` use the champion even where an ML prediction exists
    (source 'routed:<method>').
    """
    out = df.copy()
    chosen = out[segment_col].map(champions)
    fb = pd.Series(np.nan, index=out.index)
    for method in set(chosen.dropna()):
        mask = chosen == method
        fb[mask] = out.loc[mask, method]
    has_ml = out["prediction"].notna()
    routed = out[segment_col].isin(list(route_segments)) & fb.notna()
    use_ml = has_ml & ~routed
    out["final"] = np.where(use_ml, out["prediction"], fb)
    source = np.where(use_ml, "ml", "none")
    use_fb = ~use_ml & fb.notna()
    label = np.where(routed, "routed:", "fallback:")
    source = np.where(use_fb, label + chosen.fillna("").astype(str), source)
    out["source"] = source
    return out
