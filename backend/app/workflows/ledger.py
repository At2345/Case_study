"""Decision-outcome ledger for playbook runs (SQLite, insert-only, hash-chained).

Same integrity model as the override audit ledger (app.store.ledger): UPDATE and DELETE are blocked by
triggers and every row carries a SHA-256 hash chained to the previous row of its table (`verify`).

- decisions: one row per decision-relevant step of a run (agent proposals, actions, approval requests,
  approvals, rejections, failed actions). Written automatically from the run's step log; idempotent per
  (run_id, step index) so Temporal retries and repeated saves never duplicate a row.
- outcomes: what a run achieved, recorded later (loss avoided, value recovered, units sold, ...).

`performance` joins both into per-playbook figures: spend, measured value, value per euro spent,
approval and failure rates. This record is what lets playbooks be ranked by real results.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

GENESIS = "0" * 64
DECISION_CATEGORIES = ("agent", "action", "control")
DECISION_STATUSES = ("ok", "failed", "awaiting_approval", "approved", "rejected")
OUTCOME_METRICS = ("loss_avoided_eur", "recovered_eur", "units_sold", "sell_through_pct", "other")
VALUE_METRICS = ("loss_avoided_eur", "recovered_eur")  # money metrics summed into "value created"
SUBJECT_KEYS = ("sku", "marketplace", "from_marketplace", "to_marketplace", "units", "units_at_risk",
                "exposure_eur", "pct_change", "action")
MAX_DETAIL = 4000

DECISION_FIELDS = ["event_key", "timestamp", "tenant", "run_id", "step_index", "workflow_id", "workflow_version",
                   "node_id", "node_type", "category", "kind", "status", "actor", "dry_run", "cost_eur",
                   "reversible", "sku", "detail"]
OUTCOME_FIELDS = ["timestamp", "tenant", "run_id", "workflow_id", "node_id", "sku", "metric", "value",
                  "source", "recorded_by", "note", "event_key"]
OPTIONAL_HASH_FIELDS = ("event_key",)  # added later: hashed only when set, so older rows keep valid hashes

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key TEXT NOT NULL UNIQUE,
    timestamp TEXT NOT NULL,
    tenant TEXT NOT NULL,
    run_id TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    workflow_id TEXT NOT NULL,
    workflow_version INTEGER NOT NULL,
    node_id TEXT NOT NULL,
    node_type TEXT NOT NULL,
    category TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    actor TEXT,
    dry_run INTEGER NOT NULL,
    cost_eur REAL NOT NULL DEFAULT 0,
    reversible INTEGER,
    sku TEXT,
    detail TEXT,
    prev_hash TEXT NOT NULL,
    row_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decisions_run ON decisions (tenant, run_id, step_index);
CREATE INDEX IF NOT EXISTS idx_decisions_wf ON decisions (tenant, workflow_id);
CREATE TABLE IF NOT EXISTS outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    tenant TEXT NOT NULL,
    run_id TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    node_id TEXT,
    sku TEXT,
    metric TEXT NOT NULL,
    value REAL NOT NULL,
    source TEXT NOT NULL,
    recorded_by TEXT NOT NULL,
    note TEXT,
    prev_hash TEXT NOT NULL,
    row_hash TEXT NOT NULL,
    event_key TEXT
);
CREATE INDEX IF NOT EXISTS idx_outcomes_run ON outcomes (tenant, run_id);
CREATE TRIGGER IF NOT EXISTS decisions_no_update BEFORE UPDATE ON decisions
BEGIN SELECT RAISE(ABORT, 'decisions is insert-only'); END;
CREATE TRIGGER IF NOT EXISTS decisions_no_delete BEFORE DELETE ON decisions
BEGIN SELECT RAISE(ABORT, 'decisions is insert-only'); END;
CREATE TRIGGER IF NOT EXISTS outcomes_no_update BEFORE UPDATE ON outcomes
BEGIN SELECT RAISE(ABORT, 'outcomes is insert-only'); END;
CREATE TRIGGER IF NOT EXISTS outcomes_no_delete BEFORE DELETE ON outcomes
BEGIN SELECT RAISE(ABORT, 'outcomes is insert-only'); END;
"""


