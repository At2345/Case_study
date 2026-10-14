"""Concurrent-user load test of a running API server (NF-01).

Usage:
    $env:LIVE_USER="danny"; $env:LIVE_PASSWORD="..."
    python -m app.api.load_test [base_url] [users=10] [requests_per_user=60]

Each simulated user logs in once, then repeatedly requests the filtered exception queue, an item detail
and the summary. Exits non-zero when the P95 of the exception endpoint is >= 400 ms or any request failed.
"""
from __future__ import annotations

import os
import random
import statistics
import sys
import threading
import time
from typing import Dict, List

import httpx

P95_TARGET_MS = 400.0
FILTERS = [
    "/api/v1/exceptions?channel=amazon",
    "/api/v1/exceptions?channel=B2B wo Amazon&risk_type=OVERSTOCK_RISK",
    "/api/v1/exceptions?risk_type=OOS_RISK&limit=20",
    "/api/v1/exceptions?pending=true",
    "/api/v1/exceptions?state=PLANNER_LED&limit=50",
]


def percentile(values: List[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, max(0, int(round(q * len(s))) - 1))]


def worker(base: str, creds, n: int, item_ids: List[int], out: Dict[str, List[float]], errors: List[str], lock: threading.Lock, seed: int):
    rng = random.Random(seed)
    with httpx.Client(base_url=base, timeout=60) as client:
        r = client.post("/api/v1/auth/login", json={"username": creds[0], "password": creds[1]})
        if r.status_code != 200:
            with lock:
                errors.append(f"login {r.status_code}")
            return
        headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
        for _ in range(n):
            kind = rng.choices(["exceptions", "item", "summary"], weights=[6, 3, 1])[0]
            path = rng.choice(FILTERS) if kind == "exceptions" else (
                f"/api/v1/items/{rng.choice(item_ids)}" if kind == "item" else "/api/v1/summary")
            t = time.perf_counter()
            resp = client.get(path, headers=headers)
            ms = (time.perf_counter() - t) * 1000
            with lock:
                if resp.status_code != 200:
                    errors.append(f"{kind} {resp.status_code}")
                else:
                    out.setdefault(kind, []).append(ms)


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    users = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    per_user = int(sys.argv[3]) if len(sys.argv) > 3 else 60
    creds = (os.getenv("LIVE_USER"), os.getenv("LIVE_PASSWORD"))
    if not all(creds):
        print("set LIVE_USER and LIVE_PASSWORD")
        return 2
    with httpx.Client(base_url=base, timeout=60) as c:
        tok = c.post("/api/v1/auth/login", json={"username": creds[0], "password": creds[1]}).json()["access_token"]
        items = c.get("/api/v1/exceptions?limit=200", headers={"Authorization": f"Bearer {tok}"}).json()["items"]
    item_ids = [i["item_id"] for i in items]

    out: Dict[str, List[float]] = {}
    errors: List[str] = []
    lock = threading.Lock()
    threads = [threading.Thread(target=worker, args=(base, creds, per_user, item_ids, out, errors, lock, k)) for k in range(users)]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.time() - t0

    total = sum(len(v) for v in out.values())
    print(f"{users} concurrent users x {per_user} requests: {total} ok, {len(errors)} errors, {wall:.1f}s wall, {total / wall:.1f} req/s")
    for kind, v in out.items():
        print(f"  {kind:11s} n={len(v):4d}  p50={statistics.median(v):6.1f} ms  p95={percentile(v, .95):6.1f} ms  p99={percentile(v, .99):6.1f} ms  max={max(v):6.1f} ms")
    if errors:
        print("  errors:", sorted(set(errors)))
    p95 = percentile(out.get("exceptions", [0.0]), 0.95)
    verdict = p95 < P95_TARGET_MS and not errors
    print(f"NF-01 (/exceptions P95 < {P95_TARGET_MS:.0f} ms at {users} users): {'PASS' if verdict else 'FAIL'} (P95 {p95:.1f} ms)")
    return 0 if verdict else 1


if __name__ == "__main__":
    raise SystemExit(main())
