"""Demo MCP servers (ERP, CRM, Jira) over streamable HTTP.

Each server implements the MCP JSON-RPC protocol on a single POST endpoint and answers
from the simulated feeds in `demo.data`. Run one with:

    python -m app.mcp.demo --kind erp --port 9101

Real deployments replace these with the org's MCP servers next to each system; the
connection config in the Co-Pilot page is identical.
"""
from __future__ import annotations

import argparse
import logging
from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.mcp.demo import data

log = logging.getLogger("sop.mcp.demo")

SERVER_INFO = {"erp": {"name": "demo-erp", "version": "1.0.0"},
               "crm": {"name": "demo-crm", "version": "1.0.0"},
               "jira": {"name": "demo-jira", "version": "1.0.0"}}

# in-memory store for write tools (create_issue / push_forecast)
_WRITES: Dict[str, List[Dict[str, Any]]] = {"jira": [], "erp": []}


def _schema(props: Dict[str, Dict[str, Any]], required: Optional[List[str]] = None) -> Dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or []}


def _int_arg(args: Dict[str, Any], key: str) -> Optional[int]:
    v = args.get(key)
    return int(v) if v not in (None, "") else None


def _tools(kind: str) -> List[Dict[str, Any]]:
    if kind == "erp":
        return [
            {"name": "get_open_purchase_orders", "description": "Open purchase order lines with remaining quantities.",
             "inputSchema": _schema({"item_id": {"type": "integer"}, "limit": {"type": "integer"}})},
            {"name": "get_stock_levels", "description": "Latest on-hand, open PO, OOS and vendor confirmation per item.",
             "inputSchema": _schema({"item_id": {"type": "integer"}, "limit": {"type": "integer"}})},
            {"name": "get_material_master", "description": "Item master: product, brand, ABC, prices, lifecycle.",
             "inputSchema": _schema({"item_id": {"type": "integer"}, "limit": {"type": "integer"}})},
            {"name": "get_supplier_lead_times", "description": "Supplier lead times per item.",
             "inputSchema": _schema({"limit": {"type": "integer"}})},
            {"name": "push_forecast", "description": "Push a forecast quantity for an item/month to the ERP (write).",
             "inputSchema": _schema({"item_id": {"type": "integer"}, "month": {"type": "string"},
                                     "units": {"type": "integer"}}, required=["item_id", "month", "units"])},
        ]
    if kind == "crm":
        return [
            {"name": "get_account", "description": "Account master (brand/product family) with item and channel mix.",
             "inputSchema": _schema({"account_id": {"type": "string"}, "limit": {"type": "integer"}})},
            {"name": "get_opportunities", "description": "Open order value per customer/account.",
             "inputSchema": _schema({"account_id": {"type": "string"}, "limit": {"type": "integer"}})},
            {"name": "get_cases", "description": "Open support cases from delivery exceptions.",
             "inputSchema": _schema({"status": {"type": "string"}, "limit": {"type": "integer"}})},
            {"name": "get_forecast_commit", "description": "Committed forecast per account (placeholder).",
             "inputSchema": _schema({"account_id": {"type": "string"}, "limit": {"type": "integer"}})},
        ]
    return [
        {"name": "search_issues", "description": "Search Jira issues by query/status.",
         "inputSchema": _schema({"query": {"type": "string"}, "status": {"type": "string"}, "limit": {"type": "integer"}})},
        {"name": "get_issue", "description": "Fetch one Jira issue by key.",
         "inputSchema": _schema({"issue_key": {"type": "string"}}, required=["issue_key"])},
        {"name": "create_issue", "description": "Create a Jira issue (write).",
         "inputSchema": _schema({"summary": {"type": "string"}, "description": {"type": "string"}},
                                required=["summary"])},
    ]


