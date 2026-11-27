"""Cover policy lifecycle: bind, monitor sell-through, trigger rescue playbooks, settle, portfolio."""
from datetime import datetime, timedelta, timezone

import pytest

from app.cover.policies import (PolicyError, PolicyStore, assess, effective_status, new_policy, portfolio,
                                rescue_due, settle)
from tests.test_api import auth, env  # noqa: F401  (fixture reuse)

NOW = datetime(2026, 11, 27, 9, 0, tzinfo=timezone.utc)


def quote(**over):
    q = {"id": "q1", "offered": True, "valid_until": (NOW + timedelta(days=7)).isoformat(), "po_id": "PO-1",
         "sku": "MAT-BLUE", "item_id": None, "units": 1500, "covered_units": 1200, "excluded_units": 300,
         "deductible_units": 120, "payout_per_unit_eur": 3.2, "unit_landed_cost_eur": 8.0, "premium_eur": 400.0,
         "demand": {"mean_units": 1000}, "terms": {"horizon_days": 180}}
    q.update(over)
    return q


def policy(**over):
    p = new_policy(quote(), "danny", NOW, "DE", "rescue")
    p.update(over)
    return p


# ---------------------------------------------------------------- pure rules
def test_bind_rules():
    p = policy()
    assert p["status"] == "ACTIVE" and p["pilot"] is True
    assert p["ends_at"] == (NOW + timedelta(days=180)).isoformat()
    assert p["max_payout_eur"] == 3456.0  # (1200 - 120) x 3.2
    with pytest.raises(PolicyError, match="declined"):
        new_policy(quote(offered=False), "danny", NOW)
    with pytest.raises(PolicyError, match="expired"):
        new_policy(quote(), "danny", NOW + timedelta(days=8))
    with pytest.raises(PolicyError, match="threshold"):
        new_policy(quote(), "danny", NOW, rescue_gap_threshold=0.1)


def test_assessment_and_rescue_triggers():
    p = policy()
    half = assess(p, 300, NOW + timedelta(days=90))
    assert half == {"elapsed_days": 90.0, "days_to_end": 90.0, "expected_sold_to_date": 500.0,
                    "units_sold_to_date": 300, "gap": -0.4, "curve": "linear"}
    assert rescue_due(p, half, NOW + timedelta(days=90)) is None
    early = assess(p, 0, NOW + timedelta(days=10))
    assert "too early" in rescue_due(p, early, NOW + timedelta(days=10))
    fine = assess(p, 480, NOW + timedelta(days=90))
    assert "within tolerance" in rescue_due(p, fine, NOW + timedelta(days=90))
    assert "no rescue playbook" in rescue_due(policy(rescue_playbook_id=None), half, NOW + timedelta(days=90))
    cooled = policy(rescue_runs=[{"run_id": "r", "at": (NOW + timedelta(days=80)).isoformat()}])
    assert "waiting 30 days" in rescue_due(cooled, half, NOW + timedelta(days=90))
    assert rescue_due(cooled, half, NOW + timedelta(days=111)) is None
    assert "not active" in rescue_due(p, half, NOW + timedelta(days=181))
    assert effective_status(p, NOW + timedelta(days=181)) == "AWAITING_SETTLEMENT"


def test_settlement_applies_cap_and_excess():
    p = policy()
    with pytest.raises(PolicyError, match="settle after"):
        settle(p, 500, "root", NOW + timedelta(days=100))
    later = NOW + timedelta(days=181)
    with pytest.raises(PolicyError, match="between 0"):
        settle(p, 2000, "root", later)
    claim = settle(p, 700, "root", later)
    # 700 unsold: 300 above the cap are the seller's, 400 covered, 120 excess -> 280 paid x 3.2
    assert claim["covered_unsold_units"] == 400 and claim["paid_units"] == 280 and claim["payout_eur"] == 896.0
    assert p["status"] == "CLAIMED"
    with pytest.raises(PolicyError, match="already"):
        settle(p, 700, "root", later)
    small = policy()
    assert settle(small, 350, "root", later)["payout_eur"] == 0 and small["status"] == "CLOSED_NO_CLAIM"


def test_portfolio_figures():
    later = NOW + timedelta(days=181)
    claimed, clean, active = policy(), policy(), policy()
    settle(claimed, 700, "root", later)
    settle(clean, 100, "root", later)
    view = portfolio([claimed, clean, active], NOW + timedelta(days=30))
    assert view["by_status"] == {"CLAIMED": 1, "CLOSED_NO_CLAIM": 1, "ACTIVE": 1}
    assert view["premium_written_eur"] == 1200.0 and view["claims_paid_eur"] == 896.0
    assert view["loss_ratio"] == 1.12  # 896 / 800 premium on settled policies
    assert view["open_max_exposure_eur"] == 3456.0


