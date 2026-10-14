import numpy as np
import pandas as pd
import pytest

from app.triage.rules import TriageConfig, evaluate_flags, assign_tiers

CFG = TriageConfig(var_low_gbp=500.0)


def test_default_config_matches_the_documented_operating_point():
    d = TriageConfig()
    assert d.var_threshold_gbp == 5000.0 and d.var_low_gbp == 2000.0 and d.var_months == 1


def _row(**kw):
    base = dict(
        item_id=1, product_id="P1", channel="B2B wo Amazon", abc_category="B", in_scope=True, sales_price=10.0,
        baseline_h=100.0, final_h=100.0, baseline_m1=100.0, final_m1=100.0, baseline_6m=600.0, final_6m=600.0,
        phase="MATURE", months_since_launch=40.0, months_to_eol=np.nan,
        glance_mom_pct=np.nan, p70_gap_pct=np.nan, oos_rate_28d=np.nan, weeks_of_cover=np.nan,
        on_hand=np.nan, open_po=np.nan, amazon_deal_active=False, b2b_campaign_active=False,
        ghost_candidate=False, ml_vs_pred_pct=np.nan, source="ml", booked_m1=np.nan,
    )
    base.update(kw)
    return base


def run(*rows, cfg=CFG):
    df = pd.DataFrame(list(rows))
    return assign_tiers(evaluate_flags(df, cfg), cfg)


def test_quiet_item_is_auto_approved():
    out = run(_row())
    assert out["state"].tolist() == ["AUTO_APPROVED"]
    assert not out["requires_review"].iloc[0]


def test_out_of_scope_is_inactive():
    assert run(_row(in_scope=False))["state"].iloc[0] == "INACTIVE"


def test_large_value_delta_goes_to_exception_review_with_sign_based_risk():
    up = run(_row(final_h=100 + 600, sales_price=10.0)).iloc[0]  # +6000 GBP
    down = run(_row(final_h=100 - 90, baseline_h=700.0, sales_price=10.0)).iloc[0]  # -6100 GBP
    assert up["state"] == "EXCEPTION_REVIEW" and up["risk_type"] == "OOS_RISK"
    assert up["value_delta_gbp"] == pytest.approx(6000.0)
    assert down["state"] == "EXCEPTION_REVIEW" and down["risk_type"] == "OVERSTOCK_RISK"


def test_amazon_traffic_surge_flags_oos_risk_only_when_value_is_material():
    small = run(_row(channel="amazon", glance_mom_pct=0.34, final_h=105.0)).iloc[0]  # +50 GBP
    big = run(_row(channel="amazon", glance_mom_pct=0.34, final_h=140.0)).iloc[0]  # +400 -> below low threshold
    material = run(_row(channel="amazon", glance_mom_pct=0.34, final_h=180.0)).iloc[0]  # +800 GBP
    assert small["state"] == "AUTO_APPROVED" and big["state"] == "AUTO_APPROVED"
    assert material["state"] == "EXCEPTION_REVIEW" and material["risk_type"] == "OOS_RISK"
    assert "OOS_SURGE" in material["risk_flags"]


def test_p70_gap_triggers_the_same_flag_and_surge_is_amazon_only():
    p70 = run(_row(channel="amazon", p70_gap_pct=0.30, final_h=180.0)).iloc[0]
    other = run(_row(channel="D2C", glance_mom_pct=0.9, final_h=180.0)).iloc[0]
    assert "OOS_SURGE" in p70["risk_flags"]
    assert "OOS_SURGE" not in other["risk_flags"]


def test_buybox_alert_needs_a_item_oos_and_demand_above_stock():
    hit = run(_row(channel="amazon", abc_category="A", oos_rate_28d=0.08, final_m1=500.0, on_hand=100.0,
                   open_po=50.0, final_h=500.0)).iloc[0]
    covered = run(_row(channel="amazon", abc_category="A", oos_rate_28d=0.08, final_m1=100.0, on_hand=100.0,
                       open_po=50.0)).iloc[0]
    b_item = run(_row(channel="amazon", abc_category="B", oos_rate_28d=0.08, final_m1=500.0, on_hand=100.0)).iloc[0]
    assert hit["risk_type"] == "CRITICAL_AMAZON_BUYBOX_ALERT"
    assert "CRITICAL_AMAZON_BUYBOX_ALERT" not in covered["risk_flags"]
    assert "CRITICAL_AMAZON_BUYBOX_ALERT" not in b_item["risk_flags"]


def test_amazon_overstock_needs_high_cover_and_falling_traffic():
    hit = run(_row(channel="amazon", weeks_of_cover=12.0, glance_mom_pct=-0.18, baseline_h=250.0, final_h=60.0)).iloc[0]
    flat = run(_row(channel="amazon", weeks_of_cover=12.0, glance_mom_pct=0.0, baseline_h=250.0, final_h=60.0)).iloc[0]
    assert hit["risk_type"] == "OVERSTOCK_RISK" and "AMAZON_OVERSTOCK" in hit["risk_flags"]
    assert "AMAZON_OVERSTOCK" not in flat["risk_flags"]


