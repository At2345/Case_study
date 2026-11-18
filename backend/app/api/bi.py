"""Apache Superset integration: data-mart status and rebuild endpoints.

The mart is a SQLite file (`var/bi_mart.db`) built from the cycle artifacts and the audit ledger.
Superset reads it read-only; the workbench embeds the dashboard and shows the mart state.
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from app.bi.anomaly import SERIES, cause, diagnostics
from app.bi.ask import answer
from app.bi.contracts import ContractError, conformance, document
from app.bi.export_mart import MART_FILE, build_mart
from app.bi.finops import QuotaExceeded, check_quota, record, report as finops_report, tag_for
from app.bi.kpi_tree import build_tree
from app.bi.semantics import catalog
from app.bi.serving import report as serving_report
from app.bi.tenancy import TenantError, scoped, tenant_of
from app.bi.transport import compare as transport_compare, export as transport_export
from app.bi.writeback import Conflict, WriteBack
from app.config import settings


def _mart_path() -> Path:
    return Path(os.getenv("BI_MART_PATH", str(settings.store_dir / MART_FILE)))


def _writeback_path() -> Path:
    return Path(os.getenv("WRITEBACK_DB", str(settings.store_dir / "writeback.db")))


def _cycle_cutoff(tenant: str) -> str:
    """The cycle the mart was built for; the client never supplies it."""
    path = _mart_path()
    if not path.exists():
        return ""
    with scoped(path, tenant) as con:
        row = con.execute("SELECT cutoff FROM cycle_summary LIMIT 1").fetchone()
    return row[0] if row else ""


def _superset_url() -> str:
    return os.getenv("SUPERSET_URL", "http://localhost:8088").rstrip("/")


def _dashboard_slug() -> str:
    return os.getenv("SUPERSET_DASHBOARD", "sop-overview")


def _superset_reachable(base: str, timeout: float = 2.0) -> bool:
    """Server-side probe so the browser does not need cross-origin access to Superset."""
    try:
        import httpx

        return httpx.get(f"{base}/health", timeout=timeout).status_code == 200
    except Exception:
        return False


def mart_status(path: Path, tenant: str) -> Dict[str, Any]:
    """Row counts per table plus the mart generation timestamp, for one tenant."""
    if not path.exists():
        return {"path": str(path), "exists": False, "generated_at": None, "tables": {}}
    tables: Dict[str, int] = {}
    generated_at = None
    with scoped(path, tenant) as con:
        names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        for name in names:
            tables[name] = int(con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
        if "mart_meta" in tables:
            row = con.execute("SELECT generated_at FROM mart_meta LIMIT 1").fetchone()
            generated_at = row[0] if row else None
    return {"path": str(path), "exists": True, "generated_at": generated_at, "tables": tables}


def _rows(con: sqlite3.Connection, sql: str, params: tuple = ()) -> list:
    return [dict(r) for r in con.execute(sql, params).fetchall()]


def _has_table(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _exceptions_breakdown(
    con: sqlite3.Connection, channel: str | None = None, risk_type: str | None = None,
) -> Dict[str, Any]:
    """Channel, risk-type and top-exception views over EXCEPTION_REVIEW rows.

    Cross-filtering excludes a chart's own dimension from its own query: picking a
    channel narrows the risk-type breakdown and the top list, but the channel chart
    itself keeps showing every channel (with the picked one highlighted client-side)
    so the user can switch the selection without first clearing it.
    """
    channel_sql = "SELECT channel, COUNT(*) AS count FROM triage_items WHERE state = 'EXCEPTION_REVIEW'"
    channel_params: list = []
    if risk_type:
        channel_sql += " AND risk_type = ?"
        channel_params.append(risk_type)
    channel_sql += " GROUP BY channel ORDER BY count DESC"

    risk_sql = "SELECT risk_type, SUM(var_gbp) AS var_gbp FROM triage_items WHERE state = 'EXCEPTION_REVIEW'"
    risk_params: list = []
    if channel:
        risk_sql += " AND channel = ?"
        risk_params.append(channel)
    risk_sql += " GROUP BY risk_type ORDER BY var_gbp DESC"

    top_sql = ("SELECT item_id, product_id, channel, risk_type, var_gbp, value_delta_gbp "
               "FROM triage_items WHERE state = 'EXCEPTION_REVIEW'")
    top_params: list = []
    if channel:
        top_sql += " AND channel = ?"
        top_params.append(channel)
    if risk_type:
        top_sql += " AND risk_type = ?"
        top_params.append(risk_type)
    top_sql += " ORDER BY ABS(var_gbp) DESC LIMIT 10"

    totals_sql = ("SELECT COUNT(*) AS exceptions, COALESCE(SUM(var_gbp), 0) AS var_gbp "
                  "FROM triage_items WHERE state = 'EXCEPTION_REVIEW'")
    totals_params: list = []
    if channel:
        totals_sql += " AND channel = ?"
        totals_params.append(channel)
    if risk_type:
        totals_sql += " AND risk_type = ?"
        totals_params.append(risk_type)

    return {
        "exceptions_by_channel": _rows(con, channel_sql, tuple(channel_params)),
        "var_by_risk_type": _rows(con, risk_sql, tuple(risk_params)),
        "top_exceptions": _rows(con, top_sql, tuple(top_params)),
        "totals": _rows(con, totals_sql, tuple(totals_params))[0],
        "filter": {"channel": channel or None, "risk_type": risk_type or None},
    }


def dashboard_payload(path: Path, tenant: str) -> Dict[str, Any]:
    """Every dataset the native BI dashboard renders, computed from one tenant's rows."""
    if not path.exists():
        raise FileNotFoundError(f"{path} (run python -m app.bi.export_mart first)")
    with scoped(path, tenant) as con:
        cycle = con.execute("SELECT * FROM cycle_summary LIMIT 1").fetchone()
        payload: Dict[str, Any] = {
            "cycle": dict(cycle) if cycle else {},
            "by_state": _rows(con, "SELECT state, COUNT(*) AS count FROM triage_items "
                                   "GROUP BY state ORDER BY count DESC"),
            "plan_vs_baseline": _rows(con, "SELECT date AS month, SUM(final_qty) AS plan, "
                                           "SUM(baseline_qty) AS baseline, SUM(ml_prediction) AS ml "
                                           "FROM triage_monthly GROUP BY date ORDER BY date"),
            "overrides_by_reason": _rows(con, "SELECT reason_code, COUNT(*) AS count FROM overrides "
                                              "GROUP BY reason_code ORDER BY count DESC")
            if _has_table(con, "overrides") else [],
            "fva_by_planner": _rows(con, "SELECT * FROM fva_by_planner") if _has_table(con, "fva_by_planner") else [],
            "quality_warnings": _rows(con, "SELECT * FROM quality_warnings")
            if _has_table(con, "quality_warnings") else [],
        }
        payload.update(_exceptions_breakdown(con))
    del payload["filter"]
    return payload


