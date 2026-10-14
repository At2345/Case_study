from __future__ import annotations

import numpy as np
import pandas as pd

NPI_MONTHS = 6
EOL_MONTHS = 6
INTERMITTENT_SHARE = 0.4
SMOOTH_SHARE = 0.1


def months_between(start: pd.Series, end: pd.Series) -> pd.Series:
    """Whole calendar months from start to end (end - start); NaN if either is missing."""
    start = pd.to_datetime(start)
    end = pd.to_datetime(end)
    out = (end.dt.year - start.dt.year) * 12 + (end.dt.month - start.dt.month)
    return out.astype(float)


def lifecycle_phase(months_since_launch: pd.Series, months_to_eol: pd.Series) -> pd.Series:
    """Lifecycle phase as known at the forecast cutoff.

    months_to_eol follows the plan's D5 convention: >= 0 means EOL is in the future, < 0 means EOL has passed.
    NPI takes precedence over EOL_APPROACHING. Items with neither a launch nor an EOL date are UNKNOWN.
    """
    msl = pd.Series(months_since_launch).reset_index(drop=True)
    mte = pd.Series(months_to_eol).reset_index(drop=True)
    phase = pd.Series("MATURE", index=msl.index, dtype=object)
    phase[mte < 0] = "POST_EOL"
    phase[(mte >= 0) & (mte <= EOL_MONTHS)] = "EOL_APPROACHING"
    phase[(msl >= 0) & (msl <= NPI_MONTHS)] = "NPI"
    phase[msl.isna() & mte.isna()] = "UNKNOWN"
    phase.index = months_since_launch.index
    return phase


def add_lifecycle(df: pd.DataFrame, items: pd.DataFrame) -> pd.DataFrame:
    """Add phase (as of cutoff), months_since_launch, months_to_eol and after_eol_at_target."""
    cols = ["item_id", "launch_month", "end_of_life_month"]
    out = df.merge(items[cols], on="item_id", how="left")
    out["launch_month"] = pd.to_datetime(out["launch_month"])
    out["end_of_life_month"] = pd.to_datetime(out["end_of_life_month"])
    out["months_since_launch"] = months_between(out["launch_month"], out["cutoff"])
    out["months_to_eol"] = months_between(out["cutoff"], out["end_of_life_month"])
    out["phase"] = lifecycle_phase(out["months_since_launch"], out["months_to_eol"])
    out["after_eol_at_target"] = out["date"] > out["end_of_life_month"]
    return out.drop(columns=["launch_month", "end_of_life_month"])


def add_promo_flag(df: pd.DataFrame, promotions: pd.DataFrame) -> pd.DataFrame:
    """promo_active is True when any promotion window overlaps the target month of the item."""
    p = promotions[["item_id", "date_from", "date_to"]].dropna()
    pieces = []
    for row in p.itertuples(index=False):
        months = pd.period_range(row.date_from, row.date_to, freq="M").to_timestamp()
        pieces.append(pd.DataFrame({"item_id": row.item_id, "date": months}))
    if pieces:
        flagged = pd.concat(pieces).drop_duplicates()
        flagged["promo_active"] = True
    else:
        flagged = pd.DataFrame({"item_id": [], "date": pd.to_datetime([]), "promo_active": []})
    out = df.merge(flagged, on=["item_id", "date"], how="left")
    out["promo_active"] = out["promo_active"].fillna(False).astype(bool)
    return out


def add_intermittency(df: pd.DataFrame, target_ts: pd.DataFrame) -> pd.DataFrame:
    """Share of imputed months in the history strictly before each cutoff, and its bucket."""
    parts = []
    for cutoff in df["cutoff"].dropna().unique():
        hist = target_ts[target_ts["date"] < cutoff].groupby("item_id")["imputed"].mean().rename("imputed_share")
        parts.append(hist.reset_index().assign(cutoff=cutoff))
    shares = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["item_id", "imputed_share", "cutoff"])
    out = df.merge(shares, on=["item_id", "cutoff"], how="left")
    bucket = pd.Series("smooth", index=out.index, dtype=object)
    bucket[out["imputed_share"] > SMOOTH_SHARE] = "mixed"
    bucket[out["imputed_share"] > INTERMITTENT_SHARE] = "intermittent"
    bucket[out["imputed_share"].isna()] = "no_history"
    out["intermittency"] = bucket
    return out
