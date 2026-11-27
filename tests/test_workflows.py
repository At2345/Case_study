"""Workflow engine: DSL validation, guardrails, branching, waits, approvals and resumable JSON state."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.workflows import Engine, Workflow, WorkflowError, default_registry
from app.workflows.store import RunStore, WorkflowStore

T0 = datetime(2026, 11, 25, 9, 0, tzinfo=timezone.utc)


def rescue_playbook(**overrides) -> Workflow:
    """Sell-through gap -> rescue proposal -> branch -> price cut or approved transfer -> wait -> notify."""
    spec = {
        "id": "rescue-de",
        "name": "Rescue slow batches in DE",
        "trigger": "start",
        "budget_eur": 2000,
        "nodes": [
            {"id": "start", "type": "trigger.sell_through_gap"},
            {"id": "propose", "type": "agent.rescue_proposal", "params": {
                "sku": "$trigger.sku", "marketplace": "$trigger.marketplace",
                "units_on_hand": "$trigger.units_on_hand", "sell_through_gap": "$trigger.gap",
                "unit_landed_cost_eur": "$trigger.unit_cost", "days_to_trigger": "$trigger.days_to_trigger"}},
            {"id": "is_price", "type": "logic.if",
             "params": {"left": "$nodes.propose.action", "op": "==", "right": "change_price"}},
            {"id": "cut_price", "type": "action.change_price", "params": {
                "sku": "$nodes.propose.sku", "marketplace": "$nodes.propose.marketplace",
                "pct_change": "$nodes.propose.pct_change",
                "estimated_cost_eur": "$nodes.propose.estimated_cost_eur"}},
            {"id": "transfer", "type": "action.transfer_stock", "params": {
                "sku": "$nodes.propose.sku", "from_marketplace": "DE", "to_marketplace": "FR",
                "units": "$nodes.propose.units_at_risk",
                "estimated_cost_eur": "$nodes.propose.estimated_cost_eur"}},
            {"id": "wait", "type": "logic.wait", "params": {"days": 14}},
            {"id": "tell", "type": "action.notify", "params": {"message": "Rescue step done, check sell-through."}},
        ],
        "edges": [
            {"source": "start", "target": "propose"},
            {"source": "propose", "target": "is_price"},
            {"source": "is_price", "target": "cut_price", "port": "true"},
            {"source": "is_price", "target": "transfer", "port": "false"},
            {"source": "cut_price", "target": "wait"},
            {"source": "wait", "target": "tell"},
        ],
    }
    spec.update(overrides)
    return Workflow.model_validate(spec)


MILD = {"sku": "MAT-BLUE", "marketplace": "DE", "units_on_hand": 1000, "gap": -0.2,
        "unit_cost": 8.0, "days_to_trigger": 90}
DEEP = {**MILD, "gap": -0.6, "days_to_trigger": 30}


@pytest.fixture
def engine():
    return Engine(default_registry())


# ---------------------------------------------------------------- DSL and validation
def test_structural_errors_rejected():
    with pytest.raises(ValidationError, match="duplicate node ids"):
        rescue_playbook(nodes=[{"id": "start", "type": "trigger.manual"}] * 2, edges=[])
    with pytest.raises(ValidationError, match="unknown node"):
        rescue_playbook(edges=[{"source": "start", "target": "nope"}])
    with pytest.raises(ValidationError, match="cycle"):
        rescue_playbook(edges=[{"source": "propose", "target": "is_price"},
                               {"source": "is_price", "target": "propose", "port": "true"}])


def test_registry_validation_finds_type_port_and_param_errors():
    reg = default_registry()
    wf = Workflow.model_validate({
        "id": "bad", "name": "bad", "trigger": "start",
        "nodes": [{"id": "start", "type": "trigger.manual"},
                  {"id": "x", "type": "does.not_exist"},
                  {"id": "w", "type": "logic.wait", "params": {"days": -1}},
                  {"id": "c", "type": "logic.if", "params": {"left": 1, "op": "<", "right": 2}}],
        "edges": [{"source": "start", "target": "c"}, {"source": "c", "target": "w", "port": "maybe"}],
    })
    errors = " | ".join(reg.validate(wf))
    assert "unknown node type does.not_exist" in errors
    assert "w.days" in errors
    assert "port 'maybe'" in errors


def test_catalogue_hides_locked_nodes_and_exposes_json_schema():
    reg = default_registry()
    names = {s["name"] for s in reg.catalogue()}
    assert "platform.underwrite" not in names
    assert "platform.underwrite" in {s["name"] for s in reg.catalogue(include_locked=True)}
    price = next(s for s in reg.catalogue() if s["name"] == "action.change_price")
    assert price["params_schema"]["properties"]["pct_change"]["maximum"] == 50
    assert price["policy"]["cost_param"] == "estimated_cost_eur"


def test_locked_node_rejected_in_user_workflow(engine):
    wf = Workflow.model_validate({
        "id": "sneaky", "name": "sneaky", "trigger": "start",
        "nodes": [{"id": "start", "type": "trigger.manual"},
                  {"id": "uw", "type": "platform.underwrite", "params": {"po_id": "PO1", "po_value_eur": 1000}}],
        "edges": [{"source": "start", "target": "uw"}],
    })
    with pytest.raises(WorkflowError, match="platform-only"):
        engine.start(wf, now=T0)


# ---------------------------------------------------------------- running
def test_mild_gap_cuts_price_waits_then_finishes(engine):
    wf = rescue_playbook()
    state = engine.start(wf, MILD, now=T0)
    assert state["status"] == "WAITING"
    assert state["outputs"]["propose"]["action"] == "change_price"
    assert state["outputs"]["cut_price"]["pct_change"] == -10.0
    assert "transfer" not in state["outputs"]
    assert state["spent_eur"] == 160.0  # 200 units at risk x 8 EUR x 10%

    early = engine.advance(wf, state, now=T0 + timedelta(days=13))
    assert early["status"] == "WAITING"
    done = engine.advance(wf, state, now=T0 + timedelta(days=14))
    assert done["status"] == "SUCCEEDED"
    assert done["outputs"]["tell"]["recorded"] is True
    assert [s["node"] for s in done["steps"] if s["status"] == "ok"] == \
        ["start", "propose", "is_price", "cut_price", "wait", "tell"]


def test_deep_gap_transfer_needs_approval(engine):
    wf = rescue_playbook()
    state = engine.start(wf, DEEP, now=T0)
    assert state["status"] == "WAITING_APPROVAL"
    assert state["pending"]["node"] == "transfer"
    assert "transfer" not in state["outputs"]

    with pytest.raises(WorkflowError):
        engine.approve(wf, state, "cut_price", True, "danny", now=T0)
    approved = engine.approve(wf, state, "transfer", True, "danny", now=T0 + timedelta(hours=2))
    assert approved["status"] == "SUCCEEDED"
    assert approved["outputs"]["transfer"]["units"] == 600
    assert any(s["status"] == "approved" and s["detail"]["by"] == "danny" for s in approved["steps"])

    rejected = engine.approve(wf, state, "transfer", False, "danny", now=T0)
    assert rejected["status"] == "REJECTED"
    assert "transfer" not in rejected["outputs"]


def test_budget_guardrail_fails_run(engine):
    wf = rescue_playbook(budget_eur=100)
    state = engine.start(wf, MILD, now=T0)
    assert state["status"] == "FAILED"
    assert "budget exceeded" in state["error"]
    assert "cut_price" not in state["outputs"]


def test_approval_threshold_gates_reversible_action(engine):
    wf = rescue_playbook(approval_threshold_eur=100)
    state = engine.start(wf, MILD, now=T0)
    assert state["status"] == "WAITING_APPROVAL"
    assert "exceeds approval threshold" in state["pending"]["reason"]
    resumed = engine.approve(wf, state, "cut_price", True, "danny", now=T0)
    assert resumed["status"] == "WAITING"  # continues to the 14-day wait


def test_dry_run_spends_nothing(engine):
    state = engine.start(rescue_playbook(), MILD, now=T0, dry_run=True)
    assert state["spent_eur"] == 0.0
    assert state["outputs"]["cut_price"]["simulated"] is True


def test_approval_node_branches():
    reg = default_registry()
    wf = Workflow.model_validate({
        "id": "ask", "name": "ask", "trigger": "start",
        "nodes": [{"id": "start", "type": "trigger.manual"},
                  {"id": "ok", "type": "control.approval", "params": {"message": "Go?"}},
                  {"id": "yes", "type": "action.notify", "params": {"message": "approved"}},
                  {"id": "no", "type": "action.notify", "params": {"message": "rejected"}}],
        "edges": [{"source": "start", "target": "ok"},
                  {"source": "ok", "target": "yes", "port": "approved"},
                  {"source": "ok", "target": "no", "port": "rejected"}],
    })
    eng = Engine(reg)
    state = eng.start(wf, now=T0)
    assert state["pending"] == {"node": "ok", "kind": "node", "reason": "Go?", "since": T0.isoformat()}
    no = eng.approve(wf, state, "ok", False, "admin", now=T0)
    assert no["status"] == "SUCCEEDED" and "no" in no["outputs"] and "yes" not in no["outputs"]


def test_missing_reference_fails_cleanly(engine):
    state = engine.start(rescue_playbook(), {"sku": "X"}, now=T0)
    assert state["status"] == "FAILED"
    assert "propose" in state["error"] and "not found" in state["error"]


def test_node_exception_fails_run_not_engine():
    from pydantic import BaseModel

    reg = default_registry()

    class NoParams(BaseModel):
        pass

    @reg.node("action.explode", "action", "Always fails.", NoParams)
    def explode(ctx, p):
        raise RuntimeError("boom")

    wf = Workflow.model_validate({
        "id": "boom", "name": "boom", "trigger": "start",
        "nodes": [{"id": "start", "type": "trigger.manual"}, {"id": "x", "type": "action.explode"}],
        "edges": [{"source": "start", "target": "x"}],
    })
    state = Engine(reg).start(wf, now=T0)
    assert state["status"] == "FAILED" and "RuntimeError: boom" in state["error"]


def test_platform_can_run_underwrite_node():
    wf = Workflow.model_validate({
        "id": "uw", "name": "uw", "trigger": "start",
        "nodes": [{"id": "start", "type": "trigger.manual"},
                  {"id": "uw", "type": "platform.underwrite", "params": {
                      "po_id": "PO1", "units": 1000, "unit_landed_cost_eur": 8,
                      "demand_mean_units": 1100, "demand_sd_units": 200}}],
        "edges": [{"source": "start", "target": "uw"}],
    })
    state = Engine(default_registry(), allow_locked=True).start(wf, now=T0)
    assert state["status"] == "SUCCEEDED"
    assert state["outputs"]["uw"]["po_id"] == "PO1" and state["outputs"]["uw"]["offered"] is True


# ---------------------------------------------------------------- persistence and versions
def test_state_round_trips_through_json_and_store(engine, tmp_path):
    wf_store, run_store = WorkflowStore(tmp_path), RunStore(tmp_path)
    v1 = wf_store.save(rescue_playbook())
    state = engine.start(v1, MILD, now=T0)
    run_store.save(json.loads(json.dumps(state)))
    assert [r["run_id"] for r in run_store.due()] == [state["run_id"]]

    v2 = wf_store.save(rescue_playbook(name="Rescue v2", budget_eur=5000))
    assert (v1.version, v2.version) == (1, 2)
    assert wf_store.get("rescue-de").name == "Rescue v2"

    loaded = run_store.get(state["run_id"])
    with pytest.raises(WorkflowError, match="belongs to rescue-de v1"):
        engine.advance(v2, loaded, now=T0 + timedelta(days=14))
    finished = engine.advance(wf_store.get("rescue-de", loaded["workflow_version"]), loaded,
                              now=T0 + timedelta(days=14))
    assert finished["status"] == "SUCCEEDED"
