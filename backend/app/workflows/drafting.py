"""Text-to-workflow: draft a playbook from a plain-language description.

Two methods:
- "llm": the model gets the live node catalogue and DSL rules, must answer with JSON only, and its draft
  is validated exactly like a saved workflow. Validation errors are sent back for one repair attempt.
- "template": no LLM configured (or it failed): pick the closest library template from keywords and
  extract parameters (budget, marketplaces, wait days, approver, webhook URL) from the text.

A draft is never saved or run here: the editor shows it with any remaining errors for a human to review.
Locked (platform-only) node types are rejected by the same registry validation as everywhere else.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError

from app.workflows import templates
from app.workflows.dsl import Workflow
from app.workflows.registry import Registry

MARKETS = ("DE", "FR", "IT", "ES", "UK", "NL", "PL", "SE", "BE", "AT")
MAX_LLM_ATTEMPTS = 2
PLACEHOLDER_URL = "https://n8n.example.com/webhook/stock"

SYSTEM = """You design overstock-rescue playbooks as JSON for a workflow engine.
Answer with ONE JSON object only, no prose, no code fences. Schema:
{"id": "<lowercase-slug>", "name": "<short name>", "trigger": "<id of the trigger node>",
 "budget_eur": <number>, "approval_threshold_eur": <number or null>,
 "nodes": [{"id": "<snake_case>", "type": "<node type>", "params": {...}, "label": "<optional>"}],
 "edges": [{"source": "<node id>", "target": "<node id>", "port": "<output port of source, default out>"}]}
Rules:
- Exactly one trigger node; no cycles; only node types and ports from the catalogue; params must match each
  node's params schema.
- Pass values between steps with references: "$trigger.<field>" (run payload) or "$nodes.<node id>.<field>".
  The trigger payload has: sku, marketplace, units_on_hand, gap, unit_cost, days_to_trigger.
- agent.rescue_proposal outputs: sku, marketplace, units_at_risk, exposure_eur, action ("change_price" |
  "transfer_stock" | null), pct_change, estimated_cost_eur.
- logic.if has ports "true"/"false"; control.approval has ports "approved"/"rejected".
- Use the smallest playbook that does what is asked. Prefer reversible actions; costly or irreversible
  actions should pass through control.approval or rely on the budget and approval threshold.
Node catalogue:
"""


class LlmUnavailable(RuntimeError):
    pass


def catalogue_text(registry: Registry) -> str:
    lines = []
    for spec in registry.catalogue():
        schema = spec["params_schema"]
        props = schema.get("properties", {})
        required = set(schema.get("required", []))
        params = ", ".join(f"{k}{'*' if k in required else ''}:{_type(v)}" for k, v in props.items()) or "none"
        policy = spec["policy"]
        flags = [f for f, on in (("needs approval", policy["needs_approval"]),
                                 ("irreversible", not policy["reversible"])) if on]
        lines.append(f"- {spec['name']} [{spec['category']}] ports={spec['ports']} params({params})"
                     f"{' ' + '; '.join(flags) if flags else ''}: {spec['description']}")
    return "\n".join(lines)


def _type(prop: Dict[str, Any]) -> str:
    if "enum" in prop:
        return "|".join(map(str, prop["enum"]))
    if "anyOf" in prop:
        return "/".join(p.get("type", "any") for p in prop["anyOf"])
    return prop.get("type", "any")


def slug(text: str) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())[:6]
    s = "-".join(words)[:48].strip("-")
    return s if s and s[0].isalnum() else f"draft-{s}".strip("-") or "draft"


def validate(spec: Any, registry: Registry) -> List[str]:
    if not isinstance(spec, dict):
        return ["draft is not a JSON object"]
    try:
        wf = Workflow.model_validate(spec)
    except ValidationError as exc:
        return [f"{'.'.join(map(str, e['loc'])) or 'workflow'}: {e['msg']}" for e in exc.errors()]
    return registry.validate(wf)


def parse_json(text: str) -> Any:
    """Extract the JSON object from a model answer (tolerates code fences and surrounding prose)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the model answer")
    return json.loads(text[start:end + 1])


