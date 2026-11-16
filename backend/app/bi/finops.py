"""Cost attribution and sprawl governance (PR-8.3).

There is no cloud warehouse here, so `ACCOUNT_USAGE` has no analogue and no dollar figure
can be computed. What can be measured is measured: every governed metric query carries a
tag, every tag is appended to a local ledger, and the report attributes statements and
milliseconds to a team, a pipeline and a workload. The dollar thresholds from the plan are
declared as policy and reported as inactive, because inventing a cost model would be the
one thing this increment must not do.

The tag is a required argument of `semantics.evaluate`, so a metric query cannot be issued
without one — the same default-deny shape as the tenant filter and the prompt redactor.
Attribution is observability, so a failure to write the ledger logs and continues rather
than failing the query it was measuring.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from app.config import settings

log = logging.getLogger("sop.finops")

LOG_FILE = "finops.db"

# The taxonomy from the plan: every query is attributed to a team, a pipeline, an
# environment and a workload.
WORKLOADS = ("bi", "ai", "export", "admin")

# Consumer team per role. A role with no declared team cannot be attributed, so it is
# refused rather than filed under a catch-all bucket that would hide it.
TEAMS = {
    "planner": "Demand Planning",
    "admin": "Platform",
    "auditor": "Audit",
}

# The BI read surfaces that must be exercised for a dashboard to count as adopted.
KNOWN_PIPELINES = ("dashboard", "kpi_tree", "metrics", "ask", "diagnostics", "contracts")

TIERS: Sequence[Dict[str, Any]] = (
    {"id": "attribution", "period": "month", "amount_usd": 15000,
     "obligation": "Query attribution becomes mandatory on every query."},
    {"id": "capacity", "period": "year", "amount_usd": 250000,
     "obligation": "Evaluate a 1-3 year capacity pre-purchase at a 15-25% discount."},
    {"id": "third_party", "period": "month", "amount_usd": 30000,
     "obligation": "Consider paid FinOps observability tooling."},
)


class QuotaExceeded(RuntimeError):
    """A workload has spent its allowance for the window."""


@dataclass(frozen=True)
class QueryTag:
    """The cost tag carried by one query."""

    team: str
    pipeline: str
    workload: str
    env: str = "local"

    def __post_init__(self) -> None:
        if self.workload not in WORKLOADS:
            raise ValueError(f"Unknown workload {self.workload!r}; expected one of {list(WORKLOADS)}")
        if not self.team or not self.pipeline:
            raise ValueError("A query tag needs a team and a pipeline")

    def as_dict(self) -> Dict[str, str]:
        return {"team": self.team, "pipeline": self.pipeline,
                "workload": self.workload, "env": self.env}


def tag_for(user: Dict[str, Any], pipeline: str, workload: str = "bi") -> QueryTag:
    """Build the tag for an authenticated request. An unknown role has no owning team."""
    role = (user or {}).get("role", "")
    team = TEAMS.get(role)
    if team is None:
        raise ValueError(f"No owning team is declared for role {role!r}")
    return QueryTag(team=team, pipeline=pipeline, workload=workload, env=settings.app_env)


def log_path(path: Optional[Path] = None) -> Path:
    """The attribution ledger. `FINOPS_LOG` overrides it so tests do not touch `var/`."""
    if path is not None:
        return Path(path)
    override = os.getenv("FINOPS_LOG", "").strip()
    return Path(override) if override else settings.store_dir / LOG_FILE


def _ensure(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS query_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            kind TEXT NOT NULL,
            team TEXT NOT NULL,
            pipeline TEXT NOT NULL,
            env TEXT NOT NULL,
            workload TEXT NOT NULL,
            tenant TEXT NOT NULL,
            metric_ids TEXT NOT NULL DEFAULT '',
            statements INTEGER NOT NULL DEFAULT 0,
            duration_ms REAL NOT NULL DEFAULT 0
        )""")
    con.execute("CREATE INDEX IF NOT EXISTS query_log_ts ON query_log (ts)")


def record(tag: QueryTag, tenant: str, *, kind: str, metric_ids: Iterable[str] = (),
           statements: int = 0, duration_ms: float = 0.0, path: Optional[Path] = None) -> None:
    """Append one attribution row. Never raises: a broken ledger must not break a query."""
    try:
        target = log_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(target)) as con, con:
            _ensure(con)
            con.execute(
                "INSERT INTO query_log (ts, kind, team, pipeline, env, workload, tenant,"
                " metric_ids, statements, duration_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(), kind, tag.team, tag.pipeline, tag.env,
                 tag.workload, tenant, ",".join(metric_ids), int(statements), float(duration_ms)))
    except Exception as exc:  # attribution is observability, not a precondition
        log.warning("finops: could not record %s/%s: %s", tag.team, tag.pipeline, exc)