_CACHE: Dict[tuple, Any] = {}


def cached_payload(path: Path, tenant: str):
    """Reuse one tenant's aggregation until the mart file changes.

    The tenant is part of the cache key. Keying only on the file would serve the
    first caller's payload to every other tenant, which is a cross-tenant leak
    that no query-level filter can catch.
    """
    stat = path.stat()
    key = (tenant, str(path), stat.st_mtime_ns, stat.st_size)
    hit = _CACHE.get(key)
    if hit is not None:
        return dict(hit), True
    payload = dashboard_payload(path, tenant)
    _CACHE[key] = payload
    return dict(payload), False


def register(app: FastAPI, ctx: Dict[str, Any]) -> None:
    require, all_roles = ctx["require"], ctx["all_roles"]
    write_roles = ctx.get("write_roles", all_roles)
    stores: Dict[str, WriteBack] = {}

    def store_for(tenant: str) -> WriteBack:
        """One write-back view per tenant; the log file itself is shared."""
        if tenant not in stores:
            stores[tenant] = WriteBack(_writeback_path(), tenant)
        return stores[tenant]

    @app.exception_handler(TenantError)
    def tenant_error(request: Request, exc: TenantError):
        """A scope that cannot be established is a refusal, never a fallback."""
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.exception_handler(QuotaExceeded)
    def quota_error(request: Request, exc: QuotaExceeded):
        """A workload that has spent its allowance is refused, not slowed down."""
        return JSONResponse(status_code=429, content={"detail": str(exc)})

    def attributed(user: Dict[str, Any], pipeline: str, workload: str = "bi"):
        """The cost tag for this request. An unattributable role is refused."""
        try:
            return tag_for(user, pipeline, workload)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail=str(exc))

    def record_request(tag, tenant: str, started: float) -> None:
        """One row per BI read that does not go through the metric registry."""
        record(tag, tenant, kind="request", duration_ms=(time.perf_counter() - started) * 1000)

    @app.get("/api/v1/bi/status")
    def bi_status(user=Depends(require(*all_roles))):
        tenant = tenant_of(user)
        base, slug = _superset_url(), _dashboard_slug()
        return {
            "superset_url": base,
            "dashboard_url": f"{base}/superset/dashboard/{slug}/",
            "dashboard_slug": slug,
            "superset_reachable": _superset_reachable(base),
            "mart": mart_status(_mart_path(), tenant),
        }

    @app.get("/api/v1/bi/dashboard")
    def bi_dashboard(user=Depends(require(*all_roles))):
        tenant = tenant_of(user)
        tag = attributed(user, "dashboard")
        path = _mart_path()
        started = time.perf_counter()
        try:
            payload, cached = cached_payload(path, tenant)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=409, detail=f"Mart missing: {exc}")
        payload["duration_ms"] = round((time.perf_counter() - started) * 1000, 1)
        payload["cached"] = cached
        mart = mart_status(path, tenant)
        base, slug = _superset_url(), _dashboard_slug()
        payload["mart"] = mart
        payload["freshness"] = {
            "mart_generated_at": mart.get("generated_at"),
            "cycle_cutoff": payload["cycle"].get("cutoff"),
            "first_month": payload["cycle"].get("first_month"),
        }
        payload["superset"] = {
            "url": base,
            "dashboard_url": f"{base}/superset/dashboard/{slug}/",
            "slug": slug,
            "reachable": _superset_reachable(base),
        }
        record_request(tag, tenant, started)
        return payload

    @app.get("/api/v1/bi/dashboard/exceptions")
    def bi_dashboard_exceptions(channel: str = "", risk_type: str = "", user=Depends(require(*all_roles))):
        """The channel, risk-type and top-exception views alone, optionally cross-filtered.

        Deliberately separate from the cached ``/dashboard`` payload: a chart click
        should not re-probe Superset or re-read every mart table on each selection.
        """
        tenant = tenant_of(user)
        tag = attributed(user, "dashboard")
        path = _mart_path()
        if not path.exists():
            raise HTTPException(status_code=409, detail=f"Mart missing: {path}")
        started = time.perf_counter()
        with scoped(path, tenant) as con:
            payload = _exceptions_breakdown(con, channel=channel or None, risk_type=risk_type or None)
        record_request(tag, tenant, started)
        return payload

    @app.get("/api/v1/bi/kpi-tree")
    def bi_kpi_tree(user=Depends(require(*all_roles))):
        tenant = tenant_of(user)
        path = _mart_path()
        if not path.exists():
            raise HTTPException(status_code=409, detail=f"Mart missing: {path}")
        return build_tree(path, tenant, attributed(user, "kpi_tree"))

    @app.get("/api/v1/bi/metrics")
    def bi_metrics(user=Depends(require(*all_roles))):
        tenant = tenant_of(user)
        path = _mart_path()
        if not path.exists():
            raise HTTPException(status_code=409, detail=f"Mart missing: {path}")
        return {"metrics": catalog(path, tenant, attributed(user, "metrics"))}

    @app.get("/api/v1/bi/diagnostics")
    def bi_diagnostics(series: str = "", user=Depends(require(*all_roles))):
        tenant = tenant_of(user)
        tag = attributed(user, "diagnostics")
        path = _mart_path()
        if not path.exists():
            raise HTTPException(status_code=409, detail=f"Mart missing: {path}")
        if series and series not in SERIES:
            raise HTTPException(status_code=422,
                                detail=f"Unknown series {series!r}; expected one of {sorted(SERIES)}")
        started = time.perf_counter()
        out = diagnostics(path, user["role"], series or None, tenant=tenant)
        record_request(tag, tenant, started)
        return out

    @app.post("/api/v1/bi/ask")
    def bi_ask(body: Dict[str, Any], user=Depends(require(*all_roles))):
        tenant = tenant_of(user)
        question = str(body.get("question", "")).strip()
        if not question:
            raise HTTPException(status_code=422, detail="question is required")
        path = _mart_path()
        if not path.exists():
            raise HTTPException(status_code=409, detail=f"Mart missing: {path}")
        client = ctx.get("llm")
        if client is None:
            raise HTTPException(status_code=503, detail="No LLM provider is configured.")
        tag = attributed(user, "ask", "ai")
        check_quota(tenant, "ai", settings.ai_ask_quota, settings.ai_ask_window_s)
        started = time.perf_counter()
        try:
            out = answer(question, path, client, tenant=tenant, tag=tag)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        record(tag, tenant, kind="request", statements=1,
               duration_ms=(time.perf_counter() - started) * 1000)
        return out

    @app.post("/api/v1/bi/export")
    def bi_export(user=Depends(require("admin"))):
        tenant = tenant_of(user)
        path = _mart_path()
        try:
            counts = build_mart(out_path=path, tenant_id=tenant)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=409, detail=f"Cycle artifacts missing: {exc}")
        except ContractError as exc:
            raise HTTPException(status_code=409, detail=f"Mart contract violated: {exc}")
        _CACHE.clear()
        return {"path": str(path), "tenant": tenant, "tables": counts}

    @app.get("/api/v1/bi/contracts")
    def bi_contracts(user=Depends(require(*all_roles))):
        """The declared mart contract plus how the live mart measures up to it."""
        tenant = tenant_of(user)
        tag = attributed(user, "contracts")
        path = _mart_path()
        started = time.perf_counter()
        report = {"ok": False, "tables": [], "problems": [],
                  "summary": {"tables": 0, "present": 0, "errors": 0, "warnings": 0}}
        if path.exists():
            with scoped(path, tenant) as con:
                report = conformance(con)
        record_request(tag, tenant, started)
        return {"contract": document(), "conformance": report,
                "mart": str(path), "mart_present": path.exists()}

    @app.get("/api/v1/bi/finops")
    def bi_finops(days: int = 30, user=Depends(require(*all_roles))):
        """Cost attribution, storage intelligence, spend tiers and sprawl candidates."""
        tenant = tenant_of(user)
        tag = attributed(user, "finops")
        started = time.perf_counter()
        body = finops_report(_mart_path(), max(1, min(days, 365)))
        record_request(tag, tenant, started)
        return body

    @app.get("/api/v1/bi/serving")
    def bi_serving(days: int = 30, user=Depends(require(*all_roles))):
        """Freshness tiers, measured p95 per workload, and mart index coverage."""
        tenant = tenant_of(user)
        tag = attributed(user, "serving")
        started = time.perf_counter()
        body = serving_report(_mart_path(), tenant, max(1, min(days, 365)))
        record_request(tag, tenant, started)
        return body

    @app.get("/api/v1/bi/transport")
    def bi_transport(tables: str = "", user=Depends(require(*all_roles))):
        """The same tables as JSON, Arrow IPC and Parquet, with bytes and encode time."""
        tenant = tenant_of(user)
        tag = attributed(user, "transport")
        path = _mart_path()
        if not path.exists():
            raise HTTPException(status_code=409, detail=f"Mart missing: {path}")
        wanted = [t.strip() for t in tables.split(",") if t.strip()] or None
        started = time.perf_counter()
        try:
            body = transport_compare(path, tenant, wanted)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        record_request(tag, tenant, started)
        return body

    @app.get("/api/v1/bi/export/columnar")
    def bi_export_columnar(table: str, format: str = "arrow",
                           user=Depends(require(*all_roles))):
        """One tenant-scoped table as Arrow IPC or Parquet bytes."""
        tenant = tenant_of(user)
        tag = attributed(user, "transport")
        path = _mart_path()
        if not path.exists():
            raise HTTPException(status_code=409, detail=f"Mart missing: {path}")
        started = time.perf_counter()
        try:
            payload, media, filename = transport_export(path, tenant, table, format)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        record_request(tag, tenant, started)
        return Response(content=payload, media_type=media, headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(payload)),
        })

    # ------------------------------------------------------------------ write-back
    @app.get("/api/v1/bi/annotations")
    def bi_annotations(target_type: str = "", target_id: str = "", limit: int = 50,
                       user=Depends(require(*all_roles))):
        tenant = tenant_of(user)
        return {"annotations": store_for(tenant).annotations(
            target_type or None, target_id or None, None, limit)}

    @app.post("/api/v1/bi/annotations")
    def bi_annotate(body: Dict[str, Any], request: Request, user=Depends(require(*write_roles))):
        tenant = tenant_of(user)
        try:
            return store_for(tenant).annotate({
                "cutoff": _cycle_cutoff(tenant),
                "target_type": body.get("target_type"),
                "target_id": body.get("target_id"),
                "body": body.get("body"),
                "author": user["username"], "role": user["role"],
                "ip_address": request.client.host if request.client else None,
                "session_id": request.headers.get("X-Session-Id") or user["jti"],
                "idempotency_key": body.get("idempotency_key"),
            })
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.get("/api/v1/bi/actions")
    def bi_actions(limit: int = 50, user=Depends(require(*all_roles))):
        tenant = tenant_of(user)
        return {"actions": store_for(tenant).actions(None, limit)}

    @app.post("/api/v1/bi/actions")
    def bi_propose_action(body: Dict[str, Any], request: Request, user=Depends(require(*write_roles))):
        """Only an actionable cause may become an action; a hypothesis is refused."""
        tenant = tenant_of(user)
        series_id = str(body.get("series") or "").strip()
        month = str(body.get("month") or "").strip()
        if series_id not in SERIES:
            raise HTTPException(status_code=422, detail=f"Unknown series {series_id!r}.")
        path = _mart_path()
        if not path.exists():
            raise HTTPException(status_code=409, detail=f"Mart missing: {path}")
        found = cause(path, series_id, month, user["role"], tenant=tenant)
        if found is None:
            raise HTTPException(status_code=404,
                                detail=f"{month} is not an anomaly of {series_id}.")
        if not found["actionable"]:
            failed = [c["id"] for c in found["checks"] if not c["passed"]]
            raise HTTPException(
                status_code=422,
                detail="Cause is a hypothesis, not an actionable finding. "
                       f"Failed checks: {', '.join(failed)}.")
        try:
            return store_for(tenant).propose({
                "cutoff": _cycle_cutoff(tenant), "series": series_id, "month": month,
                "driver": found["leading_driver"], "approver": found["approver"],
                "note": body.get("note"), "actor": user["username"], "role": user["role"],
                "ip_address": request.client.host if request.client else None,
                "session_id": request.headers.get("X-Session-Id") or user["jti"],
                "idempotency_key": body.get("idempotency_key"),
            })
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.post("/api/v1/bi/actions/{action_id}/decide")
    def bi_decide_action(action_id: str, body: Dict[str, Any], request: Request,
                         user=Depends(require(*write_roles))):
        tenant = tenant_of(user)
        try:
            return store_for(tenant).decide(
                action_id, str(body.get("decision") or ""), body.get("note"),
                body.get("expected_state"), user["username"], user["role"],
                request.client.host if request.client else None,
                request.headers.get("X-Session-Id") or user["jti"],
                body.get("idempotency_key"))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except Conflict as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.get("/api/v1/bi/writeback/verify")
    def bi_writeback_verify(user=Depends(require("auditor", "admin"))):
        tenant = tenant_of(user)
        ok, bad = store_for(tenant).verify_chain()
        return {"ok": ok, "first_bad_id": bad}