# ---------------------------------------------------------------- end to end through the app
def test_policy_lifecycle_triggers_rescue_playbook(env):  # noqa: F811
    client, store_dir, _ = env
    planner, admin, auditor = auth(client), auth(client, "root"), auth(client, "ada")
    q = client.post("/api/v1/cover/quotes", headers=planner, json={
        "po_id": "PO-9", "sku": "MAT-BLUE", "units": 1000, "unit_landed_cost_eur": 8,
        "demand_mean_units": 1000, "demand_sd_units": 200}).json()
    client.post("/api/v1/workflows-templates/rescue-basic/install", headers=planner, json={"workflow_id": "rescue"})

    bind_url = f"/api/v1/cover/quotes/{q['id']}/bind"
    assert client.post(bind_url, headers=auditor, json={}).status_code == 403
    assert client.post(bind_url, headers=planner, json={"rescue_playbook_id": "missing"}).status_code == 422
    pol = client.post(bind_url, headers=planner, json={"marketplace": "DE", "rescue_playbook_id": "rescue"})
    assert pol.status_code == 201
    pol = pol.json()
    assert pol["effective_status"] == "ACTIVE" and pol["covered_units"] == 1000
    assert client.post(bind_url, headers=planner, json={}).status_code == 409  # one policy per quote

    start = datetime.fromisoformat(pol["starts_at"])
    obs = f"/api/v1/cover/policies/{pol['id']}/observations"
    early = client.post(obs, headers=planner, json={"as_of": (start + timedelta(days=10)).isoformat(),
                                                    "units_sold_to_date": 0, "units_on_hand": 1000}).json()
    assert early["rescue"]["started"] is False and "too early" in early["rescue"]["reason"]

    behind = client.post(obs, headers=planner, json={"as_of": (start + timedelta(days=60)).isoformat(),
                                                     "units_sold_to_date": 200, "units_on_hand": 800}).json()
    assert behind["assessment"]["gap"] == pytest.approx(-0.4, abs=0.01)
    assert behind["rescue"]["started"] is True and behind["rescue"]["status"] == "WAITING_APPROVAL"  # deep gap: transfer
    run_id = behind["rescue"]["run_id"]
    run = client.get(f"/api/v1/workflow-runs/{run_id}", headers=auditor).json()
    assert run["payload"]["policy_id"] == pol["id"] and run["started_by"] == f"cover-monitor:{pol['id']}"
    ledger = client.get(f"/api/v1/workflow-runs/{run_id}/decisions", headers=auditor).json()["decisions"]
    assert ledger[0]["actor"] == f"cover-monitor:{pol['id']}"

    key = client.post("/api/v1/admin/api-keys", headers=admin, json={"name": "erp", "role": "planner"}).json()["key"]
    via_key = client.post(obs, headers={"Authorization": f"Bearer {key}"},
                          json={"as_of": (start + timedelta(days=70)).isoformat(), "units_sold_to_date": 230,
                                "units_on_hand": 770})
    assert via_key.status_code == 201 and "waiting 30 days" in via_key.json()["rescue"]["reason"]
    assert client.post(f"/api/v1/cover/quotes/{q['id']}/bind", headers={"Authorization": f"Bearer {key}"},
                       json={}).status_code == 403  # keys cannot bind cover

    settle_url = f"/api/v1/cover/policies/{pol['id']}/settle"
    assert client.post(settle_url, headers=planner, json={"units_unsold": 300}).status_code == 403
    assert client.post(settle_url, headers=admin, json={"units_unsold": 300}).status_code == 409  # window open

    # Fast-forward: move the policy window into the past.
    store = PolicyStore(store_dir / "cover")
    stored = store.get("default", pol["id"])
    stored["starts_at"] = (NOW - timedelta(days=200)).isoformat()
    stored["ends_at"] = (NOW - timedelta(days=20)).isoformat()
    store.save("default", stored)
    assert client.get(f"/api/v1/cover/policies/{pol['id']}", headers=auditor).json()["effective_status"] == "AWAITING_SETTLEMENT"

    settled = client.post(settle_url, headers=admin, json={"units_unsold": 300}).json()
    assert settled["status"] == "CLAIMED"
    assert settled["claim"]["paid_units"] == 200 and settled["claim"]["payout_eur"] == 640.0  # (300 - 100 excess) x 3.2
    assert client.post(obs, headers=planner, json={"units_sold_to_date": 700, "units_on_hand": 300}).status_code == 409

    book = client.get("/api/v1/cover/portfolio", headers=auditor).json()
    assert book["by_status"] == {"CLAIMED": 1} and book["claims_paid_eur"] == 640.0 and book["rescue_runs"] == 1
    assert book["loss_ratio"] == round(640.0 / q["premium_eur"], 3)
    assert client.get("/api/v1/cover/policies/nope", headers=auditor).status_code == 404
