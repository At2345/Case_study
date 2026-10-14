import os
import uuid
from datetime import datetime, timezone

import pandas as pd
import pytest

from app.orchestration.scheduler import Scheduler, next_run, validate
from app.scenarios.engine import apply_edits, compare
from tests.test_api import make_service

UTC = timezone.utc


# ---------------------------------------------------------------- schedule maths (UK time, BST aware)
def test_monthly_schedule_fires_on_the_2nd_at_06_uk_time():
    spec = {"frequency": "monthly", "day": 2, "hour": 6, "minute": 0}
    assert next_run(spec, datetime(2026, 1, 15, tzinfo=UTC)) == datetime(2026, 2, 2, 6, 0, tzinfo=UTC)  # GMT
    assert next_run(spec, datetime(2026, 6, 1, tzinfo=UTC)) == datetime(2026, 6, 2, 5, 0, tzinfo=UTC)  # BST
    assert next_run(spec, datetime(2026, 12, 2, 7, 0, tzinfo=UTC)) == datetime(2027, 1, 2, 6, 0, tzinfo=UTC)


def test_daily_schedule_and_validation():
    spec = {"frequency": "daily", "hour": 5, "minute": 0}
    assert next_run(spec, datetime(2026, 7, 10, 3, 0, tzinfo=UTC)) == datetime(2026, 7, 10, 4, 0, tzinfo=UTC)
    assert next_run(spec, datetime(2026, 7, 10, 4, 0, tzinfo=UTC)) == datetime(2026, 7, 11, 4, 0, tzinfo=UTC)
    with pytest.raises(ValueError):
        validate({"frequency": "monthly", "day": 31, "hour": 6, "minute": 0})
    with pytest.raises(ValueError):
        validate({"frequency": "hourly", "hour": 6, "minute": 0})


class FakeSchedules:
    def __init__(self, due_at):
        self.rows = [{"job": "monthly_cycle", "frequency": "monthly", "day": 2, "hour": 6, "minute": 0, "next_run_at": due_at}]
        self.log = []

    def due(self, now):
        return [r for r in self.rows if r["next_run_at"] <= now.isoformat()]

    def claim(self, job, expected, new):
        r = self.rows[0]
        if r["next_run_at"] != expected:
            return False
        r["next_run_at"] = new
        return True

    def record(self, job, when, outcome):
        self.log.append(outcome)


class FakeJobs:
    def __init__(self, busy=False):
        self.started, self._busy = [], busy

    def busy(self):
        return self._busy

    def start(self, kind, user):
        self.started.append((kind, user))


def test_scheduler_fires_due_jobs_once_and_moves_next_run():
    now = datetime(2026, 3, 2, 6, 0, 30, tzinfo=UTC)
    store, jobs = FakeSchedules("2026-03-02T06:00:00+00:00"), FakeJobs()
    sched = Scheduler(store, jobs)
    assert sched.tick(now) == ["monthly_cycle"] and jobs.started == [("monthly_cycle", "scheduler")]
    assert store.rows[0]["next_run_at"] == "2026-04-02T05:00:00+00:00"
    assert sched.tick(now) == []


def test_scheduler_skips_while_another_run_is_busy():
    store, jobs = FakeSchedules("2026-03-02T06:00:00+00:00"), FakeJobs(busy=True)
    assert Scheduler(store, jobs).tick(datetime(2026, 3, 2, 7, tzinfo=UTC)) == []
    assert store.log == ["skipped: another run in progress"]


