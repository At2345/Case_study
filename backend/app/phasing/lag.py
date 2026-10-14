"""Order-book phasing (F-B2B-02).

The `target` series books an order line in the month of `material_availability_at` (demand view).
Deliveries and revenue follow `delivered_at`, often a month later. This module measures that lag,
turns a demand plan into an expected delivery plan, and exposes what the order book already knows
about the coming months (bookings, shifted bookings, deliveries carried in from earlier months).
"""
from __future__ import annotations

from typing import Dict, Tuple

import numpy as np
import pandas as pd

EOM_DAYS = 5  # "within five days of month-end" (brief)
QTY = "ordered_quantity_plan_relevant"
OPTIONAL = ["cancellation_date"]
NEEDED = ["order_id", "item_id", "channel", "customer_id", "ordered_at", "material_availability_at",
          "delivered_at", QTY, "cancellation_status"]


def prepare_lines(ob: pd.DataFrame) -> pd.DataFrame:
    """Order lines with a positive plan quantity and a material date, plus month keys, lag and end-of-month flag."""
    d = ob[NEEDED + [c for c in OPTIONAL if c in ob.columns]].copy()
    d = d[(d[QTY] > 0) & d["material_availability_at"].notna()]
    mat = d["material_availability_at"]
    d["mm"] = mat.dt.to_period("M").dt.to_timestamp()
    d["dm"] = d["delivered_at"].dt.to_period("M").dt.to_timestamp()
    d["eom"] = (mat.dt.days_in_month - mat.dt.day) < EOM_DAYS
    d["cancelled"] = d["cancellation_status"] == "cancelled_completely"
    d["lag"] = (d["dm"].dt.year - d["mm"].dt.year) * 12 + (d["dm"].dt.month - d["mm"].dt.month)
    return d.reset_index(drop=True)


def _usable(lines: pd.DataFrame, mature_before: pd.Timestamp) -> pd.DataFrame:
    """Delivered, not cancelled, material month old enough that the order book is no longer censored."""
    return lines[(lines["mm"] <= mature_before) & lines["dm"].notna() & ~lines["cancelled"] & (lines["lag"] >= 0)]


def _shares(g: pd.DataFrame) -> pd.Series:
    q = g.groupby(g["lag"].clip(upper=2))[QTY].sum()
    q = q.reindex([0, 1, 2], fill_value=0.0)
    total = q.sum()
    return pd.Series({"p0": q[0] / total, "p1": q[1] / total, "p2plus": q[2] / total, "qty": total, "lines": len(g)})


def lag_profile(lines: pd.DataFrame, mature_before: pd.Timestamp) -> pd.DataFrame:
    """P(delivery lag in months = 0 / 1 / 2+) by channel and end-of-month flag, weighted by quantity."""
    u = _usable(lines, mature_before)
    return u.groupby(["channel", "eom"]).apply(_shares).reset_index()


def overall_profile(lines: pd.DataFrame, mature_before: pd.Timestamp) -> pd.DataFrame:
    """P(lag) by channel, used to phase a demand plan whose order dates are not known yet."""
    u = _usable(lines, mature_before)
    return u.groupby("channel").apply(_shares).reset_index()


def expected_deliveries(demand: pd.Series, probs: Tuple[float, float, float], carry_in: pd.Series = None) -> pd.Series:
    """Expected units delivered per month: carry-in plus demand spread over lag 0, 1 and 2+ (2+ treated as lag 2).

    `demand` and `carry_in` are indexed by consecutive month starts. Demand that lands after the horizon is dropped.
    """
    p = np.asarray(probs, dtype=float)
    d = demand.to_numpy(dtype=float)
    out = np.zeros(len(d))
    for k, pk in enumerate(p):
        out[k:] += d[: len(d) - k] * pk
    if carry_in is not None:
        out += carry_in.reindex(demand.index).fillna(0.0).to_numpy()
    return pd.Series(out, index=demand.index)


