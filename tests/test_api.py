import json

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.api.auth import UserStore
from app.api.main import create_app
from app.api.service import DataService
from app.narrative.narrator import Narrator
from app.store.ledger import Ledger

SECRET = "s" * 48
CUTOFF = "2026-02-01"
M1 = "2026-03-01"
T = pd.Timestamp


def _item(item_id, **kw):
    base = dict(
        item_id=item_id, product_id=f"PROD_{item_id}", channel="amazon", abc_category="A", abc_derived=False,
        tier="Collaborative Exception", state="EXCEPTION_REVIEW", requires_review=True, in_scope=True,
        risk_type="OOS_RISK", risk_flags="OOS_SURGE", narrative="n", sales_price=49.99, source="ml",
        phase="MATURE", months_since_launch=40.0, months_to_eol=np.nan,
        baseline_h=620.0, final_h=880.0, baseline_m1=620.0, final_m1=880.0, ml_m1=880.0,
        value_delta_gbp=12997.4, var_gbp=12997.4, final_12m=5000.0,
        glance_mom_pct=0.34, p70_gap_pct=0.1, on_hand=182.0, open_po=0.0, oos_rate_28d=0.01, weeks_of_cover=3.0,
        amazon_deal_active=False, b2b_campaign_active=False, purchase_price=6.0,
    )
    base.update(kw)
    return base


def make_service(with_actuals=False):
    triage = pd.DataFrame([
        _item(1),
        _item(2, channel="B2B wo Amazon", risk_type="OVERSTOCK_RISK", risk_flags="GHOST_PROMO", sales_price=24.5,
              baseline_h=850.0, final_h=340.0, baseline_m1=850.0, final_m1=340.0, ml_m1=340.0,
              value_delta_gbp=-12495.0, var_gbp=12495.0),
        _item(3, state="AUTO_APPROVED", tier="Touchless Autopilot", requires_review=False, risk_type="OOS_RISK",
              risk_flags="", baseline_h=100.0, final_h=100.0, baseline_m1=100.0, final_m1=100.0, ml_m1=100.0,
              value_delta_gbp=0.0, var_gbp=0.0, sales_price=10.0),
        _item(4, state="PLANNER_LED", tier="Planner-Led", requires_review=False, phase="NPI", months_since_launch=2.0,
              risk_flags="", value_delta_gbp=50.0, var_gbp=50.0),
        _item(5, state="INACTIVE", tier="Inactive", requires_review=False, in_scope=False, risk_flags="",
              value_delta_gbp=0.0, var_gbp=0.0),
        # sister channel items of PROD_1 (kept inactive so queue/summary counts are unchanged)
        _item(6, product_id="PROD_1", channel="D2C", state="INACTIVE", tier="Inactive", requires_review=False,
              in_scope=False, risk_flags="", value_delta_gbp=0.0, var_gbp=0.0, sales_price=20.0, purchase_price=6.0,
              baseline_h=300.0, final_h=300.0, baseline_m1=300.0, final_m1=300.0, ml_m1=300.0),
        _item(7, product_id="PROD_1", channel="B2B wo Amazon", state="INACTIVE", tier="Inactive", requires_review=False,
              in_scope=False, risk_flags="", value_delta_gbp=0.0, var_gbp=0.0, sales_price=10.0, purchase_price=6.0,
              baseline_h=600.0, final_h=600.0, baseline_m1=600.0, final_m1=600.0, ml_m1=600.0),
    ])
    rows = []
    for _, r in triage.iterrows():
        for k, m in enumerate(pd.date_range(M1, periods=3, freq="MS")):
            rows.append(dict(item_id=r.item_id, date=m, final_qty=r.final_m1 + k, ml_prediction=r.ml_m1 + k,
                             source="ml", baseline_qty=r.baseline_m1 + k))
    monthly = pd.DataFrame(rows)
    act = []
    for i in range(1, 8):
        for m in pd.date_range("2025-03-01", periods=12, freq="MS"):
            act.append(dict(item_id=i, date=m, target=100.0))
    if with_actuals:
        act.append(dict(item_id=1, date=T(M1), target=850.0))
        act.append(dict(item_id=2, date=T(M1), target=340.0))
    meta = {"cutoff": CUTOFF, "first_month": M1, "var_months": 1, "var_threshold_gbp": 5000.0, "var_low_gbp": 2000.0,
            "generated_at": "2026-03-01T00:00:00+00:00",
            "quality_warnings": [{"channel": "amazon", "month": CUTOFF, "imputed_share": 1.0, "items": 3080}]}
    profile = pd.DataFrame({
        "channel": ["amazon", "B2B wo Amazon", "D2C"], "p0": [0.7, 0.65, 1.0], "p1": [0.3, 0.34, 0.0], "p2plus": [0.0, 0.01, 0.0],
    })
    phasing = pd.DataFrame({
        "item_id": [2, 2], "month": [T(M1), T("2026-04-01")],
        "booked_qty": [500.0, 80.0], "booked_shifted_qty": [100.0, 0.0],
        "carry_in_qty": [40.0, 0.0], "shifted_in_qty": [0.0, 100.0],
    })
    return DataService(triage, monthly, pd.DataFrame(act), meta, phasing_items=phasing, phasing_profile=profile)


