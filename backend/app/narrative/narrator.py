"""Narrative generation with guardrails, a persistent cache and a deterministic fallback."""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from app.narrative.guardrails import validate_narrative
from app.narrative.prompt import PROMPT_VERSION, build_messages

log = logging.getLogger("sop.narrative")


class Narrator:
    """`client` needs a `generate(system, user) -> str` method; None means the LLM is not configured."""

    def __init__(self, client: Optional[Any], cache_path: Path, model: str = "") -> None:
        self.client = client
        self.model = model or getattr(client, "model", "")
        self.cache_path = Path(cache_path)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.cache_path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS narratives (key TEXT PRIMARY KEY, text TEXT NOT NULL, "
                       "model TEXT NOT NULL, created_at TEXT NOT NULL)")

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def _key(self, telemetry: Dict[str, Any]) -> str:
        payload = json.dumps(telemetry, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(f"{PROMPT_VERSION}|{self.model}|{payload}".encode("utf-8")).hexdigest()

    def _get(self, key: str) -> Optional[str]:
        with closing(sqlite3.connect(self.cache_path)) as db:
            row = db.execute("SELECT text FROM narratives WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def _put(self, key: str, text: str) -> None:
        with closing(sqlite3.connect(self.cache_path)) as db, db:
            db.execute("INSERT OR REPLACE INTO narratives (key, text, model, created_at) VALUES (?, ?, ?, ?)",
                       (key, text, self.model, datetime.now(timezone.utc).isoformat()))

    @staticmethod
    def _template(fallback: str, reasons: list) -> Dict[str, Any]:
        return {"text": fallback, "source": "template", "cached": False, "reasons": reasons}

    def narrate(self, telemetry: Dict[str, Any], fallback: str) -> Dict[str, Any]:
        if self.client is None:
            return self._template(fallback, ["llm_not_configured"])
        key = self._key(telemetry)
        cached = self._get(key)
        if cached is not None:
            return {"text": cached, "source": "llm", "cached": True, "reasons": []}
        system, user = build_messages(telemetry)
        try:
            text = (self.client.generate(system, user) or "").strip()
        except Exception as exc:  # network, quota, SDK missing: the planner still gets the template
            log.warning("narrative generation failed: %s", type(exc).__name__)
            return self._template(fallback, [f"llm_error: {type(exc).__name__}"])
        ok, reasons = validate_narrative(text, telemetry)
        if not ok:
            log.warning("narrative rejected by guardrails: %s", "; ".join(reasons))
            return self._template(fallback, reasons)
        self._put(key, text)
        return {"text": text, "source": "llm", "cached": False, "reasons": []}
