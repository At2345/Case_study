"""Feature builders: turn raw tables into the per-item inputs the triage rules need."""
from __future__ import annotations

from typing import Iterable, Optional

import numpy as np
import pandas as pd

MIN_WINDOW_DAYS = 7


def _window_mean(df: pd.DataFrame, value: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    w = df[(df["date"] >= start) & (df["date"] <= end)]
    return w.groupby("item_id")[value].agg(["mean", "count"])


def amazon_signals(
    traffic: pd.DataFrame, inventory: pd.DataFrame, sell_out: pd.DataFrame, asof: pd.Timestamp
) -> pd.DataFrame:
    """Per-item Amazon telemetry as of `asof`.

    glance_mom_pct: mean daily glance views of the last 14 days vs the 14 days before (needs >= 7 days
    in each window and >= 1 view/day before). on_hand/open_po come from the latest inventory row within
    7 days of asof. weeks_of_cover = on_hand / (mean daily ordered units over 28 days * 7).
    """
    asof = pd.Timestamp(asof)
    ids = set()
    for d in (traffic, inventory, sell_out):
        ids |= set(d["item_id"].unique())
    out = pd.DataFrame({"item_id": sorted(ids)}).set_index("item_id")

    if len(traffic):
        last = _window_mean(traffic, "glance_views", asof - pd.Timedelta(days=13), asof)
        prev = _window_mean(traffic, "glance_views", asof - pd.Timedelta(days=27), asof - pd.Timedelta(days=14))
        j = last.join(prev, lsuffix="_last", rsuffix="_prev", how="inner")
        ok = (j["count_last"] >= MIN_WINDOW_DAYS) & (j["count_prev"] >= MIN_WINDOW_DAYS) & (j["mean_prev"] >= 1)
        out["glance_mom_pct"] = (j["mean_last"] / j["mean_prev"] - 1).where(ok)
    else:
        out["glance_mom_pct"] = np.nan

    out["on_hand"] = np.nan
    out["open_po"] = np.nan
    out["oos_rate_28d"] = np.nan
    if len(inventory):
        w28 = inventory[(inventory["date"] > asof - pd.Timedelta(days=28)) & (inventory["date"] <= asof)]
        out["oos_rate_28d"] = w28.groupby("item_id")["procurable_product_out_of_stock_rate"].mean()
        recent = inventory[(inventory["date"] >= asof - pd.Timedelta(days=7)) & (inventory["date"] <= asof)]
        latest = recent.sort_values("date").groupby("item_id").last()
        out["on_hand"] = latest["sellable_on_hand_inventory_units"]
        out["open_po"] = latest["open_purchase_order_units"]

    out["sellout_daily_28d"] = np.nan
    if len(sell_out):
        s = _window_mean(sell_out, "ordered_units", asof - pd.Timedelta(days=27), asof)
        out["sellout_daily_28d"] = s["mean"].where(s["count"] >= MIN_WINDOW_DAYS)
    demand_week = out["sellout_daily_28d"] * 7
    out["weeks_of_cover"] = (out["on_hand"] / demand_week).where(demand_week > 0)
    return out.reset_index()


def amazon_demand_gap(
    future_cov: pd.DataFrame, past_cov: pd.DataFrame, month: pd.Timestamp, n_months: int = 3
) -> pd.DataFrame:
    """Amazon p70 for `month` relative to the mean of the last n_months of Amazon sell-out (same units)."""
    month = pd.Timestamp(month)
    p70 = future_cov[future_cov["date"] == month][["item_id", "amazon_forecast_p70"]]
    start = month - pd.DateOffset(months=n_months)
    hist = past_cov[(past_cov["date"] >= start) & (past_cov["date"] < month)]
    base = hist.groupby("item_id")["amazon_total_ordered_units"].mean().rename("sellout_monthly_3m")
    out = p70.merge(base, on="item_id", how="inner")
    out["p70_gap_pct"] = (out["amazon_forecast_p70"] / out["sellout_monthly_3m"] - 1).where(out["sellout_monthly_3m"] > 0)
    return out[["item_id", "amazon_forecast_p70", "sellout_monthly_3m", "p70_gap_pct"]]


def disaggregate_baseline(
    baseline_level: pd.DataFrame, mapping: pd.DataFrame, ts: pd.DataFrame, months: int = 12
) -> pd.DataFrame:
    """Spread the product-level baseline over items, seasonally.

    For each target month the share of an item is its sales in the same month one year earlier
    (plus/minus one month) divided by the product total over that window. Where the product had no
    sales in that window the trailing `months` months are used; failing that, the split is equal.
    NaN (masked) months are skipped.
    """
    last = ts["date"].max()
    annual = ts[ts["date"] > last - pd.DateOffset(months=months)].groupby("item_id")["target"].sum(min_count=1)
    base_map = mapping[["level_id", "item_id"]].copy()
    n = base_map.groupby("level_id")["item_id"].transform("size")
    annual_hist = base_map["item_id"].map(annual).fillna(0.0)
    annual_total = annual_hist.groupby(base_map["level_id"]).transform("sum")
    annual_share = np.where(annual_total > 0, annual_hist / annual_total.where(annual_total > 0, 1.0), 1.0 / n)

    pieces = []
    for month, lvl in baseline_level.groupby("date"):
        center = pd.Timestamp(month) - pd.DateOffset(years=1)
        win = ts[(ts["date"] >= center - pd.DateOffset(months=1)) & (ts["date"] <= center + pd.DateOffset(months=1))]
        seasonal = win.groupby("item_id")["target"].sum(min_count=1)
        m = base_map.copy()
        h = m["item_id"].map(seasonal).fillna(0.0)
        tot = h.groupby(m["level_id"]).transform("sum")
        m["share"] = np.where(tot > 0, h / tot.where(tot > 0, 1.0), annual_share)
        piece = m.merge(lvl, on="level_id", how="inner")
        piece["baseline_item"] = piece["baseline_proxy"] * piece["share"]
        pieces.append(piece[["item_id", "level_id", "date", "baseline_item"]])
    return pd.concat(pieces, ignore_index=True)


def ghost_candidates(
    ts: pd.DataFrame,
    promos: pd.DataFrame,
    items: pd.DataFrame,
    month: pd.Timestamp,
    spike_ratio: float = 2.5,
    min_units: float = 100.0,
) -> pd.DataFrame:
    """B2B items whose same month last year spiked, with no retailer campaign confirmed for `month`."""
    month = pd.Timestamp(month)
    ly_month = month - pd.DateOffset(years=1)
    ly = ts[ts["date"] == ly_month].set_index("item_id")["target"]
    last = ts["date"].max()
    trailing = ts[ts["date"] > last - pd.DateOffset(months=12)].groupby("item_id")["target"].median()
    month_end = month + pd.offsets.MonthEnd(0)
    camp = promos[(promos["promotion_type"] == "retailer_campaign") & (promos["date_from"] <= month_end) & (promos["date_to"] >= month)]
    has_campaign = set(camp["item_id"])
    b2b = set(items.loc[items["channel"] == "B2B wo Amazon", "item_id"])
    out = pd.DataFrame({"item_id": sorted(set(ly.index))})
    out["ly_units"] = out["item_id"].map(ly)
    out["typical_units"] = out["item_id"].map(trailing)
    out["ly_spike_ratio"] = (out["ly_units"] / out["typical_units"]).where(out["typical_units"] > 0)
    out["ghost_candidate"] = (
        out["item_id"].isin(b2b)
        & ~out["item_id"].isin(has_campaign)
        & (out["ly_units"] >= min_units)
        & (out["ly_spike_ratio"] >= spike_ratio)
    )
    return out


def orderbook_cover(phasing_items: pd.DataFrame, month: pd.Timestamp) -> pd.DataFrame:
    """Units already booked in the order book for `month` (material-availability month), per item."""
    m = phasing_items[phasing_items["month"] == pd.Timestamp(month)]
    return m[["item_id", "booked_qty"]].rename(columns={"booked_qty": "booked_m1"})


def promo_activity(promos: pd.DataFrame, future_cov: pd.DataFrame, months: Iterable[pd.Timestamp]) -> pd.DataFrame:
    """Items with an Amazon deal or a retailer campaign inside the planning window."""
    months = sorted(pd.Timestamp(m) for m in months)
    start, end = months[0], months[-1] + pd.offsets.MonthEnd(0)
    overlap = promos[(promos["date_from"] <= end) & (promos["date_to"] >= start)]
    deals = set(overlap.loc[overlap["promotion_type"] == "marketplace_deal", "item_id"])
    camps = set(overlap.loc[overlap["promotion_type"] == "retailer_campaign", "item_id"])
    fut = future_cov[future_cov["date"].isin(months) & (future_cov["amazon_deal_days"] > 0)]
    deals |= set(fut["item_id"])
    ids = sorted(deals | camps)
    return pd.DataFrame({
        "item_id": ids,
        "amazon_deal_active": [i in deals for i in ids],
        "b2b_campaign_active": [i in camps for i in ids],
    })


def npi_analogy(ts: pd.DataFrame, predecessors: pd.DataFrame, final: pd.DataFrame, months: int = 3) -> pd.DataFrame:
    """ML month-1 forecast vs the predecessor's recent run-rate (mean of its last `months` observed months)."""
    obs = ts.dropna(subset=["target"]).sort_values("date")
    rate = obs.groupby("item_id").tail(months).groupby("item_id")["target"].mean().rename("pred_rate")
    p = predecessors[["item_id", "predecessor_item_id"]].copy()
    p["pred_rate"] = p["predecessor_item_id"].map(rate)
    p = p.merge(final[["item_id", "final_m1"]], on="item_id", how="inner")
    p["ml_vs_pred_pct"] = (p["final_m1"] / p["pred_rate"] - 1).where(p["pred_rate"] > 0)
    return p[["item_id", "pred_rate", "ml_vs_pred_pct"]]


def derive_abc(items: pd.DataFrame, ts: pd.DataFrame, a_share: float = 0.80, b_share: float = 0.95) -> pd.DataFrame:
    """Fill missing ABC classes from 12-month sales value: cumulative share before the item < 80% A, < 95% B, else C."""
    last = ts["date"].max()
    vol = ts[ts["date"] > last - pd.DateOffset(months=12)].groupby("item_id")["target"].sum()
    d = items[["item_id", "abc_category", "sales_price"]].copy()
    d["value12"] = d["item_id"].map(vol).fillna(0.0) * d["sales_price"].fillna(0.0)
    d = d.sort_values("value12", ascending=False)
    total = d["value12"].sum()
    before = (d["value12"].cumsum() - d["value12"]) / total if total > 0 else pd.Series(1.0, index=d.index)
    derived = pd.Series(np.where(before < a_share, "A", np.where(before < b_share, "B", "C")), index=d.index)
    d["abc_derived"] = d["abc_category"].isna()
    d["abc_final"] = np.where(d["abc_derived"], derived, d["abc_category"])
    return d.sort_values("item_id")[["item_id", "abc_final", "abc_derived"]]
