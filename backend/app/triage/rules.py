"""Triage rules: risk flags, tiers, states and deterministic narratives.

Pure functions over a one-row-per-item feature frame, so they are testable without any data files.
Required columns are listed in REQUIRED_COLUMNS.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

REQUIRED_COLUMNS = [
    "item_id", "channel", "abc_category", "in_scope", "sales_price",
    "baseline_h", "final_h", "baseline_m1", "final_m1", "baseline_6m", "final_6m",
    "phase", "months_since_launch", "months_to_eol",
    "glance_mom_pct", "p70_gap_pct", "oos_rate_28d", "weeks_of_cover", "on_hand", "open_po",
    "amazon_deal_active", "b2b_campaign_active", "ghost_candidate", "ml_vs_pred_pct", "booked_m1",
]

# Order matters: the first matching flag decides risk_type.
RISK_PRIORITY = [
    ("CRITICAL_AMAZON_BUYBOX_ALERT", "CRITICAL_AMAZON_BUYBOX_ALERT"),
    ("OOS_SURGE", "OOS_RISK"),
    ("ORDER_BOOK_EXCEEDS_PLAN", "OOS_RISK"),
    ("TERMINAL_OBSOLESCENCE_RISK", "TERMINAL_OBSOLESCENCE_RISK"),
    ("GHOST_PROMO", "OVERSTOCK_RISK"),
    ("AMAZON_OVERSTOCK", "OVERSTOCK_RISK"),
    ("NPI_ANALOGY_GAP", "NPI_ANALOGY_GAP"),
]
REVIEW_FLAGS = [
    "CRITICAL_AMAZON_BUYBOX_ALERT", "OOS_SURGE", "TERMINAL_OBSOLESCENCE_RISK", "GHOST_PROMO", "AMAZON_OVERSTOCK",
]


@dataclass(frozen=True)
class TriageConfig:
    var_threshold_gbp: float = 5000.0     # value-at-risk that always triggers review (brief, F-SOP-02)
    var_low_gbp: float = 2000.0           # minimum value for a flagged / promo item to be reviewed (sweep: queue 67 vs 204 at 500)
    var_months: int = 1                   # forecast months summed into the value-at-risk (open question Q12)
    suppress_pct: float = 0.05            # below this relative change the item is not reviewed (unless big)
    glance_mom_threshold: float = 0.20    # F-AMZ-01
    p70_gap_threshold: float = 0.25       # F-AMZ-01
    oos_rate_threshold: float = 0.05      # proxy for F-AMZ-03 (vendor confirmation rate is unusable in the data)
    overstock_cover_weeks: float = 8.0
    glance_decline_threshold: float = -0.10
    ghost_baseline_ratio: float = 1.2     # baseline must repeat the spike: baseline >= ratio * final
    npi_planner_months: float = 3.0       # first 90 days
    eol_planner_months: float = 3.0       # months_to_eol <= 3
    npi_gap_threshold: float = 0.5        # F-SOP-04 analogue
    terminal_ratio: float = 1.5
    terminal_min_units: float = 50.0


def evaluate_flags(df: pd.DataFrame, cfg: TriageConfig = TriageConfig()) -> pd.DataFrame:
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"missing feature columns: {missing}")
    out = df.copy()
    amazon = out["channel"] == "amazon"
    b2b = out["channel"] == "B2B wo Amazon"

    out["f_oos_surge"] = amazon & (
        (out["glance_mom_pct"] > cfg.glance_mom_threshold) | (out["p70_gap_pct"] > cfg.p70_gap_threshold)
    )
    stock = out["on_hand"].fillna(0) + out["open_po"].fillna(0)
    out["f_buybox"] = (
        amazon & (out["abc_category"] == "A") & (out["oos_rate_28d"] >= cfg.oos_rate_threshold) & (out["final_m1"] > stock)
    )
    out["f_amz_overstock"] = (
        amazon & (out["weeks_of_cover"] > cfg.overstock_cover_weeks) & (out["glance_mom_pct"] < cfg.glance_decline_threshold)
    )
    out["f_ghost"] = b2b & out["ghost_candidate"].fillna(False).astype(bool) & (
        out["baseline_m1"] >= cfg.ghost_baseline_ratio * out["final_m1"]
    )
    eol_phase = out["phase"].isin(["POST_EOL", "EOL_APPROACHING"])
    out["f_terminal"] = (
        eol_phase
        & (out["baseline_6m"] >= cfg.terminal_ratio * out["final_6m"])
        & ((out["baseline_6m"] - out["final_6m"]) >= cfg.terminal_min_units)
    )
    out["f_npi_gap"] = (out["months_since_launch"].between(0, 6)) & (out["ml_vs_pred_pct"].abs() > cfg.npi_gap_threshold)
    out["f_promo"] = out["amazon_deal_active"].fillna(False).astype(bool) | out["b2b_campaign_active"].fillna(False).astype(bool)
    # orders already booked for the month exceed the plan: a concrete under-forecast, valued at the unplanned units
    out["f_orderbook"] = out["in_scope"].astype(bool) & (out["booked_m1"] > out["final_m1"])
    out["book_gap_gbp"] = ((out["booked_m1"] - out["final_m1"]).clip(lower=0) * out["sales_price"]).where(out["f_orderbook"], 0.0)
    return out


def _flag_names(row) -> str:
    names = []
    if row["f_buybox"]:
        names.append("CRITICAL_AMAZON_BUYBOX_ALERT")
    if row["f_oos_surge"]:
        names.append("OOS_SURGE")
    if row["f_orderbook"]:
        names.append("ORDER_BOOK_EXCEEDS_PLAN")
    if row["f_terminal"]:
        names.append("TERMINAL_OBSOLESCENCE_RISK")
    if row["f_ghost"]:
        names.append("GHOST_PROMO")
    if row["f_amz_overstock"]:
        names.append("AMAZON_OVERSTOCK")
    if row["f_npi_gap"]:
        names.append("NPI_ANALOGY_GAP")
    if row["f_promo"]:
        names.append("PROMO_EVENT")
    return ",".join(names)


def _narrative(row, cfg: TriageConfig = TriageConfig()) -> str:
    parts = [
        f"{row['channel']} item {int(row['item_id'])}: ML plan {row['final_h']:,.0f} units vs baseline "
        f"{row['baseline_h']:,.0f} ({row['pct_delta'] * 100:+.0f}%), value impact \u00a3{row['value_delta_gbp']:,.0f}."
    ]
    flags = row["risk_flags"].split(",") if row["risk_flags"] else []
    if "OOS_SURGE" in flags:
        bits = []
        if row["glance_mom_pct"] > cfg.glance_mom_threshold:
            bits.append(f"14-day glance views {row['glance_mom_pct'] * 100:+.0f}%")
        if row["p70_gap_pct"] > cfg.p70_gap_threshold:
            bits.append(f"Amazon p70 {row['p70_gap_pct'] * 100:+.0f}% vs recent sell-out")
        parts.append("Amazon demand signals rising (" + ", ".join(bits) + "): risk of PO down-confirmation chargebacks (3-9% of COGS).")
        if row["delta_h"] < 0:
            parts.append("The plan is below the baseline despite these signals: verify before accepting.")
    if "ORDER_BOOK_EXCEEDS_PLAN" in flags:
        parts.append(
            f"The order book already holds {row['booked_m1']:,.0f} units for this month against a plan of "
            f"{row['final_m1']:,.0f}: unplanned demand of \u00a3{row['book_gap_gbp']:,.0f} at sales price."
        )
    if "CRITICAL_AMAZON_BUYBOX_ALERT" in flags:
        parts.append(
            f"Forecast {row['final_m1']:,.0f} units exceeds Amazon stock plus open POs "
            f"({np.nansum([row['on_hand'], row['open_po']]):,.0f}) with {row['oos_rate_28d'] * 100:.0f}% out-of-stock rate: Buy Box risk."
        )
    if "AMAZON_OVERSTOCK" in flags:
        parts.append(f"Amazon holds {row['weeks_of_cover']:.0f} weeks of cover while traffic is falling: replenishment POs likely to pause.")
    if "GHOST_PROMO" in flags:
        parts.append("Baseline repeats last year's promotional spike but no retailer campaign is confirmed for this month.")
    if "TERMINAL_OBSOLESCENCE_RISK" in flags:
        parts.append(
            f"Item is in run-out: baseline {row['baseline_6m']:,.0f} vs run-out forecast {row['final_6m']:,.0f} units over 6 months."
        )
    if "NPI_ANALOGY_GAP" in flags:
        parts.append(f"Forecast differs from predecessor run-rate by {row['ml_vs_pred_pct'] * 100:+.0f}%.")
    if "PROMO_EVENT" in flags:
        parts.append("A confirmed promotional event is active in the planning window.")
    return " ".join(parts)


def assign_tiers(df: pd.DataFrame, cfg: TriageConfig = TriageConfig()) -> pd.DataFrame:
    out = df.copy()
    out["delta_h"] = out["final_h"] - out["baseline_h"]
    out["value_delta_gbp"] = out["delta_h"] * out["sales_price"]
    out["var_gbp"] = np.maximum(out["value_delta_gbp"].abs(), out["book_gap_gbp"])
    out["pct_delta"] = out["delta_h"] / out["baseline_h"].clip(lower=1.0)

    any_risk = out[["f_buybox", "f_oos_surge", "f_terminal", "f_ghost", "f_amz_overstock", "f_orderbook"]].any(axis=1)
    big = out["var_gbp"] >= cfg.var_threshold_gbp
    mid = out["var_gbp"] >= cfg.var_low_gbp
    suppressed = (out["pct_delta"].abs() < cfg.suppress_pct) & ~big & ~out["f_orderbook"]

    npi_soon = out["months_since_launch"].between(0, cfg.npi_planner_months)
    eol_soon = out["months_to_eol"].between(0, cfg.eol_planner_months)
    planner_led = out["in_scope"] & (npi_soon | eol_soon | out["b2b_campaign_active"].fillna(False).astype(bool))
    review = out["in_scope"] & ~planner_led & (big | ((any_risk | out["f_promo"]) & mid)) & ~suppressed

    out["state"] = np.select(
        [~out["in_scope"], planner_led, review],
        ["INACTIVE", "PLANNER_LED", "EXCEPTION_REVIEW"],
        default="AUTO_APPROVED",
    )
    out["tier"] = out["state"].map({
        "AUTO_APPROVED": "Touchless Autopilot", "EXCEPTION_REVIEW": "Collaborative Exception",
        "PLANNER_LED": "Planner-Led", "INACTIVE": "Inactive",
    })
    out["requires_review"] = out["state"] == "EXCEPTION_REVIEW"

    out["risk_flags"] = out.apply(_flag_names, axis=1)
    default_risk = np.where(out["value_delta_gbp"] >= 0, "OOS_RISK", "OVERSTOCK_RISK")
    risk = pd.Series(default_risk, index=out.index, dtype=object)
    for flag, risk_type in reversed(RISK_PRIORITY):
        has = out["risk_flags"].str.contains(flag, regex=False)
        risk[has] = risk_type
    out["risk_type"] = risk
    out["narrative"] = out.apply(lambda r: _narrative(r, cfg), axis=1)
    return out