@pytest.fixture
def env(tmp_path):
    users = UserStore(tmp_path / "users.json", iterations=1000)
    users.add_user("danny", "planner", "planner-passphrase")
    users.add_user("root", "admin", "admin-passphrase!")
    users.add_user("ada", "auditor", "auditor-passphrase")
    holder = {"svc": make_service()}
    app = create_app(
        service=holder["svc"], ledger=Ledger(tmp_path / "ledger.db"), users=users, secret=SECRET,
        store_dir=tmp_path, cors_origins=("http://localhost:5173",), token_minutes=5,
        reload_fn=lambda: holder["svc"],
    )
    return TestClient(app), tmp_path, holder


def auth(client, user="danny", pw=None):
    pw = pw or {"danny": "planner-passphrase", "root": "admin-passphrase!", "ada": "auditor-passphrase"}[user]
    r = client.post("/api/v1/auth/login", json={"username": user, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def body(item_id=1, qty=850.0, reason="Commercial Intelligence (Account Feedback)", **kw):
    d = {"item_id": item_id, "override_quantity": qty, "reason_code": reason, "comment": "buyer delay"}
    d.update(kw)
    return d


# ---------------------------------------------------------------- authentication
def test_health_is_public_but_data_needs_a_token(env):
    client, _, _ = env
    assert client.get("/api/v1/health").status_code == 200
    for path in ("/api/v1/summary", "/api/v1/exceptions", "/api/v1/items/1", "/api/v1/overrides", "/api/v1/fva"):
        assert client.get(path).status_code == 401
    assert client.get("/api/v1/summary", headers={"Authorization": "Bearer nonsense"}).status_code == 401


def test_login_failure_is_generic_and_locks_after_repeated_failures(env):
    client, _, _ = env
    for _ in range(5):
        r = client.post("/api/v1/auth/login", json={"username": "danny", "password": "wrong-password"})
        assert r.status_code == 401 and r.json()["detail"] == "Invalid credentials"
    locked = client.post("/api/v1/auth/login", json={"username": "danny", "password": "planner-passphrase"})
    assert locked.status_code == 429


# ---------------------------------------------------------------- queue and detail
def test_exceptions_are_sorted_by_value_and_default_to_review_items(env):
    client, _, _ = env
    r = client.get("/api/v1/exceptions", headers=auth(client))
    data = r.json()
    assert [i["item_id"] for i in data["items"]] == [1, 2] and data["total"] == 2
    first = data["items"][0]
    for key in ("channel", "abc_category", "baseline_forecast", "ml_forecast", "current_final", "value_delta_gbp",
                "risk_type", "narrative", "has_override"):
        assert key in first
    assert first["has_override"] is False and first["current_final"] == 880.0


def test_exception_filters_state_and_pagination(env):
    client, _, _ = env
    h = auth(client)
    assert [i["item_id"] for i in client.get("/api/v1/exceptions?channel=B2B wo Amazon", headers=h).json()["items"]] == [2]
    assert [i["item_id"] for i in client.get("/api/v1/exceptions?state=PLANNER_LED", headers=h).json()["items"]] == [4]
    page = client.get("/api/v1/exceptions?limit=1&offset=1", headers=h).json()
    assert page["total"] == 2 and [i["item_id"] for i in page["items"]] == [2]
    assert client.get("/api/v1/exceptions?limit=100000", headers=h).status_code == 422


def test_item_detail_contains_plan_history_and_telemetry(env):
    client, _, _ = env
    d = client.get("/api/v1/items/1", headers=auth(client)).json()
    assert d["item"]["item_id"] == 1 and d["item"]["narrative"] == "n"
    assert len(d["plan"]) == 3 and d["plan"][0]["month"] == M1 and d["plan"][0]["final"] == 880.0
    assert len(d["history"]) == 12
    assert d["telemetry"]["glance_mom_pct"] == pytest.approx(0.34) and d["telemetry"]["on_hand"] == 182.0
    assert d["decision"] is None
    assert client.get("/api/v1/items/999", headers=auth(client)).status_code == 404


def test_summary_reports_queue_progress_and_quality_warnings(env):
    client, _, _ = env
    h = auth(client)
    s = client.get("/api/v1/summary", headers=h).json()
    assert s["exceptions_requiring_review"] == 2 and s["pending_decisions"] == 2 and s["decided"] == 0
    assert s["autopilot_approved_skus"] == 1 and s["planner_led_skus"] == 1
    assert s["quality_warnings"][0]["channel"] == "amazon"
    assert s["fva"] is None and s["committed"] is False
    client.post("/api/v1/override", json=body(), headers=h)
    s2 = client.get("/api/v1/summary", headers=h).json()
    assert s2["pending_decisions"] == 1 and s2["decided"] == 1


def test_reason_codes_endpoint_separates_the_accept_code(env):
    client, _, _ = env
    r = client.get("/api/v1/reason-codes", headers=auth(client)).json()
    assert r["accept"] == "ML Output Formally Accepted" and r["accept"] not in r["codes"]
    assert "Retailer Promotion Timing Shift" in r["codes"]
    assert client.get("/api/v1/reason-codes").status_code == 401


# ---------------------------------------------------------------- overrides
def test_override_is_audited_with_identity_from_the_token(env):
    client, _, _ = env
    h = auth(client)
    r = client.post("/api/v1/override", json=body(planner_id="someone_else"), headers={**h, "X-Session-Id": "sess_1"})
    assert r.status_code == 200, r.text
    rec = r.json()
    assert rec["planner_id"] == "danny" and rec["variance_to_ml"] == -30.0
    assert rec["financial_impact_gbp"] == pytest.approx(-30.0 * 49.99)
    assert rec["baseline_quantity"] == 620.0 and rec["ml_recommended_quantity"] == 880.0 and rec["session_id"] == "sess_1"
    ledger = client.get("/api/v1/overrides?item_id=1", headers=h).json()
    assert len(ledger) == 1 and ledger[0]["reason_code"].startswith("Commercial")
    ex = client.get("/api/v1/exceptions", headers=h).json()["items"][0]
    assert ex["has_override"] and ex["current_final"] == 850.0


def test_override_without_reason_code_is_blocked(env):
    client, _, _ = env
    h = auth(client)
    for payload in (body(reason=None), body(reason=""), body(reason="   ")):
        r = client.post("/api/v1/override", json=payload, headers=h)
        assert r.status_code == 422 and r.json()["detail"] == "Reason code is required."
    no_field = {"item_id": 1, "override_quantity": 850.0}
    assert client.post("/api/v1/override", json=no_field, headers=h).json()["detail"] == "Reason code is required."
    assert client.get("/api/v1/overrides", headers=h).json() == []


def test_override_validation_rules(env):
    client, _, _ = env
    h = auth(client)
    assert client.post("/api/v1/override", json=body(reason="Because I said so"), headers=h).status_code == 422
    assert client.post("/api/v1/override", json=body(qty=-1), headers=h).status_code == 422
    assert client.post("/api/v1/override", json=body(item_id=5), headers=h).status_code == 422  # inactive
    assert client.post("/api/v1/override", json=body(item_id=999), headers=h).status_code == 404
    assert client.post("/api/v1/override", json=body(cutoff="2025-01-01"), headers=h).status_code == 422
    assert client.post("/api/v1/override", json=body(target_month="2030-01-01"), headers=h).status_code == 422
    # touchless item: a tweak below 5% is suppressed
    small = client.post("/api/v1/override", json=body(item_id=3, qty=103.0), headers=h)
    assert small.status_code == 422 and "5%" in small.json()["detail"]
    assert client.post("/api/v1/override", json=body(item_id=3, qty=150.0), headers=h).status_code == 200


def test_accepting_the_ml_number_has_its_own_rules(env):
    client, _, _ = env
    h = auth(client)
    accept = "ML Output Formally Accepted"
    assert client.post("/api/v1/override", json=body(qty=700.0, reason=accept), headers=h).status_code == 422
    assert client.post("/api/v1/override", json=body(qty=880.0), headers=h).status_code == 422
    ok = client.post("/api/v1/override", json=body(qty=880.0, reason=accept), headers=h)
    assert ok.status_code == 200 and ok.json()["variance_to_ml"] == 0.0


def test_roles_are_enforced(env):
    client, _, _ = env
    auditor, planner, admin = auth(client, "ada"), auth(client, "danny"), auth(client, "root")
    assert client.post("/api/v1/override", json=body(), headers=auditor).status_code == 403
    assert client.get("/api/v1/exceptions", headers=auditor).status_code == 200
    assert client.post("/api/v1/admin/reload", headers=planner).status_code == 403
    assert client.post("/api/v1/admin/reload", headers=admin).status_code == 200
    assert client.get("/api/v1/audit/verify", headers=planner).status_code == 403
    v = client.get("/api/v1/audit/verify", headers=auditor).json()
    assert v == {"ok": True, "first_bad_id": None}


def test_ledger_cannot_be_modified_through_the_api(env):
    client, _, _ = env
    h = auth(client)
    client.post("/api/v1/override", json=body(), headers=h)
    assert client.delete("/api/v1/overrides/1", headers=h).status_code in (404, 405)
    assert client.put("/api/v1/overrides/1", json=body(), headers=h).status_code in (404, 405)
    assert client.patch("/api/v1/overrides/1", json=body(), headers=h).status_code in (404, 405)


# ---------------------------------------------------------------- commit
def test_commit_requires_every_exception_to_be_decided_and_happens_once(env):
    client, tmp, _ = env
    h = auth(client)
    blocked = client.post(f"/api/v1/cycles/{CUTOFF}/commit", headers=h)
    assert blocked.status_code == 409 and "2 exception" in blocked.json()["detail"]
    client.post("/api/v1/override", json=body(item_id=1, qty=850.0), headers=h)
    client.post("/api/v1/override", json=body(item_id=2, qty=340.0, reason="ML Output Formally Accepted"), headers=h)
    ok = client.post(f"/api/v1/cycles/{CUTOFF}/commit", headers=h)
    assert ok.status_code == 200, ok.text
    out = pd.read_csv(ok.json()["path"])
    row = out[(out.item_id == 1) & (out.month == M1)].iloc[0]
    assert row.consensus_qty == 850.0 and row.ml_qty == 880.0 and row.decided_by == "danny"
    later = out[(out.item_id == 1) & (out.month == "2026-04-01")].iloc[0]
    assert later.consensus_qty == 881.0  # months without a decision keep the ML plan
    assert 5 not in set(out.item_id)  # inactive items are not exported
    assert client.post(f"/api/v1/cycles/{CUTOFF}/commit", headers=h).status_code == 409
    assert client.post("/api/v1/override", json=body(item_id=3, qty=150.0), headers=h).status_code == 409
    assert client.get("/api/v1/summary", headers=h).json()["committed"] is True


def test_commit_is_for_planners_only(env):
    client, _, _ = env
    assert client.post(f"/api/v1/cycles/{CUTOFF}/commit", headers=auth(client, "ada")).status_code == 403
    assert client.post("/api/v1/cycles/2020-01-01/commit", headers=auth(client)).status_code == 404


def test_consensus_export_streams_csv(env):
    client, _, _ = env
    h = auth(client)
    client.post("/api/v1/override", json=body(), headers=h)
    r = client.get("/api/v1/export/consensus", headers=h)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "consensus_qty" in r.text.splitlines()[0]


# ---------------------------------------------------------------- FVA
def test_fva_is_empty_until_actuals_exist_then_scores_overrides(env):
    client, tmp, holder = env
    h = auth(client)
    client.post("/api/v1/override", json=body(item_id=1, qty=850.0), headers=h)
    empty = client.get("/api/v1/fva", headers=h).json()
    assert empty["matured_overrides"] == 0 and empty["rows"] == []
    holder["svc"] = make_service(with_actuals=True)
    assert client.post("/api/v1/admin/reload", headers=auth(client, "root")).status_code == 200
    scored = client.get("/api/v1/fva", headers=h).json()
    assert scored["matured_overrides"] == 1
    row = scored["rows"][0]
    assert row["planner_id"] == "danny" and row["fva_pts"] == pytest.approx(100 * (30 - 0) / 850)


# ---------------------------------------------------------------- order-book phasing
def test_item_detail_has_the_phasing_audit(env):
    client, _, _ = env
    h = auth(client)
    ph = client.get("/api/v1/items/2", headers=h).json()["phasing"]
    assert [p["month"] for p in ph] == [M1, "2026-04-01", "2026-05-01"]
    m1, m2 = ph[0], ph[1]
    assert (m1["booked"], m1["booked_shifted"], m1["carry_in"]) == (500.0, 100.0, 40.0)
    assert m1["demand"] == 340.0 and m1["expected_delivery"] == pytest.approx(40 + 0.65 * 340)
    assert m2["shifted_in"] == 100.0
    assert m2["expected_delivery"] == pytest.approx(0.65 * 341 + 0.34 * 340)
    # a floor suggestion appears when bookings exceed the plan
    assert m1["order_book_floor"] == 500.0 and ph[1]["order_book_floor"] is None


def test_expected_deliveries_follow_the_planner_decision(env):
    client, _, _ = env
    h = auth(client)
    client.post("/api/v1/override", json=body(item_id=2, qty=500.0, reason="Order Book Floor Applied"), headers=h)
    ph = client.get("/api/v1/items/2", headers=h).json()["phasing"]
    assert ph[0]["demand"] == 500.0 and ph[0]["expected_delivery"] == pytest.approx(40 + 0.65 * 500)
    assert ph[1]["expected_delivery"] == pytest.approx(0.65 * 341 + 0.34 * 500)


def test_phasing_summary_by_month_and_channel(env):
    client, _, _ = env
    out = client.get("/api/v1/phasing/summary", headers=auth(client)).json()
    rows = {(r["month"], r["channel"]): r for r in out["rows"]}
    b2b = rows[(M1, "B2B wo Amazon")]
    # in-scope B2B items: item 2 only (item 7 is inactive)
    assert b2b["demand"] == 340.0 and b2b["expected_delivery"] == pytest.approx(40 + 0.65 * 340)
    assert b2b["booked"] == 500.0
    amazon = rows[(M1, "amazon")]
    assert amazon["expected_delivery"] == pytest.approx(0.7 * amazon["demand"])
    assert out["profile"]["B2B wo Amazon"]["p1"] == 0.34
    assert client.get("/api/v1/phasing/summary").status_code == 401


def test_consensus_export_includes_expected_deliveries(env):
    client, _, _ = env
    csv = client.get("/api/v1/export/consensus", headers=auth(client)).text.splitlines()
    assert "expected_delivery_qty" in csv[0].split(",")


# ---------------------------------------------------------------- narratives
class EchoClient:
    """Stands in for Gemini: writes a valid narrative from the telemetry it receives."""

    def __init__(self):
        self.calls = []

    def generate(self, system, user):
        t = json.loads(user)
        self.calls.append(t)
        return (f"The plan is {t['plan']['plan_units']} units against a baseline of {t['plan']['baseline_units']}. "
                f"The order book already holds {t['order_book']['booked_units']} units.")


def test_narrative_needs_a_token_and_a_known_item(env):
    client, _, _ = env
    assert client.post("/api/v1/items/2/narrative").status_code == 401
    assert client.post("/api/v1/items/999999/narrative", headers=auth(client)).status_code == 404


def test_narrative_without_a_configured_llm_returns_the_deterministic_text(env):
    client, _, _ = env
    h = auth(client)
    item = client.get("/api/v1/items/2", headers=h).json()["item"]
    out = client.post("/api/v1/items/2/narrative", headers=h).json()
    assert out["source"] == "template" and out["text"] == item["narrative"]
    assert out["reasons"] == ["llm_not_configured"] and out["enabled"] is False


def test_narrative_uses_the_llm_caches_it_and_sends_only_telemetry(env):
    client, tmp, _ = env
    echo = EchoClient()
    client.app.state.narrator = Narrator(echo, tmp / "narr.db", model="fake")
    h = auth(client)
    first = client.post("/api/v1/items/2/narrative", headers=h).json()
    assert first["source"] == "llm" and first["cached"] is False and first["enabled"] is True
    assert "500 units" in first["text"]
    again = client.post("/api/v1/items/2/narrative", headers=auth(client, "ada")).json()  # auditors may read
    assert again["cached"] is True and len(echo.calls) == 1
    sent = json.dumps(echo.calls[0])
    assert "narrative" not in sent and "PROD_" not in sent and "item_id" not in sent


def test_a_planner_decision_changes_the_telemetry_and_so_the_narrative(env):
    client, tmp, _ = env
    echo = EchoClient()
    client.app.state.narrator = Narrator(echo, tmp / "narr.db", model="fake")
    h = auth(client)
    client.post("/api/v1/items/2/narrative", headers=h)
    client.post("/api/v1/override", json=body(item_id=2, qty=500.0, reason="Order Book Floor Applied"), headers=h)
    out = client.post("/api/v1/items/2/narrative", headers=h).json()
    assert len(echo.calls) == 2 and "plan is 500 units" in out["text"]


# ---------------------------------------------------------------- allocation
def test_allocation_simulation_ringfences_d2c_and_reports_the_trade_off(env):
    client, _, _ = env
    h = auth(client)
    r = client.post("/api/v1/allocation/simulate", headers=h, json={"product_id": "PROD_1", "available_units": 1200})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["month"] == M1 and out["total_demand"] == pytest.approx(880 + 300 + 600) and out["shortage"] == pytest.approx(580)
    opt = next(s for s in out["scenarios"] if s["strategy"] == "margin_optimal")
    ch = {c["channel"]: c for c in opt["channels"]}
    assert ch["D2C"]["allocated"] == pytest.approx(300.0)  # highest margin, fully served
    assert ch["amazon"]["allocated"] >= 0.6 * 880 - 1e-6  # confirmation floor
    assert sum(c["allocated"] for c in ch.values()) == pytest.approx(1200.0)
    assert all(s["profit_vs_best_gbp"] <= 1e-9 for s in out["scenarios"])
    assert out["assumptions"]["amazon_floor_pct"] == 0.6


def test_allocation_validation_and_access(env):
    client, _, _ = env
    h = auth(client)
    assert client.post("/api/v1/allocation/simulate", headers=h, json={"product_id": "NOPE", "available_units": 10}).status_code == 404
    assert client.post("/api/v1/allocation/simulate", headers=h, json={"product_id": "PROD_1", "available_units": -5}).status_code == 422
    assert client.post("/api/v1/allocation/simulate", headers=h, json={"product_id": "PROD_1", "available_units": 5, "amazon_floor_pct": 2}).status_code == 422
    assert client.post("/api/v1/allocation/simulate", json={"product_id": "PROD_1", "available_units": 5}).status_code == 401
    assert client.post("/api/v1/allocation/simulate", headers=auth(client, "ada"), json={"product_id": "PROD_1", "available_units": 5}).status_code == 200


# ---------------------------------------------------------------- CORS
def test_cors_allows_only_configured_origins(env):
    client, _, _ = env
    ok = client.options("/api/v1/summary", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = client.options("/api/v1/summary", headers={
        "Origin": "http://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in bad.headers
