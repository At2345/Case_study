"""Overstock cover quotes: pricing maths, retention terms, forecast-based demand and the API."""
import math

import numpy as np
import pytest

from app.cover.quote import CoverTerms, Demand, demand_from_forecast, expected_shortfall, quantile, quote
from tests.test_api import auth, env  # noqa: F401  (fixture reuse)


# ---------------------------------------------------------------- maths against simulation
@pytest.mark.parametrize("mean, sd, q", [(1000, 200, 1000), (1000, 400, 1300), (300, 150, 150), (50, 40, 120)])
def test_closed_forms_match_monte_carlo(mean, sd, q):
    p = Demand(mean, sd).lognormal()
    draws = np.random.default_rng(7).lognormal(p["mu"], p["s"], 400_000)
    assert draws.mean() == pytest.approx(mean, rel=0.01) and draws.std() == pytest.approx(sd, rel=0.03)
    assert expected_shortfall(q, p["mu"], p["s"]) == pytest.approx(np.maximum(q - draws, 0).mean(), rel=0.02, abs=0.5)
    assert quantile(0.8, p["mu"], p["s"]) == pytest.approx(np.quantile(draws, 0.8), rel=0.01)


def test_quote_terms_and_premium_formula():
    terms = CoverTerms()
    q = quote(1000, 8.0, Demand(1100, 220), terms)
    assert q["offered"] is True and q["binding"] is False and q["risk_grade"] == "A"
    assert q["covered_units"] == 1000 and q["excluded_units"] == 0  # under the P80 cap
    assert q["deductible_units"] == 100
    assert q["payout_per_unit_eur"] == 3.2  # 50% floor x 80% after coinsurance x 8
    # net per paid unit = 3.2 - 25% recovery x 8 = 1.2
    assert q["expected_loss_eur"] == pytest.approx(q["expected_paid_units"] * 1.2, abs=0.2)
    assert q["premium_eur"] == pytest.approx(max(50.0, q["expected_loss_eur"] * 1.35 + 25), abs=0.05)
    assert 0 < q["claim_probability"] < 0.5
    assert q["demand"] == {"mean_units": 1100, "sd_units": 220, "source": "manual"}


def test_overordering_is_capped_and_risky_orders_declined():
    capped = quote(1500, 8.0, Demand(1000, 250))
    assert capped["recommended_units"] < 1500 and capped["covered_units"] == capped["recommended_units"]
    assert capped["excluded_units"] == 1500 - capped["covered_units"]
    assert capped["risk_grade"] in ("C", "D") and capped["offered"] is True

    declined = quote(3000, 8.0, Demand(1000, 250))
    assert declined["risk_grade"] == "E" and declined["offered"] is False and declined["premium_eur"] is None
    assert "order less" in declined["decline_reasons"][0]


def test_pricing_moves_the_right_way():
    base = quote(1000, 8.0, Demand(1000, 200))
    wider = quote(1000, 8.0, Demand(1000, 400))
    pricier_stock = quote(1000, 16.0, Demand(1000, 200))
    bigger_excess = quote(1000, 8.0, Demand(1000, 200), CoverTerms(deductible_pct=0.3))
    assert wider["premium_eur"] > base["premium_eur"] and wider["expected_unsold_units"] > base["expected_unsold_units"]
    assert pricier_stock["expected_loss_eur"] == pytest.approx(2 * base["expected_loss_eur"], rel=0.01)
    assert bigger_excess["expected_loss_eur"] < base["expected_loss_eur"]
    tiny = quote(10, 1.0, Demand(1000, 50))
    assert tiny["premium_eur"] == 50.0  # minimum premium


def test_validation():
    with pytest.raises(ValueError):
        CoverTerms(floor_pct=1.5)
    with pytest.raises(ValueError):
        CoverTerms(cap_quantile=0.3)
    with pytest.raises(ValueError):
        Demand(0, 10)
    with pytest.raises(ValueError):
        quote(0, 8, Demand(100, 10))


def test_demand_from_forecast_months():
    plan = [
        {"month": "2026-03", "final": 100, "consensus": 120, "p10": None, "p90": None},
        {"month": "2026-04", "final": 100, "consensus": None, "p10": 60, "p90": 140},
        {"month": "2026-05", "final": 200, "consensus": None, "p10": None, "p90": None},
        {"month": "2026-06", "final": 999, "consensus": None},
    ]
    d = demand_from_forecast(plan, [30.0, None, None, 5.0], months=3)
    assert d.mean_units == 420  # consensus (planner override) wins over the model forecast
    band_sigma = 80 / 2.563
    assert d.sd_units == pytest.approx(math.sqrt(30 ** 2 + band_sigma ** 2 + 60 ** 2))
    assert d.detail == {"months": ["2026-03", "2026-04", "2026-05"], "months_without_sigma": 1,
                        "monthly_means": [120.0, 100.0, 200.0]}
    with pytest.raises(ValueError):
        demand_from_forecast([], [], 6)


# ---------------------------------------------------------------- API
def test_quote_api(env):  # noqa: F811
    client, _, _ = env
    planner, admin, auditor = auth(client), auth(client, "root"), auth(client, "ada")
    url = "/api/v1/cover/quotes"
    manual = {"po_id": "PO-1", "sku": "MAT-BLUE", "units": 1000, "unit_landed_cost_eur": 8,
              "demand_mean_units": 1100, "demand_sd_units": 220}
    assert client.post(url, headers=auditor, json=manual).status_code == 403
    made = client.post(url, headers=planner, json=manual)
    assert made.status_code == 201
    q = made.json()
    assert q["po_id"] == "PO-1" and q["created_by"] == "danny" and q["offered"] is True and q["valid_until"]

    from_forecast = client.post(url, headers=planner, json={"po_id": "PO-2", "units": 500, "unit_landed_cost_eur": 5,
                                                            "item_id": 1})
    assert from_forecast.status_code == 201, from_forecast.text
    assert from_forecast.json()["demand"]["source"] == "forecast" and from_forecast.json()["demand"]["months"]

    assert client.post(url, headers=planner, json={**manual, "item_id": 1}).status_code == 422  # both sources
    no_source = {k: v for k, v in manual.items() if k not in ("demand_mean_units", "demand_sd_units")}
    assert client.post(url, headers=planner, json=no_source).status_code == 422
    assert client.post(url, headers=planner, json={**no_source, "item_id": 999999}).status_code == 404

    tweaked = {**manual, "terms": {"deductible_pct": 0.3}}
    assert client.post(url, headers=planner, json=tweaked).status_code == 403
    admin_q = client.post(url, headers=admin, json=tweaked).json()
    assert admin_q["terms"]["deductible_pct"] == 0.3 and admin_q["expected_loss_eur"] < q["expected_loss_eur"]

    listed = client.get(url, headers=auditor).json()["quotes"]
    assert [x["po_id"] for x in listed] == ["PO-1", "PO-2", "PO-1"][::-1]  # newest first
    assert client.get(f"{url}/{q['id']}", headers=auditor).json()["po_id"] == "PO-1"
    assert client.get(f"{url}/nope", headers=auditor).status_code == 404
    assert client.get("/api/v1/cover/terms", headers=auditor).json()["floor_pct"] == 0.5
