"""Operations agent API: ask questions, review and decide the agent's proposed changes."""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.agents.ops import OpsAgent, ProposalStore
from app.models.enums import Role

NO_MODEL = ("No AI model is configured. For a free local model install Ollama, pull a model "
            "(e.g. `ollama pull qwen2.5:14b`) and set OLLAMA_MODEL.")


class AskRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=2000)


class DecideRequest(BaseModel):
    approved: bool


def register(app: FastAPI, ctx: Dict[str, Any]) -> None:
    require, all_roles = ctx["require"], ctx["all_roles"]
    write_roles = (Role.PLANNER.value, Role.ADMIN.value)
    llm = ctx.get("llm")
    proposals = ProposalStore(ctx["store_dir"] / "agents")
    agent = OpsAgent(ctx["mcp"], llm, proposals) if llm is not None else None

    def tenant(user: Dict[str, str]) -> str:
        return user.get("tenant") or "default"

    @app.get("/api/v1/agents/status")
    def agent_status(user=Depends(require(*all_roles))):
        model = getattr(llm, "model", None) or (type(llm).__name__ if llm else None)
        return {"available": agent is not None, "model": model, "local": bool(getattr(llm, "local", False)),
                "hint": None if agent else NO_MODEL}

    @app.post("/api/v1/agents/ask")
    async def ask(body: AskRequest, user=Depends(require(*all_roles))):
        if agent is None:
            raise HTTPException(status_code=503, detail=NO_MODEL)
        try:
            return await run_in_threadpool(agent.ask, body.question, user)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=f"The AI model did not respond: {exc}")

    @app.get("/api/v1/agents/proposals")
    def list_proposals(status: Optional[str] = Query(None, pattern="^(pending|executed|rejected|failed)$"),
                       user=Depends(require(*all_roles))):
        return {"proposals": proposals.list(tenant(user), status)}

    @app.post("/api/v1/agents/proposals/{pid}/decide")
    async def decide(pid: str, body: DecideRequest, user=Depends(require(*write_roles))):
        """Approve (runs the change as you) or reject an agent proposal."""
        if agent is None:
            raise HTTPException(status_code=503, detail=NO_MODEL)
        try:
            return await run_in_threadpool(agent.decide, pid, body.approved, user)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc.args[0]))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
