import json

import pytest

from app.narrative.client import GeminiClient
from app.narrative.guardrails import validate_narrative
from app.narrative.narrator import Narrator
from app.narrative.prompt import build_messages
from app.narrative.telemetry import build_telemetry


def detail(**item_kw):
    item = dict(
        item_id=7, product_id="PROD_7", channel="amazon", abc_category="A", phase="MATURE", months_since_launch=40.0,
        months_to_eol=None, sales_price=49.99, risk_type="OOS_RISK", risk_flags=["OOS_SURGE"], value_delta_gbp=12997.4,
        narrative="Deterministic text.", baseline_h=620.0, final_h=880.0,
    )
    item.update(item_kw)
    return {
        "item": item,
        "plan": [{"month": "2026-03-01", "baseline": 620.0, "ml": 880.0, "final": 880.0, "consensus": 880.0, "override": None, "source": "ml"}],
        "telemetry": {"glance_mom_pct": 0.274, "p70_gap_pct": None, "oos_rate_28d": 0.081, "on_hand": 182.0, "open_po": 0.0,
                      "weeks_of_cover": 2.5},
        "decision": None,
    }


PHASING = [{"month": "2026-03-01", "demand": 880.0, "expected_delivery": 656.0, "booked": 1200.0, "booked_shifted": 100.0,
            "carry_in": 40.0, "shifted_in": 0.0, "order_book_floor": 1200.0}]


def test_telemetry_is_a_whitelist_of_rounded_facts_with_percentages_as_percent():
    t = build_telemetry(detail(), PHASING)
    assert t["plan"] == {"month": "2026-03-01", "baseline_units": 620, "ml_units": 880, "plan_units": 880,
                         "change_vs_baseline_pct": 41.9, "value_delta_gbp": 12997}
    assert t["signals"] == {"glance_views_change_14d_pct": 27.4, "out_of_stock_rate_28d_pct": 8.1, "on_hand_units": 182,
                            "open_po_units": 0, "weeks_of_cover": 2.5}
    assert t["order_book"] == {"booked_units": 1200, "moving_to_later_months_units": 100, "carry_in_units": 40,
                               "expected_delivery_units": 656}
    assert t["item"]["risk_flags"] == ["OOS_SURGE"]
    flat = json.dumps(t)
    assert "Deterministic text" not in flat and "narrative" not in flat  # free text never reaches the model


def test_telemetry_drops_missing_signals_and_the_order_book_when_absent():
    d = detail()
    d["telemetry"] = {"glance_mom_pct": None, "on_hand": None}
    t = build_telemetry(d, [])
    assert t["signals"] == {} and "order_book" not in t


GOOD = ("Amazon glance views are up 27.4% over 14 days while 182 units are on hand, so the plan of 880 units "
        "is 41.9% above the baseline of 620. Check the out-of-stock risk before accepting.")


def test_guardrail_accepts_text_whose_numbers_all_come_from_the_telemetry():
    ok, reasons = validate_narrative(GOOD, build_telemetry(detail(), PHASING))
    assert ok, reasons


@pytest.mark.parametrize("text,why", [
    ("Demand is 1,500 units higher than the baseline of 620. Review it.", "1500"),
    ("The plan is 880 units. Stock covers 9 weeks. Review it now.", "9"),
    ("", "empty"),
    ("One. Two. Three. Four sentences is too many.", "sentences"),
    ("**Bold** text is not allowed in a plain narrative. Check it.", "markdown"),
    ("x" * 700 + ". Check it.", "long"),
])
def test_guardrail_rejects_invented_numbers_and_bad_shape(text, why):
    ok, reasons = validate_narrative(text, build_telemetry(detail(), PHASING))
    assert not ok and reasons
    if why in ("1500", "9"):
        assert any("not in the telemetry" in r for r in reasons)


def test_guardrail_matches_rounded_numbers_and_percentages():
    t = build_telemetry(detail(), PHASING)
    ok, reasons = validate_narrative("The plan of 880 units is 42% above the baseline of 620. Weeks of cover are 2.5.", t)
    assert not ok  # 42 is not 41.9 shown with the same precision: the model must quote the telemetry value
    ok, reasons = validate_narrative("The plan of 880 units is 41.9% above the baseline of 620. Weeks of cover are 2.5.", t)
    assert ok, reasons


def test_prompt_contains_only_the_telemetry_json():
    t = build_telemetry(detail(), PHASING)
    system, user = build_messages(t)
    assert json.loads(user) == t
    assert "only" in system.lower() and "2-3 sentences" in system


class FakeClient:
    def __init__(self, text=GOOD, error=None):
        self.text, self.error, self.calls = text, error, []

    def generate(self, system, user):
        self.calls.append((system, user))
        if self.error:
            raise self.error
        return self.text


def narrator(tmp_path, client):
    return Narrator(client, tmp_path / "narr.db", model="test-model")


def test_valid_llm_text_is_used_and_cached_by_telemetry(tmp_path):
    client = FakeClient()
    n = narrator(tmp_path, client)
    t = build_telemetry(detail(), PHASING)
    first = n.narrate(t, fallback="template")
    assert (first["text"], first["source"], first["cached"]) == (GOOD, "llm", False)
    second = n.narrate(t, fallback="template")
    assert (second["source"], second["cached"]) == ("llm", True) and len(client.calls) == 1
    changed = build_telemetry(detail(final_h=900.0), PHASING)
    changed["plan"]["plan_units"] = 900
    n.narrate(changed, fallback="template")
    assert len(client.calls) == 2  # new telemetry, new text


def test_cache_survives_a_new_narrator_instance(tmp_path):
    t = build_telemetry(detail(), PHASING)
    narrator(tmp_path, FakeClient()).narrate(t, fallback="template")
    late = FakeClient(error=RuntimeError("must not be called"))
    out = narrator(tmp_path, late).narrate(t, fallback="template")
    assert out["cached"] and not late.calls


def test_guardrail_violation_falls_back_to_the_template_and_is_not_cached(tmp_path):
    client = FakeClient(text="Demand will reach 5,000 units next quarter. Act now.")
    n = narrator(tmp_path, client)
    t = build_telemetry(detail(), PHASING)
    out = n.narrate(t, fallback="template text")
    assert out["source"] == "template" and out["text"] == "template text"
    assert any("not in the telemetry" in r for r in out["reasons"])
    n.narrate(t, fallback="template text")
    assert len(client.calls) == 2  # failures are retried, not cached


def test_client_error_and_missing_client_fall_back_cleanly(tmp_path):
    t = build_telemetry(detail(), PHASING)
    err = narrator(tmp_path, FakeClient(error=TimeoutError("slow"))).narrate(t, fallback="template text")
    assert err["source"] == "template" and err["reasons"] == ["llm_error: TimeoutError"]
    none = Narrator(None, tmp_path / "n2.db").narrate(t, fallback="template text")
    assert none["source"] == "template" and none["reasons"] == ["llm_not_configured"]


def test_gemini_client_needs_an_api_key_from_the_environment(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert GeminiClient.from_env() is None
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-key-for-test")
    monkeypatch.setenv("GEMINI_MODEL", "some-model")
    c = GeminiClient.from_env()
    assert c is not None and c.model == "some-model"
    assert "dummy-key" not in repr(c)
