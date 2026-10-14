"""Background pipeline jobs (manual or scheduled) with run tracking and planner notifications."""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, Optional

log = logging.getLogger("sop.jobs")

KINDS = ("monthly_cycle", "amazon_daily")
PLANNERS = ["planner", "admin"]
ADMINS = ["admin"]


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
        try:
            if kind == "amazon_daily":
                from app.orchestration.graph import amazon_daily
                self.runs.step(run_id, {"node": "amazon_daily", "status": "ok", "detail": amazon_daily()})
                status = "SUCCEEDED"
            else:
                from app.orchestration.graph import run_cycle
                result = run_cycle(on_step=lambda s: self.runs.step(run_id, s))
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
            self._lock.release()
        self._notify(run_id, kind, status, error)

    def _notify(self, run_id: str, kind: str, status: str, error: Optional[str]) -> None:
        if self.notes is None:
            return
        label = "Monthly cycle" if kind == "monthly_cycle" else "Amazon daily pipeline"
        if status == "FAILED":
            self.notes.add(ADMINS, "error", f"{label} failed", error or "See the Operations tab.", "operations", run_id)
        elif kind == "monthly_cycle":
            s = self.state["svc"]
            n, led = s._summary_static["exceptions_requiring_review"], s._summary_static["planner_led_skus"]
            self.notes.add(PLANNERS, "queue_ready", "Exception queue is ready",
                           f"Cycle {s.cutoff}: {n} exceptions to review and {led} planner-led items, planning from {s.first_month}.",
                           "workbench", run_id)
