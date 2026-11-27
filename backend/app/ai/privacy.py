"""Pre-prompt redaction of sensitive values, and the policy that gates which providers may be called.

OWASP LLM02 (sensitive data leakage). Every prompt is redacted at one choke point,
`PrivacyClient`, which `client_from_env()` installs around whatever provider stack it
builds. No call site can leak by forgetting to mask, because no call site masks.

A value is replaced with a stable placeholder (`[[EMAIL_1]]`) for the duration of one
request and put back in the response, so the model never receives the value while the
planner still reads their own data. Findings are recorded as counts per kind and never
as values: a redaction log that quotes what it redacted is not a redaction log.

ZDR is a *declaration*, not an attestation. `AI_ZDR_PROVIDERS` names the providers whose
zero-retention terms have been verified out of band; `AI_REQUIRE_ZDR` makes an undeclared
provider a refusal rather than a warning. With nothing declared, `report()["zdr"]["attested"]`
is False, which is the honest answer.
"""
from __future__ import annotations

import os
import re
import threading
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, Iterable, List, Optional, Tuple

PLACEHOLDER_OPEN = "[["
PLACEHOLDER_CLOSE = "]]"


@dataclass(frozen=True)
class Rule:
    """One class of sensitive value, with the pattern that finds it."""

    kind: str
    pattern: re.Pattern
    description: str


# Ordered: a value is claimed by the first rule that matches it, so the specific
# shapes (email, IBAN) run before the loose ones (phone, postcode).
RULES: Tuple[Rule, ...] = (
    Rule("email",
         re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
         "Email address"),
    Rule("iban",
         re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b"),
         "IBAN / international bank account number"),
    Rule("card",
         re.compile(r"\b\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{4}\b"),
         "Payment card number in four-digit groups"),
    Rule("ni_number",
         re.compile(r"\b[A-CEGHJ-PR-TW-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b"),
         "UK National Insurance number"),
    Rule("phone_uk",
         re.compile(r"(?<![\w.])(?:\+44[\s.-]?\(?0?\)?[\s.-]?|0)\d{2,4}[\s.-]?\d{3,4}[\s.-]?\d{3,4}(?![\w.])"),
         "UK telephone number, national or +44 form"),
    Rule("postcode_uk",
         re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}\b"),
         "UK postcode"),
    Rule("ipv4",
         re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
         "IPv4 address"),
)


class Redactor:
    """Replaces sensitive values with stable placeholders and can put them back.

    One instance per request: the map is what makes `[[EMAIL_1]]` mean the same address
    every time it appears, so the model can still tell that two mentions are one person,
    and `restore()` can undo every occurrence.
    """

    def __init__(self, rules: Iterable[Rule] = RULES) -> None:
        self._rules = tuple(rules)
        self._forward: Dict[str, str] = {}
        self._reverse: Dict[str, str] = {}
        self._counts: Counter = Counter()

    def _token(self, kind: str, value: str) -> str:
        token = self._forward.get(value)
        if token is not None:
            return token
        self._counts[kind] += 1
        token = f"{PLACEHOLDER_OPEN}{kind.upper()}_{self._counts[kind]}{PLACEHOLDER_CLOSE}"
        self._forward[value] = token
        self._reverse[token] = value
        return token

    def redact(self, text: Any) -> str:
        """Return `text` with every sensitive value replaced by its placeholder."""
        if text is None:
            return ""
        out = str(text)
        if not out:
            return out
        for rule in self._rules:
            out = rule.pattern.sub(lambda m, r=rule: self._token(r.kind, m.group(0)), out)
        return out

    def restore(self, text: Any) -> str:
        """Put the original values back into a response."""
        if text is None:
            return ""
        out = str(text)
        if not out or not self._reverse:
            return out
        for token, value in self._reverse.items():
            out = out.replace(token, value)
        return out

    @property
    def findings(self) -> List[Dict[str, Any]]:
        """Counts per kind. Never the values themselves."""
        return [{"kind": kind, "count": count} for kind, count in sorted(self._counts.items())]

    @property
    def total(self) -> int:
        return sum(self._counts.values())


class StreamRestorer:
    """Puts placeholders back into a token stream without splitting one in half.

    A provider may emit `[[EMA` and `IL_1]]` in separate chunks. Text is held back only
    while an opening delimiter is still unterminated, so nothing is delayed beyond the
    chunk that completes it.
    """

    def __init__(self, redactor: Redactor) -> None:
        self._redactor = redactor
        self._buffer = ""

    def feed(self, chunk: str) -> str:
        self._buffer += chunk or ""
        start = self._buffer.rfind(PLACEHOLDER_OPEN)
        if start != -1 and self._buffer.find(PLACEHOLDER_CLOSE, start) == -1:
            out, self._buffer = self._buffer[:start], self._buffer[start:]
        else:
            out, self._buffer = self._buffer, ""
        return self._redactor.restore(out)

    def flush(self) -> str:
        out, self._buffer = self._buffer, ""
        return self._redactor.restore(out)


