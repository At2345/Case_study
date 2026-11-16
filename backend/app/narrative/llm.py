"""Emergent universal-key client (Gemini 3 Flash by default). Same `generate(system, user)` contract as GeminiClient."""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, List, Optional, Tuple

from app.ai.privacy import scrub

log = logging.getLogger("sop.llm")

# Provider health registry: {name: {"configured": bool, "ok": bool|None, "error": str|None, "at": float|None}}
PROVIDER_STATUS: Dict[str, Dict[str, Any]] = {}

DEFAULT_PROVIDER = "gemini"
DEFAULT_MODEL = "gemini-3-flash-preview"


@dataclass
class EmergentLlmClient:
    api_key: str = field(repr=False)
    provider: str = DEFAULT_PROVIDER
    model: str = DEFAULT_MODEL

    @classmethod
    def from_env(cls) -> Optional["EmergentLlmClient"]:
        key = os.getenv("EMERGENT_LLM_KEY", "").strip()
        if not key:
            return None
        return cls(api_key=key, provider=os.getenv("LLM_PROVIDER", DEFAULT_PROVIDER), model=os.getenv("LLM_MODEL", DEFAULT_MODEL))

    def _chat(self, system: str):
        from emergentintegrations.llm.chat import LlmChat
        return LlmChat(api_key=self.api_key, session_id=uuid.uuid4().hex, system_message=system).with_model(self.provider, self.model)

    async def agenerate(self, system: str, user: str) -> str:
        from emergentintegrations.llm.chat import UserMessage
        # single validated paragraph: guardrails need the full text before anyone sees it, so no streaming here
        return (await self._chat(system).send_message(UserMessage(text=user))) or ""

    def generate(self, system: str, user: str) -> str:
        return asyncio.run(self.agenerate(system, user))

    async def stream(self, system: str, history: List[Tuple[str, str]], user: str) -> AsyncIterator[str]:
        from emergentintegrations.llm.chat import StreamDone, TextDelta, UserMessage
        convo = "".join(f"{role.upper()}: {text}\n" for role, text in history[-8:])
        prompt = f"{convo}USER: {user}" if convo else user
        async for ev in self._chat(system).stream_message(UserMessage(text=prompt)):
            if isinstance(ev, TextDelta):
                yield ev.content
            elif isinstance(ev, StreamDone):
                break


@dataclass
class FailoverClient:
    """Tries providers in order; falls through to the next when one fails (e.g. Gemini quota exhaustion)."""

    clients: List[Any]

    def _note(self, name: str, ok: bool, error: Optional[str] = None) -> None:
        # Served to the browser by /api/v1/assistant/providers, and a provider error can
        # echo the request body back, so the message is redacted before it is stored.
        PROVIDER_STATUS[name] = {"configured": True, "ok": ok,
                                 "error": scrub(error) if error else error, "at": time.time()}

    def generate(self, system: str, user: str) -> str:
        last: Optional[Exception] = None
        for client in self.clients:
            name = type(client).__name__
            try:
                out = client.generate(system, user)
                self._note(name, True)
                return out
            except Exception as exc:
                self._note(name, False, f"{type(exc).__name__}: {str(exc)[:160]}")
                log.warning("llm provider %s failed (%s), trying next", name, type(exc).__name__)
                last = exc
        raise last  # type: ignore[misc]

    async def stream(self, system: str, history: List[Tuple[str, str]], user: str) -> AsyncIterator[str]:
        last: Optional[Exception] = None
        for client in self.clients:
            name = type(client).__name__
            try:
                async for chunk in client.stream(system, history, user):
                    yield chunk
                self._note(name, True)
                return
            except Exception as exc:
                self._note(name, False, f"{type(exc).__name__}: {str(exc)[:160]}")
                log.warning("llm provider %s failed (%s), trying next", name, type(exc).__name__)
                last = exc
        raise last  # type: ignore[misc]


def client_from_env():
    """Providers in priority order: Emergent universal key, direct Gemini key, GLM key,
    then a local open-source model (LOCAL_MODEL, e.g. google/flan-t5-base).
    Multiple configured providers are wrapped in a FailoverClient so a quota-exhausted
    provider (e.g. Gemini free tier) falls through to the next. None means deterministic narratives only.

    The result is wrapped in a PrivacyClient, so every prompt is redacted before it leaves
    the process. AI_REQUIRE_ZDR removes providers that are not declared in AI_ZDR_PROVIDERS;
    if that leaves nothing, no external call is made and the deterministic text is used."""
    from app.ai.privacy import PrivacyClient, ProviderPolicy, set_active
    from app.narrative.client import GeminiClient, GlmClient
    from app.narrative.local_hf import LocalHfClient
    candidates = [c for c in (EmergentLlmClient.from_env(), GeminiClient.from_env(),
                              GlmClient.from_env(), LocalHfClient.from_env()) if c is not None]
    for c in candidates:
        PROVIDER_STATUS.setdefault(type(c).__name__, {"configured": True, "ok": None, "error": None, "at": None})
    policy = ProviderPolicy.from_env()
    names = [type(c).__name__ for c in candidates]
    permitted = [c for c in candidates if policy.permits(c)]
    allowed = {type(c).__name__ for c in permitted}
    for name in names:
        if name not in allowed:
            PROVIDER_STATUS[name] = {"configured": True, "ok": False,
                                     "error": "refused: not declared zero-retention",
                                     "at": time.time()}
            log.warning("AI_REQUIRE_ZDR is set and %s is not declared in AI_ZDR_PROVIDERS; refusing it", name)
    set_active(policy, names)
    if not permitted:
        return None
    inner = permitted[0] if len(permitted) == 1 else FailoverClient(permitted)
    return PrivacyClient(inner)
