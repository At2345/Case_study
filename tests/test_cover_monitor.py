"""Seasonal sell-through curve and the scheduled policy monitor (settlement and snapshot reminders)."""
from datetime import datetime, timedelta, timezone

import pytest

from app.cover.monitor import SNAPSHOT_DUE_DAYS, check_policies
from app.cover.policies import PolicyStore, assess, expected_share, new_policy, rescue_due, settle
from app.orchestration.scheduler import throttled
from tests.test_cover_policies import NOW, quote
from tests.test_workflows_api import FakeNotes

SEASONAL = [100, 100, 100, 100, 300, 400]  # peak at the end of the window (e.g. Q4)


def seasonal_policy():
    q = quote(demand={"mean_units": 1100, "monthly_means": SEASONAL})
    return new_policy(q, "danny", NOW, "DE", "rescue")


# ---------------------------------------------------------------- seasonal curve
def test_seasonal_curve_avoids_false_alarms():
    p = seasonal_policy()
    assert p["sales_curve"] == pytest.approx([100 / 1100] * 4 + [300 / 1100, 400 / 1100])
    day90 = NOW + timedelta(days=90)
    a = assess(p, 300, day90)  # 3 of 6 months gone, only 300 of 1100 expected so far
    assert a["curve"] == "seasonal" and a["expected_sold_to_date"] == 300.0 and a["gap"] == 0
    assert "within tolerance" in rescue_due(p, a, day90)

    linear = new_policy(quote(demand={"mean_units": 1100}), "danny", NOW, "DE", "rescue")
    b = assess(linear, 300, day90)
    assert b["curve"] == "linear" and b["gap"] == pytest.approx(-0.4545, abs=1e-3)
    assert rescue_due(linear, b, day90) is None  # the straight line would have fired a rescue


def test_expected_share_interpolates_and_caps():
    p = seasonal_policy()
    assert expected_share(p, 0) == 0
    assert expected_share(p, 15) == pytest.approx(50 / 1100)  # half of month 1
    assert expected_share(p, 135) == pytest.approx((400 + 150) / 1100)  # half way into the 300 month
    assert expected_share(p, 180) == pytest.approx(1.0) and expected_share(p, 400) == pytest.approx(1.0)
    flat = new_policy(quote(demand={"mean_units": 1000, "monthly_means": [0, 0]}), "danny", NOW)
    assert flat["sales_curve"] is None and expected_share(flat, 90) == 0.5


# ---------------------------------------------------------------- monitor
@pytest.fixture
def store(tmp_path):
    return PolicyStore(tmp_path)


def test_snapshot_reminders(store):
    notes = FakeNotes()
    p = store.save("acme", new_policy(quote(), "danny", NOW, "DE"))
    assert check_policies(store, notes, NOW + timedelta(days=10)) == {"settlement_due": 0, "snapshot_due": 0}

    first = NOW + timedelta(days=SNAPSHOT_DUE_DAYS + 1)
    assert check_policies(store, notes, first)["snapshot_due"] == 1
    assert notes.rows[-1]["roles"] == ["planner", "admin"] and notes.rows[-1]["kind"] == "cover"
    assert "No sales snapshot for 15 days" in notes.rows[-1]["body"] and notes.rows[-1]["run_id"] == p["id"]
    assert check_policies(store, notes, first + timedelta(days=1))["snapshot_due"] == 0  # no repeat yet
    assert check_policies(store, notes, first + timedelta(days=SNAPSHOT_DUE_DAYS))["snapshot_due"] == 1

    fresh = store.get("acme", p["id"])
    fresh["observations"].append({"at": (first + timedelta(days=20)).isoformat()})
    store.save("acme", fresh)
    assert check_policies(store, notes, first + timedelta(days=25))["snapshot_due"] == 0  # recent snapshot


def test_settlement_notice_once_and_not_after_settling(store):
    notes = FakeNotes()
    p = store.save("acme", new_policy(quote(), "danny", NOW, "DE"))
    store.save("globex", new_policy(quote(id="q2", po_id="PO-2"), "ann", NOW))
    ended = NOW + timedelta(days=181)
    assert check_policies(store, notes, ended) == {"settlement_due": 2, "snapshot_due": 0}
    assert notes.rows[0]["roles"] == ["admin"] and "Settle cover for" in notes.rows[0]["title"]
    assert check_policies(store, notes, ended + timedelta(days=1)) == {"settlement_due": 0, "snapshot_due": 0}

    done = store.get("acme", p["id"])
    settle(done, 100, "root", ended)
    store.save("acme", done)
    assert check_policies(store, notes, ended + timedelta(days=60)) == {"settlement_due": 0, "snapshot_due": 0}


def test_monitor_survives_a_broken_policy(store):
    notes = FakeNotes()
    store.save("acme", new_policy(quote(), "danny", NOW))
    broken = new_policy(quote(id="q2", po_id="PO-2"), "danny", NOW)
    broken["ends_at"] = "not a date"
    store.save("acme", broken)
    counts = check_policies(store, notes, NOW + timedelta(days=181))
    assert counts["settlement_due"] == 1  # the good policy is still handled
    assert check_policies(store, None, NOW) == {"settlement_due": 0, "snapshot_due": 0}


def test_throttled_hook():
    calls, clock = [], {"t": 0.0}
    hook = throttled(calls.append, seconds=600, clock=lambda: clock["t"])
    hook("a")
    clock["t"] = 300
    hook("b")
    clock["t"] = 601
    hook("c")
    assert calls == ["a", "c"]


def test_forecast_quote_carries_season_into_policy(tmp_path):
    from app.cover.quote import demand_from_forecast, quote as price
    plan = [{"month": f"2026-{m:02d}", "consensus": v} for m, v in zip(range(3, 9), SEASONAL)]
    demand = demand_from_forecast(plan, [20.0] * 6, 6)
    q = {"id": "q9", "po_id": "PO-9", "sku": "S", "unit_landed_cost_eur": 8.0,
         "valid_until": (NOW + timedelta(days=7)).isoformat(),
         **price(1000, 8.0, demand)}
    p = new_policy(q, "danny", NOW)
    assert p["sales_curve"] == pytest.approx([v / 1100 for v in SEASONAL])
