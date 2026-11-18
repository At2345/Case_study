# Superset BI integration: mart status and rebuild endpoints
import json
import os
import re
import shutil
import sqlite3
import uuid
from pathlib import Path

import pytest
import requests

BASE = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/") + "/api/v1"


def _password(test_var: str, seed_var: str, fallback: str) -> str:
    """TEST_* wins, then the server's own .env, then the documented default."""
    if os.environ.get(test_var):
        return os.environ[test_var]
    env = Path(__file__).resolve().parents[2] / "backend" / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{seed_var}="):
                return line.split("=", 1)[1].strip()
    return fallback


CREDS = {"danny": _password("TEST_PLANNER_PASSWORD", "SEED_PLANNER_PASSWORD", "PlannerPass2026!"),
         "admin": _password("TEST_ADMIN_PASSWORD", "SEED_ADMIN_PASSWORD", "AdminPass2026!"),
         "auditor": _password("TEST_AUDITOR_PASSWORD", "SEED_AUDITOR_PASSWORD", "AuditorPass2026!")}
_tok = {}


def h(user):
    if user not in _tok:
        r = requests.post(f"{BASE}/auth/login", json={"username": user, "password": CREDS[user]}, timeout=30)
        assert r.status_code == 200, r.text
        _tok[user] = r.json()["access_token"]
    return {"Authorization": f"Bearer {_tok[user]}"}


class TestBiStatus:
    def test_status_shape(self):
        r = requests.get(f"{BASE}/bi/status", headers=h("danny"), timeout=60)
        assert r.status_code == 200
        d = r.json()
        assert d["superset_url"].startswith("http")
        assert d["dashboard_url"].endswith(f"/superset/dashboard/{d['dashboard_slug']}/")
        assert "mart" in d and "tables" in d["mart"]

    def test_auditor_allowed(self):
        assert requests.get(f"{BASE}/bi/status", headers=h("auditor"), timeout=60).status_code == 200

    def test_unauth_401(self):
        assert requests.get(f"{BASE}/bi/status", timeout=30).status_code == 401


class TestBiDashboard:
    def test_dashboard_shape(self):
        r = requests.get(f"{BASE}/bi/dashboard", headers=h("danny"), timeout=120)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["cycle"]["in_scope_skus"] > 0
        assert len(d["by_state"]) >= 1
        assert len(d["plan_vs_baseline"]) >= 1
        assert {"month", "plan", "baseline", "ml"} <= set(d["plan_vs_baseline"][0])
        assert len(d["top_exceptions"]) <= 10
        assert d["mart"]["exists"] is True
        assert "reachable" in d["superset"]

    def test_dashboard_auditor_allowed(self):
        assert requests.get(f"{BASE}/bi/dashboard", headers=h("auditor"), timeout=120).status_code == 200

    def test_dashboard_unauth_401(self):
        assert requests.get(f"{BASE}/bi/dashboard", timeout=30).status_code == 401


