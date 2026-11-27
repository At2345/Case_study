"""Channel automations end to end: events start template workflows that act on other channels."""
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet

from app.channels.edi import EDI
from app.channels.hub import ChannelHub, set_current
from app.channels.store import ChannelStore
from app.channels.vault import Vault
from app.workflows.runners import LocalRunner
from app.workflows.service import WorkflowService
from tests.test_channels_hub import SHOPS, fake_type, shop

T0 = datetime(2026, 11, 27, 9, 0, tzinfo=timezone.utc)
ADMIN = {"username": "root", "role": "admin"}


@pytest.fixture
def world(tmp_path):
    SHOPS.clear()
    svc = WorkflowService(tmp_path / "workflows")
    hub = ChannelHub(ChannelStore(tmp_path / "channels" / "c.db"), Vault(Fernet.generate_key()),
                     {"fake": fake_type(), "edi": EDI}, workflows=svc, runner=LocalRunner(svc))
    set_current(hub)
    a = hub.add("acme", "fake", "Shop A", {"shop": "a", "token": "good"}, low_stock=5)
    b = hub.add("acme", "fake", "Shop B", {"shop": "b", "token": "good"})
    shop("a")["stock"] = {"MAT": 50}
    shop("b")["stock"] = {"MAT": 50}
    hub.sync("acme", a["id"], T0)
    hub.sync("acme", b["id"], T0)
    yield svc, hub, a, b
    set_current(None)


def runs(svc, tenant="acme"):
    return [svc.get_run(tenant, r["run_id"]) for r in svc.list_runs(tenant)]


def test_stock_change_syncs_other_channels_once(world):
    svc, hub, a, b = world
    svc.install_template("acme", "channel-stock-sync", "stock-sync", {"buffer": 2})
    shop("a")["stock"]["MAT"] = 40
    summary = hub.sync("acme", a["id"], T0 + timedelta(minutes=15))
    assert summary["events"] == 2  # the event + one started workflow
    assert shop("b")["writes"] == [("MAT", 38)] and shop("a")["writes"] == []
    run = runs(svc)[0]
    assert run["status"] == "SUCCEEDED" and run["started_by"] == f"channel:{a['id']}"
    assert run["outputs"]["sync"]["updated"][0]["connection_id"] == b["id"]
    actions = [d for d in svc.decisions("acme", run_id=run["run_id"]) if d["kind"] == "action"]
    assert [d["node_type"] for d in actions] == ["action.channel_broadcast_stock"]

    again = hub.sync("acme", b["id"], T0 + timedelta(minutes=30))
    assert again["events"] == 0 and len(runs(svc)) == 1  # B's new stock is our own write: no loop


def test_low_stock_reorder_waits_for_approval_then_sends_edi(world, tmp_path):
    svc, hub, a, b = world
    supplier = hub.add("acme", "edi", "Supplier", {"sender_id": "ACMEBRAND", "receiver_id": "SUPPLIER01"})
    svc.install_template("acme", "low-stock-reorder", "reorder",
                         {"supplier_connection": supplier["id"], "reorder_quantity": 300, "po_prefix": "RO-"})
    shop("a")["stock"]["MAT"] = 3
    hub.sync("acme", a["id"], T0 + timedelta(minutes=15))
    run = runs(svc)[0]
    assert run["status"] == "WAITING_APPROVAL" and run["pending"]["node"] == "order"
    outbox = tmp_path / "channels" / "data" / "acme" / supplier["id"] / "outbox"
    assert not outbox.exists() or not list(outbox.iterdir())  # nothing sent before approval

    done = svc.approve("acme", run["run_id"], "order", True, ADMIN)
    assert done["status"] == "SUCCEEDED"
    po = done["outputs"]["order"]
    assert po["po_number"] == f"RO-{run['run_id']}" and po["control_number"] == 1
    text = (outbox / po["file"]).read_text()
    assert "PO1*1*300*EA***BP*MAT" in text and f"BEG*00*SA*RO-{run['run_id']}" in text


def test_dry_runs_and_tenant_isolation(world):
    svc, hub, a, b = world
    svc.install_template("acme", "channel-stock-sync", "stock-sync", {"buffer": 0})
    payload = {"event": "inventory_changed", "connection_id": a["id"], "sku": "MAT", "quantity": 10}
    dry = svc.start("acme", "stock-sync", payload, "danny", dry_run=True)
    assert dry["status"] == "SUCCEEDED" and dry["outputs"]["sync"]["simulated"] is True
    assert shop("b")["writes"] == []

    svc.save("globex", {"id": "steal", "name": "steal", "trigger": "t", "nodes": [
        {"id": "t", "type": "trigger.manual"},
        {"id": "w", "type": "action.channel_set_stock", "params": {"connection_id": b["id"], "sku": "MAT", "quantity": 0}}],
        "edges": [{"source": "t", "target": "w"}]})
    stolen = svc.start("globex", "steal", {}, "mallory")
    assert stolen["status"] == "FAILED" and "not found" in stolen["error"]  # acme's connection is invisible
    assert shop("b")["writes"] == []


def test_channel_nodes_in_catalogue_and_drafting():
    from app.workflows import default_registry
    from app.workflows.drafting import catalogue_text
    reg = default_registry()
    names = {s["name"] for s in reg.catalogue()}
    assert {"trigger.channel_event", "action.channel_set_stock", "action.channel_set_price",
            "action.channel_broadcast_stock", "action.edi_purchase_order"} <= names
    po = reg.get("action.edi_purchase_order")
    assert po.policy.needs_approval is True and po.policy.reversible is False
    assert "action.channel_broadcast_stock [action]" in catalogue_text(reg)  # prompt-to-workflow sees them
