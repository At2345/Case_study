"""Prompt for the reasoning step. The model receives the telemetry JSON and nothing else."""
from __future__ import annotations

import json
from typing import Any, Dict, Tuple

PROMPT_VERSION = "v1"

SYSTEM_PROMPT = (
    "You write short explanations for a demand planner reviewing one item in a UK consumer-goods S&OP cycle. "
    "Use only the facts in the JSON the user sends. Do not add numbers, dates, causes or events that are not in the JSON, "
    "and quote numbers exactly as they appear there. "
    "Write 2-3 sentences of plain text: say why the item needs attention (the risk flags and the signals that support them), "
    "then what the planner should check. "
    "Percentages in the JSON are already percentages. If a signal contradicts the plan, say so. "
    "No markdown, no lists, no greetings."
)


def build_messages(telemetry: Dict[str, Any]) -> Tuple[str, str]:
    """Return (system prompt, user message). The user message is the canonical telemetry JSON."""
    return SYSTEM_PROMPT, json.dumps(telemetry, sort_keys=True, ensure_ascii=False)
