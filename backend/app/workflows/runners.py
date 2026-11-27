"""How the API executes runs: in-process (LocalRunner) or durably on Temporal (TemporalRunner).

Both expose the same synchronous `start` / `approve` so the FastAPI routes do not change.
"""
from __future__ import annotations

import asyncio
import threading
import time
import uuid
from typing import Any, Callable, Dict, Optional

from app.workflows.runtime import WorkflowError
from app.workflows.service import WorkflowService


class TemporalUnavailable(RuntimeError):
    """Temporal server or worker cannot be reached."""


class LocalRunner:
    name = "local"

    def __init__(self, service: WorkflowService) -> None:
        self.svc = service

    def start(self, tenant: str, workflow_id: str, payload: Dict[str, Any], user: str,
              dry_run: bool = False, version: Optional[int] = None) -> Dict[str, Any]:
        return self.svc.start(tenant, workflow_id, payload, user, dry_run=dry_run, version=version)

    def approve(self, tenant: str, run_id: str, node: str, approved: bool, user: Dict[str, str],
                note: Optional[str] = None) -> Dict[str, Any]:
        return self.svc.approve(tenant, run_id, node, approved, user, note=note)


class TemporalRunner:
    """Starts and signals PlaybookRun workflows. Owns a background event loop for the async client."""
    name = "temporal"

    def __init__(self, service: WorkflowService, target_host: str, namespace: str = "default",
                 task_queue: str = "playbooks", wait_seconds: float = 10.0, rpc_timeout: float = 10.0) -> None:
        self.svc = service
        self.target_host, self.namespace, self.task_queue = target_host, namespace, task_queue
        self.wait_seconds, self.rpc_timeout = wait_seconds, rpc_timeout
        self._client: Any = None
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True, name="temporal-client")
        self._thread.start()

    # ------------------------------------------------------------------ public API
    def start(self, tenant: str, workflow_id: str, payload: Dict[str, Any], user: str,
              dry_run: bool = False, version: Optional[int] = None) -> Dict[str, Any]:
        wf = self.svc.get(tenant, workflow_id, version)  # KeyError -> 404 before touching Temporal
        errors = self.svc.registry.validate(wf)
        if errors:
            raise WorkflowError("; ".join(errors))
        run_id = uuid.uuid4().hex[:12]
        inp = {"tenant": tenant, "workflow_id": wf.id, "version": wf.version, "payload": payload,
               "dry_run": dry_run, "run_id": run_id, "user": user}
        self._call(self._start(inp))
        return self._await_state(tenant, run_id, lambda s: s is not None and s["status"] != "RUNNING")

    def approve(self, tenant: str, run_id: str, node: str, approved: bool, user: Dict[str, str],
                note: Optional[str] = None) -> Dict[str, Any]:
        before = self.svc.check_approval(tenant, run_id, node, user)  # 404 / 409 / 403 before signalling
        decision = {"node": node, "approved": approved, "note": note,
                    "user": {"username": user.get("username"), "role": user.get("role")}}
        self._call(self._signal(tenant, run_id, decision))
        return self._await_state(tenant, run_id, lambda s: s is not None and (
            s["status"] != before["status"] or s.get("pending") != before.get("pending")))

    def close(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)

    # ------------------------------------------------------------------ internals
    def _call(self, coro) -> Any:
        try:
            return asyncio.run_coroutine_threadsafe(coro, self._loop).result(self.rpc_timeout)
        except TemporalUnavailable:
            raise
        except Exception as exc:
            raise TemporalUnavailable(f"Temporal at {self.target_host} is not reachable: {exc}") from exc

    async def _get_client(self):
        if self._client is None:
            from temporalio.client import Client
            try:
                self._client = await Client.connect(self.target_host, namespace=self.namespace)
            except Exception as exc:
                raise TemporalUnavailable(f"Temporal at {self.target_host} is not reachable: {exc}") from exc
        return self._client

    async def _start(self, inp: Dict[str, Any]) -> None:
        from app.workflows.temporal_workflow import WORKFLOW_NAME, temporal_workflow_id
        client = await self._get_client()
        await client.start_workflow(WORKFLOW_NAME, inp, id=temporal_workflow_id(inp["tenant"], inp["run_id"]),
                                    task_queue=self.task_queue)

    async def _signal(self, tenant: str, run_id: str, decision: Dict[str, Any]) -> None:
        from app.workflows.temporal_workflow import temporal_workflow_id
        client = await self._get_client()
        await client.get_workflow_handle(temporal_workflow_id(tenant, run_id)).signal("decide", decision)

    def _await_state(self, tenant: str, run_id: str, done: Callable[[Optional[Dict[str, Any]]], bool]) -> Dict[str, Any]:
        """Wait briefly for the worker to persist the next state; return the latest one either way."""
        deadline = time.monotonic() + self.wait_seconds
        state: Optional[Dict[str, Any]] = None
        while True:
            try:
                state = self.svc.get_run(tenant, run_id)
            except KeyError:
                state = None
            if done(state) or time.monotonic() >= deadline:
                break
            time.sleep(0.1)
        if state is None:
            raise TemporalUnavailable("The run was queued but no Temporal worker picked it up. Is the worker running?")
        return state


def runner_from_env(service: WorkflowService, environ: Dict[str, str]):
    """TEMPORAL_ADDRESS switches the API to Temporal; otherwise runs execute in-process."""
    address = (environ.get("TEMPORAL_ADDRESS") or "").strip()
    if not address:
        return LocalRunner(service)
    return TemporalRunner(service, address, namespace=environ.get("TEMPORAL_NAMESPACE", "default"),
                          task_queue=environ.get("TEMPORAL_TASK_QUEUE", "playbooks"))
