"""Recovery marketplace: lots from paid claims, bids, award, and the learned recovery rate in pricing."""
from datetime import timedelta

import pytest

from app.cover.policies import PolicyStore
from app.cover.recovery import LotError, add_bid, award, best_bid, new_lot, recovery_experience
from tests.test_api import auth, env  # noqa: F401  (fixture reuse)
from tests.test_cover_policies import NOW, policy


def claimed(paid=200):
    p = policy()
    p["claim"] = {"paid_units": paid}
    return p


# ---------------------------------------------------------------- rules
def test_lot_bids_and_award():
    lot = new_lot(claimed(), NOW)
    assert lot["units"] == 200 and lot["cost_value_eur"] == 1600.0 and lot["reserve_price_per_unit_eur"] == 0.8
    with pytest.raises(LotError, match="no bids"):
        award(lot, "root", NOW)
    add_bid(lot, "outlet-co", 0.5, "outlet", NOW)
    with pytest.raises(LotError, match="below the reserve"):
        award(lot, "root", NOW)
    add_bid(lot, "b2b-a", 2.0, "b2b", NOW + timedelta(minutes=1))
    add_bid(lot, "b2b-b", 2.0, "b2b", NOW + timedelta(minutes=2))  # tie: earliest wins
    add_bid(lot, "charity", 0, "donation", NOW)
    assert best_bid(lot)["bidder"] == "b2b-a"
    result = award(lot, "root", NOW)
    assert lot["status"] == "SOLD" and result["proceeds_eur"] == 400.0 and result["recovery_rate"] == 0.25
    with pytest.raises(LotError, match="SOLD"):
        add_bid(lot, "late", 3, "b2b", NOW)


def test_donation_only_lot_and_validation():
    lot = new_lot(claimed(), NOW)
    with pytest.raises(LotError, match="free"):
        add_bid(lot, "charity", 1.0, "donation", NOW)
    with pytest.raises(LotError, match="channel"):
        add_bid(lot, "x", 1.0, "landfill", NOW)
    add_bid(lot, "charity", 0, "donation", NOW)
    assert award(lot, "root", NOW)["proceeds_eur"] == 0 and lot["status"] == "DONATED"
    with pytest.raises(LotError, match="no paid units"):
        new_lot(claimed(paid=0), NOW)


def test_recovery_experience_blends_towards_observed_rate():
    assert recovery_experience([], 0.25)["rate"] == 0.25
    lots = []
    for _ in range(4):
        lot = new_lot(claimed(), NOW)
        add_bid(lot, "b", 0.8, "b2b", NOW)  # 10% recovery
        award(lot, "root", NOW)
        lots.append(lot)
    exp = recovery_experience(lots + [new_lot(claimed(), NOW)], 0.25)  # open lot ignored
    z = (4 / 30) ** 0.5
    assert exp["lots"] == 4 and exp["observed_rate"] == 0.1
    assert exp["rate"] == pytest.approx(z * 0.1 + (1 - z) * 0.25, abs=1e-4)


# ---------------------------------------------------------------- end to end
def test_claim_lot_bids_award_and_pricing_feedback(env):  # noqa: F811
    client, store_dir, _ = env
    planner, admin = auth(client), auth(client, "root")
    body = {"po_id": "PO-1", "units": 1000, "unit_landed_cost_eur": 8, "demand_mean_units": 1000, "demand_sd_units": 250}
    q1 = client.post("/api/v1/cover/quotes", headers=planner, json=body).json()
    assert q1["recovery"]["rate"] == 0.25 and q1["terms"]["recovery_rate"] == 0.25
    pol = client.post(f"/api/v1/cover/quotes/{q1['id']}/bind", headers=planner, json={}).json()
    store = PolicyStore(store_dir / "cover")
    stored = store.get("default", pol["id"])
    stored["starts_at"], stored["ends_at"] = (NOW - timedelta(days=200)).isoformat(), (NOW - timedelta(days=20)).isoformat()
    store.save("default", stored)
    settled = client.post(f"/api/v1/cover/policies/{pol['id']}/settle", headers=admin, json={"units_unsold": 400}).json()
    lot_id = settled["claim"]["lot_id"]
    lot = client.get(f"/api/v1/cover/lots/{lot_id}", headers=planner).json()
    assert lot["status"] == "OPEN" and lot["units"] == settled["claim"]["paid_units"] and lot["best_bid"] is None

    key = client.post("/api/v1/admin/api-keys", headers=admin, json={"name": "liquidator", "role": "planner"}).json()["key"]
    buyer = {"Authorization": f"Bearer {key}"}
    bid = client.post(f"/api/v1/cover/lots/{lot_id}/bids", headers=buyer,
                      json={"price_per_unit_eur": 0.4, "channel": "b2b", "bidder": "someone-else"}).json()
    assert bid["bid"]["bidder"] == "apikey:liquidator"  # buyer systems bid as themselves
    client.post(f"/api/v1/cover/lots/{lot_id}/bids", headers=planner,
                json={"price_per_unit_eur": 1.2, "channel": "outlet", "bidder": "Outlet GmbH"})
    assert client.post(f"/api/v1/cover/lots/{lot_id}/bids", headers=planner,
                       json={"price_per_unit_eur": 1, "channel": "landfill"}).status_code == 422
    assert client.post(f"/api/v1/cover/lots/{lot_id}/award", headers=planner).status_code == 403
    assert client.post(f"/api/v1/cover/lots/{lot_id}/award", headers=buyer).status_code == 403

    won = client.post(f"/api/v1/cover/lots/{lot_id}/award", headers=admin).json()
    assert won["status"] == "SOLD" and won["award"]["bidder"] == "Outlet GmbH" and won["award"]["recovery_rate"] == 0.15
    assert client.post(f"/api/v1/cover/lots/{lot_id}/award", headers=admin).status_code == 409

    book = client.get("/api/v1/cover/portfolio", headers=planner).json()
    proceeds = won["award"]["proceeds_eur"]
    assert book["recovered_eur"] == proceeds
    assert book["net_claims_eur"] == round(book["claims_paid_eur"] - proceeds, 2)

    q2 = client.post("/api/v1/cover/quotes", headers=planner, json={**body, "po_id": "PO-2"}).json()
    assert q2["recovery"]["lots"] == 1 and q2["recovery"]["observed_rate"] == 0.15
    assert q2["terms"]["recovery_rate"] == q2["recovery"]["rate"] < 0.25  # learned: resale earns less than assumed
    admin_q = client.post("/api/v1/cover/quotes", headers=admin,
                          json={**body, "po_id": "PO-3", "terms": {"recovery_rate": 0.3}}).json()
    assert admin_q["terms"]["recovery_rate"] == 0.3  # explicit admin terms win
