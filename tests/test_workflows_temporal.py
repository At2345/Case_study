"""Temporal executor: durable waits, approval signals, refused decisions and the synchronous runner.

Uses Temporal's time-skipping test server (downloaded once by temporalio), so a 14-day wait takes seconds.
"""
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("temporalio")

from temporalio.testing import WorkflowEnvironment  # noqa: E402

from app.workflows.runners import TemporalRunner, TemporalUnavailable, runner_from_env, LocalRunner  # noqa: E402
from app.workflows.service import WorkflowService  # noqa: E402
from app.workflows.temporal_worker import make_worker  # noqa: E402
from app.workflows.temporal_workflow import WORKFLOW_NAME, temporal_workflow_id  # noqa: E402
from tests.test_workflows import DEEP, MILD, rescue_playbook  # noqa: E402
from tests.test_workflows_api import FakeNotes, approval_spec  # noqa: E402

QUEUE = "playbooks-test"
ADMIN = {"username": "root", "role": "admin"}


class Harness:
    """Time-skipping Temporal server + worker on a background event loop."""

    def __init__(self, svc: WorkflowService) -> None:
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.env = self.run(WorkflowEnvironment.start_time_skipping())
        self.pool = ThreadPoolExecutor(max_workers=4)

        async def build():  # the worker must be created inside its event loop
            return make_worker(self.env.client, svc, QUEUE, self.pool)

        self.worker = self.run(build())
        self.worker_future = asyncio.run_coroutine_threadsafe(self.worker.run(), self.loop)

    def run(self, coro, timeout=120):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    @property
    def address(self) -> str:
        return self.env.client.service_client.config.target_host

    def result(self, tenant, run_id):
        handle = self.env.client.get_workflow_handle(temporal_workflow_id(tenant, run_id))
        return self.run(handle.result())

    def skip(self, duration: timedelta) -> None:
        """Advance the test server clock; durable timers due in that window fire."""
        self.run(self.env.sleep(duration))

    def close(self):
        self.run(self.worker.shutdown())
        self.run(self.env.shutdown())
        self.pool.shutdown(wait=False)
        self.loop.call_soon_threadsafe(self.loop.stop)


@pytest.fixture
def stack(tmp_path):
    notes = FakeNotes()
    svc = WorkflowService(tmp_path, notes=notes)
    svc.save("acme", rescue_playbook().model_dump())
    svc.save("acme", approval_spec("admin"))
    h = Harness(svc)
    runner = TemporalRunner(svc, h.address, task_queue=QUEUE, wait_seconds=20)
    yield svc, h, runner, notes
    runner.close()
    h.close()


def test_durable_wait_resumes_on_temporal_timer(stack):
    svc, h, runner, _ = stack
    state = runner.start("acme", "rescue-de", MILD, "danny")
    assert state["status"] == "WAITING" and state["executor"] == "temporal"
    started = datetime.fromisoformat(state["started_at"])

    # The local scheduler hook must leave Temporal-owned runs alone, even long after the wait expired.
    assert svc.resume_due(now=datetime.now(timezone.utc) + timedelta(days=30)) == []

    h.skip(timedelta(days=13))  # timer not due yet
    assert svc.get_run("acme", state["run_id"])["status"] == "WAITING"
    h.skip(timedelta(days=2))  # 15 days in: the 14-day timer has fired
    summary = h.result("acme", state["run_id"])
    assert summary["status"] == "SUCCEEDED"
    final = svc.get_run("acme", state["run_id"])
    wait_ok = next(s for s in final["steps"] if s["node"] == "wait" and s["status"] == "ok")
    resumed = datetime.fromisoformat(wait_ok["output"]["resumed_at"])
    assert timedelta(days=14) <= resumed - started < timedelta(days=14, minutes=5)
    assert final["outputs"]["tell"]["recorded"] is True