def _row_hash(prev_hash: str, payload: Dict[str, Any]) -> str:
    payload = {k: v for k, v in payload.items() if not (k in OPTIONAL_HASH_FIELDS and v is None)}
    body = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256((prev_hash + body).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DecisionLedger:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as con:
            con.executescript(_SCHEMA)
            cols = {r["name"] for r in con.execute("PRAGMA table_info(outcomes)")}
            if "event_key" not in cols:  # migrate ledgers created before run-produced outcomes existed
                con.execute("ALTER TABLE outcomes ADD COLUMN event_key TEXT")
            con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_outcomes_event ON outcomes (event_key)")

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        con.row_factory = sqlite3.Row
        return con

    # ------------------------------------------------------------------ writes
    def record_run(self, state: Dict[str, Any], node_info: Dict[str, Dict[str, Any]]) -> int:
        """Append decision rows for every decision-relevant step not yet recorded. Returns rows added.

        node_info maps node_id -> {"category", "reversible"} from the workflow's node types.
        """
        rows = list(_decision_rows(state, node_info))
        produced = list(_run_outcomes(state))
        if not rows and not produced:
            return 0
        added = 0
        with closing(self._connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                for table, fields, recs in (("decisions", DECISION_FIELDS, rows),
                                            ("outcomes", OUTCOME_FIELDS, produced)):
                    if not recs:
                        continue
                    keys = [r["event_key"] for r in recs]
                    existing = {r["event_key"] for r in con.execute(
                        f"SELECT event_key FROM {table} WHERE event_key IN ({','.join('?' for _ in keys)})", keys)}
                    prev = self._last_hash(con, table)
                    for rec in recs:
                        if rec["event_key"] in existing:
                            continue
                        prev = self._insert(con, table, fields, rec, prev)
                        added += 1
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise
        return added

    def record_outcome(self, rec: Dict[str, Any]) -> int:
        if rec.get("metric") not in OUTCOME_METRICS:
            raise ValueError(f"metric must be one of {list(OUTCOME_METRICS)}")
        payload = {f: rec.get(f) for f in OUTCOME_FIELDS}
        payload["timestamp"] = payload["timestamp"] or _now()
        payload["value"] = float(payload["value"])
        with closing(self._connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                self._insert(con, "outcomes", OUTCOME_FIELDS, payload, self._last_hash(con, "outcomes"))
                row_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
                con.execute("COMMIT")
                return int(row_id)
            except Exception:
                con.execute("ROLLBACK")
                raise

    @staticmethod
    def _last_hash(con: sqlite3.Connection, table: str) -> str:
        last = con.execute(f"SELECT row_hash FROM {table} ORDER BY id DESC LIMIT 1").fetchone()
        return last["row_hash"] if last else GENESIS

    @staticmethod
    def _insert(con: sqlite3.Connection, table: str, fields: List[str], rec: Dict[str, Any], prev: str) -> str:
        payload = {f: rec.get(f) for f in fields}
        digest = _row_hash(prev, payload)
        cols = fields + ["prev_hash", "row_hash"]
        con.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                    [payload[f] for f in fields] + [prev, digest])
        return digest

    # ------------------------------------------------------------------ reads
    def decisions(self, tenant: str, run_id: Optional[str] = None, workflow_id: Optional[str] = None,
                  limit: int = 500) -> List[Dict[str, Any]]:
        return self._select("decisions", tenant, run_id, workflow_id, limit, order="step_index, id")

    def outcomes(self, tenant: str, run_id: Optional[str] = None, workflow_id: Optional[str] = None,
                 limit: int = 500) -> List[Dict[str, Any]]:
        return self._select("outcomes", tenant, run_id, workflow_id, limit, order="id")

    def _select(self, table: str, tenant: str, run_id: Optional[str], workflow_id: Optional[str], limit: int,
                order: str) -> List[Dict[str, Any]]:
        where, args = ["tenant = ?"], [tenant]
        if run_id:
            where.append("run_id = ?")
            args.append(run_id)
        if workflow_id:
            where.append("workflow_id = ?")
            args.append(workflow_id)
        sql = f"SELECT * FROM {table} WHERE {' AND '.join(where)} ORDER BY {order} LIMIT ?"
        with closing(self._connect()) as con:
            rows = [dict(r) for r in con.execute(sql, args + [limit])]
        for r in rows:
            r.pop("prev_hash", None)
            r.pop("row_hash", None)
            if table == "outcomes":
                r.pop("event_key", None)  # internal idempotency key
            if "detail" in r and r["detail"]:
                r["detail"] = json.loads(r["detail"])
            if "dry_run" in r:
                r["dry_run"] = bool(r["dry_run"])
            if r.get("reversible") is not None:
                r["reversible"] = bool(r["reversible"])
        return rows

    def performance(self, tenant: str, workflow_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Per-playbook results from real (non-dry) runs, ranked by measured value per euro spent."""
        where, args = "tenant = ? AND dry_run = 0", [tenant]
        if workflow_id:
            where += " AND workflow_id = ?"
            args.append(workflow_id)
        with closing(self._connect()) as con:
            dec = con.execute(f"""
                SELECT workflow_id,
                       COUNT(DISTINCT run_id) AS runs,
                       SUM(CASE WHEN category = 'action' AND status = 'ok' THEN 1 ELSE 0 END) AS actions,
                       SUM(CASE WHEN category = 'action' AND status = 'failed' THEN 1 ELSE 0 END) AS failed_actions,
                       SUM(CASE WHEN status = 'awaiting_approval' THEN 1 ELSE 0 END) AS approval_requests,
                       SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS approved,
                       SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS rejected,
                       ROUND(SUM(cost_eur), 2) AS spent_eur
                FROM decisions WHERE {where} GROUP BY workflow_id""", args).fetchall()
            real_runs = {r["run_id"] for r in con.execute(
                f"SELECT DISTINCT run_id FROM decisions WHERE {where}", args)}
            out_where = "tenant = ?" + (" AND workflow_id = ?" if workflow_id else "")
            outs = con.execute(f"""
                SELECT workflow_id, run_id, metric, SUM(value) AS total FROM outcomes
                WHERE {out_where} GROUP BY workflow_id, run_id, metric""", args[:2] if workflow_id else args[:1]).fetchall()
        value: Dict[str, Dict[str, Any]] = {}
        for o in outs:
            if o["run_id"] not in real_runs:  # outcomes of dry runs never count
                continue
            v = value.setdefault(o["workflow_id"], {"value_eur": 0.0, "measured_runs": set(), "metrics": {}})
            v["metrics"][o["metric"]] = round(v["metrics"].get(o["metric"], 0.0) + float(o["total"]), 2)
            v["measured_runs"].add(o["run_id"])
            if o["metric"] in VALUE_METRICS:
                v["value_eur"] += float(o["total"])
        result = []
        for d in dec:
            v = value.get(d["workflow_id"], {"value_eur": 0.0, "measured_runs": set(), "metrics": {}})
            spent, val = float(d["spent_eur"] or 0), round(v["value_eur"], 2)
            decided = (d["approved"] or 0) + (d["rejected"] or 0)
            attempted = (d["actions"] or 0) + (d["failed_actions"] or 0)
            result.append({
                "workflow_id": d["workflow_id"], "runs": d["runs"], "measured_runs": len(v["measured_runs"]),
                "actions": d["actions"] or 0, "failed_actions": d["failed_actions"] or 0,
                "approval_requests": d["approval_requests"] or 0, "approved": d["approved"] or 0,
                "rejected": d["rejected"] or 0, "spent_eur": round(spent, 2), "value_eur": val,
                "net_eur": round(val - spent, 2),
                "value_per_eur": round(val / spent, 2) if spent > 0 else None,
                "approval_rate": round((d["approved"] or 0) / decided, 3) if decided else None,
                "failure_rate": round((d["failed_actions"] or 0) / attempted, 3) if attempted else None,
                "metrics": v["metrics"],
            })
        # Rank: measured value per euro first (unmeasured last), then net value.
        result.sort(key=lambda r: (r["value_per_eur"] is None, -(r["value_per_eur"] or 0), -r["net_eur"]))
        return result

    def verify(self) -> Dict[str, Any]:
        """Recompute both hash chains. Returns {"ok", "decisions", "outcomes", "first_bad"}."""
        report: Dict[str, Any] = {"ok": True, "first_bad": None}
        with closing(self._connect()) as con:
            for table, fields in (("decisions", DECISION_FIELDS), ("outcomes", OUTCOME_FIELDS)):
                prev, count = GENESIS, 0
                for r in con.execute(f"SELECT * FROM {table} ORDER BY id"):
                    payload = {f: r[f] for f in fields}
                    if r["prev_hash"] != prev or _row_hash(prev, payload) != r["row_hash"]:
                        report.update(ok=False, first_bad={"table": table, "id": r["id"]})
                        return {**report, table: count}
                    prev, count = r["row_hash"], count + 1
                report[table] = count
        return report


def _decision_rows(state: Dict[str, Any], node_info: Dict[str, Dict[str, Any]]) -> Iterable[Dict[str, Any]]:
    for index, step in enumerate(state.get("steps", [])):
        info = node_info.get(step["node"]) or {}
        category, status = info.get("category"), step["status"]
        if category not in DECISION_CATEGORIES or status not in DECISION_STATUSES:
            continue
        if category == "control" and status == "ok":
            continue  # the approval node's own "ok" duplicates the approved/rejected row
        output = step.get("output") or {}
        kind = {"approved": "human_decision", "rejected": "human_decision",
                "awaiting_approval": "approval_request"}.get(status) or (
            "measurement" if "outcomes" in output else "proposal" if category == "agent" else "action")
        detail = step.get("detail") or {}
        actor = detail.get("by") if kind == "human_decision" else state.get("started_by")
        subject = {k: output[k] for k in SUBJECT_KEYS if k in output}
        body = {"subject": subject, "port": step.get("port"), **({"detail": detail} if detail else {})}
        text = json.dumps(body, default=str)
        yield {
            "event_key": f"{state['run_id']}:{index}",
            "timestamp": step.get("at") or _now(),
            "tenant": state.get("tenant") or "default",
            "run_id": state["run_id"], "step_index": index,
            "workflow_id": state["workflow_id"], "workflow_version": state["workflow_version"],
            "node_id": step["node"], "node_type": step["type"], "category": category, "kind": kind,
            "status": status, "actor": actor, "dry_run": 1 if state.get("dry_run") else 0,
            "cost_eur": float(step.get("cost_eur") or 0) if status == "ok" else 0.0,
            "reversible": None if info.get("reversible") is None else (1 if info["reversible"] else 0),
            "sku": subject.get("sku"),
            "detail": text if len(text) <= MAX_DETAIL else json.dumps({"subject": subject, "truncated": True}),
        }


def _run_outcomes(state: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    """Outcomes produced by measurement steps (output["outcomes"]); never for dry runs."""
    if state.get("dry_run"):
        return
    for index, step in enumerate(state.get("steps", [])):
        if step["status"] != "ok":
            continue
        for i, o in enumerate((step.get("output") or {}).get("outcomes") or []):
            if not isinstance(o, dict) or o.get("metric") not in OUTCOME_METRICS:
                continue
            yield {
                "event_key": f"{state['run_id']}:{index}:{i}",
                "timestamp": step.get("at") or _now(), "tenant": state.get("tenant") or "default",
                "run_id": state["run_id"], "workflow_id": state["workflow_id"], "node_id": step["node"],
                "sku": o.get("sku"), "metric": o["metric"], "value": float(o.get("value") or 0),
                "source": "playbook", "recorded_by": f"playbook:{step['node']}", "note": o.get("note"),
            }


def node_info_for(workflow: Any, registry: Any) -> Dict[str, Dict[str, Any]]:
    """node_id -> category/reversible, from the workflow definition and node registry."""
    info: Dict[str, Dict[str, Any]] = {}
    for n in workflow.nodes:
        try:
            t = registry.get(n.type)
        except KeyError:
            continue
        info[n.id] = {"category": t.category, "reversible": t.policy.reversible}
    return info
