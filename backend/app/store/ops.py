"""MongoDB stores for schedules, in-app notifications and what-if scenarios."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.orchestration.scheduler import DEFAULTS, TZ, next_run
from app.store.runs import _out


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _oid(value: str):
    from bson import ObjectId
    from bson.errors import InvalidId
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


class ScheduleStore:
    def __init__(self, db) -> None:
        self.col = db["schedules"]
        self.col.create_index("job", unique=True)
        now = datetime.now(timezone.utc)
        for job, spec in DEFAULTS.items():
            if self.col.count_documents({"job": job}) == 0:
                doc = {"job": job, **spec, "tz": TZ, "enabled": True, "history": []}
                doc["next_run_at"] = next_run(doc, now).isoformat()
                self.col.insert_one(doc)

    def all(self) -> List[Dict[str, Any]]:
        return [{k: v for k, v in d.items() if k != "_id"} for d in self.col.find({}, sort=[("job", 1)])]

    def get(self, job: str) -> Optional[Dict[str, Any]]:
        d = self.col.find_one({"job": job})
        return None if d is None else {k: v for k, v in d.items() if k != "_id"}

    def update(self, job: str, spec: Dict[str, Any]) -> Dict[str, Any]:
        nxt = next_run({**spec, "tz": TZ}, datetime.now(timezone.utc)).isoformat()
        self.col.update_one({"job": job}, {"$set": {**spec, "next_run_at": nxt}})
        return self.get(job)

    def due(self, now: datetime) -> List[Dict[str, Any]]:
        return [{k: v for k, v in d.items() if k != "_id"}
                for d in self.col.find({"enabled": True, "next_run_at": {"$lte": now.isoformat()}})]

    def claim(self, job: str, expected_next: str, new_next: str) -> bool:
        res = self.col.update_one({"job": job, "next_run_at": expected_next}, {"$set": {"next_run_at": new_next}})
        return res.modified_count == 1

    def record(self, job: str, when: datetime, outcome: str) -> None:
        self.col.update_one({"job": job}, {"$set": {"last_run_at": when.isoformat(), "last_outcome": outcome},
                                           "$push": {"history": {"$each": [{"at": when.isoformat(), "outcome": outcome}], "$slice": -10}}})


class NotificationStore:
    def __init__(self, db) -> None:
        self.col = db["notifications"]
        self.col.create_index([("created_at", -1)])

    def add(self, roles: List[str], kind: str, title: str, body: str, link: str = "", run_id: str = "") -> str:
        return str(self.col.insert_one({"roles": roles, "kind": kind, "title": title, "body": body, "link": link,
                                        "run_id": run_id, "created_at": _now(), "read_by": []}).inserted_id)

    def for_user(self, username: str, role: str, limit: int = 30) -> Dict[str, Any]:
        q = {"roles": role}
        items = []
        for d in self.col.find(q, sort=[("created_at", -1)], limit=limit):
            o = _out(d)
            o["read"] = username in o.pop("read_by", [])
            items.append(o)
        unread = self.col.count_documents({**q, "read_by": {"$ne": username}})
        return {"unread": unread, "items": items}

    def mark_read(self, username: str, role: str, note_id: Optional[str] = None) -> int:
        q: Dict[str, Any] = {"roles": role}
        if note_id is not None:
            oid = _oid(note_id)
            if oid is None:
                return 0
            q["_id"] = oid
        return self.col.update_many(q, {"$addToSet": {"read_by": username}}).modified_count


class ScenarioStore:
    def __init__(self, db) -> None:
        self.col = db["scenarios"]
        self.col.create_index([("cutoff", 1), ("created_at", -1)])

    def list(self, cutoff: str) -> List[Dict[str, Any]]:
        return [_out(d) for d in self.col.find({"cutoff": cutoff}, sort=[("created_at", -1)])]

    def get(self, scenario_id: str) -> Optional[Dict[str, Any]]:
        oid = _oid(scenario_id)
        d = self.col.find_one({"_id": oid}) if oid else None
        return None if d is None else _out(d)

    def create(self, cutoff: str, name: str, description: str, edits: List[Dict[str, Any]], user: str) -> Dict[str, Any]:
        doc = {"cutoff": cutoff, "name": name, "description": description, "edits": edits, "created_by": user,
               "created_at": _now(), "updated_at": _now()}
        return self.get(str(self.col.insert_one(doc).inserted_id))

    def update(self, scenario_id: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        self.col.update_one({"_id": _oid(scenario_id)}, {"$set": {**fields, "updated_at": _now()}})
        return self.get(scenario_id)

    def delete(self, scenario_id: str) -> bool:
        oid = _oid(scenario_id)
        return bool(oid) and self.col.delete_one({"_id": oid}).deleted_count == 1