def backtest_profile(
    lines: pd.DataFrame, channel: str, train_end: pd.Timestamp, test_start: pd.Timestamp, test_end: pd.Timestamp
) -> Dict[str, float]:
    """Channel-month delivered units: naive (delivery month = demand month) vs profile fitted up to train_end.

    Demand per month is the actual material-month quantity, so this isolates the phasing effect from
    forecast error. WAPE is measured against the quantity actually delivered in each month.
    """
    ch = lines[(lines["channel"] == channel) & ~lines["cancelled"]]
    prof = overall_profile(lines[lines["channel"] == channel], train_end).iloc[0]
    probs = (prof["p0"], prof["p1"], prof["p2plus"])
    months = pd.date_range(test_start - pd.DateOffset(months=2), test_end, freq="MS")
    demand = ch.groupby("mm")[QTY].sum().reindex(months, fill_value=0.0)
    delivered = ch[ch["dm"].notna()].groupby("dm")[QTY].sum().reindex(months, fill_value=0.0)
    expected = expected_deliveries(demand, probs)
    test = months[months >= test_start]
    actual = delivered.loc[test].to_numpy()
    naive = demand.loc[test].to_numpy()
    model = expected.loc[test].to_numpy()
    denom = actual.sum()
    return {
        "months": int(len(test)), "units": float(denom),
        "wape_naive": float(np.abs(naive - actual).sum() / denom), "wape_profile": float(np.abs(model - actual).sum() / denom),
        "p0": float(probs[0]), "p1": float(probs[1]), "p2plus": float(probs[2]),
    }


def backtest_profile_items(
    lines: pd.DataFrame, channel: str, train_end: pd.Timestamp, test_start: pd.Timestamp, test_end: pd.Timestamp
) -> Dict[str, float]:
    """Same comparison as `backtest_profile` but at item-month grain, where month-boundary shifts do not net out."""
    ch = lines[(lines["channel"] == channel) & ~lines["cancelled"]]
    prof = overall_profile(lines[lines["channel"] == channel], train_end).iloc[0]
    p = (float(prof["p0"]), float(prof["p1"]), float(prof["p2plus"]))
    months = pd.date_range(test_start - pd.DateOffset(months=2), test_end, freq="MS")
    demand = ch.groupby(["item_id", "mm"])[QTY].sum().unstack(fill_value=0.0).reindex(columns=months, fill_value=0.0)
    d = ch[ch["dm"].notna()].groupby(["item_id", "dm"])[QTY].sum().unstack(fill_value=0.0).reindex(columns=months, fill_value=0.0)
    d = d.reindex(demand.index, fill_value=0.0)
    expected = p[0] * demand + p[1] * demand.shift(1, axis=1).fillna(0.0) + p[2] * demand.shift(2, axis=1).fillna(0.0)
    test = [m for m in months if m >= test_start]
    actual, naive, model = d[test].to_numpy(), demand[test].to_numpy(), expected[test].to_numpy()
    denom = actual.sum()
    return {
        "item_months": int((actual + naive > 0).sum()), "units": float(denom),
        "wape_naive": float(np.abs(naive - actual).sum() / denom), "wape_profile": float(np.abs(model - actual).sum() / denom),
    }


def booked_orders(lines: pd.DataFrame, first_month: pd.Timestamp) -> pd.DataFrame:
    """What the order book already holds for the planning window, per item and month.

    booked_qty: lines whose material month is `month`. booked_shifted_qty: the part of those delivered in a
    later month (shifted out). carry_in_qty: lines from material months before `first_month` that are delivered
    in `month`; this is work from the last cycle that the new demand plan does not contain. shifted_in_qty:
    lines booked inside the window for an earlier month that land in `month`.
    """
    live = lines[~lines["cancelled"]]
    booked = live[live["mm"] >= first_month]
    a = booked.groupby(["item_id", "mm"])[QTY].sum().rename("booked_qty")
    b = booked[booked["lag"] > 0].groupby(["item_id", "mm"])[QTY].sum().rename("booked_shifted_qty")
    landing = live[live["dm"].notna() & (live["dm"] >= first_month) & (live["lag"] > 0)]
    prior = landing[landing["mm"] < first_month].groupby(["item_id", "dm"])[QTY].sum().rename("carry_in_qty")
    inside = landing[landing["mm"] >= first_month].groupby(["item_id", "dm"])[QTY].sum().rename("shifted_in_qty")
    for s in (prior, inside):
        s.index = s.index.set_names(["item_id", "mm"])
    out = pd.concat([a, b, prior, inside], axis=1).fillna(0.0).reset_index().rename(columns={"mm": "month"})
    return out.sort_values(["item_id", "month"]).reset_index(drop=True)
