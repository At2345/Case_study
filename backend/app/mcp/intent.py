"""MCP intent detection for the Co-Pilot assistant.

Maps a planner's question to an external-system tool (ERP/CRM/Jira) and invokes it
through the configured MCP server, returning evidence for the assistant prompt.
Failures never raise: they return a `failed` marker so the assistant answers
deterministically instead of fabricating external figures.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

from app.mcp.client import McpError, call_tool, tool_text
from app.mcp.registry import allowlisted, is_write_tool

log = logging.getLogger("sop.mcp.intent")

# kind -> (strong substrings, weak word-boundary words, ordered tool mappings)
KINDS: Dict[str, Dict[str, Any]] = {
    "erp": {
        "strong": ("erp", "sap", "d365", "dynamics", "open po", "open purchase order", "purchase order",
                   "stock level", "material master", "lead time", "po status"),
        "weak": ("stock", "inventory", "supplier"),
        "tools": ((("open po", "open purchase order", "purchase order", "po status"), "get_open_purchase_orders"),
                  (("stock", "inventory"), "get_stock_levels"),
                  (("material",), "get_material_master"),
                  (("lead time", "lead-time"), "get_supplier_lead_times")),
    },
    "crm": {
        "strong": ("crm", "salesforce", "customer account"),
        "weak": ("account", "opportunit", "case"),
        "tools": ((("opportunit",), "get_opportunities"),
                  (("account",), "get_account"),
                  (("case",), "get_cases")),
    },
    "jira": {
        "strong": ("jira", "atlassian", "ticket"),
        "weak": ("issue",),
        "tools": ((("ticket", "issue"), "search_issues"),),
    },
}


def _mcp_intent(question: str) -> Optional[Dict[str, str]]:
    """Return {"kind", "tool"} when the question targets an external system, else None."""
    q = question.lower()
    for kind, spec in KINDS.items():
        strong = any(w in q for w in spec["strong"])
        weak = any(re.search(rf"\b{re.escape(w)}\b", q) for w in spec["weak"])
        if not (strong or weak):
            continue
        for keys, tool in spec["tools"]:
            if any(k in q for k in keys):
                return {"kind": kind, "tool": tool}
        return {"kind": kind, "tool": spec["tools"][0][1]}
    return None


def mcp_evidence(question: str, mcp_store) -> Optional[Dict[str, Any]]:
    """Call the configured MCP tool for an external-system question.

    Returns {"evidence": str, "source": dict} on success, {"failed": {...}} when the
    server is unreachable, or None when no server applies. Never raises.
    """
    if mcp_store is None:
        return None
    server: Dict[str, Any] = {}
    tool = ""
    try:
        intent = _mcp_intent(question)
        if not intent:
            return None
        servers = [s for s in mcp_store.list()
                   if s.get("kind") == intent["kind"] and s.get("enabled", True)]
        if not servers:
            return None
        tool = intent["tool"]
        if is_write_tool(intent["kind"], tool):
            return None  # the assistant is read-only; write tools are admin-gated via the API
        server = next((s for s in servers if allowlisted(s, tool)), None)
        if server is None:
            return None
        result = call_tool(server, tool, {})
    except McpError as exc:
        log.warning("mcp call failed for %s/%s: %s", server["id"], tool, exc)
        return {"failed": {"kind": intent["kind"], "server_id": server["id"]},
                "source": {"kind": "mcp", "title": f"mcp:{server['id']}",
                           "snippet": "connection unavailable"}}
    except Exception as exc:  # never let the assistant 500 on an MCP issue
        log.warning("mcp evidence failed: %s", exc)
        return None
    text = tool_text(result)
    return {
        "evidence": f"[mcp:{server['id']}] {tool}: {text}",
        "source": {"kind": "mcp", "title": f"mcp:{server['id']}",
                   "snippet": f"{tool} via {server['name']}"},
    }