def _handlers(kind: str) -> Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]]:
    def _open_pos(args):
        return {"content": [{"type": "text", "text": data.text(data.open_pos(_int_arg(args, "item_id"), int(args.get("limit", 20)) or 20))}]}

    def _stock(args):
        return {"content": [{"type": "text", "text": data.text(data.stock_levels(_int_arg(args, "item_id"), int(args.get("limit", 20)) or 20))}]}

    def _material(args):
        return {"content": [{"type": "text", "text": data.text(data.material_master(_int_arg(args, "item_id"), int(args.get("limit", 20)) or 20))}]}

    def _lead_times(args):
        return {"content": [{"type": "text", "text": data.text(data.supplier_lead_times(int(args.get("limit", 20)) or 20))}]}

    def _push_forecast(args):
        _WRITES["erp"].append({"item_id": _int_arg(args, "item_id"), "month": args.get("month"),
                               "units": int(args.get("units", 0)), "status": "accepted"})
        return {"content": [{"type": "text", "text": data.text({"status": "accepted",
                                                                "item_id": _int_arg(args, "item_id"),
                                                                "month": args.get("month"),
                                                                "units": int(args.get("units", 0))})}]}

    def _account(args):
        rows = data.accounts(int(args.get("limit", 20)) or 20)
        if args.get("account_id"):
            rows = [r for r in rows if r["account_id"] == args["account_id"]]
        return {"content": [{"type": "text", "text": data.text(rows)}]}

    def _opportunities(args):
        rows = data.opportunities(args.get("account_id"), int(args.get("limit", 20)) or 20)
        return {"content": [{"type": "text", "text": data.text(rows)}]}

    def _cases(args):
        return {"content": [{"type": "text", "text": data.text(data.cases(args.get("status"), int(args.get("limit", 20)) or 20))}]}

    def _commit(args):
        rows = data.opportunities(args.get("account_id"), int(args.get("limit", 20)) or 20)
        for r in rows:
            r["committed_units"] = 0  # placeholder: consensus commit is not exposed to the demo
        return {"content": [{"type": "text", "text": data.text(rows)}]}

    def _search(args):
        return {"content": [{"type": "text", "text": data.text(data.issues(args.get("query"), args.get("status"),
                                                                           int(args.get("limit", 20)) or 20))}]}

    def _get_issue(args):
        key = args.get("issue_key", "")
        rows = data.issues(limit=200)
        issue = next((i for i in rows if i["key"] == key), None)
        if issue is None:
            return {"content": [{"type": "text", "text": data.text({"error": f"issue {key} not found"})}], "isError": True}
        return {"content": [{"type": "text", "text": data.text(issue)}]}

    def _create_issue(args):
        key = f"OPS-{2000 + len(_WRITES['jira']) + 1}"
        issue = {"key": key, "summary": args.get("summary", ""), "description": args.get("description", ""),
                 "status": "OPEN", "priority": "MEDIUM"}
        _WRITES["jira"].append(issue)
        return {"content": [{"type": "text", "text": data.text(issue)}]}

    if kind == "erp":
        return {"get_open_purchase_orders": _open_pos, "get_stock_levels": _stock,
                "get_material_master": _material, "get_supplier_lead_times": _lead_times,
                "push_forecast": _push_forecast}
    if kind == "crm":
        return {"get_account": _account, "get_opportunities": _opportunities,
                "get_cases": _cases, "get_forecast_commit": _commit}
    return {"search_issues": _search, "get_issue": _get_issue, "create_issue": _create_issue}


def create_demo_app(kind: str) -> FastAPI:
    """FastAPI app exposing the MCP JSON-RPC endpoint for one demo server kind."""
    app = FastAPI(title=f"Demo MCP server ({kind})")
    tools = _tools(kind)
    handlers = _handlers(kind)
    info = SERVER_INFO.get(kind, SERVER_INFO["erp"])

    @app.post("/")
    async def rpc(request: Request):
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
        method = body.get("method")
        req_id = body.get("id")
        params = body.get("params") or {}
        if method == "initialize":
            return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {
                "protocolVersion": params.get("protocolVersion", "2025-03-26"),
                "capabilities": {"tools": {}}, "serverInfo": info}})
        if method == "notifications/initialized":
            return JSONResponse({})  # notification: no response body expected
        if method == "ping":
            return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {}})
        if method == "tools/list":
            return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}})
        if method == "tools/call":
            name = params.get("name")
            handler = handlers.get(name)
            if handler is None:
                return JSONResponse({"jsonrpc": "2.0", "id": req_id,
                                     "error": {"code": -32602, "message": f"Unknown tool {name}"}})
            try:
                result = handler(params.get("arguments") or {})
            except Exception as exc:  # pragma: no cover - defensive
                log.warning("tool %s failed: %s", name, exc)
                return JSONResponse({"jsonrpc": "2.0", "id": req_id,
                                     "error": {"code": -32603, "message": str(exc)}})
            return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": result})
        return JSONResponse({"jsonrpc": "2.0", "id": req_id,
                             "error": {"code": -32601, "message": f"Method not found: {method}"}})

    return app


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run a demo MCP server (erp|crm|jira).")
    parser.add_argument("--kind", choices=("erp", "crm", "jira"), default="erp")
    parser.add_argument("--port", type=int, default=9101)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args(argv)
    import uvicorn

    log.info("demo MCP server %s on http://%s:%d", args.kind, args.host, args.port)
    uvicorn.run(create_demo_app(args.kind), host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
