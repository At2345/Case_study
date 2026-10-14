"""S&OP API regression tests (public URL). Does NOT commit the cycle; does NOT run the monthly cycle."""
import json
import os
import time

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
API = f"{BASE_URL}/api/v1"
CREDS = {"planner": ("danny", "PlannerPass2026!"), "admin": ("admin", "AdminPass2026!"),
         "auditor": ("auditor", "AuditorPass2026!")}


def _login(role):
    u, p = CREDS[role]
    r = requests.post(f"{API}/auth/login", json={"username": u, "password": p}, timeout=30)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="session")
def tokens():
    return {k: _login(k)["access_token"] for k in CREDS}


def h(tokens, role):
    return {"Authorization": f"Bearer {tokens[role]}"}


# Health / auth
class TestAuth:
    def test_health(self):
        r = requests.get(f"{API}/health", timeout=30)
        assert r.status_code == 200 and r.json()["status"] == "healthy"
        assert r.json()["cutoff"] == "2026-02-01"

    def test_login_roles(self):
        for role in CREDS:
            d = _login(role)
            assert d["role"] == role and d["access_token"]

    def test_bad_login(self):
        r = requests.post(f"{API}/auth/login", json={"username": "TEST_nouser", "password": "x"}, timeout=30)
        assert r.status_code == 401

    def test_unauth_401(self):
        assert requests.get(f"{API}/summary", timeout=30).status_code == 401
        assert requests.post(f"{API}/override", json={}, timeout=30).status_code in (401, 422)


# Core workbench
class TestWorkbench:
    def test_summary_and_exceptions(self, tokens):
        s = requests.get(f"{API}/summary", headers=h(tokens, "planner"), timeout=60).json()
        assert s["exceptions_requiring_review"] == 74, s
        e = requests.get(f"{API}/exceptions?limit=500", headers=h(tokens, "planner"), timeout=60).json()
        items = e.get("items") or e.get("rows")
        assert e.get("total", len(items)) == 74

    def test_item_detail_band(self, tokens):
        e = requests.get(f"{API}/exceptions?limit=1", headers=h(tokens, "planner"), timeout=60).json()
        iid = (e.get("items") or e.get("rows"))[0]["item_id"]
        d = requests.get(f"{API}/items/{iid}", headers=h(tokens, "planner"), timeout=60)
        assert d.status_code == 200
        txt = d.text
        assert "p10" in txt.lower() and "p90" in txt.lower()
        pytest.item_id = iid

    def test_narrative(self, tokens):
        iid = getattr(pytest, "item_id", None) or 1
        r = requests.post(f"{API}/items/{iid}/narrative", headers=h(tokens, "planner"), timeout=120)
        assert r.status_code == 200, r.text
        d = r.json()
        print("narrative:", {k: (v[:120] if isinstance(v, str) else v) for k, v in d.items()})
        assert d["enabled"] is True

    def test_auditor_cannot_override(self, tokens):
        r = requests.post(f"{API}/override", headers=h(tokens, "auditor"),
                          json={"item_id": 1, "override_quantity": 1, "reason_code": "X"}, timeout=30)
        assert r.status_code == 403

    def test_override_reason_required(self, tokens):
        iid = getattr(pytest, "item_id", None)
        r = requests.post(f"{API}/override", headers=h(tokens, "planner"),
                          json={"item_id": iid, "override_quantity": 123, "reason_code": ""}, timeout=30)
        assert r.status_code == 422
        assert "Reason code is required" in r.text

    def test_metrics(self):
        r = requests.get(f"{API}/metrics", timeout=30)
        assert r.status_code == 200 and "sop_http_requests_total" in r.text and "sop_pending_exceptions" in r.text

    def test_fva_and_verify(self, tokens):
        for g in ("planner_id", "reason_code"):
            assert requests.get(f"{API}/fva?group_by={g}", headers=h(tokens, "auditor"), timeout=60).status_code == 200
        a = requests.get(f"{API}/fva/autopilot", headers=h(tokens, "auditor"), timeout=60)
        assert a.status_code == 200 and a.json()["candidates"] == []
        assert requests.get(f"{API}/audit/verify", headers=h(tokens, "planner"), timeout=30).status_code == 403
        v = requests.get(f"{API}/audit/verify", headers=h(tokens, "auditor"), timeout=60).json()
        assert v["ok"] is True


