import time

import jwt
import pytest

from app.api.auth import (
    TokenError,
    UserStore,
    decode_token,
    hash_password,
    make_token,
    verify_password,
)

SECRET = "x" * 40


def test_password_hash_roundtrip_and_salting():
    h1, h2 = hash_password("s3cret-pass", iterations=1000), hash_password("s3cret-pass", iterations=1000)
    assert h1 != h2 and h1.startswith("pbkdf2_sha256$1000$")
    assert verify_password("s3cret-pass", h1)
    assert not verify_password("wrong", h1)
    assert not verify_password("s3cret-pass", "garbage")


def test_user_store_persists_and_authenticates(tmp_path):
    store = UserStore(tmp_path / "users.json", iterations=1000)
    store.add_user("danny", "planner", "a-long-passphrase")
    again = UserStore(tmp_path / "users.json", iterations=1000)
    assert again.authenticate("danny", "a-long-passphrase")["role"] == "planner"
    assert again.authenticate("danny", "nope") is None
    assert again.authenticate("ghost", "a-long-passphrase") is None
    assert "a-long-passphrase" not in (tmp_path / "users.json").read_text()


def test_user_store_rejects_bad_role_and_short_password(tmp_path):
    store = UserStore(tmp_path / "users.json", iterations=1000)
    with pytest.raises(ValueError):
        store.add_user("x", "superuser", "a-long-passphrase")
    with pytest.raises(ValueError):
        store.add_user("x", "planner", "short")


def test_token_roundtrip_and_claims():
    token = make_token(SECRET, "danny", "planner", minutes=5)
    claims = decode_token(SECRET, token)
    assert claims["sub"] == "danny" and claims["role"] == "planner" and claims["jti"]


def test_token_rejects_wrong_secret_expired_and_missing_claims():
    token = make_token(SECRET, "danny", "planner", minutes=5)
    with pytest.raises(TokenError):
        decode_token("y" * 40, token)
    expired = jwt.encode({"sub": "d", "role": "planner", "exp": int(time.time()) - 10}, SECRET, algorithm="HS256")
    with pytest.raises(TokenError):
        decode_token(SECRET, expired)
    no_role = jwt.encode({"sub": "d", "exp": int(time.time()) + 60}, SECRET, algorithm="HS256")
    with pytest.raises(TokenError):
        decode_token(SECRET, no_role)
    none_alg = jwt.encode({"sub": "d", "role": "admin", "exp": int(time.time()) + 60}, None, algorithm="none")
    with pytest.raises(TokenError):
        decode_token(SECRET, none_alg)


def test_short_secret_is_refused():
    with pytest.raises(ValueError):
        make_token("short", "d", "planner")