# ---------------------------------------------------------------------------- LLM method
def draft_with_llm(description: str, registry: Registry, llm: Any) -> Tuple[Dict[str, Any], List[str], List[str]]:
    system = SYSTEM + catalogue_text(registry)
    prompt = f"Playbook request:\n{description}"
    notes: List[str] = []
    spec: Dict[str, Any] = {}
    errors: List[str] = ["no draft produced"]
    for attempt in range(1, MAX_LLM_ATTEMPTS + 1):
        try:
            answer = llm.generate(system, prompt)
        except Exception as exc:
            raise LlmUnavailable(str(exc)) from exc
        try:
            parsed = parse_json(answer)
        except ValueError as exc:
            errors = [f"model answer was not valid JSON: {exc}"]
            prompt = (f"Playbook request:\n{description}\n\nYour previous answer was not valid JSON ({exc}). "
                      "Answer with the JSON object only.")
            continue
        spec = parsed if isinstance(parsed, dict) else {}
        spec.pop("template", None)  # drafts are never linked to a library template by the model
        spec["id"] = slug(str(spec.get("id") or spec.get("name") or description))
        errors = validate(spec, registry)
        if not errors:
            break
        if attempt < MAX_LLM_ATTEMPTS:
            notes.append(f"Repaired after attempt {attempt}: {len(errors)} validation error(s).")
            prompt = (f"Playbook request:\n{description}\n\nYour draft:\n{json.dumps(spec)}\n\n"
                      "It failed validation:\n- " + "\n- ".join(errors[:20]) +
                      "\nReturn the corrected JSON object only.")
    return spec, errors, notes


# ---------------------------------------------------------------------------- template method
def draft_from_template(description: str) -> Tuple[Dict[str, Any], List[str]]:
    text = description.lower()
    if any(w in text for w in ("measure", "outcome", "loss avoided", "track result", "how well")):
        template_id = "rescue-measured"
    elif any(w in text for w in ("sign off", "sign-off", "signoff", "approv", "manager", "escalat")):
        template_id = "rescue-escalation"
    else:
        template_id = "rescue-basic"
    declared = {p["name"] for p in templates.get(template_id)["params"]}
    values: Dict[str, Any] = {}
    notes = [f"Matched template '{template_id}' from keywords (no LLM used)."]

    budget = re.search(r"(?:budget|max(?:imum)?|spend|up to)[^\d€]{0,20}€?\s*(\d[\d.,]*)\s*(k)?", text) or \
        re.search(r"€\s*(\d[\d.,]*)\s*(k)?", text)
    if budget:
        amount = float(budget.group(1).replace(",", ""))
        values["budget_eur"] = amount * 1000 if budget.group(2) else amount
    target = re.search(r"\b(?:to|into|towards)\s+(" + "|".join(MARKETS) + r")\b", description)
    if target and "transfer_to" in declared:
        values["transfer_to"] = target.group(1)
    wait = re.search(r"(\d{1,3})\s*days?", text)
    if wait and "wait_days" in declared:
        values["wait_days"] = float(wait.group(1))
    if "approver_role" in declared:
        values["approver_role"] = "planner" if "planner" in text else "admin"
    url = re.search(r"https?://\S+", description)
    if "stock_webhook_url" in declared:
        if url:
            values["stock_webhook_url"] = url.group(0).rstrip(".,;)")
        else:
            values["stock_webhook_url"] = PLACEHOLDER_URL
            notes.append("No stock webhook URL in the description: replace the placeholder in node 'fetch'.")
    spec = templates.render(template_id, slug(description), {k: v for k, v in values.items() if k in declared})
    spec.pop("template", None)
    spec["name"] = description.strip()[:80] or spec["name"]
    if values:
        notes.append("Parameters from your text: " + ", ".join(f"{k}={v}" for k, v in values.items()
                                                               if k in declared))
    return spec, notes


def draft(description: str, registry: Registry, llm: Any = None, use_llm: bool = True) -> Dict[str, Any]:
    """Return {"spec", "errors", "method", "notes"}. Never saves or runs anything."""
    notes: List[str] = []
    if llm is not None and use_llm:
        try:
            spec, errors, llm_notes = draft_with_llm(description, registry, llm)
            return {"spec": spec, "errors": errors, "method": "llm", "notes": llm_notes}
        except LlmUnavailable as exc:
            notes.append(f"LLM unavailable ({exc}); used the template method instead.")
    spec, template_notes = draft_from_template(description)
    return {"spec": spec, "errors": validate(spec, registry), "method": "template", "notes": notes + template_notes}
