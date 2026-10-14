"""Schedules, notifications and what-if scenario endpoints."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from app.models.enums import Role
from app.orchestration import scheduler as sched
from app.rules import otif
from app.scenarios import engine

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


def register_ops(app: FastAPI, ctx: Dict[str, Any]) -> None:
    state, require, decisions, all_roles = ctx["state"], ctx["require"], ctx["decisions"], ctx["all_roles"]
    write_roles, admin = (Role.PLANNER.value, Role.ADMIN.value), Role.ADMIN.value
    schedules, notes, scenarios, store_dir = ctx.get("schedules"), ctx.get("notes"), ctx.get("scenarios"), ctx["store_dir"]

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
