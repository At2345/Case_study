"""Checks applied to model output before a planner sees it."""
from __future__ import annotations

import re
from typing import Any, List, Set, Tuple

MAX_CHARS = 600
MAX_SENTENCES = 3
NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
FIELD_NAME = re.compile(r"\b[a-z0-9]+_[a-z0-9_]+\b")
MARKDOWN = re.compile(r"[*#`]|^\s*[-\u2022]\s", re.MULTILINE)


def _as_float(token: str) -> float:
    return float(token.replace(",", ""))


def _allowed_numbers(node: Any, out: Set[float]) -> Set[float]:
    """Every number in the telemetry: values, numbers inside strings, and numbers inside key names (e.g. '14d')."""
    if isinstance(node, dict):
        for k, v in node.items():
            out.update(_as_float(t) for t in NUMBER.findall(str(k)))
            _allowed_numbers(v, out)
    elif isinstance(node, (list, tuple)):
        for v in node:
            _allowed_numbers(v, out)
    elif isinstance(node, bool) or node is None:
        pass
    elif isinstance(node, (int, float)):
        out.add(abs(float(node)))
    elif isinstance(node, str):
        out.update(_as_float(t) for t in NUMBER.findall(node))
    return out


def validate_narrative(text: str, telemetry: dict) -> Tuple[bool, List[str]]:
    """Return (ok, reasons). Every number in the text must appear exactly in the telemetry."""
    reasons: List[str] = []
    text = (text or "").strip()
    if not text:
        return False, ["empty"]
    if len(text) > MAX_CHARS:
        reasons.append(f"too long ({len(text)} > {MAX_CHARS} characters)")
    if MARKDOWN.search(text):
        reasons.append("markdown or list formatting")
    if FIELD_NAME.search(text):
        reasons.append("raw field name in the text")
    sentences = [s for s in SENTENCE_SPLIT.split(text) if s.strip()]
    if len(sentences) > MAX_SENTENCES:
        reasons.append(f"too many sentences ({len(sentences)} > {MAX_SENTENCES})")
    allowed = _allowed_numbers(telemetry, set())
    for token in NUMBER.findall(text):
        value = _as_float(token)
        if not any(abs(value - a) < 1e-6 for a in allowed):
            reasons.append(f"number {token} is not in the telemetry")
    return (not reasons), reasons
