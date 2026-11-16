"""Automated anomaly detection and root-cause analysis (PR-5.2).

Two responsibilities, kept deliberately separate:

1. **Screening.** A monthly series is screened for outliers with the
   Iglewicz-Hoaglin modified z-score (median and MAD). The mart holds twelve
   months, far too short for a seasonal model, so the detector is a screening
   tool: it reports "this month is unusual against the year's centre", never
   "this month is wrong".

2. **Explanation.** An anomalous month is decomposed across a segment
   dimension. Every explanation is then gated by the plan's four-check RCA
   protocol. A cause that fails any check is returned as a *hypothesis* with the
   failed checks named; only a cause that passes all four is marked
   *actionable*. Nothing here claims causality.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.bi.tenancy import scoped

# Iglewicz & Hoaglin: |modified z| >= 3.5 is the conventional outlier cut.
Z_THRESHOLD = 3.5
# Contributions are summed from the same rows as the total, so the only gap is
# floating-point noise. Anything larger means the dimension does not cover the
# table and the decomposition is incomplete.
RECONCILE_TOL = 1e-6
EVIDENCE_LIMIT = 5


@dataclass(frozen=True)
class Series:
    """A monthly series the detector screens, and the dimension it decomposes by."""
    id: str
    label: str
    table: str
    value: str
    dimension: str
    unit: str
    owner: str
    metric: str = ""
    description: str = ""


SERIES: Dict[str, Series] = {s.id: s for s in [
    Series("plan_qty", "Planned units", "triage_monthly", "final_qty", "channel", "qty",
           "Head of Demand Planning", metric="planned_value_gbp",
           description="Monthly plan summed across in-scope items."),
    Series("plan_uplift_qty", "Plan vs baseline uplift", "triage_monthly",
           "(final_qty - baseline_qty)", "channel", "qty",
           "Head of Demand Planning", metric="net_value_variance_gbp",
           description="Units the plan adds over the statistical baseline."),
]}

# Evidence grain each role may read. The item grain follows the app's own write
# boundary: only roles that can act on a finding get the item-level detail, so a
# read-only auditor receives the hypothesis and the segment split but not the
# commercial item rows.
EVIDENCE_ROLES: Dict[str, set] = {
    "admin": {"item", "segment", "aggregate"},
    "planner": {"item", "segment", "aggregate"},
    "auditor": {"segment", "aggregate"},
}

# Dimensions that also exist on the item table, so item-level evidence is available.
_EVIDENCE_COLUMNS = {"channel", "abc_category", "product_id"}

PROTOCOL = [
    {"id": "temporal_precedence", "label": "Temporal precedence",
     "rule": "The proposed driver moved at or before the KPI."},
    {"id": "arithmetic_reconciliation", "label": "Arithmetic reconciliation",
     "rule": "Segment contributions sum to the total variance."},
    {"id": "permission_preserved", "label": "Permission preserved",
     "rule": "Evidence is served at a grain the requester may read."},
    {"id": "named_approver", "label": "Named approver",
     "rule": "The cause is routed to a named owner before any action."},
]


# --------------------------------------------------------------------- statistics
def _median(values: List[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    if not n:
        return 0.0
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0


def _spread(values: List[float], center: float) -> float:
    """MAD, falling back to mean absolute deviation when MAD collapses to zero."""
    mad = _median([abs(v - center) for v in values])
    if mad > 0:
        return mad
    return sum(abs(v - center) for v in values) / len(values) if values else 0.0


def _z(value: float, center: float, spread: float) -> float:
    """Modified z-score; zero spread means the series cannot be screened."""
    if spread <= 0:
        return 0.0
    return 0.6745 * (value - center) / spread


def _add_months(month: str, step: int) -> str:
    year, mon, day = (int(part) for part in month.split("-"))
    total = year * 12 + (mon - 1) + step
    return date(total // 12, total % 12 + 1, day).isoformat()


# --------------------------------------------------------------------- series reads
def _monthly(con: sqlite3.Connection, series: Series) -> List[Dict[str, Any]]:
    rows = con.execute(
        f"SELECT date AS month, SUM({series.value}) AS value FROM {series.table} "
        "GROUP BY date ORDER BY date").fetchall()
    return [{"month": r["month"], "value": float(r["value"] or 0.0)} for r in rows]


def _segment_history(con: sqlite3.Connection, series: Series) -> Dict[str, Dict[str, float]]:
    rows = con.execute(
        f"SELECT {series.dimension} AS segment, date AS month, SUM({series.value}) AS value "
        f"FROM {series.table} GROUP BY {series.dimension}, date").fetchall()
    history: Dict[str, Dict[str, float]] = {}
    for row in rows:
        history.setdefault(row["segment"], {})[row["month"]] = float(row["value"] or 0.0)
    return history


def _first_deviation(points: Dict[str, float], center: float, spread: float,
                     threshold: float) -> Optional[str]:
    """Earliest month in which this segment left its own centre."""
    for month in sorted(points):
        if abs(_z(points[month], center, spread)) >= threshold:
            return month
    return None


# --------------------------------------------------------------------- detection
def detect(con: sqlite3.Connection, series: Series, threshold: float = Z_THRESHOLD) -> Dict[str, Any]:
    """Screen one series for outlier months."""
    points = _monthly(con, series)
    values = [p["value"] for p in points]
    center = _median(values)
    spread = _spread(values, center)
    anomalies = []
    for point in points:
        z = _z(point["value"], center, spread)
        if abs(z) >= threshold:
            anomalies.append({
                "month": point["month"],
                "value": point["value"],
                "expected": center,
                "deviation": point["value"] - center,
                "z": round(z, 2),
                "direction": "above" if z > 0 else "below",
                "severity": "high" if abs(z) >= 2 * threshold else "medium",
            })
    return {
        "series": series.id,
        "label": series.label,
        "unit": series.unit,
        "owner": series.owner,
        "metric": series.metric,
        "points": points,
        "center": center,
        "spread": spread,
        "threshold": threshold,
        "anomalies": anomalies,
        "forecast": forecast(points),
    }


def forecast(points: List[Dict[str, Any]], horizon: int = 3) -> Dict[str, Any]:
    """Naive linear trend over the last six months, with a residual band."""
    tail = points[-6:]
    n = len(tail)
    if n < 3:
        return {"method": "insufficient_history", "points": [], "band": None,
                "caveat": "Fewer than three months of history."}
    xs = list(range(n))
    ys = [p["value"] for p in tail]
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom if denom else 0.0
    intercept = mean_y - slope * mean_x
    residuals = [y - (intercept + slope * x) for x, y in zip(xs, ys)]
    band = 1.96 * _spread(residuals, _median(residuals))
    last = points[-1]["month"]
    projected = []
    for step in range(1, horizon + 1):
        value = intercept + slope * (n - 1 + step)
        projected.append({"month": _add_months(last, step), "value": value,
                          "low": value - band, "high": value + band})
    return {"method": "linear_trend_last_6_months", "band": band, "points": projected,
            "caveat": "Naive trend baseline, not a seasonal model."}


# --------------------------------------------------------------------- four checks
def _check_temporal(driver: Dict[str, Any], month: str) -> Dict[str, Any]:
    first = driver.get("first_deviation")
    if first is None:
        return {"id": "temporal_precedence", "passed": False,
                "detail": f"{driver['segment']} never left its own centre, so it cannot explain the move."}
    passed = first <= month
    return {"id": "temporal_precedence", "passed": passed,
            "detail": (f"{driver['segment']} first deviated in {first}, at or before {month}."
                       if passed else
                       f"{driver['segment']} first deviated in {first}, after {month}.")}


def _check_reconcile(total: float, contributions: List[Dict[str, Any]]) -> Dict[str, Any]:
    explained = sum(c["contribution"] for c in contributions)
    gap = abs(explained - total)
    tolerance = max(RECONCILE_TOL, abs(total) * 1e-9)
    passed = gap <= tolerance
    return {"id": "arithmetic_reconciliation", "passed": passed,
            "detail": (f"Segments explain {explained:,.2f} of {total:,.2f} (gap {gap:.6f})."
                       if passed else
                       f"Segments explain {explained:,.2f} of {total:,.2f}: "
                       f"the dimension does not cover the table (gap {gap:,.2f})."),
            "explained": explained, "total": total, "gap": gap}


def _check_permission(role: str, grain: str) -> Dict[str, Any]:
    allowed = EVIDENCE_ROLES.get(role, set())
    passed = grain in allowed
    if passed:
        detail = f"{role} may read {grain}-level evidence."
    else:
        holders = sorted(r for r, grains in EVIDENCE_ROLES.items() if grain in grains)
        detail = (f"{grain}-level evidence requires one of {holders}; "
                  f"{role} is limited to {sorted(allowed)}.")
    return {"id": "permission_preserved", "passed": passed, "detail": detail,
            "grain": grain, "role": role}


def _check_approver(series: Series) -> Dict[str, Any]:
    owner = (series.owner or "").strip()
    return {"id": "named_approver", "passed": bool(owner),
            "detail": f"Routed to {owner}." if owner else "No owner is registered for this series.",
            "approver": owner or None}


# --------------------------------------------------------------------- RCA
def _contributions(con: sqlite3.Connection, series: Series, month: str,
                   threshold: float) -> List[Dict[str, Any]]:
    history = _segment_history(con, series)
    out = []
    for segment, points in history.items():
        if month not in points:
            continue
        values = list(points.values())
        center = _median(values)
        spread = _spread(values, center)
        actual = points[month]
        out.append({
            "segment": segment,
            "value": actual,
            "expected": center,
            "contribution": actual - center,
            "z": round(_z(actual, center, spread), 2),
            "first_deviation": _first_deviation(points, center, spread, threshold),
        })
    out.sort(key=lambda row: abs(row["contribution"]), reverse=True)
    return out


def _evidence(con: sqlite3.Connection, series: Series, segment: str) -> List[Dict[str, Any]]:
    """Item-level rows behind the leading driver, when the dimension supports them."""
    if series.dimension not in _EVIDENCE_COLUMNS:
        return []
    rows = con.execute(
        f"SELECT item_id, product_id, channel, risk_type, var_gbp FROM triage_items "
        f"WHERE {series.dimension} = ? AND in_scope = 1 "
        "ORDER BY ABS(var_gbp) DESC LIMIT ?", (segment, EVIDENCE_LIMIT)).fetchall()
    return [dict(row) for row in rows]


def rca(con: sqlite3.Connection, series: Series, month: str, role: str,
        threshold: float = Z_THRESHOLD) -> Dict[str, Any]:
    """Explain one anomalous month, gated by the four-check protocol.

    The baseline is the sum of the segment centres, not the series median: only
    that baseline makes the decomposition exactly additive, so the reconciliation
    check measures coverage of the dimension rather than an artefact of taking a
    median of sums.
    """
    points = _monthly(con, series)
    actual = next((p["value"] for p in points if p["month"] == month), None)

    contributions = _contributions(con, series, month, threshold)
    driver = contributions[0] if contributions else None
    baseline = sum(c["expected"] for c in contributions)
    total = (actual - baseline) if actual is not None else 0.0

    grain = "item" if series.dimension in _EVIDENCE_COLUMNS else "segment"
    permission = _check_permission(role, grain)
    evidence = _evidence(con, series, driver["segment"]) if (driver and permission["passed"]) else []

    checks = [
        _check_temporal(driver, month) if driver
        else {"id": "temporal_precedence", "passed": False,
              "detail": "No segment moved, so there is no driver to test."},
        _check_reconcile(total, contributions),
        permission,
        _check_approver(series),
    ]
    actionable = all(check["passed"] for check in checks)
    return {
        "series": series.id,
        "month": month,
        "dimension": series.dimension,
        "baseline": baseline,
        "total_deviation": total,
        "contributions": contributions,
        "leading_driver": driver["segment"] if driver else None,
        "evidence": evidence,
        "evidence_withheld": bool(driver) and not permission["passed"],
        "checks": checks,
        "actionable": actionable,
        "status": "actionable" if actionable else "hypothesis",
        "approver": next((c.get("approver") for c in checks if c["id"] == "named_approver"), None),
    }


def cause(path: Path, series_id: str, month: str, role: str,
          threshold: float = Z_THRESHOLD, *, tenant: str) -> Optional[Dict[str, Any]]:
    """One RCA cause, or None when that month is not an anomaly for the series."""
    series = SERIES[series_id]
    with scoped(path, tenant) as con:
        if not any(a["month"] == month for a in detect(con, series, threshold)["anomalies"]):
            return None
        return rca(con, series, month, role, threshold)


def diagnostics(path: Path, role: str, series_id: Optional[str] = None,
                threshold: float = Z_THRESHOLD, *, tenant: str) -> Dict[str, Any]:
    """Screen every series (or one) and explain each anomalous month."""
    selected = [SERIES[series_id]] if series_id else list(SERIES.values())
    with scoped(path, tenant) as con:
        results = []
        for series in selected:
            detection = detect(con, series, threshold)
            detection["rca"] = [rca(con, series, a["month"], role, threshold)
                                for a in detection["anomalies"]]
            results.append(detection)
    return {
        "role": role,
        "protocol": PROTOCOL,
        "series": results,
        "summary": {
            "series": len(results),
            "anomalies": sum(len(r["anomalies"]) for r in results),
            "actionable": sum(1 for r in results for c in r["rca"] if c["actionable"]),
            "hypotheses": sum(1 for r in results for c in r["rca"] if not c["actionable"]),
        },
    }