def test_ghost_promo_requires_candidate_and_baseline_repeating_the_spike():
    hit = run(_row(ghost_candidate=True, baseline_m1=850.0, final_m1=340.0, baseline_h=850.0, final_h=340.0,
                   sales_price=24.5)).iloc[0]
    kept = run(_row(ghost_candidate=True, baseline_m1=100.0, final_m1=100.0)).iloc[0]
    assert "GHOST_PROMO" in hit["risk_flags"] and hit["risk_type"] == "OVERSTOCK_RISK"
    assert hit["state"] == "EXCEPTION_REVIEW"
    assert "GHOST_PROMO" not in kept["risk_flags"]


def test_terminal_obsolescence_when_baseline_ignores_the_run_out():
    hit = run(_row(phase="POST_EOL", months_to_eol=-5.0, baseline_6m=600.0, final_6m=100.0)).iloc[0]
    fine = run(_row(phase="POST_EOL", months_to_eol=-5.0, baseline_6m=120.0, final_6m=100.0)).iloc[0]
    assert "TERMINAL_OBSOLESCENCE_RISK" in hit["risk_flags"]
    assert "TERMINAL_OBSOLESCENCE_RISK" not in fine["risk_flags"]


def test_planner_led_tier_for_npi_eol_soon_and_b2b_campaign():
    npi = run(_row(phase="NPI", months_since_launch=2.0)).iloc[0]
    eol = run(_row(phase="EOL_APPROACHING", months_to_eol=2.0)).iloc[0]
    camp = run(_row(b2b_campaign_active=True)).iloc[0]
    old_npi = run(_row(phase="NPI", months_since_launch=5.0)).iloc[0]
    for r in (npi, eol, camp):
        assert r["state"] == "PLANNER_LED" and not r["requires_review"]
    assert old_npi["state"] == "AUTO_APPROVED"


def test_npi_analogy_gap_flag():
    hit = run(_row(phase="NPI", months_since_launch=5.0, ml_vs_pred_pct=0.8, final_h=300.0)).iloc[0]
    assert "NPI_ANALOGY_GAP" in hit["risk_flags"]


def test_small_percentage_tweaks_are_suppressed_below_the_big_threshold():
    flagged_but_tiny = run(_row(channel="amazon", glance_mom_pct=0.5, baseline_h=100000.0, final_h=100050.0,
                                sales_price=2.0)).iloc[0]  # +100 GBP, 0.05%
    assert flagged_but_tiny["state"] == "AUTO_APPROVED"
    huge_pct_small = run(_row(baseline_h=100000.0, final_h=100000.0 + 4000, sales_price=2.0)).iloc[0]  # +8000 GBP, 4%
    assert huge_pct_small["state"] == "EXCEPTION_REVIEW"  # big threshold wins over the 5% suppression


def test_amazon_deal_with_material_value_is_reviewed():
    out = run(_row(channel="amazon", amazon_deal_active=True, final_h=180.0)).iloc[0]
    assert out["state"] == "EXCEPTION_REVIEW" and "PROMO_EVENT" in out["risk_flags"]


def test_order_book_above_plan_is_flagged_and_valued_at_the_unplanned_units():
    hit = run(_row(booked_m1=500.0, final_m1=100.0, baseline_h=100.0, final_h=100.0)).iloc[0]  # 400 units x 10 GBP
    assert "ORDER_BOOK_EXCEEDS_PLAN" in hit["risk_flags"] and hit["risk_type"] == "OOS_RISK"
    assert hit["book_gap_gbp"] == pytest.approx(4000.0) and hit["var_gbp"] == pytest.approx(4000.0)
    assert hit["state"] == "EXCEPTION_REVIEW"  # no baseline gap, no percentage change: still reviewed
    assert "order book already holds 500" in hit["narrative"]


def test_order_book_flag_needs_bookings_above_plan_and_enough_value():
    covered = run(_row(booked_m1=80.0, final_m1=100.0)).iloc[0]
    unknown = run(_row(booked_m1=np.nan)).iloc[0]
    small = run(_row(booked_m1=105.0, final_m1=100.0)).iloc[0]  # 50 GBP, below the review floor
    out_of_scope = run(_row(booked_m1=500.0, final_m1=100.0, in_scope=False)).iloc[0]
    for r in (covered, unknown, out_of_scope):
        assert "ORDER_BOOK_EXCEEDS_PLAN" not in r["risk_flags"]
    assert "ORDER_BOOK_EXCEEDS_PLAN" in small["risk_flags"] and small["state"] == "AUTO_APPROVED"


def test_a_large_order_book_gap_always_reaches_the_queue():
    big = run(_row(booked_m1=1000.0, final_m1=100.0, sales_price=10.0)).iloc[0]  # 9,000 GBP
    assert big["state"] == "EXCEPTION_REVIEW" and big["var_gbp"] >= 5000


def test_narrative_cites_only_signals_above_threshold_and_warns_on_contradiction():
    out = run(_row(channel="amazon", glance_mom_pct=0.27, p70_gap_pct=-0.34, baseline_h=1460.0, final_h=60.0)).iloc[0]
    text = out["narrative"]
    assert "glance views +27%" in text
    assert "p70" not in text
    assert "below the baseline despite these signals" in text


def test_narrative_contains_the_key_numbers():
    out = run(_row(final_h=700.0, baseline_h=100.0)).iloc[0]
    assert "700" in out["narrative"] and "100" in out["narrative"] and "6,000" in out["narrative"]
