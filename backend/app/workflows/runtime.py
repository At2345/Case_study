"""Resumable workflow interpreter.

Run state is a plain JSON-serialisable dict. `start`, `advance` and `approve` are pure functions of
(workflow, state, now), so the same code can later run inside a durable executor (Temporal activity)
and a paused run can be stored and resumed days later.

Guardrails are enforced here, not in the editor: locked node types, the per-run budget, and human
approval for actions that need it or that exceed the workflow's approval threshold.
"""
from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pydantic import ValidationError

from app.workflows.dsl import Workflow
from app.workflows.registry import NodeContext, Registry

TERMINAL = ("SUCCEEDED", "FAILED", "REJECTED")


class WorkflowError(Exception):
    """Invalid workflow definition or params."""


class RunStateError(WorkflowError):
    """The run is not in a state that allows the request (e.g. approving a node that is not waiting)."""


class Engine:
    def __init__(self, registry: Registry, allow_locked: bool = False) -> None:
        self.registry = registry
        self.allow_locked = allow_locked

    # ------------------------------------------------------------------ public API
    def start(self, wf: Workflow, payload: Optional[Dict[str, Any]] = None, now: Optional[datetime] = None,
              dry_run: bool = False, run_id: Optional[str] = None,
              meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """`meta` (e.g. tenant, started_by) is part of the state before the first node runs."""
        errors = self.registry.validate(wf, allow_locked=self.allow_locked)
        if errors:
            raise WorkflowError("; ".join(errors))
        now = _now(now)
        state = {
            "run_id": run_id or uuid.uuid4().hex[:12],
            "workflow_id": wf.id,
            "workflow_version": wf.version,
            "status": "RUNNING",
            "dry_run": dry_run,
            "payload": payload or {},
            "ready": [wf.trigger],
            "done": [],
            "outputs": {},
            "spent_eur": 0.0,
            "pending": None,
            "error": None,
            "started_at": now.isoformat(),
            "finished_at": None,
            "steps": [],
        }
        for key, value in (meta or {}).items():
            state.setdefault(key, value)
        return self.advance(wf, state, now)

    def advance(self, wf: Workflow, state: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
        """Continue a run: resumes an expired wait, then runs ready nodes until done or paused."""
        state = copy.deepcopy(state)
        self._check_version(wf, state)
        now = _now(now)
        if state["status"] in TERMINAL or state["status"] == "WAITING_APPROVAL":
            return state
        if state["status"] == "WAITING":
            pending = state["pending"]
            if now < datetime.fromisoformat(pending["resume_at"]):
                return state
            state["status"], state["pending"] = "RUNNING", None
            if self._run_node(wf, state, pending["node"], now, resumed={"resumed_at": now.isoformat()}) != "ok":
                return state
        while state["ready"]:
            node_id = state["ready"].pop(0)
            if node_id in state["done"]:
                continue
            if self._run_node(wf, state, node_id, now) != "ok":
                return state
        state["status"], state["finished_at"] = "SUCCEEDED", now.isoformat()
        return state

    def approve(self, wf: Workflow, state: Dict[str, Any], node_id: str, approved: bool, user: str,
                now: Optional[datetime] = None, note: Optional[str] = None) -> Dict[str, Any]:
        state = copy.deepcopy(state)
        self._check_version(wf, state)
        now = _now(now)
        pending = state.get("pending") or {}
        if state["status"] != "WAITING_APPROVAL" or pending.get("node") != node_id:
            raise RunStateError(f"node {node_id} is not awaiting approval")
        decision = {"approved": approved, "by": user, "note": note, "at": now.isoformat()}
        state["pending"], state["status"] = None, "RUNNING"
        self._log(state, node_id, wf.node(node_id).type, "approved" if approved else "rejected", now, detail=decision)
        if pending["kind"] == "gate":
            if not approved:
                state["status"], state["finished_at"] = "REJECTED", now.isoformat()
                return state
            outcome = self._run_node(wf, state, node_id, now, gate_passed=True)
        else:  # an approval node decides which branch runs next
            outcome = self._run_node(wf, state, node_id, now, resumed=decision)
        if outcome != "ok":
            return state
        return self.advance(wf, state, now)

    # ------------------------------------------------------------------ internals
    def _run_node(self, wf: Workflow, state: Dict[str, Any], node_id: str, now: datetime,
                  resumed: Optional[Dict[str, Any]] = None, gate_passed: bool = False) -> str:
        node = wf.node(node_id)
        t = self.registry.get(node.type)
        if t.policy.locked and not self.allow_locked:
            return self._fail(state, node_id, node.type, now, f"node type {node.type} is platform-only")
        try:
            params = t.params_model.model_validate(resolve(node.params, state))
        except (ValidationError, KeyError) as exc:
            return self._fail(state, node_id, node.type, now, f"invalid params: {exc}")

        estimate = float(getattr(params, t.policy.cost_param) or 0) if t.policy.cost_param else 0.0
        if estimate and state["spent_eur"] + estimate > wf.budget_eur:
            return self._fail(state, node_id, node.type, now,
                              f"budget exceeded: {state['spent_eur'] + estimate:.2f} EUR > {wf.budget_eur:.2f} EUR")
        over_threshold = wf.approval_threshold_eur is not None and estimate > wf.approval_threshold_eur
        if resumed is None and not gate_passed and (t.policy.needs_approval or over_threshold):
            reason = "action requires approval" if t.policy.needs_approval else \
                f"estimated cost {estimate:.2f} EUR exceeds approval threshold {wf.approval_threshold_eur:.2f} EUR"
            state["status"] = "WAITING_APPROVAL"
            state["pending"] = {"node": node_id, "kind": "gate", "reason": reason, "since": now.isoformat()}
            self._log(state, node_id, node.type, "awaiting_approval", now, detail={"reason": reason})
            return "paused"

        ctx = NodeContext(run_id=state["run_id"], node_id=node_id, now=now, payload=state["payload"],
                          outputs=state["outputs"], dry_run=state["dry_run"], resumed=resumed,
                          tenant=state.get("tenant") or "default")
        try:
            result = t.handler(ctx, params)
        except Exception as exc:  # a node failure fails the run, never the engine
            return self._fail(state, node_id, node.type, now, f"{type(exc).__name__}: {exc}")

        if result.status == "wait":
            if result.resume_at is None:
                return self._fail(state, node_id, node.type, now, "wait result without resume_at")
            state["status"] = "WAITING"
            state["pending"] = {"node": node_id, "kind": "wait", "resume_at": result.resume_at.isoformat()}
            self._log(state, node_id, node.type, "waiting", now, detail={"resume_at": result.resume_at.isoformat()})
            return "paused"
        if result.status == "approval":
            state["status"] = "WAITING_APPROVAL"
            state["pending"] = {"node": node_id, "kind": "node", "reason": result.reason, "since": now.isoformat()}
            self._log(state, node_id, node.type, "awaiting_approval", now, detail={"reason": result.reason})
            return "paused"
        if result.port not in t.ports:
            return self._fail(state, node_id, node.type, now, f"handler returned unknown port '{result.port}'")

        state["outputs"][node_id] = result.output
        state["spent_eur"] = round(state["spent_eur"] + float(result.cost_eur or 0), 2)
        state["done"].append(node_id)
        self._log(state, node_id, node.type, "ok", now, port=result.port, cost=result.cost_eur, output=result.output)
        for e in wf.outgoing(node_id, result.port):
            if e.target not in state["done"] and e.target not in state["ready"]:
                state["ready"].append(e.target)
        return "ok"

    def _fail(self, state: Dict[str, Any], node_id: str, node_type: str, now: datetime, error: str) -> str:
        state["status"], state["error"], state["finished_at"] = "FAILED", f"{node_id}: {error}", now.isoformat()
        self._log(state, node_id, node_type, "failed", now, detail={"error": error})
        return "failed"

    @staticmethod
    def _log(state: Dict[str, Any], node_id: str, node_type: str, status: str, now: datetime,
             port: Optional[str] = None, cost: float = 0.0, output: Optional[Dict[str, Any]] = None,
             detail: Optional[Dict[str, Any]] = None) -> None:
        """Step log: the source records for the decision-outcome ledger."""
        state["steps"].append({
            "node": node_id, "type": node_type, "status": status, "at": now.isoformat(),
            "port": port, "cost_eur": cost, "output": output, "detail": detail,
        })

    @staticmethod
    def _check_version(wf: Workflow, state: Dict[str, Any]) -> None:
        if (wf.id, wf.version) != (state["workflow_id"], state["workflow_version"]):
            raise RunStateError(
                f"run {state['run_id']} belongs to {state['workflow_id']} v{state['workflow_version']}, "
                f"not {wf.id} v{wf.version}")


def resolve(value: Any, state: Dict[str, Any]) -> Any:
    """Replace "$trigger.<path>" and "$nodes.<id>.<path>" references with run values."""
    if isinstance(value, dict):
        return {k: resolve(v, state) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, state) for v in value]
    if not isinstance(value, str):
        return value
    if value == "$trigger" or value.startswith("$trigger."):
        return _path(state["payload"], value.split(".")[1:], value)
    if value.startswith("$nodes."):
        parts = value.split(".")[1:]
        if not parts or parts[0] not in state["outputs"]:
            raise KeyError(f"{value}: node has no output yet")
        return _path(state["outputs"][parts[0]], parts[1:], value)
    return value


def _path(obj: Any, parts: Any, ref: str) -> Any:
    for p in parts:
        if isinstance(obj, dict) and p in obj:
            obj = obj[p]
        else:
            raise KeyError(f"{ref}: '{p}' not found")
    return obj


def _now(now: Optional[datetime]) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now if now.tzinfo else now.replace(tzinfo=timezone.utc)