class TestBiKpiTree:
    def test_tree_shape(self):
        r = requests.get(f"{BASE}/bi/kpi-tree", headers=h("danny"), timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        root = d["root"]
        assert root["owner"] and root["confidence"] in {"high", "medium", "low"}
        assert root["health"] in {"healthy", "watch", "breach", "unknown"}
        assert 3 <= len(root["branches"]) <= 5
        for branch in root["branches"]:
            assert 3 <= len(branch["leaves"]) <= 5
            assert branch["owner"]
            for leaf in branch["leaves"]:
                assert leaf["owner"]
                assert leaf["health"] in {"healthy", "watch", "breach", "unknown"}
        assert 13 <= d["counts"]["total"] <= 31

    def test_tree_unauth_401(self):
        assert requests.get(f"{BASE}/bi/kpi-tree", timeout=30).status_code == 401


class TestBiMetrics:
    def test_catalog_shape(self):
        r = requests.get(f"{BASE}/bi/metrics", headers=h("danny"), timeout=60)
        assert r.status_code == 200, r.text
        metrics = r.json()["metrics"]
        assert len(metrics) >= 15
        for m in metrics:
            assert m["owner"] and m["grain"] and m["aggregation"] and m["version"]
            assert m["sql"].startswith("SELECT")

    def test_golden_query_parity(self):
        """The KPI tree and the metric registry must agree on every node."""
        catalog = {m["id"]: m for m in
                   requests.get(f"{BASE}/bi/metrics", headers=h("danny"), timeout=60).json()["metrics"]}
        tree = requests.get(f"{BASE}/bi/kpi-tree", headers=h("danny"), timeout=60).json()
        nodes = [tree["root"]]
        for branch in tree["root"]["branches"]:
            nodes.append(branch)
            nodes.extend(branch["leaves"])
        for node in nodes:
            assert node["metric"] in catalog, node["metric"]
            assert node["value"] == catalog[node["metric"]]["value"], node["metric"]

    def test_metrics_unauth_401(self):
        assert requests.get(f"{BASE}/bi/metrics", timeout=30).status_code == 401


class TestMetricGuards:
    def test_semi_additive_refused_across_time(self):
        from app.bi.semantics import add_across_time
        with pytest.raises(ValueError, match="semi-additive"):
            add_across_time("avg_weeks_of_cover", [10.0, 12.0])

    def test_additive_metric_allowed(self):
        from app.bi.semantics import add_across_time
        assert add_across_time("oos_exposure_gbp", [1.0, 2.0]) == 3.0

    def test_fan_out_refused_across_grains(self):
        from app.bi.semantics import combine
        with pytest.raises(ValueError, match="fan out"):
            combine(["oos_exposure_gbp", "fva_points"])

    def test_same_grain_allowed(self):
        from app.bi.semantics import combine
        combine(["oos_exposure_gbp", "ml_coverage"])


class TestGroundedAsk:
    class FakeClient:
        def __init__(self, reply):
            self.reply = reply
            self.prompts = []

        def generate(self, system, user):
            self.prompts.append((system, user))
            return self.reply

    def test_semantic_document_lists_every_metric(self):
        from app.bi.ask import semantic_document
        from app.bi.semantics import METRICS
        doc = semantic_document()
        for metric_id in METRICS:
            assert metric_id in doc
        assert "SELECT" not in doc.upper()

    def test_grounding_prompt_contains_no_schema(self):
        from app.bi.ask import resolve
        client = self.FakeClient('{"metrics": ["oos_exposure_gbp"]}')
        resolve("How much OOS risk?", client)
        system, user = client.prompts[0]
        for table in ("triage_items", "cycle_summary", "fva_by_planner", "quality_warnings"):
            assert not re.search(rf"\b{table}\b", user), table
        assert "never write SQL" in system

    def test_sql_reply_rejected(self):
        from app.bi.ask import parse_selection
        with pytest.raises(ValueError, match="SQL"):
            parse_selection('{"metrics": []} SELECT * FROM triage_items')

    def test_unknown_ids_reported_not_silent(self):
        from app.bi.ask import parse_selection
        accepted, rejected = parse_selection('{"metrics": ["oos_exposure_gbp", "made_up_metric"]}')
        assert accepted == ["oos_exposure_gbp"]
        assert rejected == ["made_up_metric"]

    def test_too_many_metrics_rejected(self):
        from app.bi.ask import MAX_METRICS, parse_selection
        from app.bi.semantics import METRICS
        ids = list(METRICS)[:MAX_METRICS + 1]
        with pytest.raises(ValueError, match="limit"):
            parse_selection(json.dumps({"metrics": ids}))

    def test_answer_evaluates_through_registry(self):
        from app.bi.ask import answer
        from app.bi.export_mart import MART_FILE
        from app.bi.finops import QueryTag
        from app.config import settings
        client = self.FakeClient('{"metrics": ["oos_exposure_gbp", "autopilot_share"]}')
        result = answer("q", settings.store_dir / MART_FILE, client, tenant="default",
                        tag=QueryTag(team="Demand Planning", pipeline="ask", workload="ai"))
        assert result["grounded"] is True
        assert [m["id"] for m in result["metrics"]] == ["oos_exposure_gbp", "autopilot_share"]
        assert all(m["value"] is not None for m in result["metrics"])
        assert result["rejected"] == []

    def test_golden_set_matches_registry(self):
        from app.bi.ask import GOLDEN
        from app.bi.semantics import METRICS
        for case in GOLDEN:
            for metric_id in case["expect"]:
                assert metric_id in METRICS, metric_id


class TestBiAsk:
    def test_ask_requires_question(self):
        r = requests.post(f"{BASE}/bi/ask", headers=h("danny"), json={}, timeout=30)
        assert r.status_code == 422

    def test_ask_unauth_401(self):
        assert requests.post(f"{BASE}/bi/ask", json={"question": "x"}, timeout=30).status_code == 401


class TestBiExport:
    def test_planner_forbidden(self):
        assert requests.post(f"{BASE}/bi/export", headers=h("danny"), timeout=60).status_code == 403

    def test_admin_rebuilds_mart(self):
        r = requests.post(f"{BASE}/bi/export", headers=h("admin"), timeout=300)
        assert r.status_code == 200, r.text
        tables = r.json()["tables"]
        assert {"triage_items", "triage_monthly", "overrides", "cycle_summary"} <= set(tables)
        assert tables["triage_items"] > 0

    def test_status_reports_tables_after_export(self):
        d = requests.get(f"{BASE}/bi/status", headers=h("danny"), timeout=60).json()
        assert d["mart"]["exists"] is True
        assert d["mart"]["tables"].get("triage_items", 0) > 0
        assert d["mart"]["generated_at"]


MONTHS = [f"2026-{m:02d}-01" for m in range(1, 13)]
SPIKE = "2026-06-01"


@pytest.fixture
def synthetic_mart(tmp_path):
    """Twelve flat months with one spike in channel A, so detection is deterministic."""
    path = tmp_path / "mart.db"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE triage_monthly (item_id TEXT, date TEXT, final_qty REAL, "
                    "baseline_qty REAL, ml_prediction REAL, channel TEXT)")
        con.execute("CREATE TABLE triage_items (item_id TEXT, product_id TEXT, channel TEXT, "
                    "risk_type TEXT, var_gbp REAL, in_scope INTEGER)")
        for month in MONTHS:
            a = 1000.0 if month == SPIKE else 100.0
            for item, channel, qty in (("A1", "A", a), ("B1", "B", 100.0)):
                con.execute("INSERT INTO triage_monthly VALUES (?,?,?,?,?,?)",
                            (item, month, qty, qty, qty, channel))
        for i in range(3):
            con.execute("INSERT INTO triage_items VALUES (?,?,?,?,?,?)",
                        (f"A{i}", "P1", "A", "OOS_RISK", 100.0 * (i + 1), 1))
    return path


class TestAnomalyDetection:
    def test_flags_the_spike_month_only(self, synthetic_mart):
        from app.bi.anomaly import SERIES, detect
        with sqlite3.connect(synthetic_mart) as con:
            con.row_factory = sqlite3.Row
            result = detect(con, SERIES["plan_qty"])
        assert [a["month"] for a in result["anomalies"]] == [SPIKE]
        assert result["anomalies"][0]["direction"] == "above"
        assert result["anomalies"][0]["z"] >= result["threshold"]

    def test_flat_series_has_no_anomaly(self, synthetic_mart):
        from app.bi.anomaly import SERIES, detect
        with sqlite3.connect(synthetic_mart) as con:
            con.row_factory = sqlite3.Row
            result = detect(con, SERIES["plan_uplift_qty"])
        assert result["anomalies"] == []

    def test_forecast_projects_three_months(self, synthetic_mart):
        from app.bi.anomaly import SERIES, detect
        with sqlite3.connect(synthetic_mart) as con:
            con.row_factory = sqlite3.Row
            result = detect(con, SERIES["plan_qty"])
        assert len(result["forecast"]["points"]) == 3
        assert result["forecast"]["points"][0]["month"] == "2027-01-01"
        assert result["forecast"]["points"][0]["low"] <= result["forecast"]["points"][0]["value"]


class TestRcaProtocol:
    def test_reports_all_four_checks(self, synthetic_mart):
        from app.bi.anomaly import PROTOCOL, SERIES, rca
        with sqlite3.connect(synthetic_mart) as con:
            con.row_factory = sqlite3.Row
            result = rca(con, SERIES["plan_qty"], SPIKE, "planner")
        assert [c["id"] for c in result["checks"]] == [p["id"] for p in PROTOCOL]

    def test_planner_gets_actionable_cause_with_evidence(self, synthetic_mart):
        from app.bi.anomaly import SERIES, rca
        with sqlite3.connect(synthetic_mart) as con:
            con.row_factory = sqlite3.Row
            result = rca(con, SERIES["plan_qty"], SPIKE, "planner")
        assert result["leading_driver"] == "A"
        assert result["total_deviation"] == pytest.approx(900.0)
        assert all(c["passed"] for c in result["checks"]), result["checks"]
        assert result["actionable"] is True and result["status"] == "actionable"
        assert result["approver"] == "Head of Demand Planning"
        assert len(result["evidence"]) == 3

    def test_auditor_gets_hypothesis_and_no_item_evidence(self, synthetic_mart):
        from app.bi.anomaly import SERIES, rca
        with sqlite3.connect(synthetic_mart) as con:
            con.row_factory = sqlite3.Row
            result = rca(con, SERIES["plan_qty"], SPIKE, "auditor")
        permission = next(c for c in result["checks"] if c["id"] == "permission_preserved")
        assert permission["passed"] is False
        assert result["evidence"] == [] and result["evidence_withheld"] is True
        assert result["actionable"] is False and result["status"] == "hypothesis"

    def test_temporal_precedence_rejects_a_late_driver(self):
        from app.bi.anomaly import _check_temporal
        late = {"segment": "A", "first_deviation": "2026-07-01"}
        assert _check_temporal(late, SPIKE)["passed"] is False
        early = {"segment": "A", "first_deviation": "2026-05-01"}
        assert _check_temporal(early, SPIKE)["passed"] is True
        never = {"segment": "A", "first_deviation": None}
        assert _check_temporal(never, SPIKE)["passed"] is False

    def test_reconciliation_rejects_an_incomplete_decomposition(self):
        from app.bi.anomaly import _check_reconcile
        complete = [{"contribution": 60.0}, {"contribution": 40.0}]
        assert _check_reconcile(100.0, complete)["passed"] is True
        partial = [{"contribution": 60.0}]
        assert _check_reconcile(100.0, partial)["passed"] is False

    def test_named_approver_required(self):
        from app.bi.anomaly import Series, _check_approver
        owned = Series("x", "X", "t", "v", "channel", "qty", "Inventory Manager")
        assert _check_approver(owned)["passed"] is True
        unowned = Series("x", "X", "t", "v", "channel", "qty", "")
        assert _check_approver(unowned)["passed"] is False

    def test_permission_gate_matches_role(self):
        from app.bi.anomaly import _check_permission
        assert _check_permission("planner", "item")["passed"] is True
        assert _check_permission("auditor", "item")["passed"] is False
        assert _check_permission("auditor", "segment")["passed"] is True


class TestBiDiagnostics:
    def test_diagnostics_shape(self):
        r = requests.get(f"{BASE}/bi/diagnostics", headers=h("danny"), timeout=120)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["role"] == "planner"
        assert len(d["protocol"]) == 4
        assert len(d["series"]) >= 1
        for series in d["series"]:
            assert {"series", "points", "anomalies", "rca", "forecast"} <= set(series)
            assert len(series["rca"]) == len(series["anomalies"])
            for cause in series["rca"]:
                assert len(cause["checks"]) == 4
                assert cause["status"] in {"actionable", "hypothesis"}
                assert cause["actionable"] == all(c["passed"] for c in cause["checks"])

    def test_single_series_filter(self):
        r = requests.get(f"{BASE}/bi/diagnostics?series=plan_qty", headers=h("danny"), timeout=120)
        assert r.status_code == 200, r.text
        assert [s["series"] for s in r.json()["series"]] == ["plan_qty"]

    def test_unknown_series_422(self):
        r = requests.get(f"{BASE}/bi/diagnostics?series=nope", headers=h("danny"), timeout=60)
        assert r.status_code == 422

    def test_auditor_sees_no_actionable_cause(self):
        d = requests.get(f"{BASE}/bi/diagnostics", headers=h("auditor"), timeout=120).json()
        assert d["summary"]["actionable"] == 0
        for series in d["series"]:
            for cause in series["rca"]:
                assert cause["evidence"] == []

    def test_diagnostics_unauth_401(self):
        assert requests.get(f"{BASE}/bi/diagnostics", timeout=30).status_code == 401


def _annotation(**over):
    return {"cutoff": "2026-02-01", "target_type": "item", "target_id": "100001",
            "body": "Supply constrained", "author": "danny", "role": "planner",
            "ip_address": "127.0.0.1", "session_id": "s1",
            "idempotency_key": uuid.uuid4().hex, **over}


def _proposal(**over):
    return {"cutoff": "2026-02-01", "series": "plan_qty", "month": "2026-10-01",
            "driver": "amazon", "approver": "Head of Demand Planning",
            "note": "Investigate the leading driver", "actor": "danny", "role": "planner",
            "ip_address": None, "session_id": "s1",
            "idempotency_key": uuid.uuid4().hex, **over}


class TestWriteBackStore:
    @pytest.fixture
    def store(self, tmp_path):
        from app.bi.writeback import WriteBack
        return WriteBack(tmp_path / "writeback.db", "default")

    def test_annotation_is_appended_and_attributed(self, store):
        row = store.annotate(_annotation())
        assert row["author"] == "danny" and row["role"] == "planner"
        assert row["replayed"] is False
        assert [a["id"] for a in store.annotations()] == [row["id"]]

    def test_repeated_key_returns_the_same_row(self, store):
        rec = _annotation()
        first = store.annotate(rec)
        second = store.annotate(rec)
        assert first["id"] == second["id"]
        assert second["replayed"] is True
        assert len(store.annotations()) == 1

    def test_annotation_requires_a_body(self, store):
        with pytest.raises(ValueError, match="body is required"):
            store.annotate(_annotation(body="   "))

    def test_unknown_target_type_rejected(self, store):
        with pytest.raises(ValueError, match="target_type"):
            store.annotate(_annotation(target_type="nope"))

    def test_missing_idempotency_key_rejected(self, store):
        rec = _annotation()
        rec.pop("idempotency_key")
        with pytest.raises(ValueError, match="idempotency_key"):
            store.annotate(rec)

    def test_action_state_is_derived_from_events(self, store):
        action = store.propose(_proposal())
        assert store.state_of(action["action_id"]) == "pending"
        store.decide(action["action_id"], "approved", "Approved", "pending",
                     "danny", "planner", None, "s1", uuid.uuid4().hex)
        assert store.state_of(action["action_id"]) == "approved"
        row = store.actions()[0]
        assert row["state"] == "approved"
        assert [e["event"] for e in row["history"]] == ["proposed", "approved"]

    def test_decision_requires_the_expected_state(self, store):
        from app.bi.writeback import Conflict
        action = store.propose(_proposal())
        with pytest.raises(Conflict, match="not rejected"):
            store.decide(action["action_id"], "approved", "ok", "rejected",
                         "danny", "planner", None, "s1", uuid.uuid4().hex)

    def test_action_cannot_be_decided_twice(self, store):
        from app.bi.writeback import Conflict
        action = store.propose(_proposal())
        store.decide(action["action_id"], "approved", "ok", "pending",
                     "danny", "planner", None, "s1", uuid.uuid4().hex)
        with pytest.raises(Conflict, match="is approved, not pending"):
            store.decide(action["action_id"], "rejected", "no", "pending",
                         "danny", "planner", None, "s1", uuid.uuid4().hex)
        with pytest.raises(Conflict, match="already approved"):
            store.decide(action["action_id"], "rejected", "no", None,
                         "danny", "planner", None, "s1", uuid.uuid4().hex)

    def test_unknown_action_is_a_lookup_error(self, store):
        with pytest.raises(LookupError):
            store.decide("nope", "approved", "ok", "pending",
                         "danny", "planner", None, "s1", uuid.uuid4().hex)

    def test_proposal_requires_an_approver(self, store):
        with pytest.raises(ValueError, match="approver is required"):
            store.propose(_proposal(approver=""))

    def test_chain_verifies(self, store):
        store.annotate(_annotation())
        store.propose(_proposal())
        assert store.verify_chain() == (True, None)

    def test_rows_cannot_be_updated_or_deleted(self, store):
        store.annotate(_annotation())
        with sqlite3.connect(store.path) as con:
            with pytest.raises(sqlite3.IntegrityError, match="insert-only"):
                con.execute("UPDATE annotations SET body = 'tampered'")
            with pytest.raises(sqlite3.IntegrityError, match="insert-only"):
                con.execute("DELETE FROM annotations")


def _cause(status):
    """First cause of the requested status, so the tests do not hard-code the mart."""
    payload = requests.get(f"{BASE}/bi/diagnostics", headers=h("danny"), timeout=120).json()
    for series in payload["series"]:
        for cause in series["rca"]:
            if cause["status"] == status:
                return cause
    return None


class TestBiWriteBack:
    def test_annotate_and_read_back(self):
        key = uuid.uuid4().hex
        r = requests.post(f"{BASE}/bi/annotations", headers=h("danny"), timeout=60,
                          json={"target_type": "item", "target_id": "100001",
                                "body": "Supply constrained", "idempotency_key": key})
        assert r.status_code == 200, r.text
        row = r.json()
        assert row["author"] == "danny" and row["role"] == "planner"
        assert row["replayed"] is False
        listing = requests.get(f"{BASE}/bi/annotations?target_id=100001",
                               headers=h("danny"), timeout=60).json()
        assert any(a["idempotency_key"] == key for a in listing["annotations"])

    def test_annotation_is_idempotent(self):
        body = {"target_type": "segment", "target_id": "amazon", "body": "Peak driven",
                "idempotency_key": uuid.uuid4().hex}
        first = requests.post(f"{BASE}/bi/annotations", headers=h("danny"), json=body, timeout=60).json()
        second = requests.post(f"{BASE}/bi/annotations", headers=h("danny"), json=body, timeout=60).json()
        assert first["id"] == second["id"]
        assert second["replayed"] is True

    def test_annotation_requires_a_body(self):
        r = requests.post(f"{BASE}/bi/annotations", headers=h("danny"), timeout=60,
                          json={"target_type": "item", "target_id": "1", "body": "  ",
                                "idempotency_key": uuid.uuid4().hex})
        assert r.status_code == 422

    def test_auditor_cannot_write(self):
        r = requests.post(f"{BASE}/bi/annotations", headers=h("auditor"), timeout=60,
                          json={"target_type": "item", "target_id": "1", "body": "x",
                                "idempotency_key": uuid.uuid4().hex})
        assert r.status_code == 403

    def test_propose_refuses_a_hypothesis(self):
        cause = _cause("hypothesis")
        assert cause is not None, "the mart should contain at least one hypothesis"
        r = requests.post(f"{BASE}/bi/actions", headers=h("danny"), timeout=120,
                          json={"series": cause["series"], "month": cause["month"],
                                "note": "Act on a hypothesis",
                                "idempotency_key": uuid.uuid4().hex})
        assert r.status_code == 422
        assert "hypothesis" in r.json()["detail"]

    def test_propose_rejects_an_unknown_series(self):
        r = requests.post(f"{BASE}/bi/actions", headers=h("danny"), timeout=60,
                          json={"series": "nope", "month": "2026-10-01", "note": "x",
                                "idempotency_key": uuid.uuid4().hex})
        assert r.status_code == 422

    def test_propose_rejects_a_month_that_is_not_an_anomaly(self):
        r = requests.post(f"{BASE}/bi/actions", headers=h("danny"), timeout=120,
                          json={"series": "plan_qty", "month": "2026-04-01", "note": "x",
                                "idempotency_key": uuid.uuid4().hex})
        assert r.status_code == 404

    def test_propose_then_approve(self):
        cause = _cause("actionable")
        assert cause is not None, "the mart should contain at least one actionable cause"
        r = requests.post(f"{BASE}/bi/actions", headers=h("danny"), timeout=120,
                          json={"series": cause["series"], "month": cause["month"],
                                "note": "Investigate the leading driver",
                                "idempotency_key": uuid.uuid4().hex})
        assert r.status_code == 200, r.text
        action = r.json()
        assert action["event"] == "proposed"
        assert action["approver"] == cause["approver"]
        assert action["driver"] == cause["leading_driver"]

        d = requests.post(f"{BASE}/bi/actions/{action['action_id']}/decide", headers=h("danny"),
                          timeout=60, json={"decision": "approved", "note": "Approved in test",
                                            "expected_state": "pending",
                                            "idempotency_key": uuid.uuid4().hex})
        assert d.status_code == 200, d.text
        assert d.json()["event"] == "approved"

        listing = requests.get(f"{BASE}/bi/actions", headers=h("danny"), timeout=60).json()
        row = next(a for a in listing["actions"] if a["action_id"] == action["action_id"])
        assert row["state"] == "approved"
        assert [e["event"] for e in row["history"]] == ["proposed", "approved"]

    def test_decide_unknown_action_404(self):
        r = requests.post(f"{BASE}/bi/actions/nope/decide", headers=h("danny"), timeout=60,
                          json={"decision": "approved", "note": "x",
                                "idempotency_key": uuid.uuid4().hex})
        assert r.status_code == 404

    def test_decide_with_stale_state_409(self):
        cause = _cause("actionable")
        action = requests.post(f"{BASE}/bi/actions", headers=h("danny"), timeout=120,
                               json={"series": cause["series"], "month": cause["month"],
                                     "note": "Stale-state probe",
                                     "idempotency_key": uuid.uuid4().hex}).json()
        requests.post(f"{BASE}/bi/actions/{action['action_id']}/decide", headers=h("danny"),
                      timeout=60, json={"decision": "approved", "note": "first",
                                        "expected_state": "pending",
                                        "idempotency_key": uuid.uuid4().hex})
        r = requests.post(f"{BASE}/bi/actions/{action['action_id']}/decide", headers=h("danny"),
                          timeout=60, json={"decision": "rejected", "note": "second",
                                            "expected_state": "pending",
                                            "idempotency_key": uuid.uuid4().hex})
        assert r.status_code == 409

    def test_auditor_cannot_decide(self):
        r = requests.post(f"{BASE}/bi/actions/anything/decide", headers=h("auditor"), timeout=60,
                          json={"decision": "approved", "note": "x",
                                "idempotency_key": uuid.uuid4().hex})
        assert r.status_code == 403

    def test_chain_verifies(self):
        r = requests.get(f"{BASE}/bi/writeback/verify", headers=h("auditor"), timeout=60)
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True

    def test_annotations_unauth_401(self):
        assert requests.get(f"{BASE}/bi/annotations", timeout=30).status_code == 401


@pytest.fixture(scope="module")
def mart(tmp_path_factory):
    """One build for the whole class; a build reads the parquet artifacts and is slow."""
    from app.bi.export_mart import build_mart
    out = tmp_path_factory.mktemp("mart") / "mart.db"
    build_mart(out_path=out)
    return out


@pytest.fixture
def drifted(mart, tmp_path):
    """A private copy of the mart that a test may mutate without disturbing its siblings."""
    out = tmp_path / "mart.db"
    shutil.copy(mart, out)
    return out


@pytest.fixture(scope="module")
def empty_mart(tmp_path_factory):
    """A mart built with no ledger, so the optional tables are absent."""
    from app.bi.export_mart import build_mart
    base = tmp_path_factory.mktemp("empty")
    out = base / "mart.db"
    build_mart(store_dir=base / "store", out_path=out)
    return out


class TestMartContracts:
    def test_every_declared_table_names_a_consumer(self):
        from app.bi.contracts import CONTRACTS
        for table in CONTRACTS.values():
            assert table.consumers, f"{table.name} names no consumer"
            assert table.grain, f"{table.name} declares no grain"

    def test_declared_types_are_storage_classes(self):
        from app.bi.contracts import CONTRACTS, STORAGE_TYPES
        for table in CONTRACTS.values():
            for col in table.columns:
                assert col.type in STORAGE_TYPES, f"{table.name}.{col.name} is {col.type}"

    def test_column_names_are_unique(self):
        from app.bi.contracts import CONTRACTS
        for table in CONTRACTS.values():
            assert len(table.names) == len(set(table.names)), f"{table.name} repeats a column"

    def test_check_frame_rejects_a_missing_column(self):
        from app.bi.contracts import CONTRACTS, ContractError, check_frame
        with pytest.raises(ContractError, match="missing declared column"):
            check_frame(CONTRACTS["triage_items"], ["item_id", "product_id"])

    def test_check_frame_rejects_an_undeclared_column(self):
        from app.bi.contracts import CONTRACTS, ContractError, check_frame
        columns = CONTRACTS["cycle_summary"].names + ["surprise"]
        with pytest.raises(ContractError, match="undeclared column"):
            check_frame(CONTRACTS["cycle_summary"], columns)

    def test_numeric_column_rejects_text(self):
        import pandas as pd
        from app.bi.contracts import CONTRACTS, ContractError
        from app.bi.export_mart import _coerce
        with pytest.raises(ContractError, match="not numeric"):
            _coerce(CONTRACTS["triage_items"], pd.DataFrame({"item_id": ["abc"]}))

    def test_empty_tables_keep_their_declared_types(self, empty_mart):
        """The regression this closes: an empty frame used to produce an all-TEXT table."""
        with sqlite3.connect(empty_mart) as con:
            assert con.execute("SELECT COUNT(*) FROM commits").fetchone()[0] == 0
            commits = {r[1]: r[2] for r in con.execute("PRAGMA table_info(commits)")}
            overrides = {r[1]: r[2] for r in con.execute("PRAGMA table_info(overrides)")}
        assert commits["items"] == "INTEGER"
        assert commits["id"] == "INTEGER"
        assert overrides["item_id"] == "INTEGER"
        assert overrides["submitted_quantity"] == "REAL"

    def test_build_conforms_to_its_own_contract(self, mart):
        from app.bi.contracts import conformance
        with sqlite3.connect(mart) as con:
            report = conformance(con)
        assert report["ok"] is True
        assert report["summary"]["errors"] == 0

    def test_missing_column_is_an_error(self, drifted):
        from app.bi.contracts import conformance
        with sqlite3.connect(drifted) as con:
            con.execute("ALTER TABLE triage_items DROP COLUMN p70_gap_pct")
            report = conformance(con)
        assert report["ok"] is False
        problem = next(p for p in report["problems"] if p["kind"] == "missing_column")
        assert problem["column"] == "p70_gap_pct"
        assert "KPI cards" in problem["detail"]

    def test_type_mismatch_is_an_error(self, drifted):
        from app.bi.contracts import conformance
        with sqlite3.connect(drifted) as con:
            con.execute("ALTER TABLE cycle_summary RENAME COLUMN total_catalog_skus TO skus")
            con.execute("ALTER TABLE cycle_summary ADD COLUMN total_catalog_skus TEXT")
            report = conformance(con)
        assert report["ok"] is False
        assert any(p["kind"] == "type_mismatch" for p in report["problems"])

    def test_missing_table_is_an_error(self, drifted):
        from app.bi.contracts import conformance
        with sqlite3.connect(drifted) as con:
            con.execute("DROP TABLE safety_stock")
            report = conformance(con)
        assert report["ok"] is False
        problem = next(p for p in report["problems"] if p["kind"] == "missing_table")
        assert problem["table"] == "safety_stock"

    def test_undeclared_column_is_only_a_warning(self, drifted):
        from app.bi.contracts import conformance
        with sqlite3.connect(drifted) as con:
            con.execute("ALTER TABLE triage_items ADD COLUMN surprise TEXT")
            report = conformance(con)
        assert report["ok"] is True
        assert any(p["kind"] == "undeclared_column" and p["severity"] == "warning"
                   for p in report["problems"])

    def test_optional_table_may_be_absent(self, empty_mart):
        from app.bi.contracts import conformance
        with sqlite3.connect(empty_mart) as con:
            report = conformance(con)
        assert report["ok"] is True
        assert any(t["table"] == "fva_by_planner" and not t["present"] for t in report["tables"])

    def test_assert_conforms_raises_on_drift(self, drifted):
        from app.bi.contracts import ContractError, assert_conforms
        with sqlite3.connect(drifted) as con:
            con.execute("DROP TABLE cycle_summary")
            with pytest.raises(ContractError, match="violates its contract"):
                assert_conforms(con)


class TestBiContractsApi:
    def test_contract_document_and_conformance(self):
        r = requests.get(f"{BASE}/bi/contracts", headers=h("danny"), timeout=60)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["contract"]["summary"]["tables"] == 12
        names = {t["table"] for t in body["contract"]["tables"]}
        assert {"triage_items", "cycle_summary", "mart_meta"} <= names
        assert body["conformance"]["ok"] is True
        assert body["conformance"]["summary"]["errors"] == 0

    def test_every_table_names_its_consumers(self):
        body = requests.get(f"{BASE}/bi/contracts", headers=h("danny"), timeout=60).json()
        for table in body["contract"]["tables"]:
            assert table["consumers"], f"{table['table']} names no consumer"

    def test_contracts_unauth_401(self):
        assert requests.get(f"{BASE}/bi/contracts", timeout=30).status_code == 401


class TestBiFinOpsApi:
    """PR-8.3: the report attributes compute, and it does not invent a dollar figure."""

    def test_the_report_carries_attribution_storage_tiers_and_sprawl(self):
        r = requests.get(f"{BASE}/bi/finops", headers=h("danny"), timeout=60)
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body) == {"attribution", "storage", "thresholds", "sprawl"}
        assert body["storage"]["bytes"] > 0
        assert body["storage"]["tables"]
        assert body["thresholds"]["active"] is None
        assert body["thresholds"]["measured_usd"] is None
        assert "No cloud warehouse" in body["thresholds"]["reason"]

    def test_a_metric_query_is_attributed_to_the_planners_team(self):
        requests.get(f"{BASE}/bi/metrics", headers=h("danny"), timeout=60)
        body = requests.get(f"{BASE}/bi/finops", headers=h("danny"), timeout=60).json()
        assert "Demand Planning" in {b["team"] for b in body["attribution"]["by_team"]}
        assert "metrics" in {b["pipeline"] for b in body["attribution"]["by_pipeline"]}
        assert body["attribution"]["statements"] > 0

    def test_finops_unauth_401(self):
        assert requests.get(f"{BASE}/bi/finops", timeout=30).status_code == 401


