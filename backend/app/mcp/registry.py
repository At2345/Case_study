"""MCP server registry: payload normalisation, secret masking and public views.

Secrets (auth headers) are stored server-side and never returned by the API;
`public_doc` replaces every header value with a masked form.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.mcp.templates import KINDS, KIND_TEMPLATES

_ID_RE = re.compile(r"^[a-z0-9_]+$")
_TRANSPORTS = ("streamable_http", "http_sse")


def mask_secret(value: str) -> str:
    """sk-abc123456789 -> sk-****6789 (or **** when short)."""
    if not value:
        return ""
    if len(value) <= 8:
        return "****"
    return f"{value[:2]}****{value[-4:]}"


def mask_headers(headers: Optional[Dict[str, str]]) -> Dict[str, str]:
    return {k: mask_secret(v) for k, v in (headers or {}).items()}


def public_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    """API view of a stored server: secrets masked, template metadata attached."""
    out = {k: v for k, v in doc.items() if k not in ("headers",)}
    out["headers"] = mask_headers(doc.get("headers"))
    out["kind_label"] = KIND_TEMPLATES.get(doc.get("kind", "generic"), {}).get("label", "MCP")
    return out


def normalize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate + normalise a create/update payload into a stored document."""
    server_id = str(payload.get("id", "")).strip()
    if not _ID_RE.match(server_id):
        raise ValueError("id must be lowercase letters, digits or underscores (e.g. erp_sap)")
    kind = str(payload.get("kind", "generic")).strip().lower()
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    transport = str(payload.get("transport", "streamable_http")).strip().lower()
    if transport not in _TRANSPORTS:
        raise ValueError(f"transport must be one of {', '.join(_TRANSPORTS)}")
    url = str(payload.get("url", "")).strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("url must start with http:// or https://")
    name = str(payload.get("name", "")).strip()
    if not name:
        raise ValueError("name is required")
    timeout = int(payload.get("timeout_s", 10))
    if not 1 <= timeout <= 120:
        raise ValueError("timeout_s must be between 1 and 120")
    tools = [str(t).strip() for t in payload.get("tools", []) if str(t).strip()]
    headers = {str(k).strip(): str(v) for k, v in (payload.get("headers") or {}).items() if str(k).strip()}
    return {
        "id": server_id,
        "name": name,
        "kind": kind,
        "transport": transport,
        "url": url,
        "headers": headers,
        "enabled": bool(payload.get("enabled", True)),
        "tools": tools,
        "timeout_s": timeout,
    }


def merge_update(existing: Dict[str, Any], fields: Dict[str, Any]) -> Dict[str, Any]:
    """Apply an update payload on top of a stored doc (headers merge, not replace)."""
    merged = dict(existing)
    for key in ("name", "kind", "transport", "url", "enabled", "tools", "timeout_s"):
        if key in fields:
            merged[key] = fields[key]
    if "headers" in fields and fields["headers"]:
        merged["headers"] = {**merged.get("headers", {}), **fields["headers"]}
    return merged


def allowlisted(config: Dict[str, Any], tool_name: str) -> bool:
    """Empty allowlist means every discovered tool is allowed."""
    tools = config.get("tools") or []
    return not tools or tool_name in tools


def is_write_tool(kind: str, tool_name: str) -> bool:
    """Write tools (push_forecast, create_issue) are admin-gated and never called by the assistant."""
    return tool_name in KIND_TEMPLATES.get(kind, {}).get("write_tools", [])
