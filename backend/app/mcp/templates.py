"""Per-kind MCP server templates (ERP, CRM, Jira, generic).

The templates drive the Co-Pilot configuration UI (sample URL, suggested tools) and
the default tool allowlist. They are reference schemas: the actual tools a server
exposes are discovered at test time via `tools/list`.
"""
from __future__ import annotations

from typing import Dict, List

KINDS = ("erp", "crm", "jira", "generic")

KIND_TEMPLATES: Dict[str, Dict[str, object]] = {
    "erp": {
        "label": "ERP",
        "sample_url": "https://erp.example.com/mcp",
        "tools": [
            "get_open_purchase_orders",
            "get_stock_levels",
            "get_material_master",
            "get_supplier_lead_times",
            "push_forecast",
        ],
        "write_tools": ["push_forecast"],
    },
    "crm": {
        "label": "CRM",
        "sample_url": "https://crm.example.com/mcp",
        "tools": [
            "get_account",
            "get_opportunities",
            "get_cases",
            "get_forecast_commit",
        ],
        "write_tools": [],
    },
    "jira": {
        "label": "Jira",
        "sample_url": "https://jira.example.com/mcp",
        "tools": [
            "search_issues",
            "get_issue",
            "create_issue",
        ],
        "write_tools": ["create_issue"],
    },
    "generic": {
        "label": "Generic MCP",
        "sample_url": "https://mcp.example.com",
        "tools": [],
        "write_tools": [],
    },
}


def template(kind: str) -> Dict[str, object]:
    return KIND_TEMPLATES.get(kind, KIND_TEMPLATES["generic"])
