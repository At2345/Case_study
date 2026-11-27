"""MCP server (streamable HTTP, JSON responses) exposing playbook tools to AI agents (n8n AI Agent, Claude, ...).

Mounted at POST /api/v1/workflows/mcp behind the normal bearer auth (API key or login token), so the
caller's role and tenant apply to every tool call.

Agent guardrails:
- start_run defaults to dry_run=true; real actions must be asked for explicitly and still go through
  the playbook's budget and approval gates.
- decide_step (approve/reject a waiting step) is only offered when WORKFLOW_MCP_ALLOW_APPROVALS=true,
  so an agent cannot approve its own costly action unless an admin opts in.
- Auditors only see read-only tools.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.workflows import drafting
from app.workflows.runners import TemporalUnavailable
from app.workflows.runtime import WorkflowError
from app.workflows.service import WorkflowService

SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
LATEST_VERSION = SUPPORTED_VERSIONS[0]
SERVER_INFO = {"name": "sop-workbench-playbooks", "version": "1.0.0"}
WRITE_ROLES = ("planner", "admin")
STATUSES = ("RUNNING", "WAITING", "WAITING_APPROVAL", "SUCCEEDED", "FAILED", "REJECTED")
INSTRUCTIONS = (
    "Tools for overstock-rescue playbooks. Inspect playbooks and runs freely. start_run is a dry run unless "
    "dry_run=false is passed; real runs spend budget and may pause for human approval."
)


# ---------------------------------------------------------------------------- tool arguments
class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArgs(_Args):
    pass


class GetPlaybookArgs(_Args):
    playbook_id: str = Field(min_length=1, max_length=64)
    version: Optional[int] = Field(None, ge=1)


class ListRunsArgs(_Args):
    playbook_id: Optional[str] = Field(None, max_length=64)
    status: Optional[str] = Field(None, pattern="^(" + "|".join(STATUSES) + ")$")
    updated_after: Optional[datetime] = None
    limit: int = Field(20, ge=1, le=200)


class GetRunArgs(_Args):
    run_id: str = Field(min_length=1, max_length=64)


class StartRunArgs(_Args):
    playbook_id: str = Field(min_length=1, max_length=64)
    payload: Dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = True


class PerformanceArgs(_Args):
    playbook_id: Optional[str] = Field(None, max_length=64)


class RecordOutcomeArgs(_Args):
    run_id: str = Field(min_length=1, max_length=64)
    metric: str = Field(pattern="^(loss_avoided_eur|recovered_eur|units_sold|sell_through_pct|other)$")
    value: float = Field(ge=-1e9, le=1e9)
    sku: Optional[str] = Field(None, max_length=64)
    note: Optional[str] = Field(None, max_length=500)


class DraftArgs(_Args):
    description: str = Field(min_length=5, max_length=2000)


class ChannelQueryArgs(_Args):
    connection_id: Optional[str] = Field(None, max_length=32)
    sku: Optional[str] = Field(None, max_length=80)
    limit: int = Field(20, ge=1, le=200)


class ConnectionArgs(_Args):
    connection_id: str = Field(min_length=1, max_length=32)


class ChannelStockArgs(_Args):
    connection_id: str = Field(min_length=1, max_length=32)
    sku: str = Field(min_length=1, max_length=80)
    quantity: int = Field(ge=0, le=10_000_000)


class ChannelPriceArgs(_Args):
    connection_id: str = Field(min_length=1, max_length=32)
    sku: str = Field(min_length=1, max_length=80)
    price: float = Field(gt=0, le=1_000_000)
    currency: Optional[str] = Field(None, max_length=3)


class PurchaseOrderArgs(_Args):
    connection_id: str = Field(min_length=1, max_length=32)
    po_number: str = Field(min_length=1, max_length=22)
    lines: List[Dict[str, Any]] = Field(min_length=1, max_length=200)


class DecideStepArgs(_Args):
    run_id: str = Field(min_length=1, max_length=64)
    node: str = Field(min_length=1, max_length=64)
    approved: bool
    note: Optional[str] = Field(None, max_length=500)


class Tool:
    def __init__(self, name: str, description: str, args: type, fn: Callable, write: bool = False,
                 destructive: bool = False, approval: bool = False, title: str = "") -> None:
        self.name, self.description, self.args, self.fn = name, description, args, fn
        self.write, self.destructive, self.approval, self.title = write, destructive, approval, title or name

    def spec(self) -> Dict[str, Any]:
        schema = self.args.model_json_schema()
        schema.pop("title", None)
        return {
            "name": self.name, "title": self.title, "description": self.description, "inputSchema": schema,
            "annotations": {"readOnlyHint": not self.write, "destructiveHint": self.destructive,
                            "idempotentHint": not self.write, "openWorldHint": False},
        }


class RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


class PlaybookMcpServer:
    def __init__(self, service: WorkflowService, runner: Any, allow_approvals: bool = False, llm: Any = None,
                 hub: Any = None) -> None:
        self.svc, self.runner, self.allow_approvals, self.llm, self.hub = service, runner, allow_approvals, llm, hub
        self.tools: List[Tool] = [
            Tool("list_playbooks", "List saved playbooks (latest version of each).", NoArgs,
                 lambda u, a: {"playbooks": self.svc.list(_tenant(u))}, title="List playbooks"),
            Tool("get_playbook", "Get a playbook definition: nodes, edges, budget and approval threshold.",
                 GetPlaybookArgs, lambda u, a: self.svc.get(_tenant(u), a.playbook_id, a.version).model_dump(),
                 title="Get playbook"),
            Tool("list_runs", "List playbook runs, newest first; with updated_after, changes oldest first.",
                 ListRunsArgs, lambda u, a: {"runs": self.svc.list_runs(_tenant(u), a.playbook_id, a.status, a.limit,
                                                                         a.updated_after)}, title="List runs"),
            Tool("get_run", "Get one run with its status, pending step, outputs and step log.", GetRunArgs,
                 lambda u, a: self.svc.get_run(_tenant(u), a.run_id), title="Get run"),
            Tool("get_playbook_performance", "Playbooks ranked by measured value per euro spent on real runs "
                 "(spend, value created, approval and failure rates). Use it to pick the best playbook.",
                 PerformanceArgs, lambda u, a: {"playbooks": self.svc.performance(_tenant(u), a.playbook_id)},
                 title="Playbook performance"),
            Tool("list_templates", "Playbook templates with your results and, if this tenant shares, the pooled "
                 "anonymised benchmark across companies. Use it to suggest a proven playbook.", NoArgs,
                 lambda u, a: {"templates": self.svc.template_benchmark(_tenant(u))}, title="List templates"),
            Tool("draft_playbook", "Draft a playbook from a plain-language description. Returns the workflow JSON "
                 "and validation errors; nothing is saved. A human reviews and saves it in the editor.", DraftArgs,
                 lambda u, a: drafting.draft(a.description, self.svc.registry, self.llm),
                 write=True, title="Draft playbook"),
            Tool("record_outcome", "Record a measured result of a real run, e.g. loss_avoided_eur or "
                 "units_sold, so playbook performance reflects reality.", RecordOutcomeArgs,
                 lambda u, a: self.svc.record_outcome(_tenant(u), a.run_id, a.metric, a.value, u["username"],
                                                      sku=a.sku, note=a.note, source="mcp"),
                 write=True, title="Record outcome"),
            Tool("start_run", "Start a playbook run. Dry run by default (simulates actions, spends nothing); pass "
                 "dry_run=false for a real run, which may pause for human approval.", StartRunArgs,
                 lambda u, a: self.runner.start(_tenant(u), a.playbook_id, a.payload, u["username"],
                                                dry_run=a.dry_run),
                 write=True, destructive=True, title="Start run"),
        ]
        if hub is not None:
            self.tools += self._channel_tools()
        self.tools += [
            Tool("decide_step", "Approve or reject a run step that is waiting for approval.", DecideStepArgs,
                 lambda u, a: self.runner.approve(_tenant(u), a.run_id, a.node, a.approved, u, note=a.note),
                 write=True, destructive=True, approval=True, title="Approve or reject step"),
        ]

    def _channel_tools(self) -> List[Tool]:
        hub = self.hub

        def connections(u, a):
            return {"connections": [{k: c[k] for k in ("id", "type", "name", "enabled", "last_sync_at", "last_error")}
                                    for c in hub.store.list_connections(_tenant(u))]}

        def inventory(u, a):
            rows = hub.store.inventory(_tenant(u), a.connection_id, a.sku)
            return {"items": rows[: a.limit * 10]}

        def orders(u, a):
            return {"orders": hub.store.orders(_tenant(u), a.connection_id, a.limit)}

        return [
            Tool("list_channel_connections", "Connected marketplaces, shops and EDI suppliers with sync status.",
                 NoArgs, connections, title="List channel connections"),
            Tool("get_channel_inventory", "Stock per SKU and channel (optionally one SKU or connection).",
                 ChannelQueryArgs, inventory, title="Channel inventory"),
            Tool("get_channel_orders", "Recent orders from all channels (optionally one connection).",
                 ChannelQueryArgs, orders, title="Channel orders"),
            Tool("sync_channel", "Fetch the latest orders and stock from one connection now.", ConnectionArgs,
                 lambda u, a: hub.sync(_tenant(u), a.connection_id), title="Sync channel"),
            Tool("set_channel_stock", "Set the available stock of a SKU on one channel.", ChannelStockArgs,
                 lambda u, a: hub.set_inventory(_tenant(u), a.connection_id, a.sku, a.quantity),
                 write=True, destructive=True, title="Set channel stock"),
            Tool("set_channel_price", "Set the price of a SKU on one channel.", ChannelPriceArgs,
                 lambda u, a: hub.set_price(_tenant(u), a.connection_id, a.sku, a.price, a.currency),
                 write=True, destructive=True, title="Set channel price"),
            Tool("send_purchase_order", "Send an EDI purchase order to a supplier connection (commits money).",
                 PurchaseOrderArgs,
                 lambda u, a: hub.send_purchase_order(_tenant(u), a.connection_id,
                                                      {"po_number": a.po_number, "lines": a.lines}),
                 write=True, destructive=True, approval=True, title="Send purchase order"),
        ]

    # ------------------------------------------------------------------ protocol
    def visible_tools(self, user: Dict[str, str]) -> List[Tool]:
        can_write = user.get("role") in WRITE_ROLES
        return [t for t in self.tools if (not t.write or can_write) and (not t.approval or self.allow_approvals)]

    def handle(self, message: Any, user: Dict[str, str]) -> Optional[Dict[str, Any]]:
        """Handle one JSON-RPC message. Returns None for notifications (HTTP 202, no body)."""
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            return _error(message.get("id") if isinstance(message, dict) else None, -32600, "Invalid Request")
        method, req_id = message["method"], message.get("id")
        if "id" not in message:  # notification (initialized, cancelled, ...): acknowledge silently
            return None
        params = message.get("params") or {}
        try:
            if method == "initialize":
                result = self._initialize(params)
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": [t.spec() for t in self.visible_tools(user)]}
            elif method == "tools/call":
                result = self._call(params, user)
            else:
                raise RpcError(-32601, f"Method not found: {method}")
        except RpcError as exc:
            return _error(req_id, exc.code, exc.message)
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def _initialize(self, params: Dict[str, Any]) -> Dict[str, Any]:
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_VERSIONS else LATEST_VERSION
        return {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO, "instructions": INSTRUCTIONS}

    def _call(self, params: Dict[str, Any], user: Dict[str, str]) -> Dict[str, Any]:
        name = params.get("name")
        tool = next((t for t in self.visible_tools(user) if t.name == name), None)
        if tool is None:
            raise RpcError(-32602, f"Unknown tool: {name}")
        try:
            args = tool.args.model_validate(params.get("arguments") or {})
        except ValidationError as exc:
            detail = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'arguments'}: {e['msg']}" for e in exc.errors())
            raise RpcError(-32602, f"Invalid arguments for {name}: {detail}")
        try:
            data = tool.fn(user, args)
        except KeyError as exc:
            return _tool_error(f"Not found: {exc.args[0] if exc.args else exc}")
        except (PermissionError, WorkflowError, TemporalUnavailable, ValueError, RuntimeError) as exc:
            return _tool_error(str(exc))  # RuntimeError covers channel errors (rejected credentials, rate limits)
        text = json.dumps(data, default=str)
        return {"content": [{"type": "text", "text": text}], "structuredContent": json.loads(text), "isError": False}


def _tenant(user: Dict[str, str]) -> str:
    return user.get("tenant") or "default"


def _tool_error(message: str) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _error(req_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
