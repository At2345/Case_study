"""Background pipeline jobs (manual or scheduled) with run tracking and planner notifications."""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any, Callable, Dict, Optional

log = logging.getLogger("sop.jobs")

KINDS = ("monthly_cycle",)
PLANNERS = ["planner", "admin"]
ADMINS = ["admin"]

NODE_LABEL = {
    "ingest_validate": "Data quality check",
    "base_inference": "Forecast refresh",
    "reconcile": "Product reconciliation",
    "phasing": "Order-book phasing",
    "rules_triage": "Planner triage",
    "bands_safety": "Risk bands & stock",
    "narrative": "Exception narratives",
    "agent_actions": "Planner actions",
    "human_review": "Human review",
}


def _node_summary(node: str, detail: Optional[Dict[str, Any]]) -> str:
    """One-line summary of a node's work for the notification body."""
    if not detail:
        return "completed."
    d = {k: v for k, v in detail.items() if isinstance(v, (int, float, str)) and not isinstance(v, bool)}
    if node == "bands_safety" and d.get("band_coverage") is not None:
        return f"P10-P90 coverage {d['band_coverage']:.0%}, holding reduction {d.get('a_item_holding_reduction', 0):.0%} on A items."
    if node == "agent_actions" and d.get("actions") is not None:
        return f"{d['actions']} planner actions generated."
    if node == "rules_triage":
        return f"{d.get('queue', '?')} exceptions to review, {d.get('touchless', '?')} auto-approved."
    if node == "base_inference":
        return f"{d.get('items', '?')} items forecast ({d.get('rows', '?')} rows, {d.get('fallback_rows', 0)} fallback)."
    if node == "reconcile":
        return f"{d.get('products', '?')} products reconciled."
    if node == "phasing":
        return f"{d.get('booked_rows', '?')} booked order rows phased."
    if node == "narrative":
        return f"{d.get('narratives', '?')} exception narratives ready."
    if node == "ingest_validate":
        return f"{d.get('rows', '?')} rows across {d.get('tables', '?')} tables validated."
    return ", ".join(f"{k}={v}" for k, v in list(d.items())[:3]) + "."


class JobRunner:
    """One job at a time; the data service is swapped in after a successful run."""

    def __init__(self, state: Dict[str, Any], runs, reload_fn: Optional[Callable] = None, notes=None) -> None:
        self.state, self.runs, self.reload_fn, self.notes = state, runs, reload_fn, notes
        self._lock = threading.Lock()

    def busy(self) -> bool:
        return self._lock.locked()

    def start(self, kind: str, user: str, background: bool = True) -> Dict[str, Any]:
        if kind not in KINDS:
            raise ValueError(f"unknown job {kind}")
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("A pipeline run is already in progress.")
        run_id = self.runs.start(kind, user)
        if background:
            threading.Thread(target=self._run, args=(run_id, kind), daemon=True).start()
        else:
            self._run(run_id, kind)
        return {"run_id": run_id, "status": "RUNNING", "kind": kind}

    def _run(self, run_id: str, kind: str) -> None:
        status, error = "FAILED", None
        steps_seen: list = []
        try:
            from app.orchestration.graph import run_cycle

            def on_step(s):
                steps_seen.append(s)
                self.runs.step(run_id, s)

            result = run_cycle(on_step=on_step)
            status = result.get("status", "FAILED")
            failed = [s for s in result.get("steps", []) if s["status"] == "failed"]
            error = failed[0].get("error") if failed else None
            if status != "FAILED" and self.reload_fn is not None:
                self.state["svc"] = self.reload_fn()
            self.runs.finish(run_id, status, **({"error": error} if error else {}))
        except Exception as exc:
            log.exception("pipeline run failed")
            error = f"{type(exc).__name__}: {exc}"
            self.runs.finish(run_id, "FAILED", error=error)
        finally:
            try:
                self._notify(run_id, kind, status, error, steps_seen)
            finally:
                self._lock.release()

    def _notify(self, run_id: str, kind: str, status: str, error: Optional[str], steps: Optional[list] = None) -> None:
        if self.notes is None:
            return
        label = "Monthly cycle"
        if status == "FAILED":
            self.notes.add(ADMINS, "error", f"{label} failed", error or "See the Operations tab.", "operations", run_id)
            return
        # One notification per agent step so the planner can see exactly what each agent did.
        for s in steps or []:
            if s.get("status") != "ok":
                continue
            title = NODE_LABEL.get(s.get("node"), (s.get("node") or "step").replace("_", " "))
            body = _node_summary(s.get("node"), s.get("detail"))
            seconds = s.get("seconds")
            if seconds is not None:
                body = f"{body} ({seconds:.0f}s)"
            self.notes.add(PLANNERS, "agent_work", f"Agent: {title}", body, "operations", run_id)
        if kind == "monthly_cycle":
            s = self.state["svc"]
            n, led = s._summary_static["exceptions_requiring_review"], s._summary_static["planner_led_skus"]
            lbl = lambda iso: datetime.strptime(iso, "%Y-%m-%d").strftime("%b %Y")  # noqa: E731
            self.notes.add(PLANNERS, "queue_ready", "Exception queue is ready",
                           f"Actuals to {lbl(s.cutoff)}: {n} exceptions to review and {led} planner-led items, planning from {lbl(s.first_month)}.",
                           "workbench", run_id)