def _since(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _grouped(days: int, path: Optional[Path]) -> List[sqlite3.Row]:
    target = log_path(path)
    if not target.exists():
        return []
    with closing(sqlite3.connect(target)) as con:
        con.row_factory = sqlite3.Row
        _ensure(con)
        return con.execute(
            "SELECT team, pipeline, workload, kind, COUNT(*) AS n,"
            " SUM(statements) AS statements, SUM(duration_ms) AS duration_ms"
            " FROM query_log WHERE ts >= ? GROUP BY team, pipeline, workload, kind"
            " ORDER BY team, pipeline, kind", (_since(days),)).fetchall()


def _rollup(rows: List[sqlite3.Row], key: str) -> List[Dict[str, Any]]:
    """Sum the grouped rows by one dimension, keeping the two kinds apart."""
    buckets: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        bucket = buckets.setdefault(row[key], {key: row[key], "metric_queries": 0, "requests": 0,
                                               "statements": 0, "duration_ms": 0.0})
        bucket["metric_queries" if row["kind"] == "metric" else "requests"] += row["n"]
        bucket["statements"] += row["statements"] or 0
        bucket["duration_ms"] = round(bucket["duration_ms"] + (row["duration_ms"] or 0.0), 1)
    return sorted(buckets.values(), key=lambda b: (-b["statements"], b["duration_ms"], b[key]))


def durations(days: int = 30, path: Optional[Path] = None) -> Dict[str, List[float]]:
    """Raw per-request durations by pipeline, for percentile work.

    `attribute()` sums, which is enough for a total and not enough for a p95: a percentile taken
    over per-bucket averages is not a percentile of anything. This returns the individual samples.
    """
    target = log_path(path)
    if not target.exists():
        return {}
    with closing(sqlite3.connect(target)) as con:
        _ensure(con)
        rows = con.execute(
            "SELECT pipeline, duration_ms FROM query_log WHERE ts >= ? ORDER BY pipeline",
            (_since(days),)).fetchall()
    out: Dict[str, List[float]] = {}
    for pipeline, duration in rows:
        out.setdefault(pipeline, []).append(float(duration or 0.0))
    return out


def attribute(days: int = 30, path: Optional[Path] = None) -> Dict[str, Any]:
    """Where the analytical compute went, by team, pipeline and workload."""
    rows = _grouped(days, path)
    return {
        "window_days": days,
        "metric_queries": sum(r["n"] for r in rows if r["kind"] == "metric"),
        "requests": sum(r["n"] for r in rows if r["kind"] == "request"),
        "statements": sum(r["statements"] or 0 for r in rows),
        "duration_ms": round(sum(r["duration_ms"] or 0.0 for r in rows), 1),
        "by_team": _rollup(rows, "team"),
        "by_pipeline": _rollup(rows, "pipeline"),
        "by_workload": _rollup(rows, "workload"),
    }


def storage(mart_path: Path) -> Dict[str, Any]:
    """Storage intelligence: what the mart costs on disk, per table."""
    mart_path = Path(mart_path)
    if not mart_path.exists():
        return {"path": str(mart_path), "bytes": 0, "tables": [], "dbstat": False}
    tables: List[Dict[str, Any]] = []
    with closing(sqlite3.connect(f"file:{mart_path}?mode=ro", uri=True)) as con:
        names = [r[0] for r in con.execute(
            "SELECT name FROM main.sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
        for name in sorted(names):
            rows = con.execute(f'SELECT COUNT(*) FROM main."{name}"').fetchone()[0]
            try:
                size = con.execute("SELECT SUM(pgsize) FROM dbstat WHERE name = ?", (name,)).fetchone()[0]
            except sqlite3.OperationalError:  # dbstat is a compile-time option
                size = None
            tables.append({"table": name, "rows": rows, "bytes": size})
    return {"path": str(mart_path), "bytes": mart_path.stat().st_size, "tables": tables,
            "dbstat": any(t["bytes"] is not None for t in tables)}


def thresholds() -> Dict[str, Any]:
    """The spend tiers from the plan, and the honest answer about whether they can fire."""
    return {
        "tiers": [dict(t) for t in TIERS],
        "measured_usd": None,
        "active": None,
        "reason": ("No cloud warehouse is configured, so no dollar spend can be computed. The tiers "
                   "are declared policy; attribution is enforced regardless of spend."),
    }


def sprawl(days: int = 90, path: Optional[Path] = None) -> Dict[str, Any]:
    """Adoption telemetry: which metrics and surfaces nobody used in the window."""
    from app.bi.semantics import METRICS

    target = log_path(path)
    used_metrics: set = set()
    used_pipelines: set = set()
    if target.exists():
        with closing(sqlite3.connect(target)) as con:
            _ensure(con)
            for metric_ids, pipeline in con.execute(
                    "SELECT metric_ids, pipeline FROM query_log WHERE ts >= ?", (_since(days),)):
                used_metrics.update(m for m in (metric_ids or "").split(",") if m)
                used_pipelines.add(pipeline)
    return {
        "window_days": days,
        "metrics": {
            "evaluated": len(used_metrics & set(METRICS)),
            "total": len(METRICS),
            "prune_candidates": sorted(set(METRICS) - used_metrics),
        },
        "pipelines": {
            "called": sorted(used_pipelines),
            "archive_candidates": sorted(set(KNOWN_PIPELINES) - used_pipelines),
        },
    }


def check_quota(tenant: str, workload: str, limit: int, window_s: int,
                path: Optional[Path] = None) -> None:
    """Refuse a call once the tenant has spent its allowance for the window."""
    if limit <= 0:
        return
    target = log_path(path)
    if not target.exists():
        return
    since = (datetime.now(timezone.utc) - timedelta(seconds=window_s)).isoformat()
    with closing(sqlite3.connect(target)) as con:
        _ensure(con)
        used = con.execute(
            "SELECT COUNT(*) FROM query_log WHERE tenant = ? AND workload = ? AND ts >= ?",
            (tenant, workload, since)).fetchone()[0]
    if used >= limit:
        raise QuotaExceeded(
            f"{workload} quota exceeded for tenant {tenant!r}: {used} calls in {window_s}s "
            f"(limit {limit}). Raise AI_ASK_QUOTA or wait for the window to roll.")


def report(mart_path: Path, days: int = 30) -> Dict[str, Any]:
    """Everything the FinOps panel needs, in one call."""
    return {
        "attribution": attribute(days),
        "storage": storage(mart_path),
        "thresholds": thresholds(),
        "sprawl": sprawl(max(days, 90)),
    }
