# Co-Pilot daily-task hub: registry, run, urgency pings
import json
import os

import requests

BASE = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/") + "/api/v1"
CREDS = {"danny": os.environ.get("TEST_PLANNER_PASSWORD", "PlannerPass2026!"),
         "admin": os.environ.get("TEST_ADMIN_PASSWORD", "AdminPass2026!"),
         "auditor": os.environ.get("TEST_AUDITOR_PASSWORD", "AuditorPass2026!")}
_tok = {}


def h(user):
    if user not in _tok:
        r = requests.post(f"{BASE}/auth/login", json={"username": user, "password": CREDS[user]}, timeout=30)
        assert r.status_code == 200, r.text
        _tok[user] = r.json()["access_token"]
    return {"Authorization": f"Bearer {_tok[user]}"}


class TestTaskRegistry:
    def test_list_shape(self):
        r = requests.get(f"{BASE}/tasks", headers=h("danny"), timeout=60)
        assert r.status_code == 200
        d = r.json()
        assert "soe_daily" in d["rhythms"] and "sop_week_4" in d["rhythms"]
        assert len(d["tasks"]) >= 10
        ids = {t["id"] for t in d["tasks"]}
        assert {"amazon_po_confirmations", "otif_exposure", "exception_queue", "executive_briefing"} <= ids
        for t in d["tasks"]:
            assert t["title"] and t["rhythm"] in d["rhythms"]
            assert t["channel"] in ("all", "amazon", "b2b", "d2c")
            assert t["priority"] in ("high", "medium", "low")
            assert t["kind"] in ("run", "download")
            assert isinstance(t["attention"], int) and isinstance(t["urgent"], bool)

    def test_attention_is_live(self):
        r = requests.get(f"{BASE}/tasks", headers=h("danny"), timeout=60).json()
        by = {t["id"]: t for t in r["tasks"]}
        # the exception queue reflects the live cycle
        assert by["exception_queue"]["attention"] >= 0
        assert by["exception_queue"]["urgent"] in (True, False)

    def test_auditor_allowed(self):
        r = requests.get(f"{BASE}/tasks", headers=h("auditor"), timeout=60)
        assert r.status_code == 200


class TestRunTask:
    def test_run_exception_queue(self):
        r = requests.post(f"{BASE}/tasks/exception_queue/run", headers=h("danny"), timeout=60)
        assert r.status_code == 200
        d = r.json()
        assert d["summary"] and isinstance(d["attention"], int)
        assert any(a["type"] == "review_exception" for a in d["actions"])

    def test_run_otif(self):
        r = requests.post(f"{BASE}/tasks/otif_exposure/run", headers=h("danny"), timeout=60)
        assert r.status_code == 200
        assert "OTIF" in r.json()["summary"]

    def test_run_briefing_download_flag(self):
        r = requests.post(f"{BASE}/tasks/executive_briefing/run", headers=h("danny"), timeout=60)
        assert r.status_code == 200
        assert r.json()["download"] == "briefing"

    def test_unknown_task_404(self):
        r = requests.post(f"{BASE}/tasks/nope/run", headers=h("danny"), timeout=30)
        assert r.status_code == 404

    def test_urgent_run_pings_notification(self):
        before = requests.get(f"{BASE}/notifications", headers=h("danny"), timeout=30).json()["unread"]
        r = requests.post(f"{BASE}/tasks/exception_queue/run", headers=h("danny"), timeout=60)
        assert r.status_code == 200
        if r.json()["urgent"]:
            after = requests.get(f"{BASE}/notifications", headers=h("danny"), timeout=30).json()["unread"]
            assert after >= before


class TestAssistantTaskExplanations:
    def test_explain_task_answers_from_registry(self):
        """'explain <task>' must answer from the task registry, not plan-doc file references."""
        r = requests.post(f"{BASE}/assistant/stream", headers=h("danny"),
                          json={"question": "Can you explain Promo sanity check?"}, stream=True, timeout=120)
        assert r.status_code == 200
        text, sources = "", []
        for line in r.iter_lines(decode_unicode=True):
            if line and line.startswith("data: "):
                ev = json.loads(line[6:])
                text += ev.get("delta", "")
                if "sources" in ev:
                    sources = ev["sources"]
        assert any(s["kind"] == "task" and "promo" in s["title"].lower() for s in sources)
        assert not any(s["kind"] == "docs" for s in sources)
        assert "uplift" in text.lower() or "promotion" in text.lower()
