"""Data-quality gate for the latest actuals.

The target is built from the order book. If a channel's most recent months are (almost) entirely
imputed zeros, the feed has not landed yet; treating those zeros as demand would drag every
statistical forecast and baseline down. Such channel-months are masked (NaN) instead.
"""
from __future__ import annotations

import pandas as pd


def incomplete_months(
    ts: pd.DataFrame,
    items: pd.DataFrame,
    threshold: float = 0.95,
    lookback: int = 3,
    min_items: int = 20,
) -> pd.DataFrame:
    """Channel-months among the last `lookback` months whose imputed share is >= threshold."""
    x = ts.merge(items[["item_id", "channel"]], on="item_id", how="left")
    last = x["date"].max()
    start = last - pd.DateOffset(months=lookback - 1)
    x = x[x["date"] >= start]
    g = x.groupby(["channel", "date"]).agg(imputed_share=("imputed", "mean"), items=("item_id", "nunique")).reset_index()
    return g[(g["imputed_share"] >= threshold) & (g["items"] >= min_items)].reset_index(drop=True)


def mask_incomplete(ts: pd.DataFrame, items: pd.DataFrame, flagged: pd.DataFrame) -> pd.DataFrame:
    """Copy of ts with target set to NaN for flagged channel-months."""
    out = ts.copy()
    if flagged.empty:
        return out
    ch = out["item_id"].map(items.set_index("item_id")["channel"])
    key = pd.MultiIndex.from_arrays([ch, out["date"]])
    bad = key.isin(pd.MultiIndex.from_frame(flagged[["channel", "date"]]))
    out.loc[bad, "target"] = float("nan")
    return out


def mask_level_series(
    level_ts: pd.DataFrame,
    mapping: pd.DataFrame,
    items: pd.DataFrame,
    flagged: pd.DataFrame,
    ts: pd.DataFrame,
    history_months: int = 12,
) -> pd.DataFrame:
    """Blank level-months that include an item from a flagged channel that had real history.

    The level total for such a month is partial (the flagged channel's share is missing), so it is
    unusable as a demand observation.
    """
    out = level_ts.copy()
    if flagged.empty:
        return out
    last = ts["date"].max()
    recent = ts[ts["date"] > last - pd.DateOffset(months=history_months)]
    has_hist = recent.groupby("item_id")["target"].sum()
    has_hist = set(has_hist[has_hist > 0].index)
    it = items[["item_id", "channel"]].merge(mapping, on="item_id")
    it = it[it["item_id"].isin(has_hist)]
    bad_pairs = []
    for row in flagged.itertuples(index=False):
        levels = it.loc[it["channel"] == row.channel, "level_id"].unique()
        bad_pairs.extend((lv, row.date) for lv in levels)
    bad = pd.MultiIndex.from_arrays([out["level_id"], out["date"]]).isin(pd.MultiIndex.from_tuples(bad_pairs)) if bad_pairs else False
    out.loc[bad, "target"] = float("nan")
    return out
