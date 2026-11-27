"""Local credential vault: channel secrets are encrypted at rest with Fernet (AES-128-CBC + HMAC).

The key comes from CHANNEL_VAULT_KEY, or is generated once into `<store>/channels/vault.key`
(owner-only permissions where the OS supports it). Secrets are decrypted only inside the process when a
connector runs, and are never returned by the API.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger("sop.channels.vault")


class VaultError(RuntimeError):
    pass


class Vault:
    def __init__(self, key: bytes) -> None:
        self._fernet = Fernet(key)

    @classmethod
    def from_env_or_file(cls, root: Path) -> "Vault":
        env = os.environ.get("CHANNEL_VAULT_KEY", "").strip()
        if env:
            return cls(env.encode())
        path = Path(root) / "vault.key"
        if path.exists():
            return cls(path.read_bytes().strip())
        path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        path.write_bytes(key)
        try:
            os.chmod(path, 0o600)
        except OSError:  # pragma: no cover - best effort on Windows
            pass
        log.warning("Generated a new channel vault key at %s; back it up or set CHANNEL_VAULT_KEY", path)
        return cls(key)

    def seal(self, secrets: Dict[str, Any]) -> str:
        return self._fernet.encrypt(json.dumps(secrets).encode()).decode()

    def open(self, token: Optional[str]) -> Dict[str, Any]:
        if not token:
            return {}
        try:
            return json.loads(self._fernet.decrypt(token.encode()))
        except InvalidToken as exc:
            raise VaultError("stored channel credentials cannot be decrypted (wrong vault key?)") from exc
