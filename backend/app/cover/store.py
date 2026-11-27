"""Append-only quote log per tenant (JSON lines). Quotes are never edited; a new price is a new quote."""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

TENANT_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
VALID_DAYS = 7


class QuoteStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._lock = threading.Lock()

    def _path(self, tenant: str) -> Path:
        if not TENANT_RE.match(tenant or ""):
            raise ValueError("invalid tenant id")
        return self.root / tenant / "quotes.jsonl"

    def add(self, tenant: str, record: Dict[str, Any], created_by: str) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        row = {"id": uuid.uuid4().hex[:12], "created_at": now.isoformat(), "created_by": created_by,
               "valid_until": (now + timedelta(days=VALID_DAYS)).isoformat(), **record}
        path = self._path(tenant)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, default=str) + "\n")
        return row

    def list(self, tenant: str, limit: int = 50) -> List[Dict[str, Any]]:
        path = self._path(tenant)
        if not path.exists():
            return []
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return list(reversed(rows))[:limit]

    def get(self, tenant: str, quote_id: str) -> Dict[str, Any]:
        for row in self.list(tenant, limit=1_000_000):
            if row["id"] == quote_id:
                return row
        raise KeyError(f"quote {quote_id} not found")
