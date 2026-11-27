"""Free local LLM through Ollama (MIT-licensed runtime, open-weight models on your own machine).

Set OLLAMA_MODEL (e.g. qwen2.5:14b) and optionally OLLAMA_URL (default http://localhost:11434). Prompts never
leave the machine when the URL is local, so the privacy policy always permits it. Same generate()/stream()
contract as the other clients, plus chat() with tool calling for the operations agent.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import httpx

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


@dataclass
class OllamaClient:
    model: str
    url: str = "http://localhost:11434"
    timeout_s: float = 120.0
    transport: Optional[httpx.BaseTransport] = field(default=None, repr=False)

    @classmethod
    def from_env(cls) -> Optional["OllamaClient"]:
        model = os.getenv("OLLAMA_MODEL", "").strip()
        if not model:
            return None
        return cls(model=model, url=os.getenv("OLLAMA_URL", "http://localhost:11434").strip().rstrip("/"),
                   timeout_s=float(os.getenv("OLLAMA_TIMEOUT_S", "120")))

    @property
    def local(self) -> bool:
        """True when the model runs on this machine (nothing is sent elsewhere)."""
        return (urlsplit(self.url).hostname or "") in LOCAL_HOSTS

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self.timeout_s, transport=self.transport)

    def chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None,
             json_mode: bool = False) -> Dict[str, Any]:
        """One chat turn. Returns Ollama's message: {"role", "content", "tool_calls"?}."""
        body: Dict[str, Any] = {"model": self.model, "messages": messages, "stream": False,
                                "options": {"temperature": 0.2}}
        if tools:
            body["tools"] = tools
        if json_mode:
            body["format"] = "json"
        with self._client() as client:
            resp = client.post(f"{self.url}/api/chat", json=body)
        if resp.status_code >= 400:
            raise RuntimeError(f"ollama returned HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json().get("message") or {}

    def generate(self, system: str, user: str) -> str:
        msg = self.chat([{"role": "system", "content": system}, {"role": "user", "content": user}])
        return msg.get("content") or ""

    async def stream(self, system: str, history: List[Tuple[str, str]], user: str) -> AsyncIterator[str]:
        messages = [{"role": "system", "content": system}]
        messages += [{"role": "assistant" if r == "assistant" else "user", "content": t} for r, t in history[-8:]]
        messages.append({"role": "user", "content": user})
        body = {"model": self.model, "messages": messages, "stream": True}
        async with httpx.AsyncClient(timeout=self.timeout_s, transport=self.transport) as client:
            async with client.stream("POST", f"{self.url}/api/chat", json=body) as resp:
                if resp.status_code >= 400:
                    raise RuntimeError(f"ollama returned HTTP {resp.status_code}")
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    text = (chunk.get("message") or {}).get("content")
                    if text:
                        yield text
                    if chunk.get("done"):
                        break
