"""Decision-outcome ledger: automatic decision rows, outcomes, performance ranking, integrity, API and MCP."""
import sqlite3
from datetime import timedelta

import pytest

from app.workflows.ledger import DecisionLedger
from app.workflows.mcp_server import PlaybookMcpServer
from app.workflows.runners import LocalRunner
from app.workflows.service import WorkflowService
from tests.test_api import auth, env  # noqa: F401  (fixture reuse)
from tests.test_workflows import DEEP, MILD, T0, rescue_playbook

ADMIN = {"username": "root", "role": "admin"}


@pytest.fixture
def svc(tmp_path):
    s = WorkflowService(tmp_path)
    s.save("acme", rescue_playbook().model_dump())
    return s


def rows(svc, run_id):
    return [(r["node_id"], r["kind"], r["status"], r["cost_eur"], r["actor"]) for r in svc.decisions("acme", run_id=run_id)]


# ---------------------------------------------------------------- decisions are recorded automatically
def test_gate_approval_run_is_recorded(svc):
    run = svc.start("acme", "rescue-de", DEEP, "danny", now=T0)
    assert rows(svc, run["run_id"]) == [
        ("propose", "proposal", "ok", 0.0, "danny"),
        ("transfer", "approval_request", "awaiting_approval", 0.0, "danny"),
    ]
    svc.approve("acme", run["run_id"], "transfer", True, ADMIN, now=T0)
    assert rows(svc, run["run_id"])[2:] == [
        ("transfer", "human_decision", "approved", 0.0, "root"),
        ("transfer", "action", "ok", 900.0, "danny"),
    ]
    action = svc.decisions("acme", run_id=run["run_id"])[-1]
    assert action["sku"] == "MAT-BLUE" and action["reversible"] is False and action["dry_run"] is False
    assert action["detail"]["subject"]["units"] == 600


def test_waits_and_reversible_actions(svc):
    run = svc.start("acme", "rescue-de", MILD, "danny", now=T0)
    assert [r[0] for r in rows(svc, run["run_id"])] == ["propose", "cut_price"]
    svc.advance("acme", run["run_id"], now=T0 + timedelta(days=14))
    assert rows(svc, run["run_id"])[-1] == ("tell", "action", "ok", 0.0, "danny")
    assert svc.decisions("acme", run_id=run["run_id"])[1]["reversible"] is True


def test_recording_is_idempotent(svc):
    run = svc.start("acme", "rescue-de", DEEP, "danny", now=T0, run_id="retry1", executor="temporal")
    again = svc.start("acme", "rescue-de", DEEP, "danny", now=T0, run_id="retry1", executor="temporal")
    assert again["run_id"] == run["run_id"]
    assert len(svc.decisions("acme", run_id="retry1")) == 2
    assert svc.reconcile_ledger() == 0


def test_reconcile_backfills_after_ledger_outage(svc, monkeypatch):
    def broken(*a, **k):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(svc.ledger, "record_run", broken)
    run = svc.start("acme", "rescue-de", DEEP, "danny", now=T0)  # run still succeeds
    assert run["status"] == "WAITING_APPROVAL"
    monkeypatch.undo()
    assert svc.decisions("acme", run_id=run["run_id"]) == []
    assert svc.reconcile_ledger() == 2
    assert len(svc.decisions("acme", run_id=run["run_id"])) == 2


# ---------------------------------------------------------------- outcomes and performance
def test_outcomes_drive_performance_ranking(svc):
    svc.save("acme", rescue_playbook(id="rescue-fr", name="Rescue FR").model_dump())
    svc.save("acme", rescue_playbook(id="rescue-it", name="Rescue IT").model_dump())

    de = svc.start("acme", "rescue-de", DEEP, "danny", now=T0)
    svc.approve("acme", de["run_id"], "transfer", True, ADMIN, now=T0)          # spends 900
    fr = svc.start("acme", "rescue-fr", MILD, "danny", now=T0)                  # spends 160
    it = svc.start("acme", "rescue-it", DEEP, "danny", now=T0)
    svc.approve("acme", it["run_id"], "transfer", False, ADMIN, now=T0)         # rejected, spends 0
    dry = svc.start("acme", "rescue-de", DEEP, "danny", now=T0, dry_run=True)

    svc.record_outcome("acme", de["run_id"], "loss_avoided_eur", 1200, "danny", sku="MAT-BLUE")
    svc.record_outcome("acme", fr["run_id"], "loss_avoided_eur", 400, "danny")
    svc.record_outcome("acme", fr["run_id"], "units_sold", 180, "danny")
    with pytest.raises(ValueError, match="dry runs"):
        svc.record_outcome("acme", dry["run_id"], "loss_avoided_eur", 9999, "danny")
    with pytest.raises(ValueError, match="did not run"):
        svc.record_outcome("acme", de["run_id"], "loss_avoided_eur", 1, "danny", node_id="cut_price")
    with pytest.raises(ValueError, match="metric"):
        svc.record_outcome("acme", de["run_id"], "vibes", 1, "danny")
    with pytest.raises(KeyError):
        svc.record_outcome("acme", "missing", "loss_avoided_eur", 1, "danny")

    perf = {p["workflow_id"]: p for p in svc.performance("acme")}
    assert [p["workflow_id"] for p in svc.performance("acme")] == ["rescue-fr", "rescue-de", "rescue-it"]
    assert perf["rescue-fr"]["value_per_eur"] == 2.5 and perf["rescue-fr"]["metrics"]["units_sold"] == 180
    assert perf["rescue-de"] == {**perf["rescue-de"], "runs": 1, "spent_eur": 900.0, "value_eur": 1200.0,
                                 "net_eur": 300.0, "value_per_eur": 1.33, "approval_rate": 1.0, "measured_runs": 1}
    assert perf["rescue-it"]["value_per_eur"] is None and perf["rescue-it"]["approval_rate"] == 0.0
    assert svc.performance("acme", "rescue-de")[0]["workflow_id"] == "rescue-de"
    assert svc.performance("globex") == []


