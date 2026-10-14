"""Open-source local LLM client via HuggingFace transformers (flan-t5 by default).

No API key required. Set LOCAL_MODEL (default google/flan-t5-base) and optionally
LOCAL_MODEL_MAX_TOKENS. The first call downloads the model into the HuggingFace cache;
the model is loaded once per process and reused. Same `generate(system, user)` /
`stream(system, history, user)` contract as the other clients.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, List, Optional, Tuple

DEFAULT_MODEL = "google/flan-t5-base"
DEFAULT_MAX_TOKENS = 160


@dataclass
class LocalHfClient:
    model_name: str = DEFAULT_MODEL
    max_tokens: int = DEFAULT_MAX_TOKENS
    _loaded: Any = field(default=None, repr=False, init=False)

    @classmethod
    def from_env(cls) -> Optional["LocalHfClient"]:
        name = os.getenv("LOCAL_MODEL", "").strip()
        if not name:
            return None
        return cls(model_name=name, max_tokens=int(os.getenv("LOCAL_MODEL_MAX_TOKENS", DEFAULT_MAX_TOKENS)))

    def _load(self) -> Tuple[Any, Any]:
        if self._loaded is None:
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer  # optional dependency
            tokenizer = AutoTokenizer.from_pretrained(self.model_name)
            model = AutoModelForSeq2SeqLM.from_pretrained(self.model_name)
            self._loaded = (tokenizer, model)
        return self._loaded

    def generate(self, system: str, user: str) -> str:
        tokenizer, model = self._load()
        prompt = f"{system}\n\n{user}"
        inputs = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=1024)
        outputs = model.generate(**inputs, max_new_tokens=self.max_tokens, do_sample=False)
        return tokenizer.decode(outputs[0], skip_special_tokens=True).strip()

    async def stream(self, system: str, history: List[Tuple[str, str]], user: str) -> AsyncIterator[str]:
        convo = "".join(f"{role.upper()}: {text}\n" for role, text in history[-8:])
        prompt = f"{convo}USER: {user}" if convo else user
        text = await asyncio.to_thread(self.generate, system, prompt)
        words = text.split(" ")
        for i in range(0, len(words), 4):
            yield " ".join(words[i : i + 4]) + " "
