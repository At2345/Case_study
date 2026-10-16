"""Prompt for the reasoning step. The model receives the telemetry JSON and nothing else."""
from __future__ import annotations

import json
from typing import Any, Dict, Tuple

PROMPT_VERSION = "v3"

SYSTEM_PROMPT = (
    "You write short explanations for a demand planner reviewing one item in a UK consumer-goods S&OP cycle. "
    "Use only the facts in the JSON the user sends. Do not add numbers, dates, causes or events that are not in the JSON, "
    "and quote numbers exactly as they appear there. "
    "Write 2-3 sentences of plain text: say why the item needs attention (the risk flags and the signals that support them), "
    "then what the planner should check. If the JSON includes prior_decisions, reference the most recent one when it is relevant. "
    "Percentages in the JSON are already percentages. If a signal contradicts the plan, say so. "
    "Write in plain business language for a planner: never write JSON field names or snake_case words; say "
    "'the plan', 'the baseline', 'units already booked', and give money in pounds (for example \u00a355,278). "
    "No markdown, no lists, no greetings.\n\n"
    "Example 1\n"
    "JSON: {\"item\": {\"channel\": \"Amazon\", \"phase\": \"GROWTH\", \"risk_type\": \"CRITICAL_AMAZON_BUYBOX_ALERT\"}, "
    "\"plan\": {\"month\": \"2026-03-01\", \"baseline_units\": 1200, \"plan_units\": 1450, \"change_vs_baseline_pct\": 20.8}, "
    "\"signals\": {\"oos_rate_28d\": 12.5, \"on_hand\": 180}}\n"
    "Narrative: The Amazon buy-box alert is the main risk: the plan lifts the item 20.8% above baseline to 1,450 units, "
    "but only 180 units are on hand and the 28-day out-of-stock rate is already 12.5%. Check whether the buy-box position "
    "recovers before the lift is committed.\n\n"
    "Example 2\n"
    "JSON: {\"item\": {\"channel\": \"B2B\", \"phase\": \"MATURE\", \"risk_type\": \"TERMINAL_OBSOLESCENCE_RISK\"}, "
    "\"plan\": {\"month\": \"2026-04-01\", \"baseline_units\": 500, \"plan_units\": 300, \"change_vs_baseline_pct\": -40.0}, "
    "\"prior_decisions\": [{\"month\": \"2026-03-01\", \"submitted_units\": 320, \"reason\": \"EOL_RUNOUT\"}]}\n"
    "Narrative: The item is flagged for terminal obsolescence and the plan steps down 40% to 300 units. "
    "That continues the run-out decision made for March (320 units, EOL run-out). Confirm the remaining stock "
    "clears by the end-of-life date before approving."
)


def build_messages(telemetry: Dict[str, Any]) -> Tuple[str, str]:
    """Return (system prompt, user message). The user message is the canonical telemetry JSON."""
    return SYSTEM_PROMPT, json.dumps(telemetry, sort_keys=True, ensure_ascii=False)
