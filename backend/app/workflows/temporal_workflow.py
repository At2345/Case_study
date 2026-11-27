"""Temporal workflow that drives one playbook run durably.

Deterministic orchestration only: all engine work happens in activities (see temporal_activities),
which load and save the same JSON run state the local executor uses. The workflow owns the
durable parts: day-long timers for wait nodes and waiting for approval decisions (signals).
Activities get the workflow clock (`now`) so waits resume exactly at Temporal's timer time,
including in time-skipping tests.

This module imports only temporalio and the standard library so it is safe in the workflow sandbox.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

WORKFLOW_NAME = "PlaybookRun"
TERMINAL = ("SUCCEEDED", "FAILED", "REJECTED")
ACTIVITY_TIMEOUT = timedelta(seconds=60)
RETRY = RetryPolicy(maximum_attempts=5, non_retryable_error_types=["PermissionError", "RunStateError", "WorkflowError"])


def temporal_workflow_id(tenant: str, run_id: str) -> str:
    return f"playbook-{tenant}-{run_id}"


def _summary(state: Dict[str, Any]) -> Dict[str, Any]:
    keys = ("run_id", "workflow_id", "workflow_version", "status", "pending", "spent_eur", "error")
    return {k: state.get(k) for k in keys}


@workflow.defn(name=WORKFLOW_NAME)
class PlaybookRun:
    def __init__(self) -> None:
        self._decisions: List[Dict[str, Any]] = []
        self._last: Optional[Dict[str, Any]] = None

    @workflow.signal
    def decide(self, decision: Dict[str, Any]) -> None:
        """Approval decision: {"node", "approved", "user": {"username", "role"}, "note"}."""
        self._decisions.append(decision)

    @workflow.query
    def summary(self) -> Optional[Dict[str, Any]]:
        return self._last

    async def _activity(self, name: str, arg: Dict[str, Any]) -> Dict[str, Any]:
        arg = {**arg, "now": workflow.now().isoformat()}
        return await workflow.execute_activity(name, arg, start_to_close_timeout=ACTIVITY_TIMEOUT, retry_policy=RETRY)

    @workflow.run
    async def run(self, inp: Dict[str, Any]) -> Dict[str, Any]:
        tenant, run_id = inp["tenant"], inp["run_id"]
        state = await self._activity("playbook_start", inp)
        while True:
            self._last = _summary(state)
            status = state["status"]
            if status in TERMINAL:
                return self._last
            if status == "WAITING":
                delay = datetime.fromisoformat(state["pending"]["resume_at"]) - workflow.now()
                if delay > timedelta(0):
                    await workflow.sleep(delay)
                state = await self._activity("playbook_advance", {"tenant": tenant, "run_id": run_id})
            elif status == "WAITING_APPROVAL":
                await workflow.wait_condition(lambda: bool(self._decisions))
                decision = self._decisions.pop(0)
                try:
                    state = await self._activity("playbook_approve", {"tenant": tenant, "run_id": run_id, **decision})
                except ActivityError:
                    # Rejected decision (wrong role, wrong node): keep waiting for a valid one.
                    workflow.logger.warning("approval decision for run %s was refused", run_id)
                    state = await self._activity("playbook_get", {"tenant": tenant, "run_id": run_id})
            else:  # RUNNING should not persist between activities; advance defensively
                state = await self._activity("playbook_advance", {"tenant": tenant, "run_id": run_id})
