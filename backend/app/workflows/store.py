"""JSON-file storage for workflow versions and run states (same pattern as the app's other stores).

Saving a workflow never overwrites a version: each save gets the next version number, so a paused
run can always resume against the exact definition it started with.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.workflows.dsl import Workflow

WORKFLOW_ID = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,63}$")
RUN_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def _checked(value: str, pattern: "re.Pattern[str]", what: str) -> str:
    if not isinstance(value, str) or not pattern.match(value):
        raise KeyError(f"{what} {value!r} not found")
    return value


class WorkflowStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._lock = threading.Lock()

    def save(self, wf: Workflow) -> Workflow:
        with self._lock:
            folder = self.root / "definitions" / wf.id
            folder.mkdir(parents=True, exist_ok=True)
            latest = self._latest_version(wf.id)
            saved = wf.model_copy(update={"version": latest + 1})
            (folder / f"v{saved.version}.json").write_text(saved.model_dump_json(indent=2), encoding="utf-8")
            return saved

    def get(self, workflow_id: str, version: Optional[int] = None) -> Workflow:
        _checked(workflow_id, WORKFLOW_ID, "workflow")
        version = version or self._latest_version(workflow_id)
        path = self.root / "definitions" / workflow_id / f"v{version}.json"
        if not version or not path.exists():
            raise KeyError(f"workflow {workflow_id} v{version} not found")
        return Workflow.model_validate_json(path.read_text(encoding="utf-8"))

    def list(self) -> List[Dict[str, Any]]:
        base = self.root / "definitions"
        if not base.exists():
            return []
        out = []
        for folder in sorted(p for p in base.iterdir() if p.is_dir()):
            wf = self.get(folder.name)
            out.append({"id": wf.id, "name": wf.name, "version": wf.version})
        return out

    def _latest_version(self, workflow_id: str) -> int:
        folder = self.root / "definitions" / workflow_id
        if not folder.exists():
            return 0
        versions = [int(p.stem[1:]) for p in folder.glob("v*.json") if p.stem[1:].isdigit()]
        return max(versions, default=0)


class RunStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root) / "runs"
        self._lock = threading.Lock()

    def save(self, state: Dict[str, Any]) -> None:
        _checked(state["run_id"], RUN_ID, "run")
        state["updated_at"] = datetime.now(timezone.utc).isoformat()  # wall clock, for polling clients
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            tmp = self.root / f"{state['run_id']}.json.tmp"
            tmp.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
            tmp.replace(self.root / f"{state['run_id']}.json")

    def get(self, run_id: str) -> Dict[str, Any]:
        _checked(run_id, RUN_ID, "run")
        path = self.root / f"{run_id}.json"
        if not path.exists():
            raise KeyError(f"run {run_id} not found")
        return json.loads(path.read_text(encoding="utf-8"))

    def list(self, workflow_id: Optional[str] = None, status: Optional[str] = None,
             limit: int = 50, updated_after: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Newest-first run summaries (no step log or outputs). `updated_after` returns oldest-first changes."""
        if not self.root.exists():
            return []
        runs = [json.loads(p.read_text(encoding="utf-8")) for p in self.root.glob("*.json")]
        runs = [r for r in runs if (workflow_id is None or r["workflow_id"] == workflow_id)
                and (status is None or r["status"] == status)]
        if updated_after is not None:
            after = updated_after if updated_after.tzinfo else updated_after.replace(tzinfo=timezone.utc)
            runs = [r for r in runs if r.get("updated_at") and datetime.fromisoformat(r["updated_at"]) > after]
            runs.sort(key=lambda r: r["updated_at"])  # oldest change first so pollers can advance a cursor
        else:
            runs.sort(key=lambda r: r["started_at"], reverse=True)
        keys = ("run_id", "workflow_id", "workflow_version", "status", "dry_run", "spent_eur", "pending",
                "error", "started_at", "finished_at", "started_by", "updated_at", "executor")
        return [{k: r.get(k) for k in keys} for r in runs[:limit]]

    def due(self, statuses: tuple = ("WAITING",)) -> List[Dict[str, Any]]:
        """Runs a scheduler should look at (e.g. waits that may have expired)."""
        if not self.root.exists():
            return []
        runs = [json.loads(p.read_text(encoding="utf-8")) for p in self.root.glob("*.json")]
        return [r for r in runs if r["status"] in statuses]
