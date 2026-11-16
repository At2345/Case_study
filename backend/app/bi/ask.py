"""Grounded natural-language metric queries (PR-5.1).

The model never sees the database schema and never returns SQL. It receives the
semantic document built from the metric registry and may only return a list of
metric ids, which the server validates against the registry before evaluating them.
A hallucinated metric id is reported; a hallucinated query is structurally impossible
because no code path turns model output into SQL.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from app.bi.finops import QueryTag
from app.bi.semantics import METRICS, evaluate

MAX_METRICS = 8

SQL_TOKENS = re.compile(
    r"\b(SELECT|INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|JOIN|UNION|PRAGMA|ATTACH|FROM)\b",
    re.IGNORECASE,
)
JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)

SYSTEM = (
    "You map a business question to governed metrics. "
    "You never write SQL and never mention database tables or columns. "
    'Reply with JSON only, shaped exactly like {"metrics": ["metric_id", ...]}. '
    f"Choose at most {MAX_METRICS} ids from the catalog. "
    'If nothing fits, reply {"metrics": []}.'
)


def semantic_document() -> str:
    """The grounding document: the registry rendered for the model."""
    lines = ["METRIC CATALOG (the only metrics that exist):"]
    for metric in METRICS.values():
        lines.append(
            f"- {metric.id} | {metric.label} | unit={metric.unit} | grain={metric.grain} | "
            f"owner={metric.owner} | {metric.description or metric.label}"
        )
    return "\n".join(lines)


def parse_selection(text: str) -> Tuple[List[str], List[str]]:
    """Return (accepted ids, rejected ids). SQL in the reply is a hard violation."""
    raw = (text or "").strip()
    if SQL_TOKENS.search(raw):
        raise ValueError("model returned SQL; only metric ids are accepted")
    match = JSON_BLOCK.search(raw)
    if not match:
        raise ValueError("model reply contained no JSON object")
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ValueError(f"model reply was not valid JSON: {exc}") from exc
    ids = payload.get("metrics")
    if not isinstance(ids, list):
        raise ValueError("model reply had no 'metrics' list")
    if len(ids) > MAX_METRICS:
        raise ValueError(f"model selected {len(ids)} metrics, the limit is {MAX_METRICS}")
    accepted = [i for i in ids if i in METRICS]
    rejected = [i for i in ids if i not in METRICS]
    return accepted, rejected


def resolve(question: str, client: Any) -> Tuple[List[str], List[str]]:
    """Ask the model which governed metrics answer the question."""
    reply = client.generate(SYSTEM, f"{semantic_document()}\n\nQUESTION: {question}")
    return parse_selection(reply)


def answer(question: str, path: Path, client: Any, *, tenant: str, tag: QueryTag) -> Dict[str, Any]:
    """Resolve the question to metric ids, then evaluate them through the registry."""
    accepted, rejected = resolve(question, client)
    values = evaluate(path, accepted, tenant, tag) if accepted else {}
    return {
        "question": question,
        "grounded": True,
        "metrics": [
            {"id": metric_id, "label": METRICS[metric_id].label, "unit": METRICS[metric_id].unit,
             "owner": METRICS[metric_id].owner, "value": values.get(metric_id)}
            for metric_id in accepted
        ],
        "rejected": rejected,
        "catalog_size": len(METRICS),
    }


GOLDEN: List[Dict[str, Any]] = [
    {"question": "How much value is exposed to out-of-stock risk?", "expect": ["oos_exposure_gbp"]},
    {"question": "What share of SKUs went through autopilot?", "expect": ["autopilot_share"]},
    {"question": "How many exceptions per hundred SKUs are we reviewing?", "expect": ["exceptions_per_100"]},
    {"question": "Are we adding forecast value over the baseline?", "expect": ["fva_points"]},
    {"question": "How bad is the data quality this cycle?",
     "expect": ["open_quality_warnings", "imputed_items", "max_imputed_share"]},
    {"question": "What is our average weeks of cover?", "expect": ["avg_weeks_of_cover"]},
    {"question": "How much of the plan is planner-led?", "expect": ["planner_led_share"]},
    {"question": "What is the total value at risk across stock risks?", "expect": ["total_exposure_gbp"]},
]


def score_golden(client: Any) -> Dict[str, Any]:
    """Run the golden question set against a live model (PR-5.1 accuracy gate)."""
    results = []
    hits = 0
    for case in GOLDEN:
        try:
            accepted, rejected = resolve(case["question"], client)
        except ValueError as exc:
            results.append({"question": case["question"], "accepted": [], "rejected": [],
                            "expected": case["expect"], "ok": False, "error": str(exc)})
            continue
        ok = set(case["expect"]) <= set(accepted)
        hits += 1 if ok else 0
        results.append({"question": case["question"], "accepted": accepted, "rejected": rejected,
                        "expected": case["expect"], "ok": ok})
    return {"total": len(GOLDEN), "hits": hits,
            "accuracy": round(hits / len(GOLDEN), 3) if GOLDEN else 0.0, "results": results}
