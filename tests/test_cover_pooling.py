"""Pooled cover experience across sharing tenants: the credibility complement for new or thin sellers."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.auth import UserStore
from app.api.main import create_app
from app.cover.policies import PolicyStore
from app.store.ledger import Ledger
from tests.test_api import SECRET, make_service
from tests.test_cover_policies import NOW

TENANTS = ("acme", "globex", "initech", "umbrella", "newco")
QUOTE = {"units": 1000, "unit_landed_cost_eur": 8, "demand_mean_units": 1000, "demand_sd_units": 250}


@pytest.fixture
def fleet(tmp_path):
    users = UserStore(tmp_path / "users.json", iterations=1000)
    for t in TENANTS:
        users.add_user(f"plan-{t}", "planner", "planner-passphrase", tenant=t)
        users.add_user(f"boss-{t}", "admin", "admin-passphrase!", tenant=t)
    app = create_app(service=make_service(), ledger=Ledger(tmp_path / "ledger.db"), users=users, secret=SECRET,
                     store_dir=tmp_path, token_minutes=5)
    client = TestClient(app)
    tokens = {}
    for t in TENANTS:
        for who, pw in (("plan", "planner-passphrase"), ("boss", "admin-passphrase!")):
            r = client.post("/api/v1/auth/login", json={"username": f"{who}-{t}", "password": pw})
            tokens[(who, t)] = {"Authorization": f"Bearer {r.json()['access_token']}"}
    return client, tmp_path, tokens


def run_claim(client, store_dir, tokens, tenant, unsold, recovery_price):
    """Quote, bind, fast-forward, settle with `unsold` units, then sell the lot at `recovery_price`/unit."""
    plan, boss = tokens[("plan", tenant)], tokens[("boss", tenant)]
    q = client.post("/api/v1/cover/quotes", headers=plan, json={**QUOTE, "po_id": f"PO-{tenant}"}).json()
    pol = client.post(f"/api/v1/cover/quotes/{q['id']}/bind", headers=plan, json={}).json()
    store = PolicyStore(store_dir / "cover")
    stored = store.get(tenant, pol["id"])
    stored["starts_at"], stored["ends_at"] = (NOW - timedelta(days=200)).isoformat(), (NOW - timedelta(days=20)).isoformat()
    store.save(tenant, stored)
    claim = client.post(f"/api/v1/cover/policies/{pol['id']}/settle", headers=boss, json={"units_unsold": unsold}).json()["claim"]
    if claim.get("lot_id"):
        client.post(f"/api/v1/cover/lots/{claim['lot_id']}/bids", headers=plan,
                    json={"price_per_unit_eur": recovery_price, "channel": "b2b", "bidder": "buyer"})
        client.post(f"/api/v1/cover/lots/{claim['lot_id']}/award", headers=boss)
    return q


def share(client, tokens, tenant, on=True):
    client.put("/api/v1/admin/workflows-settings", headers=tokens[("boss", tenant)], json={"share_benchmarks": on})


def test_new_seller_gets_pooled_price_only_when_sharing(fleet):
    client, store_dir, tokens = fleet
    for t in ("acme", "globex", "initech"):  # three sellers with bad batches, all sharing
        run_claim(client, store_dir, tokens, t, unsold=450, recovery_price=0.8)  # recovery 10% of cost
        share(client, tokens, t)
    run_claim(client, store_dir, tokens, "umbrella", unsold=450, recovery_price=4.0)  # not sharing: excluded

    newco = tokens[("plan", "newco")]
    private = client.post("/api/v1/cover/quotes", headers=newco, json={**QUOTE, "po_id": "N1"}).json()
    assert private["experience"]["complement_source"] == "neutral" and private["experience"]["factor"] == 1.0
    assert private["recovery"]["complement_source"] == "default" and private["terms"]["recovery_rate"] == 0.25

    share(client, tokens, "newco")  # give-to-get
    pooled = client.post("/api/v1/cover/quotes", headers=newco, json={**QUOTE, "po_id": "N2"}).json()
    exp, rec = pooled["experience"], pooled["recovery"]
    assert exp["complement_source"] == "pooled" and exp["pooled_tenants"] == 3
    assert exp["policies"] == 0 and exp["factor"] == exp["complement"] > 1.0  # no own history: pooled A/E
    assert rec["complement_source"] == "pooled" and rec["pooled_tenants"] == 3
    assert rec["rate"] == pytest.approx(0.1, abs=1e-4)  # umbrella's 50% recovery is not in the pool
    assert pooled["premium_eur"] > private["premium_eur"]
    book = client.get("/api/v1/cover/portfolio", headers=newco).json()
    assert book["experience"]["factor"] == exp["factor"] and book["recovery"]["rate"] == rec["rate"]


def test_pool_needs_minimum_group(fleet):
    client, store_dir, tokens = fleet
    for t in ("acme", "globex"):  # only two sharing contributors
        run_claim(client, store_dir, tokens, t, unsold=450, recovery_price=0.8)
        share(client, tokens, t)
    share(client, tokens, "newco")
    q = client.post("/api/v1/cover/quotes", headers=tokens[("plan", "newco")], json={**QUOTE, "po_id": "N3"}).json()
    assert q["experience"]["complement_source"] == "neutral" and q["recovery"]["complement_source"] == "default"


def test_own_history_still_dominates_with_credibility(fleet):
    client, store_dir, tokens = fleet
    for t in ("acme", "globex", "initech"):
        run_claim(client, store_dir, tokens, t, unsold=450, recovery_price=0.8)
        share(client, tokens, t)
    q = client.post("/api/v1/cover/quotes", headers=tokens[("plan", "acme")], json={**QUOTE, "po_id": "A2"}).json()
    exp = q["experience"]
    # acme has 1 own policy (credibility ~18%) and the pool (which includes acme) as complement
    assert exp["policies"] == 1 and exp["complement_source"] == "pooled"
    blended = exp["credibility"] * exp["a_to_e"] + (1 - exp["credibility"]) * exp["complement"]
    assert exp["factor"] == pytest.approx(min(2.0, blended), abs=2e-3)  # capped at 2.0
