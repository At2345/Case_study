"""Thin Gemini and GLM wrappers. API keys come from the environment only; SDKs are imported lazily."""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, List, Optional, Tuple

DEFAULT_MODEL = "gemini-3.5-flash-lite"
GLM_DEFAULT_MODEL = "glm-5.2"
GLM_DEFAULT_BASE = "https://open.bigmodel.cn/api/paas/v4"

RETRYABLE = (429, 500, 502, 503, 504)
MAX_ATTEMPTS = 3
BACKOFF_S = 1.0


def _status(exc: Exception) -> Optional[int]:
    """HTTP status of a google-genai API error, if any."""
    return getattr(exc, "code", None) or getattr(exc, "status_code", None)


@dataclass
class GeminiClient:
    api_key: str = field(repr=False)
    model: str = DEFAULT_MODEL
    timeout_s: float = 15.0
    max_output_tokens: int = 300

    @classmethod
    def from_env(cls) -> Optional["GeminiClient"]:
        key = os.getenv("GEMINI_API_KEY", "").strip()
        if not key:
            return None
        return cls(api_key=key, model=os.getenv("GEMINI_MODEL", DEFAULT_MODEL))

    def _client(self):
        from google import genai  # optional dependency: pip install google-genai
        from google.genai import types

        return genai.Client(api_key=self.api_key, http_options=types.HttpOptions(timeout=int(self.timeout_s * 1000)))

    def generate(self, system: str, user: str) -> str:
        import asyncio

        from google.genai import types

        # The SDK's sync path calls asyncio.get_event_loop(), which fails in FastAPI worker threads.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())

        client = self._client()
        last: Optional[Exception] = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = client.models.generate_content(
                    model=self.model,
                    contents=user,
                    config=types.GenerateContentConfig(
                        system_instruction=system, temperature=0.2, max_output_tokens=self.max_output_tokens,
                    ),
                )
                return response.text or ""
            except Exception as exc:
                if _status(exc) not in RETRYABLE or attempt == MAX_ATTEMPTS - 1:
                    raise
                last = exc
                time.sleep(BACKOFF_S * (attempt + 1))
        raise last  # pragma: no cover - unreachable, keeps the type checker quiet

    async def stream(self, system: str, history: List[Tuple[str, str]], user: str) -> AsyncIterator[str]:
        from google.genai import types

        client = self._client()
        convo = "".join(f"{role.upper()}: {text}\n" for role, text in history[-8:])
        prompt = f"{convo}USER: {user}" if convo else user
        last: Optional[Exception] = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = client.models.generate_content_stream(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system, temperature=0.2, max_output_tokens=self.max_output_tokens,
                    ),
                )
                for chunk in response:
                    if chunk.text:
                        yield chunk.text
                return
            except Exception as exc:
                if _status(exc) not in RETRYABLE or attempt == MAX_ATTEMPTS - 1:
                    raise
                last = exc
                time.sleep(BACKOFF_S * (attempt + 1))
        raise last  # pragma: no cover - unreachable, keeps the type checker quiet


@dataclass
class GlmClient:
    """OpenAI-compatible Zhipu GLM client (glm-5.2). Same contract as GeminiClient."""

    api_key: str = field(repr=False)
    model: str = GLM_DEFAULT_MODEL
    base_url: str = GLM_DEFAULT_BASE
    timeout_s: float = 30.0
    max_output_tokens: int = 300
    transport: Any = field(default=None, repr=False)

    @classmethod
    def from_env(cls) -> Optional["GlmClient"]:
        key = os.getenv("GLM_API_KEY", "").strip()
        if not key:
            return None
        return cls(
            api_key=key,
            model=os.getenv("GLM_MODEL", GLM_DEFAULT_MODEL),
            base_url=os.getenv("GLM_BASE_URL", GLM_DEFAULT_BASE).rstrip("/"),
        )

    def _messages(self, system: str, user: str) -> List[dict]:
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def generate(self, system: str, user: str) -> str:
        import httpx

        last: Optional[Exception] = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                with httpx.Client(transport=self.transport, timeout=self.timeout_s) as client:
                    r = client.post(
                        f"{self.base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json={"model": self.model, "messages": self._messages(system, user),
                              "temperature": 0.2, "max_tokens": self.max_output_tokens, "stream": False},
                    )
                r.raise_for_status()
                return (r.json()["choices"][0]["message"]["content"] or "").strip()
            except Exception as exc:
                resp = getattr(exc, "response", None)
                code = resp.status_code if resp is not None else None
                if code not in RETRYABLE or attempt == MAX_ATTEMPTS - 1:
                    raise
                last = exc
                time.sleep(BACKOFF_S * (attempt + 1))
        raise last  # pragma: no cover - unreachable, keeps the type checker quiet

    async def stream(self, system: str, history: List[Tuple[str, str]], user: str) -> AsyncIterator[str]:
        import httpx

        convo = "".join(f"{role.upper()}: {text}\n" for role, text in history[-8:])
        prompt = f"{convo}USER: {user}" if convo else user
        last: Optional[Exception] = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                async with httpx.AsyncClient(transport=self.transport, timeout=self.timeout_s) as client:
                    async with client.stream(
                        "POST", f"{self.base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json={"model": self.model, "messages": self._messages(system, prompt),
                              "temperature": 0.2, "max_tokens": self.max_output_tokens, "stream": True},
                    ) as resp:
                        if resp.status_code != 200:
                            await resp.aread()
                            raise httpx.HTTPStatusError(f"GLM {resp.status_code}", request=resp.request, response=resp)
                        async for line in resp.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            payload = line[5:].strip()
                            if payload == "[DONE]":
                                return
                            delta = json.loads(payload)["choices"][0].get("delta", {}).get("content")
                            if delta:
                                yield delta
                return
            except Exception as exc:
                resp = getattr(exc, "response", None)
                code = resp.status_code if resp is not None else None
                if code not in RETRYABLE or attempt == MAX_ATTEMPTS - 1:
                    raise
                last = exc
                await asyncio.sleep(BACKOFF_S * (attempt + 1))
        raise last  # pragma: no cover - unreachable, keeps the type checker quiet