# ---------------------------------------------------------------- what-if engine
def test_edits_apply_in_order_and_never_go_negative():
    svc = make_service()
    base = svc.consensus({})
    out = apply_edits(base, [
        {"type": "pct", "scope": "channel", "value": "B2B wo Amazon", "pct": 10, "month": None},
        {"type": "item", "item_id": 1, "month": "2026-03-01", "qty": 1000},
        {"type": "pct", "scope": "product", "value": "PROD_4", "pct": -150, "month": "2026-03-01"},
    ])
    get = lambda df, i, m: float(df[(df.item_id == i) & (df.month == m)]["consensus_qty"].iloc[0])  # noqa: E731
    assert get(out, 2, "2026-04-01") == pytest.approx(341 * 1.1)
    assert get(out, 1, "2026-03-01") == 1000 and get(out, 1, "2026-04-01") == get(base, 1, "2026-04-01")
    assert get(out, 4, "2026-03-01") == 0.0
    assert get(base, 1, "2026-03-01") == 880.0  # the base copy is untouched


def test_compare_reports_deltas_against_the_current_consensus(tmp_path):
    from app.rules.otif import load_terms
    svc = make_service()
    raise_b2b = {"id": "s1", "name": "Raise B2B", "created_by": "danny",
                 "edits": [{"type": "item", "item_id": 2, "month": "2026-03-01", "qty": 500}]}
    out = compare(svc, svc.consensus({}), [raise_b2b], load_terms(tmp_path))
    cur, s1 = out["columns"]
    assert cur["metrics"]["otif_items_at_risk"] == 1 and s1["metrics"]["otif_items_at_risk"] == 0
    assert s1["delta"]["units_m1"] == pytest.approx(160) and s1["delta"]["value_m1_gbp"] == pytest.approx(160 * 24.5)
    assert s1["delta"]["otif_penalty_gbp"] == pytest.approx(-160 * 24.5 * 0.05)


# ---------------------------------------------------------------- Mongo stores
@pytest.fixture
def db():
    pymongo = pytest.importorskip("pymongo")
    uri = os.getenv("MONGO_URL", "mongodb://localhost:27017")
    client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=500)
    try:
        client.admin.command("ping")
    except Exception:
        pytest.skip("no local MongoDB")
    name = f"sop_test_{uuid.uuid4().hex[:8]}"
    yield client[name]
    client.drop_database(name)


def test_schedule_store_seeds_defaults_and_claims_atomically(db):
    from app.store.ops import ScheduleStore
    s = ScheduleStore(db)
    jobs = {r["job"]: r for r in s.all()}
    assert jobs["monthly_cycle"]["day"] == 2 and jobs["monthly_cycle"]["hour"] == 6 and jobs["amazon_daily"]["hour"] == 5
    nxt = jobs["amazon_daily"]["next_run_at"]
    assert s.claim("amazon_daily", nxt, "2099-01-01T00:00:00+00:00") is True
    assert s.claim("amazon_daily", nxt, "2099-01-02T00:00:00+00:00") is False
    ScheduleStore(db)  # idempotent re-seed
    assert len(s.all()) == 2


def test_notifications_are_per_role_and_per_user_read_state(db):
    from app.store.ops import NotificationStore
    n = NotificationStore(db)
    n.add(["planner", "admin"], "queue_ready", "Queue ready", "74 exceptions", "workbench")
    n.add(["admin"], "error", "Run failed", "boom")
    assert n.for_user("danny", "planner")["unread"] == 1
    assert n.for_user("root", "admin")["unread"] == 2 and n.for_user("ada", "auditor")["unread"] == 0
    first = n.for_user("danny", "planner")["items"][0]["id"]
    assert n.mark_read("danny", "planner", first) == 1
    assert n.for_user("danny", "planner")["unread"] == 0 and n.for_user("root", "admin")["unread"] == 2
    assert n.mark_read("danny", "planner", "not-an-id") == 0


def test_scenario_store_crud(db):
    from app.store.ops import ScenarioStore
    st = ScenarioStore(db)
    s = st.create("2026-02-01", "Upside", "", [{"type": "pct", "scope": "all", "value": None, "pct": 5, "month": None}], "danny")
    assert st.list("2026-02-01")[0]["id"] == s["id"] and st.list("2025-11-01") == []
    assert st.update(s["id"], {"name": "Upside v2"})["name"] == "Upside v2"
    assert st.delete(s["id"]) and st.get(s["id"]) is None and st.get("bad") is None
