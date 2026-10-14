"""Schedules, notifications and what-if scenario endpoints."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Union

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.models.enums import ReasonCode, Role
from app.orchestration import scheduler as sched
from app.rules import otif
from app.scenarios import engine, generator

MAX_COMPARE = 3


class ScheduleUpdate(BaseModel):
    enabled: bool
    frequency: Literal["daily", "monthly"]
    day: Optional[int] = None
    hour: int
    minute: int = 0


class PctEdit(BaseModel):
    type: Literal["pct"]
    scope: Literal["all", "channel", "product"] = "all"
    value: Optional[str] = Field(None, max_length=64)
    pct: float = Field(..., ge=-100, le=500)
    month: Optional[str] = None


class ItemEdit(BaseModel):
    type: Literal["item"]
    item_id: int
    qty: float = Field(..., ge=0, le=100_000_000)
    month: str


class ScenarioIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    description: str = Field("", max_length=500)
    edits: List[Union[PctEdit, ItemEdit]] = Field(default_factory=list)


class ScenarioGenerateIn(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=500)


def register_ops(app: FastAPI, ctx: Dict[str, Any]) -> None:
    state, require, decisions, all_roles = ctx["state"], ctx["require"], ctx["decisions"], ctx["all_roles"]
    write_roles, admin = (Role.PLANNER.value, Role.ADMIN.value), Role.ADMIN.value
    schedules, notes, scenarios, store_dir = ctx.get("schedules"), ctx.get("notes"), ctx.get("scenarios"), ctx["store_dir"]
    ledger = ctx.get("ledger")

    def need(store, what: str):
        if store is None:
            raise HTTPException(status_code=503, detail=f"{what} store (MongoDB) is not configured.")
        return store

    # ------------------------------------------------------------------ schedules
    @app.get("/api/v1/schedules")
    def list_schedules(user=Depends(require(*all_roles))):
        return {"timezone": sched.TZ, "schedules": need(schedules, "Schedule").all()}

    @app.put("/api/v1/admin/schedules/{job}")
    def update_schedule(job: str, payload: ScheduleUpdate, user=Depends(require(admin))):
        store = need(schedules, "Schedule")
        if store.get(job) is None:
            raise HTTPException(status_code=404, detail="Unknown schedule")
        spec = payload.model_dump()
        try:
            sched.validate(spec)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return store.update(job, spec)

    # ------------------------------------------------------------------ notifications
    @app.get("/api/v1/notifications")
    def list_notifications(user=Depends(require(*all_roles))):
        return need(notes, "Notification").for_user(user["username"], user["role"])

    @app.post("/api/v1/notifications/read-all")
    def read_all(user=Depends(require(*all_roles))):
        return {"updated": need(notes, "Notification").mark_read(user["username"], user["role"])}

    @app.post("/api/v1/notifications/{note_id}/read")
    def read_one(note_id: str, user=Depends(require(*all_roles))):
        if need(notes, "Notification").mark_read(user["username"], user["role"], note_id) == 0:
            raise HTTPException(status_code=404, detail="Notification not found")
        return {"updated": 1}

    # ------------------------------------------------------------------ what-if scenarios
    def check_edits(edits: List[Dict[str, Any]]) -> None:
        try:
            engine.validate_edits(edits, state["svc"])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    def owned(scenario_id: str, user: Dict[str, str]) -> Dict[str, Any]:
        s = need(scenarios, "Scenario").get(scenario_id)
        if s is None or s["cutoff"] != state["svc"].cutoff:
            raise HTTPException(status_code=404, detail="Scenario not found")
        if s["created_by"] != user["username"] and user["role"] != admin:
            raise HTTPException(status_code=403, detail="Only the author or an admin can change this scenario")
        return s

    @app.get("/api/v1/scenarios")
    def list_scenarios(user=Depends(require(*all_roles))):
        return {"cutoff": state["svc"].cutoff, "max_compare": MAX_COMPARE, "scenarios": need(scenarios, "Scenario").list(state["svc"].cutoff)}

    @app.post("/api/v1/scenarios")
    def create_scenario(payload: ScenarioIn, user=Depends(require(*write_roles))):
        edits = [e.model_dump() for e in payload.edits]
        check_edits(edits)
        return need(scenarios, "Scenario").create(state["svc"].cutoff, payload.name, payload.description, edits, user["username"])

    @app.post("/api/v1/scenarios/generate")
    def generate_scenario(payload: ScenarioGenerateIn, user=Depends(require(*write_roles))):
        try:
            proposal = generator.generate(payload.prompt, state["svc"])
            check_edits(proposal["edits"])
            return proposal
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.get("/api/v1/scenarios/suggestions")
    def scenario_suggestions(user=Depends(require(*all_roles))):
        return {"cutoff": state["svc"].cutoff, "suggestions": generator.suggest(state["svc"], decisions())}

    @app.put("/api/v1/scenarios/{scenario_id}")
    def update_scenario(scenario_id: str, payload: ScenarioIn, user=Depends(require(*write_roles))):
        owned(scenario_id, user)
        edits = [e.model_dump() for e in payload.edits]
        check_edits(edits)
        return scenarios.update(scenario_id, {"name": payload.name, "description": payload.description, "edits": edits})

    @app.delete("/api/v1/scenarios/{scenario_id}")
    def delete_scenario(scenario_id: str, user=Depends(require(*write_roles))):
        owned(scenario_id, user)
        scenarios.delete(scenario_id)
        return {"deleted": scenario_id}

    @app.get("/api/v1/scenarios/compare")
    def compare_scenarios(ids: str = Query("", max_length=200), user=Depends(require(*all_roles))):
        store = need(scenarios, "Scenario")
        wanted = [i for i in ids.split(",") if i]
        if len(wanted) > MAX_COMPARE:
            raise HTTPException(status_code=422, detail=f"Compare at most {MAX_COMPARE} scenarios at a time.")
        found = [store.get(i) for i in wanted]
        if any(s is None or s["cutoff"] != state["svc"].cutoff for s in found):
            raise HTTPException(status_code=404, detail="Scenario not found")
        svc = state["svc"]
        return engine.compare(svc, svc.consensus(decisions()), found, otif.load_terms(store_dir))

    @app.post("/api/v1/scenarios/{scenario_id}/promote")
    def promote_scenario(scenario_id: str, request: Request, user=Depends(require(*write_roles))):
        """Write a scenario's first-month quantities as audited overrides (PRD backlog: promote to ledger)."""
        if ledger is None:
            raise HTTPException(status_code=503, detail="Audit ledger is not configured.")
        svc = state["svc"]
        if ledger.commit_for(svc.cutoff):
            raise HTTPException(status_code=409, detail="Cycle already committed; decisions are closed.")
        s = owned(scenario_id, user)
        base = svc.consensus(decisions())
        plan = engine.apply_edits(base, s["edits"])
        m1 = plan[plan["month"] == svc.first_month]
        current = base[base["month"] == svc.first_month].set_index("item_id")["consensus_qty"]
        records = []
        for r in m1.itertuples():
            row = svc.item_row(int(r.item_id))
            if row is None or not bool(row["in_scope"]):
                continue
            qty = float(r.consensus_qty)
            if abs(qty - float(current.get(int(r.item_id), qty))) < 1e-6:
                continue  # the scenario did not change this item's first-month quantity
            baseline, recommended = svc.month_values(int(r.item_id), svc.first_month)
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(), "item_id": int(r.item_id), "cutoff": svc.cutoff,
                "target_month": svc.first_month, "baseline_quantity": baseline, "ml_recommended_quantity": recommended,
                "submitted_quantity": qty, "variance_to_ml": qty - recommended,
                "financial_impact_gbp": (qty - recommended) * float(row["sales_price"]),
                "reason_code": ReasonCode.COMMERCIAL_INTELLIGENCE.value,
                "planner_comment": f"Promoted from scenario '{s['name']}' ({scenario_id}).",
                "planner_id": user["username"],
                "ip_address": request.client.host if request.client else None,
                "session_id": request.headers.get("X-Session-Id") or user["jti"],
                "tier": row["tier"], "risk_type": row["risk_type"],
            }
            record["id"] = ledger.append_override(record)
            records.append(record)
        return {"scenario_id": scenario_id, "promoted": len(records), "records": records}