def test_gate_approval_via_signal(stack):
    svc, h, runner, notes = stack
    state = runner.start("acme", "rescue-de", DEEP, "danny")
    assert state["status"] == "WAITING_APPROVAL" and state["pending"]["node"] == "transfer"
    assert [n["kind"] for n in notes.rows] == ["approval"]  # activity notified like the local path

    done = runner.approve("acme", state["run_id"], "transfer", True, ADMIN, note="FR has capacity")
    assert done["status"] == "SUCCEEDED" and done["outputs"]["transfer"]["units"] == 600
    assert h.result("acme", state["run_id"])["status"] == "SUCCEEDED"
    # Activities write the decision ledger exactly once per step, despite running in the Temporal worker.
    ledger = [(d["node_id"], d["status"], d["actor"]) for d in svc.decisions("acme", run_id=state["run_id"])]
    assert ledger == [("propose", "ok", "danny"), ("transfer", "awaiting_approval", "danny"),
                      ("transfer", "approved", "root"), ("transfer", "ok", "danny")]


def test_rejection_ends_run(stack):
    svc, h, runner, _ = stack
    state = runner.start("acme", "rescue-de", DEEP, "danny")
    rejected = runner.approve("acme", state["run_id"], "transfer", False, ADMIN)
    assert rejected["status"] == "REJECTED"
    assert h.result("acme", state["run_id"])["status"] == "REJECTED"


def test_refused_signal_keeps_waiting_for_valid_decision(stack):
    svc, h, runner, _ = stack
    state = runner.start("acme", "ask", {}, "danny")
    assert state["pending"]["node"] == "ok"
    handle = h.env.client.get_workflow_handle(temporal_workflow_id("acme", state["run_id"]))
    # Bypass the runner's pre-check: a planner may not approve an admin-only step.
    h.run(handle.signal("decide", {"node": "ok", "approved": True, "user": {"username": "danny", "role": "planner"}}))
    h.run(handle.signal("decide", {"node": "ok", "approved": True, "user": ADMIN}))
    assert h.result("acme", state["run_id"])["status"] == "SUCCEEDED"
    steps = svc.get_run("acme", state["run_id"])["steps"]
    assert [s["detail"]["by"] for s in steps if s["status"] == "approved"] == ["root"]


def test_runner_checks_before_touching_temporal(stack):
    svc, h, runner, _ = stack
    with pytest.raises(KeyError):
        runner.start("acme", "missing", {}, "danny")
    state = runner.start("acme", "ask", {}, "danny")
    with pytest.raises(PermissionError):
        runner.approve("acme", state["run_id"], "ok", True, {"username": "danny", "role": "planner"})
    with pytest.raises(Exception, match="not awaiting approval"):
        runner.approve("acme", state["run_id"], "tell", True, ADMIN)
    runner.approve("acme", state["run_id"], "ok", True, ADMIN)


def test_workflow_module_passes_sandbox_validation():
    """Guards against heavy imports (httpx, pydantic models with side effects) leaking into the sandbox."""
    from temporalio import workflow as wf
    from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner

    from app.workflows.temporal_workflow import PlaybookRun

    async def prepare():
        SandboxedWorkflowRunner().prepare_workflow(wf._Definition.must_from_class(PlaybookRun))

    asyncio.run(prepare())


def test_unreachable_temporal_and_env_switch(tmp_path):
    svc = WorkflowService(tmp_path)
    svc.save("acme", rescue_playbook().model_dump())
    runner = TemporalRunner(svc, "127.0.0.1:1", rpc_timeout=5)
    try:
        with pytest.raises(TemporalUnavailable):
            runner.start("acme", "rescue-de", MILD, "danny")
    finally:
        runner.close()
    assert isinstance(runner_from_env(svc, {}), LocalRunner)
    switched = runner_from_env(svc, {"TEMPORAL_ADDRESS": "localhost:7233"})
    assert switched.name == "temporal" and switched.task_queue == "playbooks"
    switched.close()
    assert WORKFLOW_NAME == "PlaybookRun"
