"""Closed loop: playbooks measure their own outcome and write it to the decision-outcome ledger."""
import json
import sqlite3
from datetime import timedelta

import pytest

from app.workflows import webhooks
from app.workflows.builtin import MeasureOutcomeParams, measure_outcome
from app.workflows.ledger import DecisionLedger, _row_hash, GENESIS
from app.workflows.service import WorkflowService
from tests.test_workflows import MILD, T0


def closed_loop_playbook(wf_id="rescue-loop"):
    """Price cut, wait 60 days, fetch current stock via n8n, measure against the proposal's baseline."""
    return {
        "id": wf_id, "name": "Rescue with measured outcome", "trigger": "start", "budget_eur": 2000,
        "nodes": [
            {"id": "start", "type": "trigger.sell_through_gap"},
            {"id": "propose", "type": "agent.rescue_proposal", "params": {
                "sku": "$trigger.sku", "marketplace": "$trigger.marketplace", "units_on_hand": "$trigger.units_on_hand",
                "sell_through_gap": "$trigger.gap", "unit_landed_cost_eur": "$trigger.unit_cost",
                "days_to_trigger": "$trigger.days_to_trigger"}},
            {"id": "cut_price", "type": "action.change_price", "params": {
                "sku": "$nodes.propose.sku", "marketplace": "$nodes.propose.marketplace",
                "pct_change": "$nodes.propose.pct_change", "estimated_cost_eur": "$nodes.propose.estimated_cost_eur"}},
            {"id": "wait", "type": "logic.wait", "params": {"days": 60}},
            {"id": "fetch", "type": "action.n8n_webhook", "params": {"url": "https://n8n.example.com/webhook/stock"}},
            {"id": "measure", "type": "agent.measure_outcome", "params": {
                "sku": "$nodes.propose.sku", "baseline_units_at_risk": "$nodes.propose.units_at_risk",
                "units_unsold": "$nodes.fetch.response.units_on_hand", "unit_landed_cost_eur": "$trigger.unit_cost",
                "recovery_rate": 0.25, "units_sold": "$nodes.fetch.response.units_sold"}},
        ],
        "edges": [{"source": a, "target": b} for a, b in
                  (("start", "propose"), ("propose", "cut_price"), ("cut_price", "wait"), ("wait", "fetch"),
                   ("fetch", "measure"))],
    }


@pytest.fixture
def svc(tmp_path, monkeypatch):
    stock = {"units_on_hand": 50, "units_sold": 950}

    def fake_post(url, body, idempotency_key, secret_ref=None, timeout_s=10.0):
        return {"status_code": 200, "response": dict(stock), "truncated": False}

    monkeypatch.setattr(webhooks, "post_json", fake_post)
    s = WorkflowService(tmp_path)
    s.save("acme", closed_loop_playbook())
    s.stock = stock  # lets a test change what the "ERP" returns
    return s


def test_measurement_math():
    out = measure_outcome(MeasureOutcomeParams(sku="A", baseline_units_at_risk=200, units_unsold=50,
                                               unit_landed_cost_eur=8, recovery_rate=0.25, units_sold=950))
    assert out["avoided_units"] == 150 and out["loss_avoided_eur"] == 900.0  # 150 x 8 x 0.75
    assert out["outcomes"] == [{"metric": "loss_avoided_eur", "value": 900.0, "sku": "A"},
                               {"metric": "units_sold", "value": 950, "sku": "A"}]
    worse = measure_outcome(MeasureOutcomeParams(sku="A", baseline_units_at_risk=100, units_unsold=130,
                                                 unit_landed_cost_eur=10))
    assert worse["loss_avoided_eur"] == -300.0 and len(worse["outcomes"]) == 1


def test_closed_loop_writes_outcome_and_ranks_playbook(svc):
    run = svc.start("acme", "rescue-loop", MILD, "danny", now=T0)
    assert run["status"] == "WAITING" and svc.outcomes("acme", run_id=run["run_id"]) == []

    done = svc.advance("acme", run["run_id"], now=T0 + timedelta(days=60))
    assert done["status"] == "SUCCEEDED"
    assert done["outputs"]["measure"]["loss_avoided_eur"] == 900.0  # (200 - 50) x 8 x 0.75

    outs = svc.outcomes("acme", run_id=run["run_id"])
    assert [(o["metric"], o["value"], o["source"], o["recorded_by"], o["node_id"]) for o in outs] == [
        ("loss_avoided_eur", 900.0, "playbook", "playbook:measure", "measure"),
        ("units_sold", 950.0, "playbook", "playbook:measure", "measure")]
    kinds = [d["kind"] for d in svc.decisions("acme", run_id=run["run_id"])]
    assert kinds == ["proposal", "action", "action", "measurement"]

    perf = svc.performance("acme")[0]
    assert perf["spent_eur"] == 160.0 and perf["value_eur"] == 900.0 and perf["value_per_eur"] == 5.62
    assert perf["measured_runs"] == 1 and perf["metrics"]["units_sold"] == 950.0


