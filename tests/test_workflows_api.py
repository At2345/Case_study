"""Workflow API through the real app (auth, roles, tenancy) plus the service's scheduler hook and notifications."""
from datetime import datetime, timedelta, timezone

from app.orchestration.scheduler import Scheduler
from app.workflows.service import WorkflowService
from tests.test_api import auth, env  # noqa: F401  (fixture reuse)
from tests.test_workflows import DEEP, MILD, rescue_playbook

T0 = datetime(2026, 11, 25, 9, 0, tzinfo=timezone.utc)


def spec(**overrides):
    return rescue_playbook(**overrides).model_dump()


def approval_spec(role="admin"):
    return {
        "id": "ask", "name": "Ask first", "trigger": "start",
        "nodes": [{"id": "start", "type": "trigger.manual"},
                  {"id": "ok", "type": "control.approval", "params": {"approver_role": role, "message": "Go?"}},
                  {"id": "tell", "type": "action.notify", "params": {"message": "approved"}}],
        "edges": [{"source": "start", "target": "ok"}, {"source": "ok", "target": "tell", "port": "approved"}],
    }


class FakeNotes:
    def __init__(self):
        self.rows = []

    def add(self, roles, kind, title, body, link="", run_id=""):
        self.rows.append({"roles": roles, "kind": kind, "title": title, "body": body, "run_id": run_id})
        return str(len(self.rows))


# ---------------------------------------------------------------- API
def test_catalogue_and_validation(env):  # noqa: F811
    client, _, _ = env
    h = auth(client, "ada")  # auditors can read
    types = {t["name"] for t in client.get("/api/v1/workflows/node-types", headers=h).json()["node_types"]}
    assert "action.change_price" in types and "platform.underwrite" not in types
    ok = client.post("/api/v1/workflows/validate", headers=h, json=spec()).json()
    assert ok == {"valid": True, "errors": []}
    bad = client.post("/api/v1/workflows/validate", headers=h,
                      json={"id": "x", "name": "x", "trigger": "s", "nodes": [{"id": "s", "type": "nope"}]}).json()
    assert bad["valid"] is False and "unknown node type nope" in bad["errors"][0]
    assert client.get("/api/v1/workflows/node-types").status_code == 401


def test_save_versions_and_roles(env):  # noqa: F811
    client, _, _ = env
    planner, auditor = auth(client), auth(client, "ada")
    assert client.post("/api/v1/workflows", headers=auditor, json=spec()).status_code == 403
    r1 = client.post("/api/v1/workflows", headers=planner, json=spec(version=7))
    assert r1.status_code == 201 and r1.json()["version"] == 1  # client version is ignored
    r2 = client.post("/api/v1/workflows", headers=planner, json=spec(name="Rescue v2"))
    assert r2.json()["version"] == 2
    assert client.get("/api/v1/workflows", headers=auditor).json()["workflows"] == \
        [{"id": "rescue-de", "name": "Rescue v2", "version": 2}]
    assert client.get("/api/v1/workflows/rescue-de?version=1", headers=planner).json()["name"] == \
        "Rescue slow batches in DE"
    assert client.get("/api/v1/workflows/missing", headers=planner).status_code == 404
    assert client.get("/api/v1/workflows/..%2F..%2Fusers", headers=planner).status_code in (404, 422)

    locked = {"id": "uw", "name": "uw", "trigger": "s",
              "nodes": [{"id": "s", "type": "trigger.manual"},
                        {"id": "u", "type": "platform.underwrite", "params": {"po_id": "P", "po_value_eur": 1}}],
              "edges": [{"source": "s", "target": "u"}]}
    r = client.post("/api/v1/workflows", headers=planner, json=locked)
    assert r.status_code == 422 and "platform-only" in r.json()["detail"]


def test_run_gate_approval_flow(env):  # noqa: F811
    client, _, _ = env
    planner, admin, auditor = auth(client), auth(client, "root"), auth(client, "ada")
    client.post("/api/v1/workflows", headers=planner, json=spec())

    run = client.post("/api/v1/workflows/rescue-de/runs", headers=planner, json={"payload": DEEP})
    assert run.status_code == 201
    state = run.json()
    assert state["status"] == "WAITING_APPROVAL" and state["started_by"] == "danny"
    run_id = state["run_id"]

    listed = client.get("/api/v1/workflow-runs?status=WAITING_APPROVAL", headers=auditor).json()["runs"]
    assert [r["run_id"] for r in listed] == [run_id] and "steps" not in listed[0]

    assert client.post(f"/api/v1/workflow-runs/{run_id}/approve", headers=auditor,
                       json={"node": "transfer", "approved": True}).status_code == 403
    wrong = client.post(f"/api/v1/workflow-runs/{run_id}/approve", headers=planner,
                        json={"node": "cut_price", "approved": True})
    assert wrong.status_code == 409
    done = client.post(f"/api/v1/workflow-runs/{run_id}/approve", headers=admin,
                       json={"node": "transfer", "approved": True, "note": "FR has capacity"}).json()
    assert done["status"] == "SUCCEEDED" and done["outputs"]["transfer"]["units"] == 600
    assert client.get(f"/api/v1/workflow-runs/{run_id}", headers=auditor).json()["status"] == "SUCCEEDED"
    assert client.get("/api/v1/workflow-runs/nope", headers=auditor).status_code == 404


