"""Hybrid retrieval for the Co-Pilot: project data first, Gemini knowledge base as fallback."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.config import REPO_ROOT
from app.narrative.concepts import lookup_concept

# Intents the structured router can answer from the live cycle context.
STRUCTURED_INTENTS: Dict[str, tuple] = {
    "otif": ("otif", "penalty", "exposure"),
    "quality": ("quality", "data-quality", "warning"),
    "safety": ("safety", "safety stock", "inventory"),
    "exception": ("exception", "value at risk", "risk flag"),
    "fva": ("fva", "forecast value added", "override value"),
    "cycle": ("cutoff", "pending decisions", "cycle summary", "cycle status"),
}

# Channel words are qualifiers only - they never trigger an intent by themselves.
CHANNELS = ("amazon", "b2b", "d2c")

# Ranking words + product words -> "top_products" intent (best sellers by channel demand).
RANKING_WORDS = ("best", "top", "highest", "most", "worst", "selling", "popular", "largest", "biggest")
PRODUCT_WORDS = ("products", "product", "items", "item", "skus", "sku", "sellers", "selling")

# Phasing needs >= 2 of these, or a channel plus one of them.
PHASING_WORDS = ("phasing", "delivery", "deliveries", "expected delivery", "b2b demand", "shift", "supply")

DOC_PATHS = ("README.md", "DEVELOPMENT_PLAN.md", "ENHANCEMENT_PLAN.md", "memory/PRD.md")

STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "could", "do", "does", "for", "from",
    "give", "how", "in", "is", "it", "its", "list", "many", "me", "much", "of", "on", "or", "please",
    "question", "show", "summarise", "summarize", "tell", "that", "the", "this", "to", "what", "when",
    "where", "which", "who", "why", "will", "with", "would", "you", "your", "data", "system", "work",
})

ITEM_ID_RE = re.compile(r"\b\d{6}\b")
TOKEN_RE = re.compile(r"[a-z0-9_\-]+")


@dataclass
class Source:
    kind: str  # "structured" | "docs" | "ledger"
    title: str
    snippet: str = ""
    score: float = 0.0


@dataclass
class Retrieval:
    tier: str  # "project" | "general_knowledge" | "none"
    sources: List[Source] = field(default_factory=list)


def _tokens(text: str) -> List[str]:
    return [t for t in TOKEN_RE.findall(text.lower()) if t not in STOPWORDS and len(t) > 1]


def _structured_intent(question: str) -> Optional[tuple]:
    """Returns (intent, channel) or None. Channel is a qualifier, never an intent trigger by itself."""
    q = question.lower()
    channel = next((ch for ch in CHANNELS if ch in q), None)
    for intent, words in STRUCTURED_INTENTS.items():
        if any(w in q for w in words):
            return (intent, channel)
    tokens = set(_tokens(question))
    if tokens & set(RANKING_WORDS) and (tokens & set(PRODUCT_WORDS)):
        return ("top_products", channel)
    phasing_hits = sum(1 for w in PHASING_WORDS if re.search(rf"\b{re.escape(w)}\b", q))
    if phasing_hits >= 2 or (channel and phasing_hits >= 1):
        return ("phasing", channel)
    return None


def _snippet(text: str, q_tokens: set, width: int = 220) -> str:
    words = text.split()
    best, best_score = 0, -1
    for i in range(len(words)):
        window = words[i:i + 40]
        score = len(set(_tokens(" ".join(window))) & q_tokens)
        if score > best_score:
            best, best_score = i, score
    snippet = " ".join(words[best:best + 40])
    return snippet if len(snippet) <= width else snippet[:width] + "..."


def _lexical_hits(question: str, ledger) -> List[Source]:
    """Keyword-overlap search over project docs and ledger comments (no vector DB needed)."""
    q_tokens = set(_tokens(question))
    if not q_tokens:
        return []
    rows: List[tuple] = []
    for name in DOC_PATHS:
        path = REPO_ROOT / name
        if path.exists():
            rows.append(("docs", name, path.read_text(encoding="utf-8", errors="ignore")))
    for r in ledger.overrides(limit=500):
        comment = (r.get("planner_comment") or "").strip()
        if comment:
            rows.append(("ledger", f"Override {r.get('item_id')} {r.get('target_month')}", comment))
    scored = []
    for kind, title, text in rows:
        overlap = len(q_tokens & set(_tokens(text)))
        if overlap:
            scored.append((overlap, kind, title, text))
    scored.sort(key=lambda x: (-x[0], x[2]))
    return [Source(kind=k, title=t, snippet=_snippet(text, q_tokens), score=float(o))
            for o, k, t, text in scored[:3]]


def retrieve(question: str, svc, ledger, mode: str) -> Retrieval:
    """Tier 1 project retrieval. `mode` is the HYBRID_SEARCH setting: off | tier1 | tier2."""
    if mode == "off":
        return Retrieval(tier="none")
    intent = _structured_intent(question)
    if intent:
        name, channel = intent
        title = f"{name}:{channel}" if channel else name
        return Retrieval(tier="project", sources=[Source(kind="structured", title=title)])
    concept = lookup_concept(question)
    if concept:
        # General-knowledge concept (e.g. Croston's method): the project docs never answer these.
        return Retrieval(tier="general_knowledge", sources=[Source(kind="concept", title=concept["title"])])
    m = ITEM_ID_RE.search(question)
    if m and svc.item_row(int(m.group())) is not None:
        return Retrieval(tier="project", sources=[Source(kind="structured", title="item")])
    hits = _lexical_hits(question, ledger)
    if hits and hits[0].score >= 2:
        return Retrieval(tier="project", sources=hits)
    if mode == "tier2":
        return Retrieval(tier="general_knowledge")
    return Retrieval(tier="none")
