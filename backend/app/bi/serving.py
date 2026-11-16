"""Serving-layer SLAs: freshness tiers (PR-3.1) and workload latency (PR-3.2).

There is no Kafka, Flink, Iceberg or ClickHouse here, so the ingestion-pipeline and OLAP-engine
halves of these requirements have no analogue: there is no stream to replay and no engine to
select. What can be measured is measured.

Freshness: each mart table declares a tier and the tier carries an SLA, and the report compares
the mart's actual age against it. The mart is rebuilt as a unit, so every table shares one
`generated_at`; the tier is what differs, which is the point — the write-back tables change on a
human action and are held to an hourly SLA, while the tables rebuilt from cycle artifacts are
held to a daily one. A table that is stale relative to its own tier is reported as a breach
rather than averaged away.

Latency: each BI workload declares a target from the persona SLA matrix, and the report compares
the p95 measured in the FinOps ledger against it. The target is the PR-2.2 interaction budget for
interactive surfaces and the PR-1.2 Operations ceiling for the LLM round-trip.

Serving: the mart is indexed on the columns the governed queries filter by, and the report says
which of those indexes are actually present, so a mart built before this change is reported as
uncovered rather than assumed to be indexed.
"""
from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.bi.finops import durations
from app.bi.tenancy import TENANT_COLUMN, scoped

TIERS: Dict[str, int] = {
    "realtime": 60,
    "hourly": 3600,
    "daily": 86400,
    "weekly": 604800,
}

# The write-back tables change whenever a planner acts; everything else changes on a mart rebuild.
FRESHNESS: Dict[str, str] = {
    "triage_items": "daily",
    "triage_monthly": "daily",
    "quantile_bands": "daily",
    "phasing_items": "daily",
    "safety_stock": "daily",
    "overrides": "hourly",
    "commits": "hourly",
    "cycle_summary": "daily",
    "quality_warnings": "daily",
    "fva_by_planner": "daily",
    "fva_by_reason": "daily",
    "mart_meta": "daily",
}

# The columns each governed query filters or groups by, in the order the index should lead with.
INDEXES: Dict[str, tuple] = {
    "triage_items": (("in_scope",), ("channel",), ("risk_type",), ("item_id",)),
    "triage_monthly": (("date",), ("item_id",)),
    "quantile_bands": (("date",),),
    "phasing_items": (("month",),),
    "safety_stock": (("item_id",),),
    "overrides": (("planner_id",), ("reason_code",)),
    "commits": (("cutoff",),),
}

# 400 ms is the PR-2.2 interaction budget; 60 s is the PR-1.2 Operations ceiling, applied to the
# one workload that waits on a model round-trip rather than on the mart.
WORKLOADS: Dict[str, Dict[str, Any]] = {
    "dashboard": {"persona": "Executive / Operations", "target_p95_ms": 400},
    "kpi_tree": {"persona": "Executive", "target_p95_ms": 400},
    "metrics": {"persona": "Analyst", "target_p95_ms": 400},
    "diagnostics": {"persona": "Operations", "target_p95_ms": 400},
    "contracts": {"persona": "Analyst", "target_p95_ms": 400},
    "finops": {"persona": "Analyst", "target_p95_ms": 400},
    "ask": {"persona": "Analyst", "target_p95_ms": 60000},
}

GAPS = (
    "No streaming ingestion exists: the mart is rebuilt on demand from cycle artifacts, so the "
    "tier is a declared SLA rather than a delivery guarantee.",
    "No OLAP engine is deployed: SQLite serves the mart, so the workload targets are measured "
    "against a single-file database rather than a columnar engine.",
    "The latency figures come from the FinOps ledger, which only holds requests made since it was "
    "introduced, so a workload with no samples reports no measurement rather than a pass.",
)


def _index_name(table: str, columns: tuple) -> str:
    return f"ix_{table}_{'_'.join(columns)}"


