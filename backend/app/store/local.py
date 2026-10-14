from __future__ import annotations

import json
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.orchestration.scheduler import DEFAULTS, TZ, next_run


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


class JsonCollection:
    def __init__(self, path: Path, default: Any) -> None:
        self.path = Path(path)
        self.default = default
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write(default)

    def _read(self) -> Any:
        if not self.path.exists():
            return deepcopy(self.default)
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return deepcopy(self.default)

    def _write(self, data: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    def read(self) -> Any:
        with self._lock:
            return self._read()

    def mutate(self, fn):
        with self._lock:
            data = self._read()
            result = fn(data)
            self._write(data)
            return result


class JsonRunStore:
    def __init__(self, path: Path) -> None:
        self.db = JsonCollection(path, [])

    def start(self, kind: str, user: str) -> str:
        run_id = _new_id()

        def op(rows):
            rows.append({"id": run_id, "kind": kind, "status": "RUNNING", "started_at": _now(), "started_by": user, "steps": []})
            return run_id

        return self.db.mutate(op)

    def step(self, run_id: str, step: Dict[str, Any]) -> None:
        def op(rows):
            for row in rows:
                if row.get("id") == run_id:
                    row.setdefault("steps", []).append(step)
                    break

        self.db.mutate(op)

    def finish(self, run_id: str, status: str, **extra) -> None:
        def op(rows):
            for row in rows:
                if row.get("id") == run_id:
                    row.update({"status": status, "finished_at": _now(), **extra})
                    break

        self.db.mutate(op)

    def running(self) -> bool:
        return any(row.get("status") == "RUNNING" for row in self.db.read())

    def mark_committed(self, cutoff: str) -> None:
        def op(rows):
            for row in rows:
                if row.get("status") == "AWAITING_REVIEW":
                    row.update({"status": "COMMITTED", "committed_cutoff": cutoff})

        self.db.mutate(op)

    def recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        rows = sorted(self.db.read(), key=lambda row: row.get("started_at", ""), reverse=True)
        return [dict(row) for row in rows[:limit]]

    def get(self, run_id: str) -> Optional[Dict[str, Any]]:
        return next((dict(row) for row in self.db.read() if row.get("id") == run_id), None)

    def fail_stale(self) -> List[str]:
        def op(rows):
            ids = [row["id"] for row in rows if row.get("status") == "RUNNING"]
            for row in rows:
                if row.get("status") == "RUNNING":
                    row.update({"status": "FAILED", "error": "server restarted during the run"})
            return ids

        return self.db.mutate(op)


class JsonChatStore:
    def __init__(self, path: Path) -> None:
        self.db = JsonCollection(path, [])

    def add(self, username: str, role: str, text: str) -> None:
        def op(rows):
            rows.append({"id": _new_id(), "username": username, "role": role, "text": text, "created_at": _now()})

        self.db.mutate(op)

    def history(self, username: str, limit: int = 50) -> List[Dict[str, Any]]:
        rows = [row for row in self.db.read() if row.get("username") == username]
        rows = sorted(rows, key=lambda row: row.get("created_at", ""))
        return [dict(row) for row in rows[-limit:]]

    def clear(self, username: str) -> None:
        def op(rows):
            rows[:] = [row for row in rows if row.get("username") != username]

        self.db.mutate(op)


class JsonScheduleStore:
    def __init__(self, path: Path) -> None:
        self.db = JsonCollection(path, [])
        self._ensure_defaults()

    def _ensure_defaults(self) -> None:
        now = datetime.now(timezone.utc)

        def op(rows):
            existing = {row.get("job") for row in rows}
            for job, spec in DEFAULTS.items():
                if job not in existing:
                    doc = {"job": job, **spec, "tz": TZ, "enabled": True, "history": []}
                    doc["next_run_at"] = next_run(doc, now).isoformat()
                    rows.append(doc)

        self.db.mutate(op)

    def all(self) -> List[Dict[str, Any]]:
        order = list(DEFAULTS)
        docs = [dict(row) for row in self.db.read() if row.get("job") in DEFAULTS]
        return sorted(docs, key=lambda row: order.index(row["job"]) if row.get("job") in order else len(order))

    def get(self, job: str) -> Optional[Dict[str, Any]]:
        if job not in DEFAULTS:
            return None
        return next((dict(row) for row in self.db.read() if row.get("job") == job), None)

    def update(self, job: str, spec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        spec = dict(spec)
        spec["next_run_at"] = next_run({**spec, "tz": TZ}, datetime.now(timezone.utc)).isoformat()

        def op(rows):
            for row in rows:
                if row.get("job") == job:
                    row.update(spec)
                    return dict(row)
            return None

        return self.db.mutate(op)

    def due(self, now: datetime) -> List[Dict[str, Any]]:
        return [dict(row) for row in self.db.read() if row.get("job") in DEFAULTS and row.get("enabled") and row.get("next_run_at", "") <= now.isoformat()]

    def claim(self, job: str, expected_next: str, new_next: str) -> bool:
        def op(rows):
            for row in rows:
                if row.get("job") == job and row.get("next_run_at") == expected_next:
                    row["next_run_at"] = new_next
                    return True
            return False

        return bool(self.db.mutate(op))

    def record(self, job: str, when: datetime, outcome: str) -> None:
        def op(rows):
            for row in rows:
                if row.get("job") == job:
                    row["last_run_at"] = when.isoformat()
                    row["last_outcome"] = outcome
                    history = row.setdefault("history", [])
                    history.append({"at": when.isoformat(), "outcome": outcome})
                    row["history"] = history[-10:]
                    break

        self.db.mutate(op)


class JsonNotificationStore:
    def __init__(self, path: Path) -> None:
        self.db = JsonCollection(path, [])

    def add(self, roles: List[str], kind: str, title: str, body: str, link: str = "", run_id: str = "") -> str:
        note_id = _new_id()

        def op(rows):
            rows.append({"id": note_id, "roles": roles, "kind": kind, "title": title, "body": body, "link": link,
                         "run_id": run_id, "created_at": _now(), "read_by": []})
            return note_id

        return self.db.mutate(op)

    def for_user(self, username: str, role: str, limit: int = 30) -> Dict[str, Any]:
        rows = [row for row in self.db.read() if role in row.get("roles", [])]
        rows = sorted(rows, key=lambda row: row.get("created_at", ""), reverse=True)
        unread = sum(1 for row in rows if username not in row.get("read_by", []))
        items = []
        for row in rows[:limit]:
            item = dict(row)
            item["read"] = username in item.pop("read_by", [])
            items.append(item)
        return {"unread": unread, "items": items}

    def mark_read(self, username: str, role: str, note_id: Optional[str] = None) -> int:
        def op(rows):
            count = 0
            for row in rows:
                if role not in row.get("roles", []):
                    continue
                if note_id is not None and row.get("id") != note_id:
                    continue
                read_by = row.setdefault("read_by", [])
                if username not in read_by:
                    read_by.append(username)
                    count += 1
            return count

        return int(self.db.mutate(op))


class JsonScenarioStore:
    def __init__(self, path: Path) -> None:
        self.db = JsonCollection(path, [])

    def list(self, cutoff: str) -> List[Dict[str, Any]]:
        rows = [row for row in self.db.read() if row.get("cutoff") == cutoff]
        rows = sorted(rows, key=lambda row: row.get("created_at", ""), reverse=True)
        return [dict(row) for row in rows]

    def get(self, scenario_id: str) -> Optional[Dict[str, Any]]:
        return next((dict(row) for row in self.db.read() if row.get("id") == scenario_id), None)

    def create(self, cutoff: str, name: str, description: str, edits: List[Dict[str, Any]], user: str) -> Dict[str, Any]:
        scenario_id = _new_id()
        doc = {"id": scenario_id, "cutoff": cutoff, "name": name, "description": description, "edits": edits,
               "created_by": user, "created_at": _now(), "updated_at": _now()}

        def op(rows):
            rows.append(doc)
            return dict(doc)

        return self.db.mutate(op)

    def update(self, scenario_id: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        def op(rows):
            for row in rows:
                if row.get("id") == scenario_id:
                    row.update({**fields, "updated_at": _now()})
                    return dict(row)
            return None

        return self.db.mutate(op)

    def delete(self, scenario_id: str) -> bool:
        def op(rows):
            before = len(rows)
            rows[:] = [row for row in rows if row.get("id") != scenario_id]
            return len(rows) != before

        return bool(self.db.mutate(op))
