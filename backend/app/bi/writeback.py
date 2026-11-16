"""Dashboard write-back (PR-5.3): authenticated, idempotent, audited mutations.

The dashboard stops being read-only here. Three mutations are supported, and each
one is authenticated, validated, idempotent and appended to a hash-chained log:

* annotate an exception, a segment or a metric,
* propose an action from an RCA cause, routed to that cause's named approver,
* record the approver's decision on the proposed action.

Nothing is updated in place. An action's state is derived from its latest event,
so the log stays append-only and every transition is attributable to a person.
Retrying a mutation with the same idempotency key returns the original row rather
than writing a second one, which is what makes a double-clicked button safe.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.bi.tenancy import DEFAULT_TENANT, TENANT_COLUMN, normalise

GENESIS = "0" * 64
MAX_BODY = 2000
ANNOTATION_TARGETS = {"item", "segment", "metric", "cause"}
DECISIONS = {"approved", "rejected"}

_ANNOTATION_FIELDS = [
    TENANT_COLUMN, "timestamp", "cutoff", "target_type", "target_id", "body", "author", "role",
    "ip_address", "session_id", "idempotency_key",
]
_ACTION_FIELDS = [
    TENANT_COLUMN, "timestamp", "action_id", "event", "cutoff", "series", "month", "driver",
    "approver", "note", "actor", "role", "ip_address", "session_id", "idempotency_key",
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS annotations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    timestamp TEXT NOT NULL,
    cutoff TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    body TEXT NOT NULL CHECK (length(trim(body)) > 0),
    author TEXT NOT NULL,
    role TEXT NOT NULL,
    ip_address TEXT,
    session_id TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    prev_hash TEXT NOT NULL,
    row_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_annotations_target ON annotations (target_type, target_id, id);
CREATE TABLE IF NOT EXISTS action_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    timestamp TEXT NOT NULL,
    action_id TEXT NOT NULL,
    event TEXT NOT NULL CHECK (event IN ('proposed', 'approved', 'rejected')),
    cutoff TEXT NOT NULL,
    series TEXT NOT NULL,
    month TEXT NOT NULL,
    driver TEXT,
    approver TEXT NOT NULL,
    note TEXT,
    actor TEXT NOT NULL,
    role TEXT NOT NULL,
    ip_address TEXT,
    session_id TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    prev_hash TEXT NOT NULL,
    row_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_action_events_action ON action_events (action_id, id);
CREATE TRIGGER IF NOT EXISTS annotations_no_update BEFORE UPDATE ON annotations
BEGIN SELECT RAISE(ABORT, 'annotations is insert-only'); END;
CREATE TRIGGER IF NOT EXISTS annotations_no_delete BEFORE DELETE ON annotations
BEGIN SELECT RAISE(ABORT, 'annotations is insert-only'); END;
CREATE TRIGGER IF NOT EXISTS action_events_no_update BEFORE UPDATE ON action_events
BEGIN SELECT RAISE(ABORT, 'action_events is insert-only'); END;
CREATE TRIGGER IF NOT EXISTS action_events_no_delete BEFORE DELETE ON action_events
BEGIN SELECT RAISE(ABORT, 'action_events is insert-only'); END;
"""