def ensure_indexes(con: sqlite3.Connection) -> int:
    """Create the declared indexes on a mart connection. Returns how many were declared.

    `CREATE INDEX IF NOT EXISTS` still raises when the table is absent, so the table list is read
    first: an optional table that was never written is skipped rather than failing the build.
    """
    present = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    declared = 0
    for table, column_sets in INDEXES.items():
        if table not in present:
            continue
        for columns in column_sets:
            con.execute(
                f'CREATE INDEX IF NOT EXISTS "{_index_name(table, columns)}" ON "{table}" '
                f'({TENANT_COLUMN}, {", ".join(columns)})')
            declared += 1
    return declared


def index_coverage(path: Path) -> Dict[str, Any]:
    """Which declared indexes the mart actually carries."""
    if not path.exists():
        return {"declared": [], "present": [], "missing": [], "ok": False,
                "reason": f"{path} does not exist"}
    with sqlite3.connect(path) as con:
        existing = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    declared = [
        {"table": table, "columns": list(columns), "index": _index_name(table, columns),
         "table_present": table in tables}
        for table, column_sets in INDEXES.items() for columns in column_sets
    ]
    present = [d["index"] for d in declared if d["index"] in existing]
    missing = [d["index"] for d in declared if d["index"] not in existing and d["table_present"]]
    return {"declared": declared, "present": present, "missing": missing, "ok": not missing}


def _generated_at(path: Path, tenant: str) -> Optional[datetime]:
    if not path.exists():
        return None
    with scoped(path, tenant) as con:
        row = con.execute("SELECT generated_at FROM mart_meta LIMIT 1").fetchone()
    if not row or not row[0]:
        return None
    try:
        parsed = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def freshness(path: Path, tenant: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Each table's declared tier and SLA, against the mart's actual age."""
    moment = now or datetime.now(timezone.utc)
    generated = _generated_at(path, tenant)
    age_s = None if generated is None else max(0.0, (moment - generated).total_seconds())
    present = set()
    if path.exists():
        with sqlite3.connect(path) as con:
            present = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    rows = []
    for table, tier in FRESHNESS.items():
        sla = TIERS[tier]
        exists = table in present
        rows.append({
            "table": table,
            "tier": tier,
            "sla_s": sla,
            "present": exists,
            "age_s": None if age_s is None else round(age_s, 1),
            "breach": exists and age_s is not None and age_s > sla,
        })
    breaches = [r["table"] for r in rows if r["breach"]]
    return {
        "generated_at": generated.isoformat() if generated else None,
        "age_s": None if age_s is None else round(age_s, 1),
        "tables": rows,
        "breaches": breaches,
        "ok": generated is not None and not breaches,
        "tiers": TIERS,
    }


def _p95(values: List[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def latency(window_days: int = 30) -> Dict[str, Any]:
    """Measured p95 per workload against its declared target."""
    samples = durations(window_days)
    rows = []
    for workload, spec in WORKLOADS.items():
        values = samples.get(workload) or []
        p95 = _p95(values)
        target = spec["target_p95_ms"]
        rows.append({
            "workload": workload,
            "persona": spec["persona"],
            "target_p95_ms": target,
            "measured_p95_ms": None if p95 is None else round(p95, 1),
            "samples": len(values),
            "ok": None if p95 is None else p95 <= target,
        })
    return {
        "window_days": window_days,
        "workloads": rows,
        "unmeasured": [r["workload"] for r in rows if r["measured_p95_ms"] is None],
        "breaches": [r["workload"] for r in rows if r["ok"] is False],
    }


def report(path: Path, tenant: str, window_days: int = 30) -> Dict[str, Any]:
    """Freshness, latency and index coverage in one call."""
    return {
        "freshness": freshness(path, tenant),
        "latency": latency(window_days),
        "indexes": index_coverage(path),
        "gaps": list(GAPS),
    }