class TestBiServingApi:
    """PR-3.1/3.2: the mart is measured against its freshness and latency SLAs."""

    def test_the_report_carries_freshness_latency_indexes_and_gaps(self):
        r = requests.get(f"{BASE}/bi/serving", headers=h("danny"), timeout=60)
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body) == {"freshness", "latency", "indexes", "gaps"}
        assert body["freshness"]["generated_at"]
        assert body["freshness"]["tables"]
        assert body["latency"]["workloads"]
        assert any("streaming ingestion" in gap for gap in body["gaps"])

    def test_the_mart_is_indexed_on_the_filter_columns(self):
        body = requests.get(f"{BASE}/bi/serving", headers=h("danny"), timeout=60).json()
        assert body["indexes"]["ok"] is True, body["indexes"]["missing"]
        assert "ix_triage_items_in_scope" in body["indexes"]["present"]

    def test_a_workload_with_no_samples_is_unmeasured_not_passing(self):
        body = requests.get(f"{BASE}/bi/serving", headers=h("danny"), timeout=60).json()
        for row in body["latency"]["workloads"]:
            if row["measured_p95_ms"] is None:
                assert row["ok"] is None

    def test_serving_unauth_401(self):
        assert requests.get(f"{BASE}/bi/serving", timeout=30).status_code == 401


