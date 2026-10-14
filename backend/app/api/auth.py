"""Users, password hashing and JWT handling (NF-02)."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import jwt

from app.models.enums import Role

MIN_SECRET_LENGTH = 32
MIN_PASSWORD_LENGTH = 10
DEFAULT_ITERATIONS = 240_000
ALGORITHM = "HS256"


class TokenError(Exception):
    pass


def hash_password(password: str, iterations: int = DEFAULT_ITERATIONS) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iters, salt_hex, digest_hex = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iters))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


class UserStore:
    """JSON file of {username: {role, password_hash}}. Passwords are never stored in clear text."""

    def __init__(self, path, iterations: int = DEFAULT_ITERATIONS) -> None:
        self.path = Path(path)
        self.iterations = iterations
        self._users: Dict[str, Dict[str, str]] = {}
        if self.path.exists():
            self._users = json.loads(self.path.read_text(encoding="utf-8"))
        self._dummy_hash = hash_password(secrets.token_hex(8), iterations)

    def add_user(self, username: str, role: str, password: str) -> None:
        if role not in {r.value for r in Role}:
            raise ValueError(f"role must be one of {[r.value for r in Role]}")
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"password must have at least {MIN_PASSWORD_LENGTH} characters")
        self._users[username] = {"role": role, "password_hash": hash_password(password, self.iterations)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._users, indent=2), encoding="utf-8")

    def authenticate(self, username: str, password: str) -> Optional[Dict[str, str]]:
        user = self._users.get(username)
        # verify against a dummy hash for unknown users so timing does not reveal valid usernames
        ok = verify_password(password, user["password_hash"] if user else self._dummy_hash)
        return {"username": username, "role": user["role"]} if (user and ok) else None

    def __len__(self) -> int:
        return len(self._users)


def make_token(secret: str, username: str, role: str, minutes: int = 480) -> str:
    if len(secret) < MIN_SECRET_LENGTH:
        raise ValueError(f"JWT secret must have at least {MIN_SECRET_LENGTH} characters")
    now = int(time.time())
    claims = {"sub": username, "role": role, "iat": now, "exp": now + minutes * 60, "jti": uuid.uuid4().hex}
    return jwt.encode(claims, secret, algorithm=ALGORITHM)


def decode_token(secret: str, token: str) -> Dict[str, Any]:
    try:
        claims = jwt.decode(token, secret, algorithms=[ALGORITHM], options={"require": ["exp", "sub", "role"]})
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if claims["role"] not in {r.value for r in Role}:
        raise TokenError("unknown role")
    return claims