# ---------------------------------------------------------------- integrity
def test_ledger_is_insert_only_and_tamper_evident(svc):
    run = svc.start("acme", "rescue-de", DEEP, "danny", now=T0)
    svc.approve("acme", run["run_id"], "transfer", True, ADMIN, now=T0)
    svc.record_outcome("acme", run["run_id"], "loss_avoided_eur", 1200, "danny")
    assert svc.ledger.verify() == {"ok": True, "first_bad": None, "decisions": 4, "outcomes": 1}

    con = sqlite3.connect(svc.ledger.path)
    with pytest.raises(sqlite3.DatabaseError, match="insert-only"):
        con.execute("UPDATE decisions SET cost_eur = 0 WHERE node_id = 'transfer'")
    with pytest.raises(sqlite3.DatabaseError, match="insert-only"):
        con.execute("DELETE FROM outcomes")
    con.execute("DROP TRIGGER decisions_no_update")  # an attacker with file access...
    con.execute("UPDATE decisions SET cost_eur = 1 WHERE status = 'ok' AND node_id = 'transfer'")
    con.commit()
    con.close()
    report = svc.ledger.verify()
    assert report["ok"] is False and report["first_bad"]["table"] == "decisions"


# ---------------------------------------------------------------- API and MCP
def test_ledger_api(env):  # noqa: F811
    client, _, _ = env
    planner, admin, auditor = auth(client), auth(client, "root"), auth(client, "ada")
    client.post("/api/v1/workflows", headers=planner, json=rescue_playbook().model_dump())
    run = client.post("/api/v1/workflows/rescue-de/runs", headers=planner, json={"payload": DEEP}).json()
    client.post(f"/api/v1/workflow-runs/{run['run_id']}/approve", headers=admin, json={"node": "transfer", "approved": True})

    ledger = client.get(f"/api/v1/workflow-runs/{run['run_id']}/decisions", headers=auditor).json()
    assert [d["kind"] for d in ledger["decisions"]] == ["proposal", "approval_request", "human_decision", "action"]
    assert client.get("/api/v1/workflow-runs/nope/decisions", headers=auditor).status_code == 404

    url = f"/api/v1/workflow-runs/{run['run_id']}/outcomes"
    assert client.post(url, headers=auditor, json={"metric": "loss_avoided_eur", "value": 1}).status_code == 403
    assert client.post(url, headers=planner, json={"metric": "vibes", "value": 1}).status_code == 422
    made = client.post(url, headers=planner, json={"metric": "loss_avoided_eur", "value": 1800, "sku": "MAT-BLUE"})
    assert made.status_code == 201

    key = client.post("/api/v1/admin/api-keys", headers=admin, json={"name": "erp-sync", "role": "planner"}).json()["key"]
    via_key = client.post(url, headers={"Authorization": f"Bearer {key}"}, json={"metric": "units_sold", "value": 550})
    assert via_key.status_code == 201
    outs = client.get(f"/api/v1/workflow-runs/{run['run_id']}/decisions", headers=planner).json()["outcomes"]
    assert [(o["metric"], o["source"], o["recorded_by"]) for o in outs] == [
        ("loss_avoided_eur", "user", "danny"), ("units_sold", "api_key", "apikey:erp-sync")]

    perf = client.get("/api/v1/workflows-performance", headers={"Authorization": f"Bearer {key}"}).json()["playbooks"]
    assert perf[0]["workflow_id"] == "rescue-de" and perf[0]["value_per_eur"] == 2.0

    dry = client.post("/api/v1/workflows/rescue-de/runs", headers=planner, json={"payload": DEEP, "dry_run": True}).json()
    assert client.post(f"/api/v1/workflow-runs/{dry['run_id']}/outcomes", headers=planner,
                       json={"metric": "loss_avoided_eur", "value": 5}).status_code == 422

    assert client.get("/api/v1/workflow-ledger/verify", headers=planner).status_code == 403
    assert client.get("/api/v1/workflow-ledger/verify", headers=auditor).json()["ok"] is True
    assert client.post("/api/v1/admin/workflow-ledger/reconcile", headers=admin).json() == {"added": 0}


def test_mcp_outcome_and_performance_tools(svc):
    server = PlaybookMcpServer(svc, LocalRunner(svc), allow_approvals=True)
    user = {"username": "agent", "role": "planner", "tenant": "acme"}

    def call(name, args):
        return server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": name, "arguments": args}}, user)["result"]

    run = call("start_run", {"playbook_id": "rescue-de", "payload": MILD, "dry_run": False})["structuredContent"]
    out = call("record_outcome", {"run_id": run["run_id"], "metric": "loss_avoided_eur", "value": 320})
    assert out["isError"] is False
    perf = call("get_playbook_performance", {})["structuredContent"]["playbooks"]
    assert perf[0]["value_per_eur"] == 2.0  # 320 / 160
    assert svc.outcomes("acme", run_id=run["run_id"])[0]["source"] == "mcp"


def test_ledger_file_location(tmp_path):
    svc = WorkflowService(tmp_path / "workflows")
    assert svc.ledger.path == tmp_path / "workflows" / "decisions.db"
    assert isinstance(DecisionLedger(tmp_path / "x.db").verify(), dict)
