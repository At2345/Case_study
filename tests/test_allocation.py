import pytest

from app.allocation.optimiser import AllocationConfig, ChannelDemand, build_demands, simulate


def brief_case():
    """Scenario D2C-1 of the brief: 1,200 units of supply against 2,000 of demand."""
    return [
        ChannelDemand("amazon", 1, demand=1100.0, unit_margin=8.30, unit_penalty=0.5, floor_pct=0.60),
        ChannelDemand("B2B wo Amazon", 2, demand=600.0, unit_margin=8.30, unit_penalty=1.0),  # OTIF fine > chargeback
        ChannelDemand("D2C", 3, demand=300.0, unit_margin=25.49, unit_penalty=0.0),
    ]


def by_channel(scenario):
    return {r["channel"]: r for r in scenario["channels"]}


def scenario(result, name):
    return next(s for s in result["scenarios"] if s["strategy"] == name)


def test_margin_optimal_ringfences_d2c_and_keeps_the_amazon_floor():
    res = simulate(brief_case(), supply=1200.0)
    opt = by_channel(scenario(res, "margin_optimal"))
    assert opt["D2C"]["allocated"] == pytest.approx(300.0)
    assert opt["amazon"]["allocated"] == pytest.approx(660.0)  # 60% confirmation floor
    assert opt["B2B wo Amazon"]["allocated"] == pytest.approx(240.0)
    assert sum(r["allocated"] for r in opt.values()) == pytest.approx(1200.0)
    assert opt["D2C"]["gross_margin_gbp"] == pytest.approx(300 * 25.49)  # 7,647 profit from the brief


def test_margin_optimal_is_never_worse_than_the_other_strategies():
    res = simulate(brief_case(), supply=1200.0)
    best = scenario(res, "margin_optimal")["total_profit_gbp"]
    for s in res["scenarios"]:
        assert s["total_profit_gbp"] <= best + 1e-9
    prio = scenario(res, "priority_wholesale")
    assert by_channel(prio)["D2C"]["allocated"] == pytest.approx(0.0)  # wholesale first starves D2C
    assert prio["total_profit_gbp"] < best


def test_proportional_gives_every_channel_the_same_fill_rate():
    prop = by_channel(scenario(simulate(brief_case(), supply=1200.0), "proportional"))
    assert {round(r["fill_rate"], 6) for r in prop.values()} == {0.6}


def test_no_shortage_fills_everything():
    res = simulate(brief_case(), supply=5000.0)
    for s in res["scenarios"]:
        assert all(r["unmet"] == pytest.approx(0.0) for r in s["channels"])
        assert s["shortage_units"] == pytest.approx(0.0)


def test_floors_larger_than_supply_are_scaled_and_reported():
    d = [ChannelDemand("amazon", 1, 1000.0, 5.0, 0.0, floor_pct=0.8), ChannelDemand("D2C", 2, 1000.0, 20.0, 0.0, floor_pct=0.8)]
    res = simulate(d, supply=800.0)
    opt = scenario(res, "margin_optimal")
    assert opt["floors_scaled"] is True
    assert sum(r["allocated"] for r in opt["channels"]) == pytest.approx(800.0)


def test_penalties_enter_the_objective_and_can_flip_the_priority():
    d = [ChannelDemand("amazon", 1, 100.0, unit_margin=5.0, unit_penalty=20.0), ChannelDemand("D2C", 2, 100.0, unit_margin=15.0, unit_penalty=0.0)]
    opt = by_channel(scenario(simulate(d, supply=100.0), "margin_optimal"))
    assert opt["amazon"]["allocated"] == pytest.approx(100.0)  # 25 per unit beats 15 per unit
    assert scenario(simulate(d, supply=100.0), "margin_optimal")["total_penalty_gbp"] == pytest.approx(100 * 0.0)


def test_total_profit_is_margin_minus_penalties():
    res = simulate(brief_case(), supply=1200.0)
    for s in res["scenarios"]:
        assert s["total_profit_gbp"] == pytest.approx(s["total_margin_gbp"] - s["total_penalty_gbp"])


def test_b2b_floor_protects_critical_accounts_even_when_amazon_is_worth_more():
    d = [
        ChannelDemand("amazon", 1, 1000.0, unit_margin=8.0, unit_penalty=2.0),
        ChannelDemand("B2B wo Amazon", 2, 600.0, unit_margin=8.0, unit_penalty=0.1, floor_pct=0.5),
    ]
    opt = by_channel(scenario(simulate(d, supply=800.0), "margin_optimal"))
    assert opt["B2B wo Amazon"]["allocated"] == pytest.approx(300.0)
    assert opt["amazon"]["allocated"] == pytest.approx(500.0)


def test_invalid_inputs():
    with pytest.raises(ValueError):
        simulate(brief_case(), supply=-1.0)
    with pytest.raises(ValueError):
        simulate([], supply=10.0)


def test_build_demands_uses_cogs_for_chargeback_and_sales_value_for_otif():
    rows = [
        {"item_id": 1, "channel": "amazon", "demand": 100.0, "sales_price": 10.0, "purchase_price": 6.0},
        {"item_id": 2, "channel": "B2B wo Amazon", "demand": 50.0, "sales_price": 10.0, "purchase_price": 6.0},
        {"item_id": 3, "channel": "D2C", "demand": 20.0, "sales_price": 18.0, "purchase_price": 6.0},
    ]
    cfg = AllocationConfig(chargeback_pct=0.06, otif_penalty_pct=0.05, amazon_floor_pct=0.6, b2b_floor_pct=0.25)
    d = {x.channel: x for x in build_demands(rows, cfg)}
    assert d["amazon"].unit_margin == pytest.approx(4.0)
    assert d["amazon"].unit_penalty == pytest.approx(0.06 * 6.0)
    assert d["amazon"].floor_pct == 0.6
    assert d["B2B wo Amazon"].unit_penalty == pytest.approx(0.05 * 10.0)
    assert d["B2B wo Amazon"].floor_pct == 0.25
    assert d["D2C"].unit_margin == pytest.approx(12.0) and d["D2C"].unit_penalty == 0.0