class TestBiTransportApi:
    """PR-3.3: the same table is measured as JSON, Arrow IPC and Parquet."""

    def test_the_comparison_measures_every_format(self):
        r = requests.get(f"{BASE}/bi/transport", params={"tables": "cycle_summary"},
                         headers=h("danny"), timeout=120)
        assert r.status_code == 200, r.text
        body = r.json()
        row = body["tables"][0]
        assert row["table"] == "cycle_summary"
        for fmt in ("json", "arrow", "parquet"):
            assert row[f"{fmt}_bytes"] > 0
        assert body["totals"]["json_bytes"] == row["json_bytes"]

    def test_an_unknown_table_is_refused(self):
        r = requests.get(f"{BASE}/bi/transport", params={"tables": "not_a_table"},
                         headers=h("danny"), timeout=60)
        assert r.status_code == 422
        assert "Unknown table" in r.json()["detail"]

    def test_the_arrow_export_returns_the_media_type_and_the_bytes(self):
        r = requests.get(f"{BASE}/bi/export/columnar",
                         params={"table": "cycle_summary", "format": "arrow"},
                         headers=h("danny"), timeout=60)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "application/vnd.apache.arrow.stream"
        assert "cycle_summary.arrow" in r.headers["content-disposition"]
        assert len(r.content) > 0

    def test_the_parquet_export_is_readable(self):
        import io

        import pyarrow.parquet as pq
        r = requests.get(f"{BASE}/bi/export/columnar",
                         params={"table": "cycle_summary", "format": "parquet"},
                         headers=h("danny"), timeout=60)
        assert r.status_code == 200, r.text
        assert pq.read_table(io.BytesIO(r.content)).num_rows >= 1

    def test_the_build_metadata_is_not_exportable(self):
        r = requests.get(f"{BASE}/bi/export/columnar", params={"table": "mart_meta"},
                         headers=h("danny"), timeout=60)
        assert r.status_code == 422

    def test_an_unknown_format_is_refused(self):
        r = requests.get(f"{BASE}/bi/export/columnar",
                         params={"table": "cycle_summary", "format": "csv"},
                         headers=h("danny"), timeout=60)
        assert r.status_code == 422

    def test_transport_unauth_401(self):
        assert requests.get(f"{BASE}/bi/transport", timeout=30).status_code == 401


