"""Insert-only audit ledger (SQLite).

Meets the intent of NF-03 for local use: UPDATE and DELETE are blocked by triggers, every row carries
a SHA-256 hash chained to the previous row so any edit made around the triggers is detectable
(`verify_chain`). A MongoDB implementation would enforce the same rule with a role that only has
`insert` and `find` on the collection.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

OVERRIDE_FIELDS = [
    "timestamp", "item_id", "cutoff", "target_month", "baseline_quantity", "ml_recommended_quantity",
    "submitted_quantity", "variance_to_ml", "financial_impact_gbp", "reason_code", "planner_comment",
    "planner_id", "ip_address", "session_id", "tier", "risk_type",
]
COMMIT_FIELDS = ["timestamp", "cutoff", "planner_id", "items", "path"]
GENESIS = "0" * 64

_SCHEMA = """
CREATE TABLE IF NOT EXISTS overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    item_id INTEGER NOT NULL,
    cutoff TEXT NOT NULL,
    target_month TEXT NOT NULL,
    baseline_quantity REAL,
    ml_recommended_quantity REAL,
    submitted_quantity REAL NOT NULL,
    variance_to_ml REAL,
    financial_impact_gbp REAL,
    reason_code TEXT NOT NULL CHECK (length(trim(reason_code)) > 0),
    planner_comment TEXT,
    planner_id TEXT NOT NULL,
    ip_address TEXT,
    session_id TEXT,
    tier TEXT,
    risk_type TEXT,
    prev_hash TEXT NOT NULL,
    row_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_overrides_item ON overrides (cutoff, target_month, item_id, id);
CREATE TABLE IF NOT EXISTS commits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    cutoff TEXT NOT NULL UNIQUE,
    planner_id TEXT NOT NULL,
    items INTEGER NOT NULL,
    path TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    row_hash TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS overrides_no_update BEFORE UPDATE ON overrides
BEGIN SELECT RAISE(ABORT, 'overrides is insert-only'); END;
CREATE TRIGGER IF NOT EXISTS overrides_no_delete BEFORE DELETE ON overrides
BEGIN SELECT RAISE(ABORT, 'overrides is insert-only'); END;
CREATE TRIGGER IF NOT EXISTS commits_no_update BEFORE UPDATE ON commits
BEGIN SELECT RAISE(ABORT, 'commits is insert-only'); END;
CREATE TRIGGER IF NOT EXISTS commits_no_delete BEFORE DELETE ON commits
BEGIN SELECT RAISE(ABORT, 'commits is insert-only'); END;
"""


def _row_hash(prev_hash: str, payload: Dict[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256((prev_hash + body).encode("utf-8")).hexdigest()


class Ledger:
    def __init__(self, path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as con:
            con.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        con.row_factory = sqlite3.Row
        return con

    def _append(self, table: str, fields: List[str], rec: Dict[str, Any]) -> int:
        payload = {f: rec.get(f) for f in fields}
        with closing(self._connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                last = con.execute(f"SELECT row_hash FROM {table} ORDER BY id DESC LIMIT 1").fetchone()
                prev = last["row_hash"] if last else GENESIS
                digest = _row_hash(prev, payload)
                cols = fields + ["prev_hash", "row_hash"]
                cur = con.execute(
                    f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                    [payload[f] for f in fields] + [prev, digest],
                )
                con.execute("COMMIT")
                return int(cur.lastrowid)
            except sqlite3.IntegrityError as exc:
                con.execute("ROLLBACK")
                if "commits.cutoff" in str(exc):
                    raise ValueError(f"cutoff {payload['cutoff']} already committed") from exc
                raise
            except Exception:
                con.execute("ROLLBACK")
                raise

    def append_override(self, rec: Dict[str, Any]) -> int:
        reason = rec.get("reason_code")
        if reason is None or not str(reason).strip():
            raise ValueError("Reason code is required.")
        return self._append("overrides", OVERRIDE_FIELDS, rec)

    def append_commit(self, rec: Dict[str, Any]) -> int:
        return self._append("commits", COMMIT_FIELDS, rec)

    def overrides(
        self, cutoff: Optional[str] = None, item_id: Optional[int] = None, planner_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        clauses, args = [], []
        for col, val in (("cutoff", cutoff), ("item_id", item_id), ("planner_id", planner_id)):
            if val is not None:
                clauses.append(f"{col} = ?")
                args.append(val)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM overrides {where} ORDER BY id"
        if limit:
            sql += f" LIMIT {int(limit)}"
        with closing(self._connect()) as con:
            return [dict(r) for r in con.execute(sql, args).fetchall()]

    def latest_by_item(self, cutoff: str, target_month: str) -> Dict[int, Dict[str, Any]]:
        sql = (
            "SELECT o.* FROM overrides o JOIN ("
            "SELECT MAX(id) AS id FROM overrides WHERE cutoff = ? AND target_month = ? GROUP BY item_id"
            ") m ON o.id = m.id"
        )
        with closing(self._connect()) as con:
            return {int(r["item_id"]): dict(r) for r in con.execute(sql, [cutoff, target_month]).fetchall()}

    def latest_decisions(self, cutoff: str) -> Dict[Tuple[int, str], Dict[str, Any]]:
        """Latest ledger row per (item_id, target_month) for a cutoff."""
        sql = (
            "SELECT o.* FROM overrides o JOIN ("
            "SELECT MAX(id) AS id FROM overrides WHERE cutoff = ? GROUP BY item_id, target_month"
            ") m ON o.id = m.id"
        )
        with closing(self._connect()) as con:
            return {(int(r["item_id"]), r["target_month"]): dict(r) for r in con.execute(sql, [cutoff]).fetchall()}

    def commit_for(self, cutoff: str) -> Optional[Dict[str, Any]]:
        with closing(self._connect()) as con:
            row = con.execute("SELECT * FROM commits WHERE cutoff = ?", [cutoff]).fetchone()
            return dict(row) if row else None

    def verify_chain(self) -> Tuple[bool, Optional[int]]:
        """Recompute the hash chain of both tables. Returns (ok, id_of_first_bad_row)."""
        for table, fields in (("overrides", OVERRIDE_FIELDS), ("commits", COMMIT_FIELDS)):
            prev = GENESIS
            with closing(self._connect()) as con:
                for r in con.execute(f"SELECT * FROM {table} ORDER BY id"):
                    payload = {f: r[f] for f in fields}
                    if r["prev_hash"] != prev or _row_hash(prev, payload) != r["row_hash"]:
                        return False, int(r["id"])
                    prev = r["row_hash"]
        return True, None
