"""Black-box check of a running API server.

Usage: $env:LIVE_USER="danny"; $env:LIVE_PASSWORD="..."; python -m app.api.live_check [http://127.0.0.1:8000]
"""
from __future__ import annotations

import os
import sys

import httpx

DEV_ORIGIN = "http://localhost:5173"


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    user, password = os.getenv("LIVE_USER"), os.getenv("LIVE_PASSWORD")
    if not user or not password:
        print("set LIVE_USER and LIVE_PASSWORD")
        return 2
    ok = True

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        ok &= bool(cond)
        print(f"[{'ok' if cond else 'FAIL'}] {name} {detail}")

    health = httpx.get(f"{base}/api/v1/health", timeout=60)
    check("health", health.status_code == 200, str(health.json().get("cutoff")))
    login = httpx.post(f"{base}/api/v1/auth/login", json={"username": user, "password": password})
    check("login", login.status_code == 200)
    if login.status_code != 200:
        return 1
    h = {"Authorization": f"Bearer {login.json()['access_token']}"}
    s = httpx.get(f"{base}/api/v1/summary", headers=h).json()
    check("summary", s["exceptions_requiring_review"] > 0,
          f"queue={s['exceptions_requiring_review']} touchless={s['autopilot_approved_skus']} pending={s['pending_decisions']}")
    q = httpx.get(f"{base}/api/v1/exceptions?limit=1", headers=h).json()
    check("exceptions", q["total"] > 0, f"top item {q['items'][0]['item_id']} {q['items'][0]['risk_type']}")
    check("no token is rejected", httpx.get(f"{base}/api/v1/summary").status_code == 401)
    ok_pf = httpx.options(f"{base}/api/v1/summary", headers={
        "Origin": DEV_ORIGIN, "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"})
    check("CORS allows the dev origin", ok_pf.headers.get("access-control-allow-origin") == DEV_ORIGIN)
    bad_pf = httpx.options(f"{base}/api/v1/summary", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"})
    check("CORS rejects other origins", "access-control-allow-origin" not in bad_pf.headers)
    check("blank reason code is rejected", httpx.post(
        f"{base}/api/v1/override", headers=h, json={"item_id": q["items"][0]["item_id"], "override_quantity": 1}).json()["detail"] == "Reason code is required.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
