"""Playbook template library.

Templates are ready-made playbooks with typed parameters. Installing one renders a normal workflow
(editable afterwards) that remembers its template id and version, so results from every tenant's copy
can be pooled into a template benchmark (see WorkflowService.template_benchmark).

Placeholders: a string value that is exactly "{{name}}" becomes the typed parameter value; "{{name}}"
inside a longer string is replaced textually.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional

PLACEHOLDER = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")
TYPES = {"string": str, "number": float, "integer": int, "url": str}


def _param(name: str, type_: str, default: Any, description: str, required: bool = False) -> Dict[str, Any]:
    return {"name": name, "type": type_, "default": default, "description": description, "required": required}


_PROPOSE = {"id": "propose", "type": "agent.rescue_proposal", "params": {
    "sku": "$trigger.sku", "marketplace": "$trigger.marketplace", "units_on_hand": "$trigger.units_on_hand",
    "sell_through_gap": "$trigger.gap", "unit_landed_cost_eur": "$trigger.unit_cost",
    "days_to_trigger": "$trigger.days_to_trigger"}}
_CUT = {"id": "cut_price", "type": "action.change_price", "params": {
    "sku": "$nodes.propose.sku", "marketplace": "$nodes.propose.marketplace",
    "pct_change": "$nodes.propose.pct_change", "estimated_cost_eur": "$nodes.propose.estimated_cost_eur"}}
_TRANSFER = {"id": "transfer", "type": "action.transfer_stock", "params": {
    "sku": "$nodes.propose.sku", "from_marketplace": "$nodes.propose.marketplace",
    "to_marketplace": "{{transfer_to}}", "units": "$nodes.propose.units_at_risk",
    "estimated_cost_eur": "$nodes.propose.estimated_cost_eur"}}
TRIGGER_PAYLOAD = {"sku": "MAT-BLUE", "marketplace": "DE", "units_on_hand": 1000, "gap": -0.2, "unit_cost": 8.0,
                   "days_to_trigger": 90}


def _edges(*pairs) -> List[Dict[str, str]]:
    return [{"source": p[0], "target": p[1], "port": p[2] if len(p) > 2 else "out"} for p in pairs]


TEMPLATES: Dict[str, Dict[str, Any]] = {
    "rescue-basic": {
        "id": "rescue-basic", "version": 1, "category": "overstock rescue",
        "name": "Rescue: discount or transfer",
        "description": "When a batch sells slower than forecast: small gaps get a price cut, deep gaps a stock "
                       "transfer to another marketplace (needs approval).",
        "params": [_param("budget_eur", "number", 2000, "Max spend per run"),
                   _param("transfer_to", "string", "FR", "Marketplace that receives transferred stock")],
        "spec": {
            "name": "Rescue: discount or transfer", "trigger": "start", "budget_eur": "{{budget_eur}}",
            "nodes": [{"id": "start", "type": "trigger.sell_through_gap"}, _PROPOSE,
                      {"id": "is_price", "type": "logic.if",
                       "params": {"left": "$nodes.propose.action", "op": "==", "right": "change_price"}},
                      _CUT, _TRANSFER,
                      {"id": "tell", "type": "action.notify", "params": {"message": "Rescue action done for {{transfer_to}} plan."}}],
            "edges": _edges(("start", "propose"), ("propose", "is_price"), ("is_price", "cut_price", "true"),
                            ("is_price", "transfer", "false"), ("cut_price", "tell"), ("transfer", "tell")),
        },
    },
    "rescue-measured": {
        "id": "rescue-measured", "version": 1, "category": "overstock rescue",
        "name": "Rescue with measured outcome",
        "description": "Price cut, wait, then fetch current stock from n8n and record the loss avoided "
                       "automatically. The template whose results feed the benchmark.",
        "params": [_param("budget_eur", "number", 2000, "Max spend per run"),
                   _param("stock_webhook_url", "url", None, "n8n Webhook that returns {units_on_hand, units_sold}",
                          required=True),
                   _param("wait_days", "number", 60, "Days between the action and the measurement"),
                   _param("recovery_rate", "number", 0.25, "Share of cost recovered on unsold stock anyway")],
        "spec": {
            "name": "Rescue with measured outcome", "trigger": "start", "budget_eur": "{{budget_eur}}",
            "nodes": [{"id": "start", "type": "trigger.sell_through_gap"}, _PROPOSE, _CUT,
                      {"id": "wait", "type": "logic.wait", "params": {"days": "{{wait_days}}"}},
                      {"id": "fetch", "type": "action.n8n_webhook", "params": {"url": "{{stock_webhook_url}}"}},
                      {"id": "measure", "type": "agent.measure_outcome", "params": {
                          "sku": "$nodes.propose.sku", "baseline_units_at_risk": "$nodes.propose.units_at_risk",
                          "units_unsold": "$nodes.fetch.response.units_on_hand",
                          "unit_landed_cost_eur": "$trigger.unit_cost", "recovery_rate": "{{recovery_rate}}",
                          "units_sold": "$nodes.fetch.response.units_sold"}}],
            "edges": _edges(("start", "propose"), ("propose", "cut_price"), ("cut_price", "wait"),
                            ("wait", "fetch"), ("fetch", "measure")),
        },
    },
    "rescue-escalation": {
        "id": "rescue-escalation", "version": 1, "category": "overstock rescue",
        "name": "Rescue with manager sign-off",
        "description": "Every rescue needs a named approver role before stock moves; the planner is told "
                       "either way.",
        "params": [_param("budget_eur", "number", 5000, "Max spend per run"),
                   _param("approver_role", "string", "admin", "Role that signs off (planner or admin)"),
                   _param("transfer_to", "string", "FR", "Marketplace that receives transferred stock")],
        "spec": {
            "name": "Rescue with manager sign-off", "trigger": "start", "budget_eur": "{{budget_eur}}",
            "nodes": [{"id": "start", "type": "trigger.sell_through_gap"}, _PROPOSE,
                      {"id": "sign_off", "type": "control.approval", "params": {
                          "approver_role": "{{approver_role}}", "message": "Approve the proposed rescue?"}},
                      _TRANSFER,
                      {"id": "told_no", "type": "action.notify", "params": {"message": "Rescue was not approved."}}],
            "edges": _edges(("start", "propose"), ("propose", "sign_off"), ("sign_off", "transfer", "approved"),
                            ("sign_off", "told_no", "rejected")),
        },
    },
}


CHANNEL_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "channel-stock-sync": {
        "id": "channel-stock-sync", "version": 1, "category": "channels",
        "name": "Stock sync across all channels",
        "description": "When stock changes on any channel, write the same available quantity to every other "
                       "channel, minus a safety buffer, so nothing is sold twice.",
        "params": [_param("buffer", "integer", 2, "Units held back on every channel")],
        "example_payload": {"event": "inventory_changed", "connection_id": "<connection id>", "channel_type": "shopify", "sku": "MAT-BLUE",
                            "quantity": 42, "previous_quantity": 45},
        "spec": {
            "name": "Stock sync across all channels", "trigger": "start", "budget_eur": 0,
            "nodes": [{"id": "start", "type": "trigger.channel_event", "params": {"event": "inventory_changed"}},
                      {"id": "sync", "type": "action.channel_broadcast_stock", "params": {
                          "sku": "$trigger.sku", "quantity": "$trigger.quantity",
                          "source_connection": "$trigger.connection_id", "buffer": "{{buffer}}"}}],
            "edges": _edges(("start", "sync")),
        },
    },
    "low-stock-reorder": {
        "id": "low-stock-reorder", "version": 1, "category": "channels",
        "name": "Low stock: reorder from supplier (EDI)",
        "description": "When a channel reports low stock, send a purchase order to your EDI supplier after a "
                       "planner approves it, then tell the team.",
        "params": [_param("supplier_connection", "string", None, "EDI supplier connection id", required=True),
                   _param("reorder_quantity", "number", 500, "Units to order"),
                   _param("po_prefix", "string", "PO-", "Purchase order number prefix")],
        "example_payload": {"event": "stock_low", "connection_id": "<connection id>", "channel_type": "shopify", "sku": "MAT-BLUE",
                            "quantity": 4, "threshold": 5},
        "spec": {
            "name": "Low stock: reorder from supplier", "trigger": "start", "budget_eur": 0,
            "nodes": [{"id": "start", "type": "trigger.channel_event", "params": {"event": "stock_low"}},
                      {"id": "order", "type": "action.edi_purchase_order", "params": {
                          "connection_id": "{{supplier_connection}}", "po_prefix": "{{po_prefix}}",
                          "lines": [{"sku": "$trigger.sku", "quantity": "{{reorder_quantity}}"}]}},
                      {"id": "tell", "type": "action.notify", "params": {
                          "message": "Purchase order sent for a low-stock SKU."}}],
            "edges": _edges(("start", "order"), ("order", "tell")),
        },
    },
    "order-to-fulfilment": {
        "id": "order-to-fulfilment", "version": 1, "category": "channels",
        "name": "New order to your warehouse (3PL) via n8n",
        "description": "Every new order from any channel is posted to an n8n webhook that books it with your "
                       "warehouse or 3PL.",
        "params": [_param("fulfilment_webhook_url", "url", None, "n8n Webhook that books the order", required=True)],
        "example_payload": {"event": "order_created", "connection_id": "<connection id>", "channel_type": "shopify",
                            "order": {"external_id": "1001", "lines": [{"sku": "MAT-BLUE", "quantity": 1}]}},
        "spec": {
            "name": "New order to warehouse", "trigger": "start", "budget_eur": 0,
            "nodes": [{"id": "start", "type": "trigger.channel_event", "params": {"event": "order_created"}},
                      {"id": "book", "type": "action.n8n_webhook", "params": {
                          "url": "{{fulfilment_webhook_url}}", "body": {"order": "$trigger.order",
                                                                        "channel": "$trigger.channel_type"}}}],
            "edges": _edges(("start", "book")),
        },
    },
}
TEMPLATES.update(CHANNEL_TEMPLATES)


def public(template: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: template[k] for k in ("id", "version", "name", "description", "category", "params")}
    out["example_payload"] = dict(template.get("example_payload") or TRIGGER_PAYLOAD)
    return out


def get(template_id: str) -> Dict[str, Any]:
    if template_id not in TEMPLATES:
        raise KeyError(f"template {template_id} not found")
    return TEMPLATES[template_id]


def render(template_id: str, workflow_id: str, values: Optional[Dict[str, Any]] = None,
           name: Optional[str] = None) -> Dict[str, Any]:
    """Return a workflow spec (not yet validated) for the template with parameter values applied."""
    template = get(template_id)
    values = dict(values or {})
    declared = {p["name"]: p for p in template["params"]}
    unknown = sorted(set(values) - set(declared))
    if unknown:
        raise ValueError(f"unknown template parameters: {unknown}")
    resolved: Dict[str, Any] = {}
    for pname, p in declared.items():
        raw = values.get(pname, p["default"])
        if raw is None or raw == "":
            if p["required"]:
                raise ValueError(f"parameter '{pname}' is required")
            continue
        try:
            resolved[pname] = TYPES[p["type"]](raw)
        except (TypeError, ValueError):
            raise ValueError(f"parameter '{pname}' must be a {p['type']}")
        if p["type"] == "url" and not str(resolved[pname]).startswith(("http://", "https://")):
            raise ValueError(f"parameter '{pname}' must be an http(s) URL")
    spec = _substitute(copy.deepcopy(template["spec"]), resolved)
    spec.update(id=workflow_id, name=name or spec["name"],
                template={"id": template["id"], "version": template["version"]})
    return spec


def _substitute(value: Any, params: Dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {k: _substitute(v, params) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, params) for v in value]
    if not isinstance(value, str):
        return value
    whole = PLACEHOLDER.fullmatch(value)
    if whole:
        if whole.group(1) not in params:
            raise ValueError(f"parameter '{whole.group(1)}' has no value")
        return params[whole.group(1)]
    return PLACEHOLDER.sub(lambda m: str(params[m.group(1)]) if m.group(1) in params else m.group(0), value)