class Conflict(Exception):
    """The mutation is valid but the target has moved; the caller must re-read."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_hash(prev_hash: str, payload: Dict[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256((prev_hash + body).encode("utf-8")).hexdigest()


def _chain_ok(row: sqlite3.Row, fields: List[str], prev: str) -> bool:
    """Does this row continue the chain from `prev`?"""
    if row["prev_hash"] != prev:
        return False
    if _row_hash(prev, {f: row[f] for f in fields}) == row["row_hash"]:
        return True
    # Rows written before tenant scoping (PR-8.1) were hashed without tenant_id.
    # They are accepted only while bound to the default tenant, so the exemption
    # cannot be used to move a row from one tenant to another.
    if row[TENANT_COLUMN] != DEFAULT_TENANT:
        return False
    legacy = {f: row[f] for f in fields if f != TENANT_COLUMN}
    return _row_hash(prev, legacy) == row["row_hash"]


def _migrate(con: sqlite3.Connection) -> None:
    """Add tenant_id to a log written before tenant scoping existed."""
    for table in ("annotations", "action_events"):
        columns = {r[1] for r in con.execute(f"PRAGMA main.table_info({table})")}
        if TENANT_COLUMN not in columns:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {TENANT_COLUMN} TEXT NOT NULL "
                        f"DEFAULT '{DEFAULT_TENANT}'")


def _clean_body(value: Any, field: str = "body") -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required.")
    if len(text) > MAX_BODY:
        raise ValueError(f"{field} exceeds {MAX_BODY} characters.")
    return text


class WriteBack:
    """Append-only store for dashboard annotations and RCA action decisions.

    One instance is one tenant's view of the log: every read is filtered to that
    tenant and every write is stamped with it. The hash chain itself is global,
    because each row commits to the row before it regardless of tenant.
    """

    def __init__(self, path, tenant: str) -> None:
        self.path = Path(path)
        self.tenant = normalise(tenant)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as con:
            con.executescript(_SCHEMA)
            _migrate(con)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        con.row_factory = sqlite3.Row
        return con

    def _append(self, table: str, fields: List[str], rec: Dict[str, Any]) -> Dict[str, Any]:
        key = rec.get("idempotency_key")
        if not key:
            raise ValueError("idempotency_key is required.")
        with closing(self._connect()) as con:
            existing = con.execute(
                f"SELECT * FROM {table} WHERE idempotency_key = ? AND {TENANT_COLUMN} = ?",
                [key, self.tenant]).fetchone()
            if existing is not None:
                return {**dict(existing), "replayed": True}
            con.execute("BEGIN IMMEDIATE")
            try:
                last = con.execute(f"SELECT row_hash FROM {table} ORDER BY id DESC LIMIT 1").fetchone()
                prev = last["row_hash"] if last else GENESIS
                # The tenant is forced from the session, never taken from the caller.
                payload = {f: (self.tenant if f == TENANT_COLUMN else rec.get(f)) for f in fields}
                digest = _row_hash(prev, payload)
                cols = fields + ["prev_hash", "row_hash"]
                cur = con.execute(
                    f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                    [payload[f] for f in fields] + [prev, digest])
                row_id = int(cur.lastrowid)
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise
            row = con.execute(f"SELECT * FROM {table} WHERE id = ?", [row_id]).fetchone()
            return {**dict(row), "replayed": False}

    # ------------------------------------------------------------------ annotations
    def annotate(self, rec: Dict[str, Any]) -> Dict[str, Any]:
        target_type = str(rec.get("target_type") or "").strip()
        if target_type not in ANNOTATION_TARGETS:
            raise ValueError(f"target_type must be one of {sorted(ANNOTATION_TARGETS)}.")
        target_id = str(rec.get("target_id") or "").strip()
        if not target_id:
            raise ValueError("target_id is required.")
        return self._append("annotations", _ANNOTATION_FIELDS, {
            **rec,
            "timestamp": rec.get("timestamp") or _now(),
            "target_type": target_type,
            "target_id": target_id,
            "body": _clean_body(rec.get("body")),
        })

    def annotations(self, target_type: Optional[str] = None, target_id: Optional[str] = None,
                    cutoff: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        clauses, args = [f"{TENANT_COLUMN} = ?"], [self.tenant]
        for col, val in (("target_type", target_type), ("target_id", target_id), ("cutoff", cutoff)):
            if val:
                clauses.append(f"{col} = ?")
                args.append(val)
        where = f"WHERE {' AND '.join(clauses)}"
        sql = f"SELECT * FROM annotations {where} ORDER BY id DESC LIMIT ?"
        with closing(self._connect()) as con:
            return [dict(r) for r in con.execute(sql, args + [int(limit)]).fetchall()]

    # ------------------------------------------------------------------ actions
    def propose(self, rec: Dict[str, Any]) -> Dict[str, Any]:
        approver = str(rec.get("approver") or "").strip()
        if not approver:
            raise ValueError("approver is required: a cause cannot be routed to nobody.")
        return self._append("action_events", _ACTION_FIELDS, {
            **rec,
            "timestamp": rec.get("timestamp") or _now(),
            "action_id": rec.get("action_id") or uuid.uuid4().hex,
            "event": "proposed",
            "approver": approver,
            "note": _clean_body(rec.get("note"), "note"),
        })

    def _latest(self, action_id: str) -> Optional[Dict[str, Any]]:
        with closing(self._connect()) as con:
            row = con.execute(
                f"SELECT * FROM action_events WHERE action_id = ? AND {TENANT_COLUMN} = ? "
                "ORDER BY id DESC LIMIT 1",
                [action_id, self.tenant]).fetchone()
            return dict(row) if row else None

    def state_of(self, action_id: str) -> Optional[str]:
        """pending / approved / rejected, derived from the latest event."""
        latest = self._latest(action_id)
        if latest is None:
            return None
        return "pending" if latest["event"] == "proposed" else latest["event"]

    def decide(self, action_id: str, decision: str, note: Any, expected_state: Optional[str],
               actor: str, role: str, ip_address: Optional[str], session_id: Optional[str],
               idempotency_key: str) -> Dict[str, Any]:
        if decision not in DECISIONS:
            raise ValueError(f"decision must be one of {sorted(DECISIONS)}.")
        current = self.state_of(action_id)
        if current is None:
            raise LookupError(f"Unknown action {action_id}.")
        if expected_state and expected_state != current:
            raise Conflict(f"Action {action_id} is {current}, not {expected_state}.")
        if current != "pending":
            raise Conflict(f"Action {action_id} is already {current}.")
        proposed = self._latest(action_id)
        return self._append("action_events", _ACTION_FIELDS, {
            "timestamp": _now(), "action_id": action_id, "event": decision,
            "cutoff": proposed["cutoff"], "series": proposed["series"], "month": proposed["month"],
            "driver": proposed["driver"], "approver": proposed["approver"],
            "note": _clean_body(note, "note"), "actor": actor, "role": role,
            "ip_address": ip_address, "session_id": session_id, "idempotency_key": idempotency_key,
        })

    def actions(self, cutoff: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """One row per action, carrying its derived state and full event history."""
        sql = ("SELECT e.* FROM action_events e JOIN ("
               f"SELECT action_id, MAX(id) AS id FROM action_events WHERE {TENANT_COLUMN} = ? "
               ") m ON e.id = m.id")
        args: List[Any] = [self.tenant]
        if cutoff:
            sql += " WHERE e.cutoff = ?"
            args.append(cutoff)
        sql += " ORDER BY e.id DESC LIMIT ?"
        args.append(int(limit))
        with closing(self._connect()) as con:
            rows = [dict(r) for r in con.execute(sql, args).fetchall()]
            for row in rows:
                history = con.execute(
                    "SELECT event, actor, role, note, timestamp FROM action_events "
                    f"WHERE action_id = ? AND {TENANT_COLUMN} = ? ORDER BY id",
                    [row["action_id"], self.tenant]).fetchall()
                row["state"] = "pending" if row["event"] == "proposed" else row["event"]
                row["history"] = [dict(h) for h in history]
        return rows

    # ------------------------------------------------------------------ integrity
    def verify_chain(self) -> Tuple[bool, Optional[int]]:
        """Verify the whole log. The chain is global, so this is not tenant-scoped."""
        for table, fields in (("annotations", _ANNOTATION_FIELDS), ("action_events", _ACTION_FIELDS)):
            prev = GENESIS
            with closing(self._connect()) as con:
                for row in con.execute(f"SELECT * FROM {table} ORDER BY id"):
                    if not _chain_ok(row, fields, prev):
                        return False, int(row["id"])
                    prev = row["row_hash"]
        return True, None
