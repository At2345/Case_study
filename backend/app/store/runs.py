"""MongoDB stores for pipeline runs (`cycle_runs`) and assistant conversations."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _out(doc: Dict[str, Any]) -> Dict[str, Any]:
    d = dict(doc)
    d["id"] = str(d.pop("_id"))
    return d


class RunStore:
    def __init__(self, db) -> None:
        self.col = db["cycle_runs"]
        self.col.create_index("started_at")

    def start(self, kind: str, user: str) -> str:
        return str(self.col.insert_one({"kind": kind, "status": "RUNNING", "started_at": _now(), "started_by": user,
                                        "steps": []}).inserted_id)

    def step(self, run_id: str, step: Dict[str, Any]) -> None:
        from bson import ObjectId
        self.col.update_one({"_id": ObjectId(run_id)}, {"$push": {"steps": step}})

    def finish(self, run_id: str, status: str, **extra) -> None:
        from bson import ObjectId
        self.col.update_one({"_id": ObjectId(run_id)}, {"$set": {"status": status, "finished_at": _now(), **extra}})

    def running(self) -> bool:
        return self.col.count_documents({"status": "RUNNING"}) > 0

    def mark_committed(self, cutoff: str) -> None:
        self.col.update_many({"status": "AWAITING_REVIEW"}, {"$set": {"status": "COMMITTED", "committed_cutoff": cutoff}})

    def recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        return [_out(d) for d in self.col.find({}, sort=[("started_at", -1)], limit=limit)]

    def fail_stale(self) -> List[str]:
        ids = [str(d["_id"]) for d in self.col.find({"status": "RUNNING"}, {"_id": 1})]
        self.col.update_many({"status": "RUNNING"}, {"$set": {"status": "FAILED", "error": "server restarted during the run"}})
        return ids


class ChatStore:
    def __init__(self, db) -> None:
        self.col = db["assistant_messages"]
        self.col.create_index([("username", 1), ("created_at", 1)])

    def add(self, username: str, role: str, text: str) -> None:
        self.col.insert_one({"username": username, "role": role, "text": text, "created_at": _now()})

    def history(self, username: str, limit: int = 50) -> List[Dict[str, Any]]:
        docs = list(self.col.find({"username": username}, sort=[("created_at", -1)], limit=limit))
        return [_out(d) for d in reversed(docs)]

    def clear(self, username: str) -> None:
        self.col.delete_many({"username": username})


def mongo_db(uri: str, name: str, client: Optional[Any] = None):
    from pymongo import MongoClient
    return (client or MongoClient(uri))[name]