def test_run_errors_and_admin_advance(env):  # noqa: F811
    client, _, _ = env
    planner, admin = auth(client), auth(client, "root")
    assert client.post("/api/v1/workflows/missing/runs", headers=planner, json={}).status_code == 404
    client.post("/api/v1/workflows", headers=planner, json=spec())
    state = client.post("/api/v1/workflows/rescue-de/runs", headers=planner,
                        json={"payload": MILD, "dry_run": True}).json()
    assert state["status"] == "WAITING" and state["spent_eur"] == 0.0
    assert client.post(f"/api/v1/workflow-runs/{state['run_id']}/advance", headers=planner).status_code == 403
    still = client.post(f"/api/v1/workflow-runs/{state['run_id']}/advance", headers=admin).json()
    assert still["status"] == "WAITING"  # wait has 14 days left


def test_approval_node_respects_approver_role(env):  # noqa: F811
    client, _, _ = env
    planner, admin = auth(client), auth(client, "root")
    client.post("/api/v1/workflows", headers=planner, json=approval_spec("admin"))
    run_id = client.post("/api/v1/workflows/ask/runs", headers=planner, json={}).json()["run_id"]
    denied = client.post(f"/api/v1/workflow-runs/{run_id}/approve", headers=planner,
                         json={"node": "ok", "approved": True})
    assert denied.status_code == 403 and "may not approve" in denied.json()["detail"]
    ok = client.post(f"/api/v1/workflow-runs/{run_id}/approve", headers=admin, json={"node": "ok", "approved": True})
    assert ok.json()["status"] == "SUCCEEDED"


# ---------------------------------------------------------------- service: scheduler hook, notifications, tenancy
def test_scheduler_hook_resumes_expired_waits(tmp_path):
    notes = FakeNotes()
    svc = WorkflowService(tmp_path, notes=notes)
    svc.save("acme", spec())
    waiting = svc.start("acme", "rescue-de", MILD, "danny", now=T0)
    assert waiting["status"] == "WAITING"

    assert svc.resume_due(now=T0 + timedelta(days=13)) == []
    assert svc.get_run("acme", waiting["run_id"])["status"] == "WAITING"

    class NoSchedules:
        def due(self, now):
            return []

    class NoJobs:
        def busy(self):
            return False

    sched = Scheduler(NoSchedules(), NoJobs(), hooks=[svc.resume_due, lambda now: 1 / 0])
    assert sched.tick(now=T0 + timedelta(days=14)) == []  # failing hook is logged, not raised
    assert svc.get_run("acme", waiting["run_id"])["status"] == "SUCCEEDED"


def test_notifications_for_approval_and_failure(tmp_path):
    notes = FakeNotes()
    svc = WorkflowService(tmp_path, notes=notes)
    svc.save("acme", spec())
    svc.save("acme", approval_spec("planner"))

    gate = svc.start("acme", "rescue-de", DEEP, "danny", now=T0)
    node = svc.start("acme", "ask", {}, "danny", now=T0)
    failed = svc.start("acme", "rescue-de", {"sku": "X"}, "danny", now=T0)
    kinds = [(n["kind"], n["roles"], n["run_id"]) for n in notes.rows]
    assert kinds == [("approval", ["planner", "admin"], gate["run_id"]),
                     ("approval", ["admin", "planner"], node["run_id"]),
                     ("error", ["planner", "admin"], failed["run_id"])]


def test_four_eyes_and_tenant_isolation(tmp_path):
    svc = WorkflowService(tmp_path, four_eyes=True)
    svc.save("acme", spec())
    state = svc.start("acme", "rescue-de", DEEP, "danny", now=T0)
    planner = {"username": "danny", "role": "planner"}
    try:
        svc.approve("acme", state["run_id"], "transfer", True, planner, now=T0)
        raise AssertionError("starter approved own gated action")
    except PermissionError:
        pass
    other = svc.approve("acme", state["run_id"], "transfer", True, {"username": "mia", "role": "planner"}, now=T0)
    assert other["status"] == "SUCCEEDED"

    assert svc.list("globex") == []
    try:
        svc.get_run("globex", state["run_id"])
        raise AssertionError("run leaked across tenants")
    except KeyError:
        pass
    try:
        svc.list("../acme")
        raise AssertionError("bad tenant id accepted")
    except ValueError:
        pass
