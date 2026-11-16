"""MCP server configuration endpoints (Phase A: registry, test connection, tool calls).

Registered in `main.py` like `tasks.py`. Admin manages servers; planner/admin can call
allowlisted tools; every call is written to the `mcp_calls` audit log.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from app.mcp.client import McpError, call_tool, list_tools, tool_text
from app.mcp.registry import allowlisted, is_write_tool, merge_update, normalize_payload, public_doc
from app.mcp.templates import KIND_TEMPLATES
from app.models.enums import Role

log = logging.getLogger("sop.mcp")


class McpServerPayload(BaseModel):
    id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=120)
    kind: str = Field("generic", pattern="^(erp|crm|jira|generic)$")
    transport: str = Field("streamable_http", pattern="^(streamable_http|http_sse)$")
    url: str = Field(..., min_length=1, max_length=500)
    headers: Dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    tools: List[str] = Field(default_factory=list)
    timeout_s: int = Field(10, ge=1, le=120)


class McpUpdatePayload(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    kind: Optional[str] = Field(None, pattern="^(erp|crm|jira|generic)$")
    transport: Optional[str] = Field(None, pattern="^(streamable_http|http_sse)$")
    url: Optional[str] = Field(None, min_length=1, max_length=500)
    headers: Optional[Dict[str, str]] = None
    enabled: Optional[bool] = None
    tools: Optional[List[str]] = None
    timeout_s: Optional[int] = Field(None, ge=1, le=120)


class McpCallPayload(BaseModel):
    tool: str = Field(..., min_length=1, max_length=120)
    arguments: Dict[str, Any] = Field(default_factory=dict)


def register(app, ctx: Dict[str, Any]) -> None:
    state, require, all_roles = ctx["state"], ctx["require"], ctx["all_roles"]
    store = ctx["mcp_store"]
    admin = require(Role.ADMIN.value)
    writers = require(Role.PLANNER.value, Role.ADMIN.value)

    def _get(server_id: str) -> Dict[str, Any]:
        doc = store.get(server_id)
        if doc is None:
            raise HTTPException(status_code=404, detail=f"MCP server {server_id} not found")
        return doc

    @app.get("/api/v1/mcp/servers")
    def mcp_list(user=Depends(require(*all_roles))):
        return {"servers": [public_doc(d) for d in store.list()], "kinds": KIND_TEMPLATES}

    @app.get("/api/v1/mcp/servers/{server_id}")
    def mcp_get(server_id: str, user=Depends(require(*all_roles))):
        return public_doc(_get(server_id))

    @app.get("/api/v1/mcp/calls")
    def mcp_calls(user=Depends(require(*all_roles)), limit: int = 50):
        return {"calls": store.calls(min(max(limit, 1), 200))}

    @app.post("/api/v1/mcp/servers")
    def mcp_create(payload: McpServerPayload, user=Depends(admin)):
        try:
            doc = normalize_payload(payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        try:
            created = store.create(doc)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return public_doc(created)

    @app.put("/api/v1/mcp/servers/{server_id}")
    def mcp_update(server_id: str, payload: McpUpdatePayload, user=Depends(admin)):
        existing = _get(server_id)
        fields = payload.model_dump(exclude_unset=True)
        try:
            merged = merge_update(existing, fields)
            merged = normalize_payload(merged)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        updated = store.update(server_id, merged)
        return public_doc(updated)

    @app.delete("/api/v1/mcp/servers/{server_id}")
    def mcp_delete(server_id: str, user=Depends(admin)):
        if not store.delete(server_id):
            raise HTTPException(status_code=404, detail=f"MCP server {server_id} not found")
        return {"deleted": server_id}

    @app.post("/api/v1/mcp/servers/{server_id}/test")
    def mcp_test(server_id: str, user=Depends(admin)):
        doc = _get(server_id)
        try:
            tools = list_tools(doc)
            store.update(server_id, {"status": "connected", "last_error": "", "discovered_tools": tools})
            return {"status": "connected", "tools": tools}
        except McpError as exc:
            store.update(server_id, {"status": "failed", "last_error": exc.message})
            raise HTTPException(status_code=502, detail=f"{exc.code}: {exc.message}")

    @app.get("/api/v1/mcp/servers/{server_id}/tools")
    def mcp_tools(server_id: str, user=Depends(require(*all_roles))):
        doc = _get(server_id)
        return {"server_id": server_id, "status": doc.get("status", "never_tested"),
                "last_error": doc.get("last_error", ""), "tools": doc.get("discovered_tools", [])}

    @app.post("/api/v1/mcp/servers/{server_id}/call")
    def mcp_call(server_id: str, payload: McpCallPayload, user=Depends(writers)):
        doc = _get(server_id)
        if not doc.get("enabled", True):
            raise HTTPException(status_code=409, detail=f"MCP server {server_id} is disabled")
        if not allowlisted(doc, payload.tool):
            raise HTTPException(status_code=403, detail=f"Tool {payload.tool} is not on the allowlist")
        if is_write_tool(doc.get("kind", "generic"), payload.tool) and user["role"] != Role.ADMIN.value:
            raise HTTPException(status_code=403, detail=f"Tool {payload.tool} is a write tool; only admin can call it")
        args_summary = json.dumps(payload.arguments, default=str)[:200]
        t0 = time.perf_counter()
        try:
            result = call_tool(doc, payload.tool, payload.arguments)
        except McpError as exc:
            store.log_call({"server_id": server_id, "tool": payload.tool, "user": user["username"],
                            "ok": False, "error": f"{exc.code}: {exc.message}",
                            "args": args_summary, "latency_ms": int((time.perf_counter() - t0) * 1000)})
            raise HTTPException(status_code=502, detail=f"{exc.code}: {exc.message}")
        store.log_call({"server_id": server_id, "tool": payload.tool, "user": user["username"], "ok": True,
                        "args": args_summary, "latency_ms": int((time.perf_counter() - t0) * 1000)})
        return {"server_id": server_id, "tool": payload.tool, "result": result, "text": tool_text(result)}