# ---------------------------------------------------------------------- counters
_LOCK = threading.Lock()
_PROMPTS = 0
_FINDINGS: Counter = Counter()


def _record(redactor: Redactor) -> None:
    global _PROMPTS
    with _LOCK:
        _PROMPTS += 1
        for kind, count in redactor._counts.items():
            _FINDINGS[kind] += count


def counters() -> Dict[str, Any]:
    with _LOCK:
        return {"prompts": _PROMPTS, "findings": dict(sorted(_FINDINGS.items()))}


def reset_counters() -> None:
    """Test helper: start a fresh tally."""
    global _PROMPTS
    with _LOCK:
        _PROMPTS = 0
        _FINDINGS.clear()


def scrub(text: Any) -> str:
    """Redact a log line or error message with a throwaway map.

    Provider errors can echo the request body back, and `PROVIDER_STATUS` is served to
    the browser by `/api/v1/assistant/providers`, so an unscrubbed message is a leak path
    that never touches a prompt.
    """
    return Redactor().redact(text)


# ---------------------------------------------------------------------- provider policy
@dataclass(frozen=True)
class ProviderPolicy:
    """Which providers may receive a prompt, and whether that is enforced."""

    require_zdr: bool = False
    zdr_declared: frozenset = frozenset()

    @classmethod
    def from_env(cls) -> "ProviderPolicy":
        declared = frozenset(
            name.strip().lower()
            for name in os.getenv("AI_ZDR_PROVIDERS", "").split(",")
            if name.strip())
        required = os.getenv("AI_REQUIRE_ZDR", "false").strip().lower() in {"1", "true", "yes", "on"}
        return cls(require_zdr=required, zdr_declared=declared)

    def permits(self, client: Any) -> bool:
        if not self.require_zdr:
            return True
        if getattr(client, "local", False) is True:
            return True  # runs on this machine (e.g. Ollama on localhost): no prompt leaves the process host
        return type(client).__name__.lower() in self.zdr_declared

    def attested(self, providers: Iterable[str]) -> bool:
        """True only when every configured provider is declared zero-retention."""
        names = [p.lower() for p in providers]
        return bool(names) and all(n in self.zdr_declared for n in names)


_ACTIVE: Dict[str, Any] = {"policy": None, "providers": []}


def set_active(policy: ProviderPolicy, providers: Iterable[str]) -> None:
    """Record the policy and provider set that `client_from_env()` actually built."""
    _ACTIVE["policy"] = policy
    _ACTIVE["providers"] = list(providers)


def active_policy() -> ProviderPolicy:
    return _ACTIVE["policy"] or ProviderPolicy.from_env()


def report() -> Dict[str, Any]:
    """What leaves the process before a prompt reaches a provider, and what is permitted."""
    policy = active_policy()
    providers = list(_ACTIVE["providers"])
    return {
        "masking": {
            "enabled": True,
            "placeholder": f"{PLACEHOLDER_OPEN}KIND_n{PLACEHOLDER_CLOSE}",
            "restored_in_response": True,
            "rules": [{"kind": r.kind, "description": r.description} for r in RULES],
        },
        "zdr": {
            "required": policy.require_zdr,
            "declared": sorted(policy.zdr_declared),
            "providers": providers,
            "attested": policy.attested(providers),
        },
        "counters": counters(),
        "limitations": [
            "Person names are not detected: a name is not distinguishable from an ordinary "
            "word without a roster, so free-text comments can still carry one.",
            "Redaction is pattern-based, so an unusual format for a supported kind can pass through.",
            "ZDR is a declaration from AI_ZDR_PROVIDERS, not a verified provider attestation.",
        ],
    }


# ---------------------------------------------------------------------- the choke point
@dataclass
class PrivacyClient:
    """Wraps a provider client so every prompt is redacted and every response restored.

    `client_from_env()` installs this around the whole failover stack, so masking happens
    once per call rather than once per provider attempt.
    """

    inner: Any = field(repr=False)

    @property
    def model(self) -> str:
        return getattr(self.inner, "model", "")

    @property
    def provider(self) -> str:
        return type(self.inner).__name__

    def generate(self, system: str, user: str) -> str:
        redactor = Redactor()
        try:
            text = self.inner.generate(redactor.redact(system), redactor.redact(user))
        finally:
            _record(redactor)
        return redactor.restore(text or "")

    async def stream(self, system: str, history: List[Tuple[str, str]],
                     user: str) -> AsyncIterator[str]:
        redactor = Redactor()
        masked_history = [(role, redactor.redact(text)) for role, text in history or []]
        restorer = StreamRestorer(redactor)
        try:
            async for chunk in self.inner.stream(redactor.redact(system), masked_history,
                                                 redactor.redact(user)):
                out = restorer.feed(chunk)
                if out:
                    yield out
            tail = restorer.flush()
            if tail:
                yield tail
        finally:
            _record(redactor)