def test_measured_outcomes_are_written_once(svc):
    run = svc.start("acme", "rescue-loop", MILD, "danny", now=T0)
    svc.advance("acme", run["run_id"], now=T0 + timedelta(days=60))
    state = svc.get_run("acme", run["run_id"])
    wf = svc.get("acme", "rescue-loop")
    from app.workflows.ledger import node_info_for
    assert svc.ledger.record_run(state, node_info_for(wf, svc.registry)) == 0  # retry / repeated save
    assert svc.reconcile_ledger() == 0
    assert len(svc.outcomes("acme", run_id=run["run_id"])) == 2
    assert svc.ledger.verify()["ok"] is True


def test_dry_runs_measure_but_never_record(svc, monkeypatch):
    def must_not_call(*a, **k):
        raise AssertionError("dry run called n8n")

    monkeypatch.setattr(webhooks, "post_json", must_not_call)
    spec = closed_loop_playbook("rescue-dry")
    spec["nodes"][4]["params"]["dry_run_response"] = {"units_on_hand": 120, "units_sold": 880}
    svc.save("acme", spec)
    run = svc.start("acme", "rescue-dry", MILD, "danny", now=T0, dry_run=True)
    done = svc.advance("acme", run["run_id"], now=T0 + timedelta(days=60))
    assert done["status"] == "SUCCEEDED"
    assert done["outputs"]["fetch"] == {"simulated": True, "url": "https://n8n.example.com/webhook/stock",
                                        "response": {"units_on_hand": 120, "units_sold": 880}}
    assert done["outputs"]["measure"]["loss_avoided_eur"] == 480.0  # simulated: (200 - 120) x 8 x 0.75
    assert svc.outcomes("acme", run_id=run["run_id"]) == []  # ...and never writes outcomes
    assert svc.performance("acme") == []


def test_webhook_body_is_a_snapshot(svc):
    """Regression: the default body must not embed the live outputs dict (circular JSON)."""
    run = svc.start("acme", "rescue-loop", MILD, "danny", now=T0)
    done = svc.advance("acme", run["run_id"], now=T0 + timedelta(days=60))
    json.dumps(done)  # state stays serialisable


def test_worse_than_baseline_is_recorded_honestly(svc):
    svc.stock.update(units_on_hand=260, units_sold=740)
    run = svc.start("acme", "rescue-loop", MILD, "danny", now=T0)
    svc.advance("acme", run["run_id"], now=T0 + timedelta(days=60))
    loss = svc.outcomes("acme", run_id=run["run_id"])[0]
    assert loss["value"] == -360.0  # (200 - 260) x 8 x 0.75
    assert svc.performance("acme")[0]["net_eur"] == -520.0


def test_migrates_older_ledger_without_breaking_hashes(tmp_path):
    path = tmp_path / "decisions.db"
    old_fields = ["timestamp", "tenant", "run_id", "workflow_id", "node_id", "sku", "metric", "value",
                  "source", "recorded_by", "note"]
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE outcomes (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, tenant TEXT NOT NULL,
          run_id TEXT NOT NULL, workflow_id TEXT NOT NULL, node_id TEXT, sku TEXT, metric TEXT NOT NULL,
          value REAL NOT NULL, source TEXT NOT NULL, recorded_by TEXT NOT NULL, note TEXT,
          prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL);""")
    payload = {"timestamp": "2026-11-20T10:00:00+00:00", "tenant": "acme", "run_id": "r0", "workflow_id": "w",
               "node_id": None, "sku": None, "metric": "units_sold", "value": 5.0, "source": "user",
               "recorded_by": "danny", "note": None}
    digest = _row_hash(GENESIS, payload)
    con.execute(f"INSERT INTO outcomes ({', '.join(old_fields)}, prev_hash, row_hash) VALUES "
                f"({', '.join('?' for _ in old_fields)}, ?, ?)", [payload[f] for f in old_fields] + [GENESIS, digest])
    con.commit()
    con.close()

    ledger = DecisionLedger(path)  # migrates: adds event_key + unique index
    assert ledger.verify() == {"ok": True, "first_bad": None, "decisions": 0, "outcomes": 1}
    ledger.record_outcome({"tenant": "acme", "run_id": "r1", "workflow_id": "w", "metric": "units_sold",
                           "value": 3, "source": "user", "recorded_by": "danny"})
    assert ledger.verify()["outcomes"] == 2
    assert [o["value"] for o in ledger.outcomes("acme")] == [5.0, 3.0]
    assert "event_key" not in json.dumps(ledger.outcomes("acme"))
