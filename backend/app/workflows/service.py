"""Application service shared by the API and the scheduler: tenant-scoped stores, approvals, notifications.

Every load-modify-save of a run happens under one lock, so an approval from the API and a scheduler
resume can never overwrite each other.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError

from app.workflows.builtin import default_registry
from app.workflows import templates
from app.workflows.dsl import Workflow
from app.workflows.ledger import DecisionLedger, node_info_for
from app.workflows.registry import Registry, is_reference
from app.workflows.runtime import Engine, RunStateError, WorkflowError
from app.workflows.store import RunStore, WorkflowStore

log = logging.getLogger("sop.workflows")

TENANT_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
GATE_ROLES = ("planner", "admin")
RESERVED_IDS = ("mcp", "node-types", "validate", "draft")  # path segments used by /api/v1/workflows/*


class WorkflowService:
    def __init__(self, root: Path, registry: Optional[Registry] = None, notes: Any = None,
                 four_eyes: bool = False, ledger: Optional[DecisionLedger] = None) -> None:
        self.root = Path(root)
        self.registry = registry or default_registry()
        self.engine = Engine(self.registry)
        self.notes = notes
        self.four_eyes = four_eyes  # starter of a run may not approve its guarded actions (off in demo)
        self.ledger = ledger or DecisionLedger(self.root / "decisions.db")
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ definitions
    def catalogue(self) -> List[Dict[str, Any]]:
        return self.registry.catalogue()

    def check(self, spec: Dict[str, Any]) -> Tuple[Optional[Workflow], List[str]]:
        """Parse and validate without saving. Returns (workflow or None, errors)."""
        try:
            wf = Workflow.model_validate(spec)
        except ValidationError as exc:
            return None, [f"{'.'.join(map(str, e['loc'])) or 'workflow'}: {e['msg']}" for e in exc.errors()]
        errors = self.registry.validate(wf)
        if wf.id in RESERVED_IDS:
            errors.insert(0, f"id: '{wf.id}' is reserved by the API; choose another workflow id")
        return wf, errors

    def save(self, tenant: str, spec: Dict[str, Any]) -> Workflow:
        wf, errors = self.check(spec)
        if errors:
            raise WorkflowError("; ".join(errors))
        return self._stores(tenant)[0].save(wf)

    def get(self, tenant: str, workflow_id: str, version: Optional[int] = None) -> Workflow:
        return self._stores(tenant)[0].get(workflow_id, version)

    def list(self, tenant: str) -> List[Dict[str, Any]]:
        return self._stores(tenant)[0].list()

    # ------------------------------------------------------------------ runs
    def start(self, tenant: str, workflow_id: str, payload: Dict[str, Any], user: str,
              dry_run: bool = False, version: Optional[int] = None,
              now: Optional[datetime] = None, run_id: Optional[str] = None,
              executor: str = "local") -> Dict[str, Any]:
        """Start a run. With an explicit run_id the call is idempotent (safe for Temporal activity retries)."""
        runs = self._stores(tenant)[1]
        if run_id is not None:
            try:
                existing = runs.get(run_id)
                self._record(self.get(tenant, existing["workflow_id"], existing["workflow_version"]), existing)
                return existing
            except KeyError:
                pass
        wf = self.get(tenant, workflow_id, version)
        with self._lock:
            state = self.engine.start(wf, payload, now=now, dry_run=dry_run, run_id=run_id,
                                      meta={"started_by": user, "tenant": tenant, "executor": executor})
            runs.save(state)
            self._record(wf, state)
        self._notify(wf, None, state)
        return state

    def get_run(self, tenant: str, run_id: str) -> Dict[str, Any]:
        return self._stores(tenant)[1].get(run_id)

    def list_runs(self, tenant: str, workflow_id: Optional[str] = None, status: Optional[str] = None,
                  limit: int = 50, updated_after: Optional[datetime] = None) -> List[Dict[str, Any]]:
        return self._stores(tenant)[1].list(workflow_id, status, limit, updated_after)

    def check_approval(self, tenant: str, run_id: str, node_id: str, user: Dict[str, str]) -> Dict[str, Any]:
        """Raise unless `user` may approve `node_id` now. Used before signalling a Temporal run."""
        wf_store, runs = self._stores(tenant)
        state = runs.get(run_id)
        pending = state.get("pending") or {}
        if state["status"] != "WAITING_APPROVAL" or pending.get("node") != node_id:
            raise RunStateError(f"node {node_id} is not awaiting approval")
        self._check_approver(wf_store.get(state["workflow_id"], state["workflow_version"]), state, node_id, user)
        return state

    def approve(self, tenant: str, run_id: str, node_id: str, approved: bool, user: Dict[str, str],
                note: Optional[str] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
        wf_store, runs = self._stores(tenant)
        with self._lock:
            state = runs.get(run_id)
            wf = wf_store.get(state["workflow_id"], state["workflow_version"])
            self._check_approver(wf, state, node_id, user)
            new = self.engine.approve(wf, state, node_id, approved, user["username"], now=now, note=note)
            runs.save(new)
            self._record(wf, new)
        self._notify(wf, state, new)
        return new

    def advance(self, tenant: str, run_id: str, now: Optional[datetime] = None) -> Dict[str, Any]:
        wf_store, runs = self._stores(tenant)
        with self._lock:
            state = runs.get(run_id)
            wf = wf_store.get(state["workflow_id"], state["workflow_version"])
            new = self.engine.advance(wf, state, now=now)
            if new != state:
                runs.save(new)
                self._record(wf, new)
        if new != state:
            self._notify(wf, state, new)
        return new

    def resume_due(self, now: Optional[datetime] = None) -> List[str]:
        """Scheduler hook: resume every waiting run whose wait has expired, across tenants."""
        now = now or datetime.now(timezone.utc)
        resumed = []
        for tenant in self._tenants():
            for state in self._stores(tenant)[1].due():
                if state.get("executor") == "temporal":
                    continue  # Temporal owns this run's timer
                if now < datetime.fromisoformat(state["pending"]["resume_at"]):
                    continue
                try:
                    self.advance(tenant, state["run_id"], now=now)
                    resumed.append(state["run_id"])
                except Exception:  # one broken run must not stop the others
                    log.exception("resuming workflow run %s failed", state["run_id"])
        return resumed

    # ------------------------------------------------------------------ decision-outcome ledger
    def decisions(self, tenant: str, run_id: Optional[str] = None, workflow_id: Optional[str] = None,
                  limit: int = 500) -> List[Dict[str, Any]]:
        self._stores(tenant)  # validates the tenant id
        return self.ledger.decisions(tenant, run_id, workflow_id, limit)

    def outcomes(self, tenant: str, run_id: Optional[str] = None, workflow_id: Optional[str] = None,
                 limit: int = 500) -> List[Dict[str, Any]]:
        self._stores(tenant)
        return self.ledger.outcomes(tenant, run_id, workflow_id, limit)

    def record_outcome(self, tenant: str, run_id: str, metric: str, value: float, recorded_by: str,
                       sku: Optional[str] = None, node_id: Optional[str] = None, note: Optional[str] = None,
                       source: str = "api") -> Dict[str, Any]:
        """Attach a measured result to a real run (dry runs have no outcomes)."""
        state = self.get_run(tenant, run_id)  # KeyError -> 404, also enforces the tenant
        if state.get("dry_run"):
            raise ValueError("dry runs have no outcomes")
        if node_id is not None and node_id not in {s["node"] for s in state.get("steps", [])}:
            raise ValueError(f"node {node_id} did not run in {run_id}")
        row_id = self.ledger.record_outcome({
            "tenant": tenant, "run_id": run_id, "workflow_id": state["workflow_id"], "node_id": node_id,
            "sku": sku, "metric": metric, "value": value, "source": source, "recorded_by": recorded_by,
            "note": note})
        return {"id": row_id, "run_id": run_id, "metric": metric, "value": float(value)}

    def performance(self, tenant: str, workflow_id: Optional[str] = None) -> List[Dict[str, Any]]:
        self._stores(tenant)
        return self.ledger.performance(tenant, workflow_id)

    def reconcile_ledger(self) -> int:
        """Record any decision steps missing from the ledger (e.g. after a ledger write failed)."""
        added = 0
        for tenant in self._tenants():
            wf_store, runs = self._stores(tenant)
            for summary in runs.list(limit=100_000):
                try:
                    state = runs.get(summary["run_id"])
                    wf = wf_store.get(state["workflow_id"], state["workflow_version"])
                    added += self.ledger.record_run({**state, "tenant": tenant}, node_info_for(wf, self.registry))
                except Exception:
                    log.exception("ledger reconcile failed for run %s", summary["run_id"])
        return added

    # ------------------------------------------------------------------ template library and benchmark
    def get_settings(self, tenant: str) -> Dict[str, Any]:
        self._stores(tenant)
        path = self.root / tenant / "settings.json"
        stored = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        return {"share_benchmarks": bool(stored.get("share_benchmarks", False))}

    def update_settings(self, tenant: str, share_benchmarks: bool) -> Dict[str, Any]:
        self._stores(tenant)
        path = self.root / tenant / "settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"share_benchmarks": bool(share_benchmarks)}), encoding="utf-8")
        return self.get_settings(tenant)

    def install_template(self, tenant: str, template_id: str, workflow_id: str,
                         values: Optional[Dict[str, Any]] = None, name: Optional[str] = None) -> Workflow:
        spec = templates.render(template_id, workflow_id, values, name)
        try:
            self.get(tenant, workflow_id)
            raise ValueError(f"workflow id '{workflow_id}' already exists; choose another id")
        except KeyError:
            pass
        return self.save(tenant, spec)

    def template_benchmark(self, tenant: str, min_tenants: int = 3) -> List[Dict[str, Any]]:
        """Template library with results: yours, and pooled across tenants that share (give-to-get).

        Pooled figures appear only when this tenant shares too and at least `min_tenants` sharing tenants
        have real runs of the template, so no single company's numbers can be read off.
        """
        sharing = self.get_settings(tenant)["share_benchmarks"]
        own: Dict[str, Dict[str, Any]] = {}
        pooled: Dict[str, Dict[str, Any]] = {}
        for t in self._tenants():
            t_shares = self.get_settings(t)["share_benchmarks"]
            if t != tenant and not t_shares:
                continue
            origin = {}
            for w in self.list(t):
                tpl = self.get(t, w["id"]).template or {}
                if tpl.get("id"):
                    origin[w["id"]] = tpl["id"]
            for perf in self.ledger.performance(t):
                tid = origin.get(perf["workflow_id"])
                if tid is None:
                    continue
                if t == tenant:
                    _accumulate(own.setdefault(tid, _empty_agg()), perf, t)
                if t_shares:
                    _accumulate(pooled.setdefault(tid, _empty_agg()), perf, t)
        result = []
        for tid, tpl in templates.TEMPLATES.items():
            pool = pooled.get(tid)
            if not sharing:
                status, pool_out = "share_to_see", None
            elif pool is None or len(pool["tenants"]) < min_tenants:
                status, pool_out = "insufficient_data", None
            else:
                status, pool_out = "available", _finish(pool, include_tenants=True)
            result.append({"template": templates.public(tpl), "yours": _finish(own[tid]) if tid in own else None,
                           "pooled": pool_out, "pooled_status": status})
        return result

    def _record(self, wf: Workflow, state: Dict[str, Any]) -> None:
        """Write new decision steps to the ledger. Never fails the run; reconcile_ledger backfills gaps."""
        try:
            self.ledger.record_run(state, node_info_for(wf, self.registry))
        except Exception:
            log.exception("decision ledger write failed for run %s", state.get("run_id"))

    # ------------------------------------------------------------------ internals
    def _check_approver(self, wf: Workflow, state: Dict[str, Any], node_id: str, user: Dict[str, str]) -> None:
        role = user.get("role")
        pending = state.get("pending") or {}
        if pending.get("node") != node_id:
            return  # the engine reports the state conflict
        if pending.get("kind") == "node":
            wanted = wf.node(node_id).params.get("approver_role", "planner")
            allowed = {"admin"} | ({wanted} if isinstance(wanted, str) and not is_reference(wanted) else set())
        else:
            allowed = set(GATE_ROLES)
        if role not in allowed:
            raise PermissionError(f"role '{role}' may not approve this step (allowed: {sorted(allowed)})")
        if (self.four_eyes and pending.get("kind") == "gate" and role != "admin"
                and user.get("username") == state.get("started_by")):
            raise PermissionError("the person who started this run cannot approve its guarded action")

    def _notify(self, wf: Workflow, before: Optional[Dict[str, Any]], after: Dict[str, Any]) -> None:
        if self.notes is None or (before is not None and before.get("status") == after["status"]
                                  and before.get("pending") == after.get("pending")):
            return
        status, run_id = after["status"], after["run_id"]
        try:
            if status == "WAITING_APPROVAL":
                pending = after["pending"]
                roles = list(GATE_ROLES)
                if pending["kind"] == "node":
                    wanted = wf.node(pending["node"]).params.get("approver_role", "planner")
                    roles = sorted({"admin", wanted}) if isinstance(wanted, str) else ["admin"]
                self.notes.add(roles, "approval", f"Approval needed: {wf.name}",
                               f"Step '{pending['node']}': {pending.get('reason') or 'review required'}",
                               "workflows", run_id)
            elif status == "FAILED":
                self.notes.add(["planner", "admin"], "error", f"Workflow failed: {wf.name}",
                               after.get("error") or "See the run history.", "workflows", run_id)
        except Exception:  # notifications must never break a run
            log.exception("workflow notification failed for run %s", run_id)

    def _stores(self, tenant: str) -> Tuple[WorkflowStore, RunStore]:
        if not TENANT_RE.match(tenant or ""):
            raise ValueError("invalid tenant id")
        base = self.root / tenant
        return WorkflowStore(base), RunStore(base)

    def _tenants(self) -> List[str]:
        if not self.root.exists():
            return []
        return [p.name for p in self.root.iterdir() if p.is_dir() and TENANT_RE.match(p.name)]


def _empty_agg() -> Dict[str, Any]:
    return {"tenants": set(), "playbooks": 0, "runs": 0, "measured_runs": 0, "spent_eur": 0.0, "value_eur": 0.0}


def _accumulate(agg: Dict[str, Any], perf: Dict[str, Any], tenant: str) -> None:
    agg["tenants"].add(tenant)
    agg["playbooks"] += 1
    for key in ("runs", "measured_runs", "spent_eur", "value_eur"):
        agg[key] += perf[key]


def _finish(agg: Dict[str, Any], include_tenants: bool = False) -> Dict[str, Any]:
    spent, value = round(agg["spent_eur"], 2), round(agg["value_eur"], 2)
    out = {"playbooks": agg["playbooks"], "runs": agg["runs"], "measured_runs": agg["measured_runs"],
           "spent_eur": spent, "value_eur": value, "net_eur": round(value - spent, 2),
           "value_per_eur": round(value / spent, 2) if spent > 0 else None}
    if include_tenants:
        out["tenants"] = len(agg["tenants"])  # a count only; never which tenants
    return out
