"""Temporal activities: thin, retry-safe wrappers around WorkflowService.

Each activity is idempotent: start returns the existing run when it was already created, advance
is a no-op until the wait expires, and approve returns the current state when the decision was
already applied by an earlier attempt.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.workflows.runtime import RunStateError, WorkflowError
from app.workflows.service import WorkflowService


def _now(arg: Dict[str, Any]) -> datetime:
    return datetime.fromisoformat(arg["now"])


class PlaybookActivities:
    def __init__(self, service: WorkflowService) -> None:
        self.svc = service

    @activity.defn(name="playbook_start")
    def start(self, arg: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return self.svc.start(arg["tenant"], arg["workflow_id"], arg.get("payload") or {}, arg["user"],
                                  dry_run=bool(arg.get("dry_run")), version=arg.get("version"),
                                  now=_now(arg), run_id=arg["run_id"], executor="temporal")
        except (WorkflowError, KeyError, ValueError) as exc:
            # Invalid workflow or missing definition: retrying cannot help.
            raise ApplicationError(str(exc), type="WorkflowError", non_retryable=True)

    @activity.defn(name="playbook_advance")
    def advance(self, arg: Dict[str, Any]) -> Dict[str, Any]:
        return self.svc.advance(arg["tenant"], arg["run_id"], now=_now(arg))

    @activity.defn(name="playbook_get")
    def get(self, arg: Dict[str, Any]) -> Dict[str, Any]:
        return self.svc.get_run(arg["tenant"], arg["run_id"])

    @activity.defn(name="playbook_approve")
    def approve(self, arg: Dict[str, Any]) -> Dict[str, Any]:
        tenant, run_id, node = arg["tenant"], arg["run_id"], arg["node"]
        try:
            return self.svc.approve(tenant, run_id, node, bool(arg["approved"]), arg["user"],
                                    note=arg.get("note"), now=_now(arg))
        except RunStateError as exc:
            current = self.svc.get_run(tenant, run_id)
            if _already_decided(current, node, arg["user"].get("username")):
                return current  # an earlier attempt applied this decision
            raise ApplicationError(str(exc), type="RunStateError", non_retryable=True)
        except PermissionError as exc:
            raise ApplicationError(str(exc), type="PermissionError", non_retryable=True)

    def all(self) -> List[Any]:
        return [self.start, self.advance, self.get, self.approve]


def _already_decided(state: Dict[str, Any], node: str, username: Any) -> bool:
    return any(s["node"] == node and s["status"] in ("approved", "rejected")
               and (s.get("detail") or {}).get("by") == username for s in state.get("steps", []))
