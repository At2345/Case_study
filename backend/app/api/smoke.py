"""Smoke test of the API against the real triage artifacts, using a throw-away store.

Usage: $env:PYTHONPATH="backend"; python -m app.api.smoke
"""
from __future__ import annotations

import statistics
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.auth import UserStore
from app.api.main import create_app
from app.api.service import DataService
from app.config import settings
from app.store.ledger import Ledger

PASSWORD = "smoke-test-passphrase"


def _latency(client, path, headers, n=60):
    ms = []
    for _ in range(n):
        t = time.perf_counter()
        client.get(path, headers=headers)
        ms.append((time.perf_counter() - t) * 1000)
    ms.sort()
    return round(statistics.median(ms), 1), round(ms[int(0.95 * len(ms)) - 1], 1)


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    t0 = time.time()
    svc = DataService.from_reports(settings.reports_dir)
    print(f"service load: {time.time() - t0:.1f}s")
    users = UserStore(tmp / "users.json", iterations=1000)
    users.add_user("danny", "planner", PASSWORD)
    client = TestClient(create_app(svc, Ledger(tmp / "ledger.db"), users, "k" * 48, tmp))
    token = client.post("/api/v1/auth/login", json={"username": "danny", "password": PASSWORD}).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}

    s = client.get("/api/v1/summary", headers=h).json()
    keys = ["total_catalog_skus", "in_scope_skus", "autopilot_approved_skus", "exceptions_requiring_review",
            "planner_led_skus", "pending_decisions", "critical_alerts", "net_value_variance_gbp"]
    print({k: s[k] for k in keys})
    print("quality warnings:", s["quality_warnings"])

    q = client.get("/api/v1/exceptions?limit=3", headers=h).json()
    top = q["items"][0]
    print(f"queue total {q['total']}; top: item {top['item_id']} {top['channel']} {top['risk_type']} {top['value_delta_gbp']:.0f} GBP")
    print("narrative:", top["narrative"][:200])
    d = client.get(f"/api/v1/items/{top['item_id']}", headers=h).json()
    print("detail: plan months", len(d["plan"]), "history", len(d["history"]), "telemetry", d["telemetry"])

    r = client.post("/api/v1/override", headers=h, json={
        "item_id": top["item_id"], "override_quantity": round(top["ml_forecast"] * 0.8, 1),
        "reason_code": "Commercial Intelligence (Account Feedback)", "comment": "smoke test"})
    print("override:", r.status_code, {k: r.json().get(k) for k in ("id", "variance_to_ml", "financial_impact_gbp", "planner_id")})
    blocked = client.post(f"/api/v1/cycles/{svc.cutoff}/commit", headers=h)
    print("commit with pending exceptions:", blocked.status_code, blocked.json())

    ph = client.get("/api/v1/phasing/summary", headers=h).json()
    print("phasing profile:", {c: round(p["p1"], 3) for c, p in ph["profile"].items()}, "(share delivered one month later)")
    for r in [x for x in ph["rows"] if x["channel"] == "B2B wo Amazon"][:3]:
        print(f"  B2B {r['month']}: demand {r['demand']:,.0f}  expected delivery {r['expected_delivery']:,.0f}  booked {r['booked']:,.0f}  carry-in {r['carry_in']:,.0f}")
    floored = [i for i in client.get("/api/v1/exceptions?limit=500", headers=h).json()["items"] if "ORDER_BOOK_EXCEEDS_PLAN" in i["risk_flags"]]
    if floored:
        fi = client.get(f"/api/v1/items/{floored[0]['item_id']}", headers=h).json()
        p0 = fi["phasing"][0]
        print(f"order-book item {floored[0]['item_id']}: plan {p0['demand']:,.0f} vs booked {p0['booked']:,.0f} -> floor {p0['order_book_floor']}")
    csv = client.get("/api/v1/export/consensus", headers=h).text.splitlines()[0]
    print("consensus export columns:", csv)

    # allocation: pick the product with the most planned demand across all three channels
    t = svc.triage
    multi = t[t["in_scope"]].groupby("product_id")["channel"].nunique()
    cands = t[t["product_id"].isin(multi[multi == 3].index)].groupby("product_id")["final_m1"].sum().sort_values(ascending=False)
    if len(cands):
        pid = cands.index[0]
        rows = svc.product_demand(pid, svc.first_month)
        demand = sum(r["demand"] for r in rows)
        a = client.post("/api/v1/allocation/simulate", headers=h, json={"product_id": pid, "available_units": round(demand * 0.6)}).json()
        print(f"allocation for {pid}: demand {a['total_demand']:.0f}, supply {a['supply']:.0f}")
        for s in a["scenarios"]:
            fills = {c["channel"]: f"{c['allocated']:.0f}/{c['demand']:.0f}" for c in s["channels"]}
            print(f"  {s['strategy']:19s} profit {s['total_profit_gbp']:9.0f} GBP  {fills}")

    print("latency ms (p50, p95) /exceptions filtered:", _latency(client, "/api/v1/exceptions?channel=amazon&risk_type=OOS_RISK", h))
    print("latency ms (p50, p95) /items/{id}:", _latency(client, f"/api/v1/items/{top['item_id']}", h))
    print("latency ms (p50, p95) /summary:", _latency(client, "/api/v1/summary", h, n=30))
    print("audit chain:", client.get("/api/v1/audit/verify", headers=h).status_code, "(planner is not allowed: expected 403)")


if __name__ == "__main__":
    main()