# Phase 2-4 extended
class TestExtended:
    def test_safety_stock(self, tokens):
        r = requests.get(f"{API}/inventory/safety-stock", headers=h(tokens, "planner"), timeout=60)
        assert r.status_code == 200 and r.json()["available"] is True and r.json()["items"]
        ch = r.json()["items"][0]["channel"]
        f = requests.get(f"{API}/inventory/safety-stock?channel={ch}", headers=h(tokens, "planner"), timeout=60).json()
        assert all(i["channel"] == ch for i in f["items"])

    def test_otif(self, tokens):
        r = requests.get(f"{API}/otif", headers=h(tokens, "planner"), timeout=60)
        assert r.status_code == 200
        d = r.json()
        assert "items_at_risk" in d and "total_penalty_gbp" in d
        assert "by_customer" in d  # customer dimension does not break the channel view
        if d["by_customer"]:
            assert all("customer_id" in c and "penalty_gbp" in c for c in d["by_customer"])

    def test_supply_risk_flags(self, tokens):
        r = requests.get(f"{API}/supply/risk-flags", headers=h(tokens, "planner"), timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert d["rows"] and all("channel" in f and "risk_type" in f and "count" in f for f in d["rows"])

    def test_scenario_promote(self, tokens):
        iid = getattr(pytest, "item_id", None) or 106205
        s = requests.get(f"{API}/summary", headers=h(tokens, "planner"), timeout=30).json()
        m1 = s["first_month"]
        d = requests.get(f"{API}/items/{iid}", headers=h(tokens, "planner"), timeout=60).json()
        cur = next(p for p in d["plan"] if p["month"] == m1)["final"]
        body = {"name": "TEST promote", "description": "regression",
                "edits": [{"type": "item", "item_id": iid, "month": m1, "qty": cur + 100}]}
        created = requests.post(f"{API}/scenarios", headers=h(tokens, "planner"), json=body, timeout=30).json()
        assert requests.post(f"{API}/scenarios/{created['id']}/promote", headers=h(tokens, "auditor"),
                             timeout=30).status_code == 403
        r = requests.post(f"{API}/scenarios/{created['id']}/promote", headers=h(tokens, "planner"), timeout=60)
        assert r.status_code == 200, r.text
        assert r.json()["promoted"] >= 1
        ov = requests.get(f"{API}/overrides?limit=200", headers=h(tokens, "planner"), timeout=30).json()
        assert any(o["item_id"] == iid and "TEST promote" in (o.get("planner_comment") or "") for o in ov)
        requests.delete(f"{API}/scenarios/{created['id']}", headers=h(tokens, "planner"), timeout=30)

    def test_disaggregate(self, tokens):
        iid = getattr(pytest, "item_id", None)
        d = requests.get(f"{API}/items/{iid}", headers=h(tokens, "planner"), timeout=60).json()
        pid = d["item"]["product_id"]
        for m in ("mint_diag", "proportional", "ols"):
            r = requests.post(f"{API}/disaggregate", headers=h(tokens, "planner"),
                              json={"product_id": pid, "total": 1000, "method": m}, timeout=60)
            assert r.status_code == 200, r.text
            tot = sum(x["proposed"] for x in r.json()["rows"])
            assert abs(tot - 1000) < 1.0, (m, tot)
        bad = requests.post(f"{API}/disaggregate", headers=h(tokens, "planner"),
                            json={"product_id": "TEST_nope", "total": 10}, timeout=30)
        assert bad.status_code == 404
        badm = requests.post(f"{API}/disaggregate", headers=h(tokens, "planner"),
                             json={"product_id": pid, "total": 10, "method": "foo"}, timeout=30)
        assert badm.status_code == 422

    def test_agentic_forecast(self, tokens):
        iid = getattr(pytest, "item_id", None) or 106205
        r = requests.get(f"{API}/forecast/items/{iid}/agentic", headers=h(tokens, "planner"), timeout=180)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["stages"] == ["research", "process", "forecast", "inform"]
        assert d["research"]["history_points"] > 0 and "nonzero_share" in d["research"]
        assert d["process"]["chosen_method"] and d["process"]["method_scores"]
        assert d["forecast"]["rows"] and d["forecast"]["rows"][0]["dynamic"] is not None
        assert d["inform"]["text"] and d["inform"]["source"] in ("llm", "deterministic")
        assert d["actions"] and any(a["type"] == "use_dynamic_forecast" for a in d["actions"])
        bad = requests.get(f"{API}/forecast/items/99999999/agentic", headers=h(tokens, "planner"), timeout=30)
        assert bad.status_code == 404

    def test_cycle_runs(self, tokens):
        runs = requests.get(f"{API}/cycles/runs", headers=h(tokens, "planner"), timeout=60)
        assert runs.status_code == 200 and "human_review" in runs.json()["nodes"]
        if runs.json()["runs"]:
            rid = runs.json()["runs"][0]["id"]
            d = requests.get(f"{API}/cycles/runs/{rid}", headers=h(tokens, "planner"), timeout=30)
            assert d.status_code == 200 and "steps" in d.json() and "quality_warnings" in d.json()

    def test_rerun_node_rbac(self, tokens):
        assert requests.post(f"{API}/admin/cycles/rerun-node", headers=h(tokens, "planner"),
                             json={"run_id": "x", "node": "ingest_validate"}, timeout=30).status_code == 403
        runs = requests.get(f"{API}/cycles/runs", headers=h(tokens, "admin"), timeout=30).json()["runs"]
        rid = runs[0]["id"] if runs else "000000000000000000000000"
        r = requests.post(f"{API}/admin/cycles/rerun-node", headers=h(tokens, "admin"),
                          json={"run_id": rid, "node": "ingest_validate"}, timeout=60)
        assert r.status_code in (404, 409)  # no failed node to re-run in a healthy cycle

    def test_run_cycle_rbac(self, tokens):
        assert requests.post(f"{API}/admin/cycles/run", headers=h(tokens, "planner"), timeout=30).status_code == 403

    def test_briefing_and_csv(self, tokens):
        r = requests.get(f"{API}/export/briefing.pdf", headers=h(tokens, "planner"), timeout=120)
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/pdf") and r.content[:4] == b"%PDF"
        c = requests.get(f"{API}/export/consensus", headers=h(tokens, "planner"), timeout=60)
        assert c.status_code == 200 and "text/csv" in c.headers["content-type"]

    def test_assistant_stream_and_history(self, tokens):
        hd = h(tokens, "auditor")
        requests.delete(f"{API}/assistant/history", headers=hd, timeout=30)
        r = requests.post(f"{API}/assistant/stream", headers=hd, json={"question": "How many exceptions need review?"},
                          stream=True, timeout=120)
        assert r.status_code == 200
        text, done, err = "", False, None
        for line in r.iter_lines(decode_unicode=True):
            if line and line.startswith("data: "):
                ev = json.loads(line[6:])
                text += ev.get("delta", "")
                done = done or ev.get("done", False)
                err = err or ev.get("error")
        print("assistant:", text[:300], err)
        assert done and text and not err
        hist = requests.get(f"{API}/assistant/history", headers=hd, timeout=30).json()["messages"]
        assert len(hist) == 2
        assert requests.delete(f"{API}/assistant/history", headers=hd, timeout=30).json()["cleared"]
        assert requests.get(f"{API}/assistant/history", headers=hd, timeout=30).json()["messages"] == []
