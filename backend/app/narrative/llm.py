"""Emergent universal-key client (Gemini 3 Flash by default). Same `generate(system, user)` contract as GeminiClient."""
from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator, List, Optional, Tuple

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


def client_from_env():
    """Universal key first, then a direct Gemini key, then a local open-source model
    (LOCAL_MODEL, e.g. google/flan-t5-base); None means deterministic narratives only."""
    from app.narrative.client import GeminiClient
    from app.narrative.local_hf import LocalHfClient
    return EmergentLlmClient.from_env() or GeminiClient.from_env() or LocalHfClient.from_env()
