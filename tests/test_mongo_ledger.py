import os
import uuid

import pytest

pymongo = pytest.importorskip("pymongo")
URI = os.getenv("MONGO_URL", "mongodb://localhost:27017")


@pytest.fixture
def ledger():
    try:
        pymongo.MongoClient(URI, serverSelectionTimeoutMS=500).admin.command("ping")
    except Exception:
        pytest.skip("no local MongoDB")
    from app.store.mongo_ledger import MongoLedger
    name = f"sop_test_{uuid.uuid4().hex[:8]}"
    led = MongoLedger(URI, name)
    yield led
    led.client.drop_database(name)


def rec(item_id=1, qty=10.0, month="2026-03-01"):
    return {"timestamp": "t", "item_id": item_id, "cutoff": "2026-02-01", "target_month": month, "baseline_quantity": 5.0,
            "ml_recommended_quantity": 8.0, "submitted_quantity": qty, "reason_code": "Commercial Intelligence (Account Feedback)",
            "planner_id": "danny"}


def test_mongo_ledger_appends_chains_and_detects_tampering(ledger):
    first = ledger.append_override(rec(qty=10.0))
    ledger.append_override(rec(qty=12.0))
    rows = ledger.overrides(cutoff="2026-02-01")
    assert len(rows) == 2 and rows[0]["id"] == first
    assert ledger.latest_decisions("2026-02-01")[(1, "2026-03-01")]["submitted_quantity"] == 12.0
    assert ledger.verify_chain() == (True, None)
    ledger._ov.update_one({"submitted_quantity": 10.0}, {"$set": {"submitted_quantity": 99.0}})
    ok, bad = ledger.verify_chain()
    assert not ok and bad == first


def test_mongo_ledger_rejects_missing_reason_and_double_commit(ledger):
    with pytest.raises(ValueError, match="Reason code is required."):
        ledger.append_override({**rec(), "reason_code": " "})
    c = {"timestamp": "t", "cutoff": "2026-02-01", "planner_id": "danny", "items": 3, "path": "p"}
    ledger.append_commit(c)
    with pytest.raises(ValueError):
        ledger.append_commit(c)
    assert ledger.commit_for("2026-02-01")["items"] == 3
