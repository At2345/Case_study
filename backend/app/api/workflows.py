"""Workflow automation endpoints: node catalogue, versioned definitions, runs, approvals."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.bi.tenancy import DEFAULT_TENANT
from app.models.enums import Role
from app.workflows import drafting
from app.workflows.mcp_server import SUPPORTED_VERSIONS, PlaybookMcpServer
from app.workflows.runners import LocalRunner, TemporalUnavailable
from app.workflows.runtime import RunStateError, WorkflowError
from app.workflows.service import WorkflowService

STATUSES = "^(RUNNING|WAITING|WAITING_APPROVAL|SUCCEEDED|FAILED|REJECTED)$"


class RunRequest(BaseModel):
    payload: Dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False
    version: Optional[int] = Field(None, ge=1)


class ApiKeyRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    role: str = Field("planner", pattern="^(planner|auditor)$")


class OutcomeRequest(BaseModel):
    metric: str = Field(..., pattern="^(loss_avoided_eur|recovered_eur|units_sold|sell_through_pct|other)$")
    value: float = Field(..., ge=-1e9, le=1e9)
    sku: Optional[str] = Field(None, max_length=64)
    node: Optional[str] = Field(None, max_length=64)
    note: Optional[str] = Field(None, max_length=500)


class InstallTemplateRequest(BaseModel):
    workflow_id: str = Field(..., pattern=r"^[a-z0-9][a-z0-9_\-]{0,63}$")
    name: Optional[str] = Field(None, max_length=120)
    params: Dict[str, Any] = Field(default_factory=dict)


class DraftRequest(BaseModel):
    description: str = Field(..., min_length=5, max_length=2000)
    use_llm: bool = True


class WorkflowSettings(BaseModel):
    share_benchmarks: bool


class ApproveRequest(BaseModel):
    node: str = Field(..., min_length=1, max_length=64)
    approved: bool
    note: Optional[str] = Field(None, max_length=500)


def register(app: FastAPI, ctx: Dict[str, Any]) -> None:
    require, all_roles = ctx["require"], ctx["all_roles"]
    write_roles, admin = (Role.PLANNER.value, Role.ADMIN.value), Role.ADMIN.value
    svc: WorkflowService = ctx["workflows"]
    runner = ctx.get("runner") or LocalRunner(svc)  # starts and approvals go through the runner (local or Temporal)

    def tenant(user: Dict[str, str]) -> str:
        return user.get("tenant") or DEFAULT_TENANT

    def call(fn, *args, **kwargs):
        """Map service errors to HTTP status codes in one place."""
        try:
            return fn(*args, **kwargs)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc.args[0]) if exc.args else "Not found")
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except TemporalUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except RunStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except (WorkflowError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    # ------------------------------------------------------------------ MCP server (AI agents)
    # Registered before /workflows/{workflow_id} so "mcp" is never read as a workflow id.
    mcp = PlaybookMcpServer(svc, runner, allow_approvals=bool(ctx.get("mcp_allow_approvals")), llm=ctx.get("llm"),
                            hub=ctx.get("hub"))
    app.state.playbook_mcp = mcp  # the operations agent uses the same tools and permission checks
    allowed_origins = set(ctx.get("cors_origins") or ())

    @app.post("/api/v1/workflows/mcp")
    async def mcp_endpoint(request: Request, user=Depends(require(*all_roles))):
        """Streamable HTTP MCP endpoint (JSON responses). Auth: API key or login token."""
        origin = request.headers.get("origin")
        if origin and origin not in allowed_origins:  # DNS-rebinding guard required by the MCP spec
            raise HTTPException(status_code=403, detail="Origin not allowed")
        version = request.headers.get("mcp-protocol-version")
        if version and version not in SUPPORTED_VERSIONS:
            raise HTTPException(status_code=400, detail=f"Unsupported MCP-Protocol-Version {version}")
        try:
            message = await request.json()
        except ValueError:
            return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
        if isinstance(message, list):
            return JSONResponse({"jsonrpc": "2.0", "id": None,
                                 "error": {"code": -32600, "message": "Batch requests are not supported"}})
        reply = await run_in_threadpool(mcp.handle, message, user)  # tools do blocking file/Temporal work
        return Response(status_code=202) if reply is None else JSONResponse(reply)

    @app.get("/api/v1/workflows/mcp")
    def mcp_sse_not_offered(user=Depends(require(*all_roles))):
        return Response(status_code=405, headers={"Allow": "POST"})

    # ------------------------------------------------------------------ text-to-workflow
    llm = ctx.get("llm")

    @app.post("/api/v1/workflows/draft")
    async def draft_workflow(body: DraftRequest, user=Depends(require(*write_roles))):
        """Draft a playbook from plain language. Returns the draft and its validation errors; never saves."""
        return await run_in_threadpool(drafting.draft, body.description, svc.registry, llm, body.use_llm)

    # ------------------------------------------------------------------ catalogue and definitions
    @app.get("/api/v1/workflows/node-types")
    def node_types(user=Depends(require(*all_roles))):
        return {"node_types": svc.catalogue()}

    @app.post("/api/v1/workflows/validate")
    def validate_workflow(spec: Dict[str, Any], user=Depends(require(*all_roles))):
        _, errors = svc.check(spec)
        return {"valid": not errors, "errors": errors}

    @app.get("/api/v1/workflows")
    def list_workflows(user=Depends(require(*all_roles))):
        return {"workflows": call(svc.list, tenant(user))}

    @app.post("/api/v1/workflows", status_code=201)
    def save_workflow(spec: Dict[str, Any], user=Depends(require(*write_roles))):
        """Each save creates a new version; the client's version field is ignored."""
        return call(svc.save, tenant(user), spec).model_dump()

    @app.get("/api/v1/workflows/{workflow_id}")
    def get_workflow(workflow_id: str, version: Optional[int] = Query(None, ge=1),
                     user=Depends(require(*all_roles))):
        return call(svc.get, tenant(user), workflow_id, version).model_dump()

    # ------------------------------------------------------------------ runs
    @app.post("/api/v1/workflows/{workflow_id}/runs", status_code=201)
    def start_run(workflow_id: str, body: RunRequest, user=Depends(require(*write_roles))):
        return call(runner.start, tenant(user), workflow_id, body.payload, user["username"],
                    dry_run=body.dry_run, version=body.version)

    @app.get("/api/v1/workflow-runs")
    def list_runs(workflow_id: Optional[str] = Query(None, max_length=64),
                  status: Optional[str] = Query(None, pattern=STATUSES),
                  updated_after: Optional[datetime] = Query(None, description="ISO time; for polling clients such as n8n"),
                  limit: int = Query(50, ge=1, le=500), user=Depends(require(*all_roles))):
        return {"runs": call(svc.list_runs, tenant(user), workflow_id, status, limit, updated_after)}

    @app.get("/api/v1/workflow-runs/{run_id}")
    def get_run(run_id: str, user=Depends(require(*all_roles))):
        return call(svc.get_run, tenant(user), run_id)

    # ------------------------------------------------------------------ decision-outcome ledger
    @app.get("/api/v1/workflow-runs/{run_id}/decisions")
    def run_decisions(run_id: str, user=Depends(require(*all_roles))):
        call(svc.get_run, tenant(user), run_id)  # 404 for unknown runs or other tenants
        return {"decisions": svc.decisions(tenant(user), run_id=run_id),
                "outcomes": svc.outcomes(tenant(user), run_id=run_id)}

    @app.post("/api/v1/workflow-runs/{run_id}/outcomes", status_code=201)
    def record_outcome(run_id: str, body: OutcomeRequest, user=Depends(require(*write_roles))):
        source = "api_key" if user.get("via") == "api_key" else "user"
        return call(svc.record_outcome, tenant(user), run_id, body.metric, body.value, user["username"],
                    sku=body.sku, node_id=body.node, note=body.note, source=source)

    @app.get("/api/v1/workflows-performance")
    def performance(workflow_id: Optional[str] = Query(None, max_length=64), user=Depends(require(*all_roles))):
        """Playbooks ranked by measured value per euro spent (real runs only)."""
        return {"playbooks": call(svc.performance, tenant(user), workflow_id)}

    # ------------------------------------------------------------------ template library + benchmark
    min_tenants = int(ctx.get("benchmark_min_tenants") or 3)

    @app.get("/api/v1/workflows-templates")
    def list_templates(user=Depends(require(*all_roles))):
        """Templates with your results and, when you share, the pooled cross-tenant benchmark."""
        return {"templates": call(svc.template_benchmark, tenant(user), min_tenants),
                "sharing": call(svc.get_settings, tenant(user))["share_benchmarks"], "min_tenants": min_tenants}

    @app.post("/api/v1/workflows-templates/{template_id}/install", status_code=201)
    def install_template(template_id: str, body: InstallTemplateRequest, user=Depends(require(*write_roles))):
        return call(svc.install_template, tenant(user), template_id, body.workflow_id, body.params,
                    body.name).model_dump()

    @app.get("/api/v1/admin/workflows-settings")
    def get_workflow_settings(user=Depends(require(admin))):
        return call(svc.get_settings, tenant(user))

    @app.put("/api/v1/admin/workflows-settings")
    def put_workflow_settings(body: WorkflowSettings, user=Depends(require(admin))):
        """Opt this tenant in or out of the anonymised template benchmark (give-to-get)."""
        return call(svc.update_settings, tenant(user), body.share_benchmarks)

    @app.get("/api/v1/workflow-ledger/verify")
    def verify_ledger(user=Depends(require(Role.AUDITOR.value, admin))):
        return svc.ledger.verify()

    @app.post("/api/v1/admin/workflow-ledger/reconcile")
    def reconcile_ledger(user=Depends(require(admin))):
        return {"added": svc.reconcile_ledger()}

    @app.post("/api/v1/workflow-runs/{run_id}/approve")
    def approve_run(run_id: str, body: ApproveRequest, user=Depends(require(*write_roles))):
        return call(runner.approve, tenant(user), run_id, body.node, body.approved, user, note=body.note)

    # ------------------------------------------------------------------ API keys (admin; for n8n and scripts)
    keys = ctx.get("api_keys")

    @app.get("/api/v1/admin/api-keys")
    def list_api_keys(user=Depends(require(admin))):
        return {"keys": keys.list(tenant(user)) if keys else []}

    @app.post("/api/v1/admin/api-keys", status_code=201)
    def create_api_key(body: ApiKeyRequest, user=Depends(require(admin))):
        """Returns the full key once; only its hash is stored."""
        if keys is None:
            raise HTTPException(status_code=503, detail="API keys are not configured")
        return call(keys.create, body.name, body.role, tenant(user), user["username"])

    @app.delete("/api/v1/admin/api-keys/{key_id}")
    def revoke_api_key(key_id: str, user=Depends(require(admin))):
        if keys is None or not keys.revoke(key_id, tenant(user)):
            raise HTTPException(status_code=404, detail="API key not found")
        return {"revoked": key_id}

    @app.get("/api/v1/workflows-executor")
    def executor_info(user=Depends(require(*all_roles))):
        return {"executor": runner.name}

    @app.post("/api/v1/workflow-runs/{run_id}/advance")
    def advance_run(run_id: str, user=Depends(require(admin))):
        """Manual resume (normally done by the scheduler when a wait expires). Not for Temporal runs."""
        def advance():
            if svc.get_run(tenant(user), run_id).get("executor") == "temporal":
                raise RunStateError("this run is managed by Temporal; its timer resumes it")
            return svc.advance(tenant(user), run_id)
        return call(advance)
