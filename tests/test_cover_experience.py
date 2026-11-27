"""Experience rating: settled policies feed back into the next quote's price."""
import math
from datetime import timedelta

import pytest

from app.cover.experience import FULL_CREDIBILITY_POLICIES, experience
from app.cover.policies import PolicyStore
from app.cover.quote import Demand, quote
from tests.test_api import auth, env  # noqa: F401  (fixture reuse)
from tests.test_cover_policies import NOW


def settled(expected, paid, status="CLAIMED"):
    return {"status": status, "expected_paid_units": expected, "claim": {"paid_units": paid}}


def test_no_history_means_neutral_factor():
    assert experience([]) == {"policies": 0, "expected_paid_units": 0, "actual_paid_units": 0,
                              "complement": 1.0, "complement_source": "neutral", "pooled_tenants": None,
                              "a_to_e": None, "credibility": 0.0, "factor": 1.0}
    active = {"status": "ACTIVE", "expected_paid_units": 50, "claim": None}
    legacy = {"status": "CLAIMED", "claim": {"paid_units": 400}}  # bound before expectations were stored
    assert experience([active, legacy])["policies"] == 0


def test_credibility_weighting():
    few_bad = experience([settled(100, 200) for _ in range(4)])
    z = math.sqrt(4 / FULL_CREDIBILITY_POLICIES)
    assert few_bad["a_to_e"] == 2.0 and few_bad["credibility"] == pytest.approx(z, abs=1e-3)
    assert few_bad["factor"] == pytest.approx(z * 2 + (1 - z), abs=1e-3)  # ~1.37, not 2.0 after 4 policies

    many_good = experience([settled(100, 50, "CLOSED_NO_CLAIM" if i % 2 else "CLAIMED") for i in range(30)])
    assert many_good["credibility"] == 1.0 and many_good["factor"] == 0.5

    extreme = experience([settled(10, 100) for _ in range(40)])
    assert extreme["a_to_e"] == 10.0 and extreme["factor"] == 2.0  # capped


def test_factor_scales_the_rated_loss():
    base = quote(1000, 8.0, Demand(1000, 250))
    rated = quote(1000, 8.0, Demand(1000, 250), experience={"policies": 12, "a_to_e": 1.8, "credibility": 0.63,
                                                             "factor": 1.5})
    assert rated["expected_loss_eur"] == base["expected_loss_eur"]  # the model's view is unchanged
    assert rated["rated_loss_eur"] == pytest.approx(1.5 * base["expected_loss_eur"], abs=0.02)
    assert rated["premium_eur"] == pytest.approx(rated["rated_loss_eur"] * 1.35 + 25, abs=0.05)
    assert rated["experience"]["factor"] == 1.5 and base["experience"]["factor"] == 1.0


def test_settled_losses_raise_the_next_price(env):  # noqa: F811
    client, store_dir, _ = env
    planner, admin = auth(client), auth(client, "root")
    body = {"po_id": "PO-1", "units": 1000, "unit_landed_cost_eur": 8, "demand_mean_units": 1000,
            "demand_sd_units": 250}
    first = client.post("/api/v1/cover/quotes", headers=planner, json=body).json()
    assert first["experience"]["policies"] == 0
    pol = client.post(f"/api/v1/cover/quotes/{first['id']}/bind", headers=planner, json={}).json()
    assert pol["expected_paid_units"] == first["expected_paid_units"]

    store = PolicyStore(store_dir / "cover")
    stored = store.get("default", pol["id"])
    stored["starts_at"], stored["ends_at"] = (NOW - timedelta(days=200)).isoformat(), (NOW - timedelta(days=20)).isoformat()
    store.save("default", stored)
    settled_pol = client.post(f"/api/v1/cover/policies/{pol['id']}/settle", headers=admin, json={"units_unsold": 500}).json()
    paid = settled_pol["claim"]["paid_units"]
    assert paid > first["expected_paid_units"]  # a much worse batch than priced

    second = client.post("/api/v1/cover/quotes", headers=planner, json={**body, "po_id": "PO-2"}).json()
    exp = second["experience"]
    assert exp["policies"] == 1 and exp["a_to_e"] == pytest.approx(paid / first["expected_paid_units"], abs=0.01)
    assert 1.0 < exp["factor"] < exp["a_to_e"]  # moves towards the evidence, limited by credibility (1 policy)
    assert second["premium_eur"] > first["premium_eur"]
    assert client.get("/api/v1/cover/portfolio", headers=planner).json()["experience"]["factor"] == exp["factor"]
