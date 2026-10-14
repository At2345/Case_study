"""FastAPI application: exception-based S&OP workbench API."""
from __future__ import annotations

import logging
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.auth import TokenError, UserStore, decode_token, make_token
from app.api.service import DataService, clean
from app.allocation.optimiser import AllocationConfig, build_demands, simulate
from app.config import settings
from app.evaluation.fva import override_fva
from app.models.domain import AllocationRequest, LoginRequest, OverrideRequest
from app.models.enums import ReasonCode, Role
from app.narrative.llm import client_from_env
from app.narrative.narrator import Narrator
from app.narrative.telemetry import build_telemetry
from app.store.ledger import Ledger

log = logging.getLogger("sop.api")

MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 300
SUPPRESS_PCT = 0.05
ALL_ROLES = (Role.PLANNER.value, Role.ADMIN.value, Role.AUDITOR.value)
WRITE_ROLES = (Role.PLANNER.value, Role.ADMIN.value)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_app(
    service: DataService,
    ledger: Ledger,
    users: UserStore,
    secret: str,
    store_dir: Path,
    cors_origins=("http://localhost:5173",),
    token_minutes: int = 480,
    reload_fn: Optional[Callable[[], DataService]] = None,
    narrator: Optional[Narrator] = None,
    db: Optional[Any] = None,
    llm: Optional[Any] = None,
) -> FastAPI:
    state = {"svc": service}
    failures: Dict[str, List[float]] = {}
    bearer = HTTPBearer(auto_error=False)
    store_dir = Path(store_dir)

    app = FastAPI(
        title="S&OP Demand Planning API",
        description="Exception-based demand planning backend (triage queue, audited overrides, FVA, consensus commit)",
        version="1.0.0",
    )
    app.state.narrator = narrator or Narrator(None, store_dir / "narratives.db")
    runs = chats = jobs = schedules = notes = scenarios = None
    if db is not None:
        from app.orchestration.jobs import JobRunner
        from app.store.ops import NotificationStore, ScenarioStore, ScheduleStore
        from app.store.runs import ChatStore, RunStore
        runs, chats = RunStore(db), ChatStore(db)
        schedules, notes, scenarios = ScheduleStore(db), NotificationStore(db), ScenarioStore(db)
        for run_id in runs.fail_stale():
            notes.add(["admin"], "error", "Pipeline run interrupted", "The server restarted during the run; start it again from Operations.",
                      "operations", run_id)
        jobs = JobRunner(state, runs, reload_fn, notes)
    app.state.jobs, app.state.schedules = jobs, schedules
    metrics = Metrics()

    @app.middleware("http")
    async def observe(request: Request, call_next):
        t0 = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        metrics.observe(request.method, getattr(route, "path", "unmatched"), response.status_code, time.perf_counter() - t0)
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Session-Id"],
    )

    # ------------------------------------------------------------------ auth helpers
    def current_user(creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer)) -> Dict[str, str]:
        if settings.demo_mode:
            # Demo: pretend to be a planner by default
            return {"username": "demo", "role": Role.PLANNER.value, "jti": "demo"}
        if creds is None:
            raise HTTPException(status_code=401, detail="Not authenticated", headers={"WWW-Authenticate": "Bearer"})
        try:
            claims = decode_token(secret, creds.credentials)
        except TokenError:
            raise HTTPException(status_code=401, detail="Invalid or expired token", headers={"WWW-Authenticate": "Bearer"})
        return {"username": claims["sub"], "role": claims["role"], "jti": claims.get("jti", "")}

    def require(*roles: str):
        def dep(user: Dict[str, str] = Depends(current_user)) -> Dict[str, str]:
            if settings.demo_mode:
                return user
            if user["role"] not in roles:
                raise HTTPException(status_code=403, detail="Insufficient permissions")
            return user
        return dep

    def decisions() -> Dict:
        return ledger.latest_decisions(state["svc"].cutoff)

    def fva_frame(group_by=("planner_id",)) -> pd.DataFrame:
        svc = state["svc"]
        rows = ledger.overrides(cutoff=svc.cutoff)
        return override_fva(pd.DataFrame(rows), svc.actuals, group_by=group_by) if rows else override_fva(pd.DataFrame(), svc.actuals, group_by=group_by)

    # ------------------------------------------------------------------ public
    @app.get("/api/v1/health")
    def health():
        svc = state["svc"]
        return {"status": "healthy", "timestamp": _now(), "cutoff": svc.cutoff, "data_generated_at": svc.meta.get("generated_at")}

    @app.post("/api/v1/auth/login")
    def login(payload: LoginRequest):
        now = time.monotonic()
        recent = [t for t in failures.get(payload.username, []) if now - t < LOCKOUT_SECONDS]
        failures[payload.username] = recent
        if len(recent) >= MAX_FAILED_LOGINS:
            raise HTTPException(status_code=429, detail="Too many failed attempts. Try again later.")
        user = users.authenticate(payload.username, payload.password)
        if user is None:
            failures[payload.username].append(now)
            log.warning("failed login for %s", payload.username)
            raise HTTPException(status_code=401, detail="Invalid credentials")
        failures.pop(payload.username, None)
        token = make_token(secret, user["username"], user["role"], token_minutes)
        return {"access_token": token, "token_type": "bearer", "role": user["role"],
                "username": user["username"], "expires_in": token_minutes * 60}

    # ------------------------------------------------------------------ read
    @app.get("/api/v1/summary")
    def summary(user=Depends(require(*ALL_ROLES))):
        svc = state["svc"]
        df = fva_frame(("planner_id",))
        fva = None
        if not df.empty:
            n = int(df["n_overrides"].sum())
            vol = df["volume"].sum()
            fpe_ml = float((df["fpe_ml"] * df["volume"]).sum() / vol)
            fpe_pl = float((df["fpe_planner"] * df["volume"]).sum() / vol)
            fva = {"matured_overrides": n, "fva_pts": fpe_ml - fpe_pl}
        return svc.summary(decisions(), committed=ledger.commit_for(svc.cutoff) is not None, fva=fva)

    @app.get("/api/v1/exceptions")
    def exceptions(
        state_filter: str = Query("EXCEPTION_REVIEW", alias="state", pattern="^(EXCEPTION_REVIEW|PLANNER_LED|AUTO_APPROVED)$"),
        channel: Optional[str] = None,
        abc_category: Optional[str] = None,
        risk_type: Optional[str] = None,
        pending: Optional[bool] = None,
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
        user=Depends(require(*ALL_ROLES)),
    ):
        return state["svc"].exceptions(decisions(), state_filter, channel, abc_category, risk_type, pending, limit, offset)

    @app.get("/api/v1/items/{item_id}")
    def item_detail(item_id: int, user=Depends(require(*ALL_ROLES))):
        detail = state["svc"].item_detail(item_id, decisions())
        if detail is None:
            raise HTTPException(status_code=404, detail="Item not found")
        detail["overrides"] = ledger.overrides(cutoff=state["svc"].cutoff, item_id=item_id)
        detail["phasing"] = state["svc"].phasing_detail(item_id, decisions())
        return detail

    @app.post("/api/v1/items/{item_id}/narrative")
    def item_narrative(item_id: int, user=Depends(require(*ALL_ROLES))):
        """LLM explanation built from telemetry only; guardrails and the deterministic text back it up."""
        svc, dec = state["svc"], decisions()
        detail = svc.item_detail(item_id, dec)
        if detail is None:
            raise HTTPException(status_code=404, detail="Item not found")
        telemetry = build_telemetry(detail, svc.phasing_detail(item_id, dec))
        result = app.state.narrator.narrate(telemetry, fallback=detail["item"].get("narrative") or "")
        return {"item_id": item_id, "enabled": app.state.narrator.enabled, **result}

    @app.get("/api/v1/phasing/summary")
    def phasing_summary(user=Depends(require(*ALL_ROLES))):
        """Demand view (month the material is available) vs expected delivery view, by month and channel."""
        return state["svc"].phasing_summary(decisions())

    @app.get("/api/v1/overrides")
    def list_overrides(item_id: Optional[int] = None, limit: int = Query(200, ge=1, le=2000), user=Depends(require(*ALL_ROLES))):
        return ledger.overrides(cutoff=state["svc"].cutoff, item_id=item_id, limit=limit)

    @app.get("/api/v1/fva")
    def fva(group_by: str = Query("planner_id", pattern="^(planner_id|reason_code)$"), user=Depends(require(*ALL_ROLES))):
        df = fva_frame((group_by,))
        rows = [{k: clean(v) for k, v in r.items()} for r in df.to_dict("records")]
        return {
            "group_by": group_by,
            "matured_overrides": int(df["n_overrides"].sum()) if not df.empty else 0,
            "rows": rows,
            "note": "FVA = FPE(ML) - FPE(planner) in points over overrides whose month has actuals; positive means the override added value.",
        }

    @app.post("/api/v1/allocation/simulate")
    def allocation_simulate(payload: AllocationRequest, user=Depends(require(*ALL_ROLES))):
        """Read-only what-if: split constrained supply of one product across its channels (F-D2C-01)."""
        svc = state["svc"]
        month = payload.month or svc.first_month
        rows = svc.product_demand(payload.product_id, month)
        if not rows:
            raise HTTPException(status_code=404, detail=f"No planned demand for product {payload.product_id} in {month}.")
        cfg = AllocationConfig(
            chargeback_pct=payload.chargeback_pct, otif_penalty_pct=payload.otif_penalty_pct,
            amazon_floor_pct=payload.amazon_floor_pct, b2b_floor_pct=payload.b2b_floor_pct, d2c_churn_pct=payload.d2c_churn_pct,
        )
        result = simulate(build_demands(rows, cfg), payload.available_units)
        result.update({"product_id": payload.product_id, "month": month, "assumptions": payload.model_dump(exclude={"product_id", "available_units", "month"})})
        return result

    @app.get("/api/v1/reason-codes")
    def reason_codes(user=Depends(require(*ALL_ROLES))):
        return {"accept": ReasonCode.ML_ACCEPTED.value,
                "codes": [r.value for r in ReasonCode if r is not ReasonCode.ML_ACCEPTED]}

    @app.get("/api/v1/export/consensus")
    def export_consensus(user=Depends(require(*ALL_ROLES))):
        df = state["svc"].consensus_with_deliveries(decisions())
        return Response(df.to_csv(index=False), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="consensus_{state["svc"].cutoff}_draft.csv"'})

    # ------------------------------------------------------------------ write
    @app.post("/api/v1/override")
    def submit_override(payload: OverrideRequest, request: Request, user=Depends(require(*WRITE_ROLES))):
        svc = state["svc"]
        cutoff = payload.cutoff or svc.cutoff
        if cutoff != svc.cutoff:
            raise HTTPException(status_code=422, detail=f"Only the current cycle (cutoff {svc.cutoff}) accepts decisions.")
        if ledger.commit_for(cutoff):
            raise HTTPException(status_code=409, detail="Cycle already committed; decisions are closed.")
        reason = payload.reason_code
        if reason is None or not reason.strip():
            raise HTTPException(status_code=422, detail="Reason code is required.")
        if reason not in {r.value for r in ReasonCode}:
            raise HTTPException(status_code=422, detail="Unknown reason code.")
        row = svc.item_row(payload.item_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Item not found")
        if not bool(row["in_scope"]):
            raise HTTPException(status_code=422, detail="Item is out of scope (no demand); nothing to decide.")
        month = payload.target_month or svc.first_month
        if not svc.has_month(payload.item_id, month):
            raise HTTPException(status_code=422, detail=f"No forecast for {month}.")
        baseline, recommended = svc.month_values(payload.item_id, month)
        qty = float(payload.override_quantity)
        accepted = reason == ReasonCode.ML_ACCEPTED.value
        same = abs(qty - recommended) < 1e-6
        if accepted and not same:
            raise HTTPException(status_code=422, detail="Accepting the ML output cannot change the quantity.")
        if not accepted and same:
            raise HTTPException(status_code=422, detail="Quantity equals the ML recommendation: use 'ML Output Formally Accepted'.")
        if row["state"] == "AUTO_APPROVED" and not accepted and abs(qty - recommended) / max(recommended, 1.0) < SUPPRESS_PCT:
            raise HTTPException(status_code=422, detail="Adjustments below 5% on touchless items are suppressed.")
        record = {
            "timestamp": _now(), "item_id": payload.item_id, "cutoff": cutoff, "target_month": month,
            "baseline_quantity": baseline, "ml_recommended_quantity": recommended, "submitted_quantity": qty,
            "variance_to_ml": qty - recommended, "financial_impact_gbp": (qty - recommended) * float(row["sales_price"]),
            "reason_code": reason, "planner_comment": payload.comment, "planner_id": user["username"],
            "ip_address": request.client.host if request.client else None,
            "session_id": request.headers.get("X-Session-Id") or user["jti"],
            "tier": row["tier"], "risk_type": row["risk_type"],
        }
        record["id"] = ledger.append_override(record)
        return record

    @app.post("/api/v1/cycles/{cutoff}/commit")
    def commit_cycle(cutoff: str, user=Depends(require(*WRITE_ROLES))):
        svc = state["svc"]
        if cutoff != svc.cutoff:
            raise HTTPException(status_code=404, detail="Unknown cycle")
        if ledger.commit_for(cutoff):
            raise HTTPException(status_code=409, detail="Cycle already committed.")
        pending = svc.pending_review_ids(decisions())
        if pending:
            raise HTTPException(status_code=409, detail=f"{len(pending)} exception(s) still need a decision (e.g. item {pending[0]}).")
        df = svc.consensus_with_deliveries(decisions())
        store_dir.mkdir(parents=True, exist_ok=True)
        path = store_dir / f"consensus_{cutoff}.csv"
        df.to_csv(path, index=False)
        try:
            ledger.append_commit({"timestamp": _now(), "cutoff": cutoff, "planner_id": user["username"],
                                  "items": int(df["item_id"].nunique()), "path": str(path)})
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        if runs is not None:
            runs.mark_committed(cutoff)
        return {"cutoff": cutoff, "path": str(path), "items": int(df["item_id"].nunique()), "rows": int(len(df)),
                "committed_by": user["username"]}

    # ------------------------------------------------------------------ admin / audit
    @app.post("/api/v1/admin/reload")
    def reload_data(user=Depends(require(Role.ADMIN.value))):
        if reload_fn is None:
            raise HTTPException(status_code=501, detail="Reload is not configured")
        state["svc"] = reload_fn()
        return {"status": "reloaded", "cutoff": state["svc"].cutoff, "data_generated_at": state["svc"].meta.get("generated_at")}

    @app.get("/api/v1/audit/verify")
    def verify_audit(user=Depends(require(Role.AUDITOR.value, Role.ADMIN.value))):
        ok, bad = ledger.verify_chain()
        return {"ok": ok, "first_bad_id": bad}

    @app.get("/api/v1/metrics")
    def prometheus_metrics():
        svc = state["svc"]
        queue = len(svc.pending_review_ids(decisions()))
        return Response(metrics.render({"sop_pending_exceptions": queue, "sop_queue_size": len(svc._review_ids)}),
                        media_type="text/plain; version=0.0.4")

    from app.api.extended import register
    register(app, {"state": state, "ledger": ledger, "require": require, "decisions": decisions, "all_roles": ALL_ROLES,
                   "runs": runs, "chats": chats, "llm": llm, "store_dir": store_dir, "reload_fn": reload_fn, "jobs": jobs})
    from app.api.ops_routes import register_ops
    register_ops(app, {"state": state, "require": require, "decisions": decisions, "all_roles": ALL_ROLES,
                       "schedules": schedules, "notes": notes, "scenarios": scenarios, "store_dir": store_dir})
    return app


class Metrics:
    """Minimal Prometheus exposition: request counts and latency histograms per route."""
    BUCKETS = (0.05, 0.1, 0.25, 0.4, 1.0, 2.5, 10.0)

    def __init__(self) -> None:
        self.counts: Dict[tuple, int] = {}
        self.hist: Dict[str, List[float]] = {}

    def observe(self, method: str, path: str, status: int, seconds: float) -> None:
        key = (method, path, status)
        self.counts[key] = self.counts.get(key, 0) + 1
        h = self.hist.setdefault(path, [0.0] * (len(self.BUCKETS) + 2))
        for i, b in enumerate(self.BUCKETS):
            if seconds <= b:
                h[i] += 1
        h[-2] += 1
        h[-1] += seconds

    def render(self, gauges: Dict[str, float]) -> str:
        out = ["# TYPE sop_http_requests_total counter"]
        out += [f'sop_http_requests_total{{method="{m}",path="{p}",status="{s}"}} {n}' for (m, p, s), n in self.counts.items()]
        out.append("# TYPE sop_http_request_seconds histogram")
        for p, h in self.hist.items():
            out += [f'sop_http_request_seconds_bucket{{path="{p}",le="{b}"}} {int(h[i])}' for i, b in enumerate(self.BUCKETS)]
            out += [f'sop_http_request_seconds_bucket{{path="{p}",le="+Inf"}} {int(h[-2])}',
                    f'sop_http_request_seconds_count{{path="{p}"}} {int(h[-2])}', f'sop_http_request_seconds_sum{{path="{p}"}} {h[-1]:.4f}']
        for k, v in gauges.items():
            out += [f"# TYPE {k} gauge", f"{k} {v}"]
        return "\n".join(out) + "\n"


def create_default_app() -> FastAPI:
    """Production-style wiring from environment/settings (used by uvicorn --factory)."""
    secret = settings.jwt_secret
    if not secret:
        secret = secrets.token_urlsafe(48)
        log.warning("JWT_SECRET not set: using a random secret, tokens will not survive a restart")
    users = UserStore(settings.store_dir / "users.json")
    if len(users) == 0:
        log.warning("No users found. Create one with: python -m app.api.users add <name> <planner|admin|auditor>")

    def loader() -> DataService:
        return DataService.from_reports(settings.reports_dir)

    db = None
    if settings.mongo_uri:
        from app.store.mongo_ledger import MongoLedger
        from app.store.runs import mongo_db
        ledger_impl: Any = MongoLedger(settings.mongo_uri, settings.mongo_db)
        db = mongo_db(settings.mongo_uri, settings.mongo_db, ledger_impl.client)
        log.info("Using MongoDB ledger (db=%s)", settings.mongo_db)
    else:
        ledger_impl = Ledger(settings.store_dir / "ledger.db")

    client = client_from_env()
    if client is None:
        log.warning("No LLM key set (EMERGENT_LLM_KEY / GEMINI_API_KEY): narratives use the deterministic text")
    return create_app(
        service=loader(), ledger=ledger_impl, users=users, secret=secret,
        store_dir=settings.store_dir, cors_origins=settings.cors_origins, token_minutes=settings.token_minutes,
        reload_fn=loader, narrator=Narrator(client, settings.store_dir / "narratives.db"), db=db, llm=client,
    )
