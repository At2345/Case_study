# Iteration 2: schedules, notifications, what-if scenarios
import os
import time

import pytest
import requests

BASE = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/") + "/api/v1"
CREDS = {"danny": "PlannerPass2026!", "admin": "AdminPass2026!", "auditor": "AuditorPass2026!"}
_tok = {}


def h(user):
    if user not in _tok:
        r = requests.post(f"{BASE}/auth/login", json={"username": user, "password": CREDS[user]}, timeout=30)
        assert r.status_code == 200, r.text
        _tok[user] = r.json()["access_token"]
    return {"Authorization": f"Bearer {_tok[user]}"}


def first_month():
    r = requests.get(f"{BASE}/scenarios/compare?ids=", headers=h("danny"), timeout=60)
    assert r.status_code == 200
    return r.json()["first_month"]


# ---- schedules
class TestSchedules:
    def test_list_defaults(self):
        r = requests.get(f"{BASE}/schedules", headers=h("auditor"), timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert d["timezone"] == "Europe/London"
        s = {x["job"]: x for x in d["schedules"]}
        assert s["monthly_cycle"]["frequency"] == "monthly" and s["monthly_cycle"]["day"] == 2
        assert s["monthly_cycle"]["hour"] == 6 and s["monthly_cycle"]["minute"] == 0
        assert s["amazon_daily"]["frequency"] == "daily" and s["amazon_daily"]["hour"] == 5
        assert "_id" not in s["amazon_daily"]

    def test_planner_forbidden(self):
        r = requests.put(f"{BASE}/admin/schedules/amazon_daily", headers=h("danny"),
                         json={"enabled": True, "frequency": "daily", "hour": 5, "minute": 0}, timeout=30)
        assert r.status_code == 403

    def test_unknown_job_404(self):
        r = requests.put(f"{BASE}/admin/schedules/nope", headers=h("admin"),
                         json={"enabled": True, "frequency": "daily", "hour": 5, "minute": 0}, timeout=30)
        assert r.status_code == 404

    @pytest.mark.parametrize("body", [
        {"enabled": True, "frequency": "monthly", "day": 29, "hour": 6, "minute": 0},
        {"enabled": True, "frequency": "daily", "hour": 24, "minute": 0},
        {"enabled": True, "frequency": "monthly", "day": None, "hour": 6, "minute": 0},
        {"enabled": True, "frequency": "weekly", "hour": 6, "minute": 0},
        {"enabled": True, "frequency": "daily", "hour": 5, "minute": 60},
    ])
    def test_invalid_422(self, body):
        r = requests.put(f"{BASE}/admin/schedules/monthly_cycle", headers=h("admin"), json=body, timeout=30)
        assert r.status_code == 422, r.text

    def test_update_and_restore(self):
        r = requests.put(f"{BASE}/admin/schedules/monthly_cycle", headers=h("admin"),
                         json={"enabled": True, "frequency": "monthly", "day": 15, "hour": 7, "minute": 30}, timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert d["day"] == 15 and d["hour"] == 7 and d["minute"] == 30
        # next run should be 07:30 UK on 15th -> 06:30Z (BST) or 07:30Z (GMT)
        assert "-15T0" in d["next_run_at"] and ":30:00" in d["next_run_at"]
        r = requests.put(f"{BASE}/admin/schedules/monthly_cycle", headers=h("admin"),
                         json={"enabled": True, "frequency": "monthly", "day": 2, "hour": 6, "minute": 0}, timeout=30)
        assert r.status_code == 200 and r.json()["day"] == 2 and r.json()["hour"] == 6

    def test_disable_and_restore(self):
        r = requests.put(f"{BASE}/admin/schedules/amazon_daily", headers=h("admin"),
                         json={"enabled": False, "frequency": "daily", "hour": 5, "minute": 0}, timeout=30)
        assert r.status_code == 200 and r.json()["enabled"] is False
        r = requests.put(f"{BASE}/admin/schedules/amazon_daily", headers=h("admin"),
                         json={"enabled": True, "frequency": "daily", "hour": 5, "minute": 0}, timeout=30)
        assert r.status_code == 200 and r.json()["enabled"] is True


# ---- notifications
class TestNotifications:
    def test_planner_has_queue_ready(self):
        r = requests.get(f"{BASE}/notifications", headers=h("danny"), timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert isinstance(d["unread"], int)
        assert any(n["title"] == "Exception queue is ready" and n["link"] == "workbench" for n in d["items"])
        assert all("_id" not in n and "read_by" not in n for n in d["items"])

    def test_auditor_gets_none(self):
        d = requests.get(f"{BASE}/notifications", headers=h("auditor"), timeout=30).json()
        assert not any(n["kind"] == "queue_ready" for n in d["items"])

    def test_read_bad_id_404(self):
        assert requests.post(f"{BASE}/notifications/zzz/read", headers=h("danny"), timeout=30).status_code == 404
        assert requests.post(f"{BASE}/notifications/507f1f77bcf86cd799439011/read", headers=h("danny"), timeout=30).status_code == 404

    def test_auditor_cannot_read_planner_note(self):
        nid = requests.get(f"{BASE}/notifications", headers=h("danny"), timeout=30).json()["items"][0]["id"]
        assert requests.post(f"{BASE}/notifications/{nid}/read", headers=h("auditor"), timeout=30).status_code == 404

    def test_admin_read_one_and_all(self):
        d = requests.get(f"{BASE}/notifications", headers=h("admin"), timeout=30).json()
        nid = d["items"][0]["id"]
        r = requests.post(f"{BASE}/notifications/{nid}/read", headers=h("admin"), timeout=30)
        assert r.status_code == 200
        d2 = requests.get(f"{BASE}/notifications", headers=h("admin"), timeout=30).json()
        assert [n for n in d2["items"] if n["id"] == nid][0]["read"] is True
        r = requests.post(f"{BASE}/notifications/read-all", headers=h("admin"), timeout=30)
        assert r.status_code == 200
        assert requests.get(f"{BASE}/notifications", headers=h("admin"), timeout=30).json()["unread"] == 0

    def test_unauth(self):
        assert requests.get(f"{BASE}/notifications", timeout=30).status_code in (401, 403)


# ---- scenarios
@pytest.fixture(scope="class")
def created():
    ids = []
    yield ids
    for i in ids:
        requests.delete(f"{BASE}/scenarios/{i}", headers=h("admin"), timeout=30)


class TestScenarios:
    def test_list(self):
        r = requests.get(f"{BASE}/scenarios", headers=h("auditor"), timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert d["max_compare"] == 3 and isinstance(d["scenarios"], list)

    def test_auditor_cannot_create(self):
        r = requests.post(f"{BASE}/scenarios", headers=h("auditor"), json={"name": "TEST_x", "edits": []}, timeout=30)
        assert r.status_code == 403

    @pytest.mark.parametrize("edits", [
        [{"type": "item", "item_id": 999999999, "qty": 10, "month": "2026-03-01"}],
        [{"type": "pct", "scope": "all", "pct": 10, "month": "2031-01-01"}],
        [{"type": "pct", "scope": "product", "pct": 10}],
        [{"type": "pct", "scope": "channel", "value": "", "pct": 10}],
        [{"type": "pct", "scope": "all", "pct": -150}],
        [{"type": "item", "item_id": 103125, "qty": -1, "month": "2026-03-01"}],
    ])
    def test_invalid_edits_422(self, edits):
        r = requests.post(f"{BASE}/scenarios", headers=h("danny"), json={"name": "TEST_bad", "edits": edits}, timeout=30)
        assert r.status_code == 422, r.text

    def test_empty_name_422(self):
        assert requests.post(f"{BASE}/scenarios", headers=h("danny"), json={"name": "", "edits": []}, timeout=30).status_code == 422

    def test_crud_and_compare(self, created):
        fm = first_month()
        r = requests.post(f"{BASE}/scenarios", headers=h("danny"), timeout=30, json={
            "name": "TEST_b2b10", "description": "t", "edits": [{"type": "pct", "scope": "channel", "value": "B2B wo Amazon", "pct": 10}]})
        assert r.status_code == 200, r.text
        s = r.json()
        created.append(s["id"])
        assert s["created_by"] == "danny" and s["edits"][0]["pct"] == 10
        r2 = requests.post(f"{BASE}/scenarios", headers=h("danny"), timeout=30, json={
            "name": "TEST_item", "edits": [{"type": "item", "item_id": 103125, "qty": 5633, "month": "2026-03-01"}]})
        assert r2.status_code == 200, r2.text
        created.append(r2.json()["id"])
        # compare
        c = requests.get(f"{BASE}/scenarios/compare?ids={s['id']},{r2.json()['id']}", headers=h("auditor"), timeout=60)
        assert c.status_code == 200, c.text
        cols = c.json()["columns"]
        assert cols[0]["id"] == "current" and len(cols) == 3
        b2b, item = cols[1], cols[2]
        assert b2b["delta"]["value_12m_gbp"] > 0
        assert b2b["delta"]["holding_cost_gbp"] is None or b2b["delta"]["holding_cost_gbp"] >= 0
        assert item["delta"]["otif_penalty_gbp"] <= 0
        print("first_month", fm, "b2b delta", b2b["delta"], "item delta", item["delta"])
        # update by author
        u = requests.put(f"{BASE}/scenarios/{s['id']}", headers=h("danny"), timeout=30,
                         json={"name": "TEST_b2b20", "edits": [{"type": "pct", "scope": "channel", "value": "B2B wo Amazon", "pct": 20}]})
        assert u.status_code == 200 and u.json()["name"] == "TEST_b2b20"
        got = [x for x in requests.get(f"{BASE}/scenarios", headers=h("danny"), timeout=30).json()["scenarios"] if x["id"] == s["id"]]
        assert got and got[0]["edits"][0]["pct"] == 20

    def test_non_author_forbidden_admin_allowed(self, created):
        r = requests.post(f"{BASE}/scenarios", headers=h("admin"), timeout=30, json={"name": "TEST_admin", "edits": []})
        assert r.status_code == 200
        aid = r.json()["id"]
        created.append(aid)
        body = {"name": "TEST_hack", "edits": []}
        assert requests.put(f"{BASE}/scenarios/{aid}", headers=h("danny"), json=body, timeout=30).status_code == 403
        assert requests.delete(f"{BASE}/scenarios/{aid}", headers=h("danny"), timeout=30).status_code == 403
        assert requests.delete(f"{BASE}/scenarios/{aid}", headers=h("auditor"), timeout=30).status_code == 403
        # admin edits danny's
        did = created[0]
        assert requests.put(f"{BASE}/scenarios/{did}", headers=h("admin"), json={"name": "TEST_by_admin", "edits": []}, timeout=30).status_code == 200

    def test_delete_then_404(self, created):
        r = requests.post(f"{BASE}/scenarios", headers=h("danny"), timeout=30, json={"name": "TEST_del", "edits": []})
        sid = r.json()["id"]
        assert requests.delete(f"{BASE}/scenarios/{sid}", headers=h("danny"), timeout=30).status_code == 200
        assert requests.delete(f"{BASE}/scenarios/{sid}", headers=h("danny"), timeout=30).status_code == 404
        assert requests.get(f"{BASE}/scenarios/compare?ids={sid}", headers=h("danny"), timeout=30).status_code == 404

    def test_compare_limits(self):
        r = requests.get(f"{BASE}/scenarios/compare?ids=a,b,c,d", headers=h("danny"), timeout=30)
        assert r.status_code == 422
        r = requests.get(f"{BASE}/scenarios/compare?ids=", headers=h("danny"), timeout=60)
        assert r.status_code == 200 and len(r.json()["columns"]) == 1
        assert requests.get(f"{BASE}/scenarios/compare?ids=bogus", headers=h("danny"), timeout=30).status_code == 404
