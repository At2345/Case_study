"""API keys for machine clients (n8n, scripts).

Key format: `sopk_<key_id>_<secret>`. Only a SHA-256 hash of the secret is stored; the full key is
shown once at creation. Keys carry a role (planner or auditor, never admin) and a tenant, and the
API restricts them to the workflow endpoints.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

PREFIX = "sopk_"
KEY_ROLES = ("planner", "auditor")
KEY_RE = re.compile(r"^sopk_([a-f0-9]{12})_([A-Za-z0-9_\-]{20,128})$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def is_api_key(token: str) -> bool:
    return isinstance(token, str) and token.startswith(PREFIX)


class ApiKeyStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def create(self, name: str, role: str, tenant: str, created_by: str) -> Dict[str, Any]:
        if role not in KEY_ROLES:
            raise ValueError(f"role must be one of {list(KEY_ROLES)}")
        name = (name or "").strip()
        if not 1 <= len(name) <= 80:
            raise ValueError("name must be 1-80 characters")
        key_id, secret = secrets.token_hex(6), secrets.token_urlsafe(32)
        row = {"id": key_id, "name": name, "role": role, "tenant": tenant, "hash": _hash(secret),
               "created_by": created_by, "created_at": _now(), "last_used_at": None, "revoked_at": None}
        with self._lock:
            rows = self._read()
            rows.append(row)
            self._write(rows)
        return {**_public(row), "key": f"{PREFIX}{key_id}_{secret}"}

    def list(self, tenant: str) -> List[Dict[str, Any]]:
        return [_public(r) for r in self._read() if r["tenant"] == tenant]

    def revoke(self, key_id: str, tenant: str) -> bool:
        with self._lock:
            rows = self._read()
            for r in rows:
                if r["id"] == key_id and r["tenant"] == tenant and r["revoked_at"] is None:
                    r["revoked_at"] = _now()
                    self._write(rows)
                    return True
        return False

    def authenticate(self, token: str) -> Optional[Dict[str, Any]]:
        m = KEY_RE.match(token or "")
        if not m:
            return None
        key_id, secret = m.groups()
        with self._lock:
            rows = self._read()
            for r in rows:
                if r["id"] == key_id and r["revoked_at"] is None and hmac.compare_digest(r["hash"], _hash(secret)):
                    r["last_used_at"] = _now()
                    self._write(rows)
                    return _public(r)
        return None

    def _read(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, rows: List[Dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        tmp.replace(self.path)


def _public(row: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in row.items() if k != "hash"}
