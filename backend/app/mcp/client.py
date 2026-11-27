"""Minimal MCP client over streamable HTTP (JSON-RPC 2.0).

Implements the MCP handshake (initialize -> notifications/initialized -> tools/list
or tools/call) with a bounded timeout and result-size cap. The official `mcp` SDK
is optional; this client works against any standards-compliant MCP server that
accepts POST JSON-RPC on a single URL.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import httpx

PROTOCOL_VERSION = "2025-03-26"
MAX_RESULT_BYTES = 256 * 1024


class McpError(Exception):
    """code: timeout | auth | not_found | server_error"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _headers(config: Dict[str, Any]) -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        **{k: str(v) for k, v in (config.get("headers") or {}).items()},
    }


def _rpc(config: Dict[str, Any], method: str, params: Optional[Dict[str, Any]] = None,
         request_id: Optional[int] = None) -> Dict[str, Any]:
    url = config["url"]
    timeout = float(config.get("timeout_s", 10))
    payload: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        payload["params"] = params
    if request_id is not None:
        payload["id"] = request_id
    try:
        resp = httpx.post(url, json=payload, headers=_headers(config), timeout=timeout)
    except httpx.TimeoutException as exc:
        raise McpError("timeout", f"MCP server timed out after {timeout:.0f}s") from exc
    except httpx.HTTPError as exc:
        raise McpError("server_error", f"MCP connection failed: {exc}") from exc
    if resp.status_code in (401, 403):
        raise McpError("auth", f"MCP server rejected the credentials (HTTP {resp.status_code})")
    if resp.status_code >= 400:
        raise McpError("server_error", f"MCP server returned HTTP {resp.status_code}")
    if request_id is None and (resp.status_code == 202 or not resp.content):
        return {}  # notification acknowledged (spec: 202 Accepted, no body)
    if len(resp.content) > MAX_RESULT_BYTES:
        raise McpError("server_error", f"MCP response exceeds the {MAX_RESULT_BYTES // 1024} KB cap")
    try:
        body = resp.json()
    except ValueError as exc:
        raise McpError("server_error", "MCP server returned a non-JSON response") from exc
    if isinstance(body, dict) and body.get("error"):
        raise McpError("server_error", str(body["error"].get("message", "MCP error")))
    return body.get("result", {}) if isinstance(body, dict) else {}


def _connect(config: Dict[str, Any]) -> None:
    """MCP handshake: initialize + initialized notification."""
    _rpc(config, "initialize", {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "sop-copilot", "version": "1.0"},
    }, request_id=1)
    _rpc(config, "notifications/initialized")


def list_tools(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Discover the tools a server exposes (after the handshake)."""
    _connect(config)
    result = _rpc(config, "tools/list", {}, request_id=2)
    return result.get("tools", [])


def call_tool(config: Dict[str, Any], name: str, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Invoke one tool; returns the MCP result (content + isError)."""
    _connect(config)
    result = _rpc(config, "tools/call", {"name": name, "arguments": args or {}}, request_id=3)
    if result.get("isError"):
        raise McpError("server_error", "MCP tool reported an error")
    return result


def tool_text(result: Dict[str, Any]) -> str:
    """Flatten an MCP tool result into a compact string for evidence/prompts."""
    parts = []
    for item in result.get("content", []):
        if isinstance(item, dict):
            if item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            else:
                parts.append(json.dumps(item, default=str))
        else:
            parts.append(str(item))
    return "\n".join(p for p in parts if p) or json.dumps(result, default=str)
