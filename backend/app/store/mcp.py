"""MCP server registry + call audit log (Mongo and local JSON variants)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.store.local import JsonCollection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Drop the Mongo _id but keep the slug `id` field."""
    return {k: v for k, v in doc.items() if k != "_id"}


class McpStore:
    """MongoDB-backed MCP server registry + call audit log."""

    def __init__(self, db) -> None:
        self.col = db["mcp_servers"]
        self.col.create_index("id", unique=True)
        self.calls = db["mcp_calls"]
        self.calls.create_index([("at", -1)])

    def list(self) -> List[Dict[str, Any]]:
        return [_strip(d) for d in self.col.find(sort=[("name", 1)])]

    def get(self, server_id: str) -> Optional[Dict[str, Any]]:
        d = self.col.find_one({"id": server_id})
        return None if d is None else _strip(d)

    def create(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        if self.col.count_documents({"id": doc["id"]}):
            raise ValueError(f"MCP server {doc['id']} already exists")
        self.col.insert_one({**doc, "created_at": _now(), "updated_at": _now(),
                             "status": "never_tested", "last_error": "", "discovered_tools": []})
        return self.get(doc["id"])

    def update(self, server_id: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        self.col.update_one({"id": server_id}, {"$set": {**fields, "updated_at": _now()}})
        return self.get(server_id)

    def delete(self, server_id: str) -> bool:
        return self.col.delete_one({"id": server_id}).deleted_count == 1

    def log_call(self, entry: Dict[str, Any]) -> None:
        self.calls.insert_one({**entry, "at": _now()})

    def calls(self, limit: int = 50) -> List[Dict[str, Any]]:
        return [_strip(d) for d in self.calls.find(sort=[("at", -1)], limit=limit)]


class JsonMcpStore:
    """Local JSON variant (no MongoDB): servers in mcp_servers.json, calls in mcp_calls.json."""

    def __init__(self, store_dir) -> None:
        self.db = JsonCollection(store_dir / "mcp_servers.json", [])
        self.calls_db = JsonCollection(store_dir / "mcp_calls.json", [])

    def list(self) -> List[Dict[str, Any]]:
        return [dict(row) for row in self.db.read()]

    def get(self, server_id: str) -> Optional[Dict[str, Any]]:
        return next((dict(row) for row in self.db.read() if row.get("id") == server_id), None)

    def create(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        if self.get(doc["id"]) is not None:
            raise ValueError(f"MCP server {doc['id']} already exists")
        full = {**doc, "created_at": _now(), "updated_at": _now(),
                "status": "never_tested", "last_error": "", "discovered_tools": []}

        def op(rows):
            rows.append(full)
            return dict(full)

        return self.db.mutate(op)

    def update(self, server_id: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        def op(rows):
            for row in rows:
                if row.get("id") == server_id:
                    row.update({**fields, "updated_at": _now()})
                    return dict(row)
            return None

        return self.db.mutate(op)

    def delete(self, server_id: str) -> bool:
        def op(rows):
            before = len(rows)
            rows[:] = [row for row in rows if row.get("id") != server_id]
            return len(rows) != before

        return bool(self.db.mutate(op))

    def log_call(self, entry: Dict[str, Any]) -> None:
        def op(rows):
            rows.append({**entry, "at": _now()})

        self.calls_db.mutate(op)

    def calls(self, limit: int = 50) -> List[Dict[str, Any]]:
        rows = sorted(self.calls_db.read(), key=lambda row: row.get("at", ""), reverse=True)
        return [dict(row) for row in rows[:limit]]