class TestTenantIsolation:
    """PR-8.1: one tenant's request must never return another tenant's rows."""

    @pytest.fixture(scope="class")
    def shared_mart(self, mart, tmp_path_factory):
        """One mart file holding two tenants' rows: the default tenant plus globex."""
        from app.bi.export_mart import build_mart
        out = tmp_path_factory.mktemp("tenants") / "mart.db"
        shutil.copy(mart, out)
        build_mart(out_path=out, tenant_id="globex")
        return out

    def test_a_second_build_appends_rather_than_replacing(self, shared_mart):
        from app.bi.tenancy import scoped
        with sqlite3.connect(shared_mart) as con:
            total = con.execute("SELECT COUNT(*) FROM triage_items").fetchone()[0]
        with scoped(shared_mart, "default") as con:
            first = con.execute("SELECT COUNT(*) FROM triage_items").fetchone()[0]
        assert first > 0
        assert total == 2 * first, "the second tenant's build replaced the first tenant's rows"

    def test_a_scoped_read_returns_zero_rows_from_another_tenant(self, shared_mart):
        from app.bi.tenancy import scoped
        with scoped(shared_mart, "default") as con:
            tenants = {r[0] for r in con.execute("SELECT DISTINCT tenant_id FROM triage_items")}
            foreign = con.execute(
                "SELECT COUNT(*) FROM triage_items WHERE tenant_id = 'globex'").fetchone()[0]
        assert tenants == {"default"}
        assert foreign == 0

    def test_the_dashboard_payload_is_scoped(self, shared_mart):
        from app.api.bi import dashboard_payload
        first = dashboard_payload(shared_mart, "default")
        second = dashboard_payload(shared_mart, "globex")
        assert first["cycle"]["tenant_id"] == "default"
        assert second["cycle"]["tenant_id"] == "globex"

    def test_the_cross_filter_breakdown_is_scoped(self, shared_mart):
        """An unscoped breakdown would double every count in a two-tenant mart."""
        from app.api.bi import _exceptions_breakdown
        from app.bi.tenancy import scoped
        with scoped(shared_mart, "default") as con:
            first = _exceptions_breakdown(con)
            expected = con.execute(
                "SELECT COUNT(*) FROM triage_items WHERE state = 'EXCEPTION_REVIEW'").fetchone()[0]
        assert first["totals"]["exceptions"] == expected
        assert sum(r["count"] for r in first["exceptions_by_channel"]) == expected

    def test_metric_values_are_scoped(self, shared_mart):
        """An unscoped aggregate would sum both tenants' cycle rows."""
        from app.bi.finops import QueryTag
        from app.bi.semantics import evaluate
        with sqlite3.connect(shared_mart) as con:
            unscoped = con.execute("SELECT SUM(in_scope_skus) FROM cycle_summary").fetchone()[0]
        first = evaluate(shared_mart, ["in_scope_skus"], "default",
                         QueryTag(team="Demand Planning", pipeline="metrics", workload="bi"))["in_scope_skus"]
        assert first is not None
        assert unscoped == 2 * first

    def test_the_cache_does_not_serve_one_tenant_to_another(self, shared_mart):
        from app.api.bi import _CACHE, cached_payload
        _CACHE.clear()
        first, first_cached = cached_payload(shared_mart, "default")
        second, second_cached = cached_payload(shared_mart, "globex")
        assert first["cycle"]["tenant_id"] == "default"
        assert second["cycle"]["tenant_id"] == "globex"
        assert (first_cached, second_cached) == (False, False)

    def test_a_table_without_a_tenant_column_is_refused(self, tmp_path):
        from app.bi.tenancy import TenantError, scoped
        path = tmp_path / "legacy.db"
        with sqlite3.connect(path) as con:
            con.execute("CREATE TABLE triage_items (item_id INTEGER)")
        with pytest.raises(TenantError, match="no tenant_id column"):
            with scoped(path, "acme"):
                pass

    def test_an_invalid_tenant_id_is_refused(self):
        from app.bi.tenancy import TenantError, normalise
        for bad in ("", "Acme", "acme'; DROP TABLE triage_items; --", "a" * 65):
            with pytest.raises(TenantError):
                normalise(bad)

    def test_a_scoped_connection_cannot_write(self, shared_mart):
        from app.bi.tenancy import scoped
        with scoped(shared_mart, "default") as con:
            with pytest.raises(sqlite3.Error):
                con.execute("INSERT INTO triage_items (tenant_id, item_id) VALUES ('globex', 1)")

    def test_writeback_reads_are_scoped(self, tmp_path):
        from app.bi.writeback import WriteBack
        path = tmp_path / "writeback.db"
        acme, globex = WriteBack(path, "acme"), WriteBack(path, "globex")
        acme.annotate({"cutoff": "2026-02-01", "target_type": "item", "target_id": "1",
                       "body": "acme note", "author": "danny", "role": "planner",
                       "idempotency_key": uuid.uuid4().hex})
        globex.annotate({"cutoff": "2026-02-01", "target_type": "item", "target_id": "1",
                         "body": "globex note", "author": "gina", "role": "planner",
                         "idempotency_key": uuid.uuid4().hex})
        assert [a["body"] for a in acme.annotations()] == ["acme note"]
        assert [a["body"] for a in globex.annotations()] == ["globex note"]
        assert acme.verify_chain() == (True, None)

    def test_one_tenant_cannot_decide_another_tenants_action(self, tmp_path):
        from app.bi.writeback import WriteBack
        path = tmp_path / "writeback.db"
        acme, globex = WriteBack(path, "acme"), WriteBack(path, "globex")
        action = acme.propose({"cutoff": "2026-02-01", "series": "plan_qty",
                               "month": "2026-09-01", "driver": "amazon",
                               "approver": "Head of Demand Planning", "note": "investigate",
                               "actor": "danny", "role": "planner",
                               "idempotency_key": uuid.uuid4().hex})
        assert globex.state_of(action["action_id"]) is None
        with pytest.raises(LookupError):
            globex.decide(action["action_id"], "approved", "ok", "pending",
                          "gina", "planner", None, "s1", uuid.uuid4().hex)

    def test_a_pre_tenancy_row_still_verifies(self, tmp_path):
        """Rows hashed before tenant scoping are bound to the default tenant and still verify."""
        from app.bi.writeback import GENESIS, WriteBack, _ANNOTATION_FIELDS, _row_hash
        path = tmp_path / "legacy.db"
        legacy_fields = [f for f in _ANNOTATION_FIELDS if f != "tenant_id"]
        rec = {"timestamp": "2026-01-01T00:00:00Z", "cutoff": "2026-02-01",
               "target_type": "item", "target_id": "1", "body": "legacy",
               "author": "danny", "role": "planner", "ip_address": None,
               "session_id": None, "idempotency_key": "k1"}
        with sqlite3.connect(path) as con:
            con.execute("""CREATE TABLE annotations (
                id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
                cutoff TEXT NOT NULL, target_type TEXT NOT NULL, target_id TEXT NOT NULL,
                body TEXT NOT NULL, author TEXT NOT NULL, role TEXT NOT NULL,
                ip_address TEXT, session_id TEXT, idempotency_key TEXT NOT NULL UNIQUE,
                prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL)""")
            con.execute(
                f"INSERT INTO annotations ({', '.join(legacy_fields)}, prev_hash, row_hash) "
                f"VALUES ({', '.join('?' for _ in legacy_fields)}, ?, ?)",
                [rec[f] for f in legacy_fields] + [GENESIS, _row_hash(GENESIS, rec)])
        store = WriteBack(path, "default")
        assert store.verify_chain() == (True, None)
        assert [a["body"] for a in store.annotations()] == ["legacy"]


