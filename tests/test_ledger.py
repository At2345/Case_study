import sqlite3

import pytest

from app.store.ledger import Ledger


def _rec(**kw):
    base = dict(
        timestamp="2026-03-02T10:14:22+00:00", item_id=1, cutoff="2026-02-01", target_month="2026-03-01",
        baseline_quantity=620.0, ml_recommended_quantity=880.0, submitted_quantity=850.0,
        variance_to_ml=-30.0, financial_impact_gbp=-300.0, reason_code="Commercial Intelligence (Account Feedback)",
        planner_comment="buyer delay", planner_id="danny", ip_address="10.0.0.1", session_id="s1",
        tier="Collaborative Exception", risk_type="OOS_RISK",
    )
    base.update(kw)
    return base


def test_append_and_read_back(tmp_path):
    led = Ledger(tmp_path / "ledger.db")
    rid = led.append_override(_rec())
    rows = led.overrides()
    assert rid == rows[0]["id"] == 1
    assert rows[0]["submitted_quantity"] == 850.0 and rows[0]["planner_id"] == "danny"


def test_updates_and_deletes_are_rejected(tmp_path):
    led = Ledger(tmp_path / "ledger.db")
    led.append_override(_rec())
    with sqlite3.connect(tmp_path / "ledger.db") as con:
        with pytest.raises(sqlite3.DatabaseError, match="insert-only"):
            con.execute("UPDATE overrides SET submitted_quantity = 1")
        with pytest.raises(sqlite3.DatabaseError, match="insert-only"):
            con.execute("DELETE FROM overrides")
    assert led.overrides()[0]["submitted_quantity"] == 850.0


def test_reason_code_is_mandatory_in_the_database_too(tmp_path):
    led = Ledger(tmp_path / "ledger.db")
    with pytest.raises(ValueError, match="Reason code is required"):
        led.append_override(_rec(reason_code=""))
    with pytest.raises(ValueError, match="Reason code is required"):
        led.append_override(_rec(reason_code=None))


def test_latest_decision_per_item_wins(tmp_path):
    led = Ledger(tmp_path / "ledger.db")
    led.append_override(_rec(item_id=1, submitted_quantity=850.0))
    led.append_override(_rec(item_id=1, submitted_quantity=900.0, timestamp="2026-03-02T11:00:00+00:00"))
    led.append_override(_rec(item_id=2, submitted_quantity=5.0))
    latest = led.latest_by_item("2026-02-01", "2026-03-01")
    assert latest[1]["submitted_quantity"] == 900.0
    assert set(latest) == {1, 2}
    assert len(led.overrides(item_id=1)) == 2  # history is kept


def test_hash_chain_detects_tampering(tmp_path):
    led = Ledger(tmp_path / "ledger.db")
    for i in range(3):
        led.append_override(_rec(item_id=i))
    assert led.verify_chain() == (True, None)
    # an attacker who drops the triggers and edits a row is detected by the chain
    with sqlite3.connect(tmp_path / "ledger.db") as con:
        con.execute("DROP TRIGGER overrides_no_update")
        con.execute("UPDATE overrides SET submitted_quantity = 1 WHERE id = 2")
    ok, bad_id = led.verify_chain()
    assert not ok and bad_id == 2


def test_commit_is_recorded_once_per_cutoff(tmp_path):
    led = Ledger(tmp_path / "ledger.db")
    led.append_commit({"timestamp": "t", "cutoff": "2026-02-01", "planner_id": "danny", "items": 10, "path": "x.csv"})
    assert led.commit_for("2026-02-01")["items"] == 10
    assert led.commit_for("2025-01-01") is None
    with pytest.raises(ValueError, match="already committed"):
        led.append_commit({"timestamp": "t", "cutoff": "2026-02-01", "planner_id": "x", "items": 1, "path": "y"})
