"""Thin Gemini wrapper. The API key comes from the environment only; the SDK is imported lazily."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

DEFAULT_MODEL = "gemini-2.5-flash"


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

    def generate(self, system: str, user: str) -> str:
        from google import genai  # optional dependency: pip install google-genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key, http_options=types.HttpOptions(timeout=int(self.timeout_s * 1000)))
        response = client.models.generate_content(
            model=self.model,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system, temperature=0.2, max_output_tokens=self.max_output_tokens,
            ),
        )
        return response.text or ""