class TestTenantSession:
    """The tenant is derived from the verified session, never from the request."""

    @pytest.fixture(scope="class")
    def rebuilt(self):
        r = requests.post(f"{BASE}/bi/export", headers=h("admin"), timeout=300)
        assert r.status_code == 200, r.text
        return r.json()

    def test_login_returns_the_tenant(self):
        r = requests.post(f"{BASE}/auth/login", timeout=30,
                          json={"username": "danny", "password": CREDS["danny"]})
        assert r.status_code == 200, r.text
        assert r.json()["tenant"] == "default"

    def test_the_export_reports_the_session_tenant(self, rebuilt):
        assert rebuilt["tenant"] == "default"

    def test_the_dashboard_carries_the_session_tenant(self, rebuilt):
        d = requests.get(f"{BASE}/bi/dashboard", headers=h("danny"), timeout=120).json()
        assert d["cycle"]["tenant_id"] == "default"

    def test_a_tenant_query_parameter_is_ignored(self, rebuilt):
        """A caller cannot ask to be served as another tenant."""
        d = requests.get(f"{BASE}/bi/dashboard?tenant_id=globex", headers=h("danny"),
                         timeout=120).json()
        assert d["cycle"]["tenant_id"] == "default"


class TestCrossFilter:
    """Chart-to-chart filtering over the exception breakdowns."""

    @pytest.fixture(scope="class")
    def breakdown(self, mart):
        from app.api.bi import _exceptions_breakdown
        from app.bi.tenancy import scoped
        with scoped(mart, "default") as con:
            return _exceptions_breakdown(con)

    def test_the_unfiltered_breakdown_matches_the_dashboard(self, mart, breakdown):
        from app.api.bi import dashboard_payload
        payload = dashboard_payload(mart, "default")
        for key in ("exceptions_by_channel", "var_by_risk_type", "top_exceptions"):
            assert breakdown[key] == payload[key]

    def test_the_top_list_stays_capped_at_ten(self, breakdown):
        assert len(breakdown["top_exceptions"]) <= 10

    def test_a_channel_filter_narrows_the_risk_breakdown(self, mart, breakdown):
        from app.api.bi import _exceptions_breakdown
        from app.bi.tenancy import scoped
        channel = breakdown["exceptions_by_channel"][0]["channel"]
        with scoped(mart, "default") as con:
            filtered = _exceptions_breakdown(con, channel=channel)
        assert filtered["filter"] == {"channel": channel, "risk_type": None}
        assert all(r["channel"] == channel for r in filtered["top_exceptions"])
        assert sum(r["var_gbp"] for r in filtered["var_by_risk_type"]) <= sum(
            r["var_gbp"] for r in breakdown["var_by_risk_type"])

    def test_a_chart_does_not_filter_its_own_dimension(self, mart, breakdown):
        """Picking a channel must leave every channel visible so the pick can change."""
        from app.api.bi import _exceptions_breakdown
        from app.bi.tenancy import scoped
        channel = breakdown["exceptions_by_channel"][0]["channel"]
        with scoped(mart, "default") as con:
            filtered = _exceptions_breakdown(con, channel=channel)
        assert filtered["exceptions_by_channel"] == breakdown["exceptions_by_channel"]

    def test_a_risk_filter_narrows_the_channel_breakdown(self, mart, breakdown):
        from app.api.bi import _exceptions_breakdown
        from app.bi.tenancy import scoped
        risk = breakdown["var_by_risk_type"][0]["risk_type"]
        with scoped(mart, "default") as con:
            filtered = _exceptions_breakdown(con, risk_type=risk)
        assert filtered["var_by_risk_type"] == breakdown["var_by_risk_type"]
        assert all(r["risk_type"] == risk for r in filtered["top_exceptions"])
        assert sum(r["count"] for r in filtered["exceptions_by_channel"]) <= sum(
            r["count"] for r in breakdown["exceptions_by_channel"])

    def test_both_filters_apply_to_the_top_list(self, mart, breakdown):
        from app.api.bi import _exceptions_breakdown
        from app.bi.tenancy import scoped
        channel = breakdown["exceptions_by_channel"][0]["channel"]
        risk = breakdown["var_by_risk_type"][0]["risk_type"]
        with scoped(mart, "default") as con:
            filtered = _exceptions_breakdown(con, channel=channel, risk_type=risk)
        assert all(r["channel"] == channel and r["risk_type"] == risk
                   for r in filtered["top_exceptions"])

    def test_the_totals_match_the_unfiltered_breakdown(self, breakdown):
        assert breakdown["totals"]["exceptions"] == sum(
            r["count"] for r in breakdown["exceptions_by_channel"])
        assert breakdown["totals"]["var_gbp"] == pytest.approx(
            sum(r["var_gbp"] for r in breakdown["var_by_risk_type"]))

    def test_the_totals_follow_the_filter(self, mart, breakdown):
        from app.api.bi import _exceptions_breakdown
        from app.bi.tenancy import scoped
        channel = breakdown["exceptions_by_channel"][0]["channel"]
        with scoped(mart, "default") as con:
            filtered = _exceptions_breakdown(con, channel=channel)
        assert filtered["totals"]["exceptions"] == next(
            r["count"] for r in breakdown["exceptions_by_channel"] if r["channel"] == channel)
