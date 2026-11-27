"""Channel Hub: vault, connections, sync events, broadcast without ping-pong, webhooks, workflow triggers, API."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet

from app.channels.base import ChannelError, ConfigField, Connector, ConnectorType
from app.channels.hub import ChannelHub
from app.channels.store import ChannelStore
from app.channels.vault import Vault, VaultError
from tests.test_api import auth, env  # noqa: F401  (fixture reuse)

T0 = datetime(2026, 11, 27, 9, 0, tzinfo=timezone.utc)
SHOPS = {}  # connection name -> simulated channel state


def shop(name):
    return SHOPS.setdefault(name, {"orders": [], "stock": {}, "writes": []})


class FakeConnector(Connector):
    type_name = "fake"

    @property
    def state(self):
        return SHOPS.setdefault(self.config["shop"], {"orders": [], "stock": {}, "writes": []})

    def test(self):
        if self.config["token"] != "good":
            raise ChannelError("fake: the channel rejected the credentials (HTTP 401)")
        return {"account": self.config["shop"]}

    def fetch_orders(self, since):
        if self.state.get("down"):
            raise ChannelError("fake: cannot reach the channel")
        return [o for o in self.state["orders"] if not since or o["updated_at"] >= since.isoformat()]

    def fetch_inventory(self):
        return [{"sku": k, "quantity": v} for k, v in self.state["stock"].items()]

    def set_inventory(self, sku, quantity):
        self.state["writes"].append((sku, quantity))
        self.state["stock"][sku] = quantity
        return {}

    def parse_webhook(self, headers, body):
        if headers.get("x-sig") != "ok":
            raise ChannelError("fake: webhook signature does not match")
        return [json.loads(body)]


def fake_type(name="fake", writable=True):
    caps = ("orders", "inventory") + (("inventory_write",) if writable else ())
    return ConnectorType(name, f"Fake {name}", "shop",
                         [ConfigField("shop", "Shop"), ConfigField("token", "Token", secret=True)],
                         FakeConnector, capabilities=caps)


def order(oid, at, total=10.0):
    return {"external_id": oid, "status": "open", "created_at": at, "updated_at": at, "currency": "EUR",
            "total": total, "country": "DE", "lines": [{"sku": "MAT", "quantity": 1, "price": total}]}


class Recorder:
    """Stands in for the workflow service and runner."""

    def __init__(self, workflows=()):
        self._wfs, self.started = list(workflows), []

    def list(self, tenant):
        return [{"id": w.id} for w in self._wfs]

    def get(self, tenant, wid):
        return next(w for w in self._wfs if w.id == wid)

    def start(self, tenant, wid, payload, user):
        self.started.append((wid, payload["event"], payload.get("sku"), user))
        return {"run_id": "r"}


@pytest.fixture
def hub(tmp_path):
    SHOPS.clear()
    return ChannelHub(ChannelStore(tmp_path / "c.db"), Vault(Fernet.generate_key()),
                      {"fake": fake_type(), "readonly": fake_type("readonly", writable=False)})


# ---------------------------------------------------------------- vault and connections
def test_secrets_are_encrypted_and_never_listed(hub, tmp_path):
    conn = hub.add("acme", "fake", "Shop A", {"shop": "a", "token": "super-secret-token"})
    assert "secrets" not in conn and conn["config"] == {"shop": "a"}
    raw = sqlite3.connect(tmp_path / "c.db").execute("SELECT secrets FROM connections").fetchone()[0]
    assert "super-secret-token" not in raw
    assert hub.connector("acme", conn["id"]).config["token"] == "super-secret-token"
    other = ChannelHub(hub.store, Vault(Fernet.generate_key()), hub.types)
    with pytest.raises(VaultError, match="wrong vault key"):
        other.connector("acme", conn["id"])
    with pytest.raises(ValueError, match="required"):
        hub.add("acme", "fake", "x", {"shop": "a"})
    with pytest.raises(ValueError, match="unknown settings"):
        hub.add("acme", "fake", "x", {"shop": "a", "token": "t", "colour": "red"})
    with pytest.raises(KeyError):
        hub.add("acme", "nope", "x", {})


def test_vault_key_file_is_created_once(tmp_path, monkeypatch):
    monkeypatch.delenv("CHANNEL_VAULT_KEY", raising=False)
    v1 = Vault.from_env_or_file(tmp_path / "vault")
    token = v1.seal({"a": 1})
    assert Vault.from_env_or_file(tmp_path / "vault").open(token) == {"a": 1}
    monkeypatch.setenv("CHANNEL_VAULT_KEY", Fernet.generate_key().decode())
    with pytest.raises(VaultError):
        Vault.from_env_or_file(tmp_path / "vault").open(token)


def test_connection_test_records_errors(hub):
    good = hub.add("acme", "fake", "A", {"shop": "a", "token": "good"})
    bad = hub.add("acme", "fake", "B", {"shop": "b", "token": "bad"})
    assert hub.test("acme", good["id"]) == {"ok": True, "account": "a"}
    out = hub.test("acme", bad["id"])
    assert out["ok"] is False and "rejected" in out["error"]
    assert "rejected" in hub.store.get_connection("acme", bad["id"])["last_error"]


# ---------------------------------------------------------------- sync and events
def test_first_sync_is_silent_then_changes_raise_events(hub):
    conn = hub.add("acme", "fake", "A", {"shop": "a", "token": "good"}, low_stock=5)
    shop("a")["orders"] = [order("1", "2026-11-20T10:00:00+00:00")]
    shop("a")["stock"] = {"MAT": 40, "MUG": 3}
    first = hub.sync("acme", conn["id"], T0)
    assert first["initial"] is True and first["orders_created"] == 1 and first["events"] == 0
    assert hub.store.events("acme") == []  # history import fires nothing (MUG is already low)

    SHOPS["a"]["orders"].append(order("2", "2026-11-27T08:50:00+00:00"))
    SHOPS["a"]["orders"][0] = order("1", "2026-11-27T08:55:00+00:00", total=12.0)  # updated
    SHOPS["a"]["stock"]["MAT"] = 4  # crosses the low-stock threshold
    second = hub.sync("acme", conn["id"], T0 + timedelta(minutes=15))
    types = sorted(e["type"] for e in hub.store.events("acme"))
    assert types == ["inventory_changed", "order_created", "order_updated", "stock_low"]
    assert second["orders_created"] == 1 and second["orders_updated"] == 1 and second["inventory_changed"] == 1

    third = hub.sync("acme", conn["id"], T0 + timedelta(minutes=30))
    assert third["events"] == 0  # nothing changed
    cursor = hub.store.get_connection("acme", conn["id"])["cursor"]
    assert cursor["synced"] is True and cursor["orders_since"].startswith("2026-11-27T08:50")  # 5-minute overlap


def test_sync_errors_are_recorded_and_due_logic(hub):
    a = hub.add("acme", "fake", "A", {"shop": "a", "token": "good"}, sync_minutes=15)
    b = hub.add("acme", "fake", "B", {"shop": "b", "token": "good"}, sync_minutes=15)
    SHOPS["b"] = {"orders": [], "stock": {}, "writes": [], "down": True}
    results = hub.sync_due(T0)
    assert {r["connection_id"] for r in results} == {a["id"], b["id"]}
    assert "cannot reach" in hub.store.get_connection("acme", b["id"])["last_error"]
    assert hub.store.get_connection("acme", b["id"])["cursor"]["synced"] is False  # retry the silent import
    assert hub.sync_due(T0 + timedelta(minutes=5)) == []  # interval not passed
    hub.store.update_connection("acme", a["id"], enabled=0)
    assert [r["connection_id"] for r in hub.sync_due(T0 + timedelta(minutes=16))] == [b["id"]]


def test_broadcast_writes_other_channels_without_ping_pong(hub):
    a = hub.add("acme", "fake", "A", {"shop": "a", "token": "good"})
    b = hub.add("acme", "fake", "B", {"shop": "b", "token": "good"})
    c = hub.add("acme", "readonly", "C", {"shop": "c", "token": "good"})
    for conn, shop in ((a, "a"), (b, "b"), (c, "c")):
        SHOPS.setdefault(shop, {"orders": [], "stock": {}, "writes": []})["stock"] = {"MAT": 50}
        hub.sync("acme", conn["id"], T0)
    out = hub.broadcast_inventory("acme", "MAT", 30, source_connection=a["id"], buffer=2)
    assert [u["connection_id"] for u in out["updated"]] == [b["id"]] and out["quantity"] == 28
    assert SHOPS["b"]["writes"] == [("MAT", 28)] and SHOPS["a"]["writes"] == [] and SHOPS["c"]["writes"] == []
    again = hub.sync("acme", b["id"], T0 + timedelta(minutes=20))
    assert again["inventory_changed"] == 0  # our own write is not seen as a change


def test_events_start_matching_workflows(hub):
    from app.workflows.dsl import Workflow

    def wf(wid, event, connection=None):
        params = {"event": event, **({"connection_id": connection} if connection else {})}
        return Workflow.model_validate({"id": wid, "name": wid, "trigger": "t",
                                        "nodes": [{"id": "t", "type": "trigger.channel_event", "params": params}]})

    a = hub.add("acme", "fake", "A", {"shop": "a", "token": "good"}, low_stock=5)
    rec = Recorder([wf("on-low", "stock_low"), wf("on-low-b-only", "stock_low", "other-id"), wf("on-order", "order_created")])
    hub.workflows, hub.runner = rec, rec
    shop("a")["stock"] = {"MAT": 20}
    hub.sync("acme", a["id"], T0)
    SHOPS["a"]["stock"] = {"MAT": 2}
    hub.sync("acme", a["id"], T0 + timedelta(minutes=20))
    assert rec.started == [("on-low", "stock_low", "MAT", f"channel:{a['id']}")]


def test_webhooks_are_verified(hub):
    a = hub.add("acme", "fake", "A", {"shop": "a", "token": "good"})
    body = json.dumps({"type": "order", "order": order("9", "2026-11-27T09:00:00+00:00")}).encode()
    with pytest.raises(ChannelError, match="signature"):
        hub.handle_webhook("acme", a["id"], {"X-Sig": "forged"}, body)
    assert hub.handle_webhook("acme", a["id"], {"X-Sig": "ok"}, body) == {"received": 1, "events": 1}
    assert hub.store.orders("acme")[0]["external_id"] == "9"
    assert hub.handle_webhook("acme", a["id"], {"X-Sig": "ok"}, body)["events"] == 0  # duplicate delivery


# ---------------------------------------------------------------- API
def test_channels_api(env):  # noqa: F811
    client, _, _ = env
    SHOPS.clear()
    client.app.state.channel_hub.types["fake"] = fake_type()
    admin, planner, auditor = auth(client, "root"), auth(client), auth(client, "ada")
    types = client.get("/api/v1/channels/types", headers=auditor).json()["types"]
    assert {"shopify", "woocommerce", "amazon", "ebay", "mirakl"} <= {t["name"] for t in types}

    body = {"type": "fake", "name": "Shop A", "config": {"shop": "a", "token": "good"}, "low_stock": 5}
    secret_value = "good"
    assert client.post("/api/v1/channels/connections", headers=planner, json=body).status_code == 403
    made = client.post("/api/v1/channels/connections", headers=admin, json=body)
    assert made.status_code == 201
    conn = made.json()
    assert secret_value not in json.dumps(conn) and conn["secret_fields_set"] == ["token"] and conn["webhook_path"] == f"/api/v1/channels/webhooks/default/{conn['id']}"
    assert client.post("/api/v1/channels/connections", headers=admin,
                       json={**body, "config": {"shop": "a"}}).status_code == 422

    assert client.post(f"/api/v1/channels/connections/{conn['id']}/test", headers=planner).json()["ok"] is True
    shop("a")["stock"] = {"MAT": 9}
    synced = client.post(f"/api/v1/channels/connections/{conn['id']}/sync", headers=planner).json()
    assert synced["inventory_items"] == 1
    inv = client.get("/api/v1/channels/inventory", headers=auditor).json()
    assert inv["by_sku"] == [{"sku": "MAT", "total": 9.0, "channels": {conn["id"]: 9.0}}]
    assert client.post(f"/api/v1/channels/connections/{conn['id']}/inventory", headers=auditor,
                       json={"sku": "MAT", "quantity": 3}).status_code == 403
    assert client.post(f"/api/v1/channels/connections/{conn['id']}/inventory", headers=planner,
                       json={"sku": "MAT", "quantity": 3}).json()["quantity"] == 3
    assert client.post(f"/api/v1/channels/connections/{conn['id']}/price", headers=planner,
                       json={"sku": "MAT", "price": 9.9}).status_code == 409  # fake has no price support

    hook = f"/api/v1/channels/webhooks/default/{conn['id']}"
    payload = json.dumps({"type": "order", "order": order("7", "2026-11-27T09:00:00+00:00")})
    assert client.post(hook, content=payload, headers={"X-Sig": "bad"}).status_code == 401
    assert client.post(hook, content=payload, headers={"X-Sig": "ok"}).json()["received"] == 1
    assert client.post("/api/v1/channels/webhooks/default/nope", content=payload).status_code == 404
    assert client.get("/api/v1/channels/orders", headers=auditor).json()["orders"][0]["external_id"] == "7"

    upd = client.patch(f"/api/v1/channels/connections/{conn['id']}", headers=admin,
                       json={"enabled": False, "secrets": {"token": "rotated"}}).json()
    assert upd["enabled"] is False
    assert client.app.state.channel_hub.connector("default", conn["id"]).config["token"] == "rotated"
    assert client.delete(f"/api/v1/channels/connections/{conn['id']}", headers=admin).json() == {"deleted": conn["id"]}
