import pytest

from tests.test_api import auth, body, env  # noqa: F401  (fixture reuse)


class FakeLlm:
    model = "fake"

    def generate(self, system, user):
        return "ok"

    async def stream(self, system, history, user):
        for part in ("The queue ", "has 2 items."):
            yield part


class MemChats:
    def __init__(self):
        self.rows = []

    def add(self, username, role, text):
        self.rows.append({"id": str(len(self.rows)), "username": username, "role": role, "text": text})

    def history(self, username, limit=50):
        return [r for r in self.rows if r["username"] == username][-limit:]

    def clear(self, username):
        self.rows = [r for r in self.rows if r["username"] != username]


def test_otif_endpoint_values_booked_units_the_plan_does_not_cover(env):  # noqa: F811
    client, _, _ = env
    out = client.get("/api/v1/otif", headers=auth(client)).json()
    assert out["items_at_risk"] == 1
    r = out["rows"][0]
    assert r["item_id"] == 2 and r["booked"] == 500.0 and r["plan"] == 340.0
    assert r["penalty_gbp"] == pytest.approx(160 * 24.5 * 0.05)
    client.post("/api/v1/override", json=body(item_id=2, qty=500.0, reason="Order Book Floor Applied"), headers=auth(client))
    assert client.get("/api/v1/otif", headers=auth(client)).json()["items_at_risk"] == 0


def test_disaggregate_endpoint_splits_a_product_total(env):  # noqa: F811
    client, _, _ = env
    h = auth(client)
    out = client.post("/api/v1/disaggregate", headers=h, json={"product_id": "PROD_1", "total": 1000, "method": "ols"}).json()
    assert out["current_total"] == 880.0 and sum(r["proposed"] for r in out["rows"]) == pytest.approx(1000.0)
    assert client.post("/api/v1/disaggregate", headers=h, json={"product_id": "NOPE", "total": 1}).status_code == 404
    assert client.post("/api/v1/disaggregate", headers=h, json={"product_id": "PROD_1", "total": 1, "method": "x"}).status_code == 422
    assert client.post("/api/v1/disaggregate", json={"product_id": "PROD_1", "total": 1}).status_code == 401


def test_phase_3_and_pipeline_endpoints_report_missing_artifacts(env):  # noqa: F811
    client, _, _ = env
    h = auth(client)
    assert client.get("/api/v1/inventory/safety-stock", headers=h).json()["available"] is False
    assert client.get("/api/v1/pipeline/amazon", headers=h).json()["meta"] is None
    assert client.get("/api/v1/cycles/runs", headers=h).json()["runs"] == []
    assert client.post("/api/v1/admin/cycles/run", headers=h).status_code == 403
    assert client.post("/api/v1/admin/cycles/run", headers=auth(client, "root")).status_code == 503


def test_autopilot_and_metrics(env):  # noqa: F811
    client, _, _ = env
    h = auth(client)
    a = client.get("/api/v1/fva/autopilot", headers=h).json()
    assert a["candidates"] == [] and a["segment"] == "channel"
    m = client.get("/api/v1/metrics").text
    assert "sop_http_requests_total" in m and "sop_queue_size 2" in m


def test_briefing_pdf(env):  # noqa: F811
    client, _, _ = env
    r = client.get("/api/v1/export/briefing.pdf", headers=auth(client, "ada"))
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    assert client.get("/api/v1/export/briefing.pdf").status_code == 401


def test_assistant_streams_and_keeps_history(tmp_path):
    from fastapi.testclient import TestClient

    from app.api.auth import UserStore
    from app.api.main import create_app
    from app.store.ledger import Ledger
    from tests.test_api import SECRET, make_service

    users = UserStore(tmp_path / "users.json", iterations=1000)
    users.add_user("danny", "planner", "planner-passphrase")
    app = create_app(service=make_service(), ledger=Ledger(tmp_path / "l.db"), users=users, secret=SECRET,
                     store_dir=tmp_path, llm=FakeLlm())
    from app.api import extended
    chats = MemChats()
    app.router.routes = [r for r in app.router.routes if not getattr(r, "path", "").startswith("/api/v1/assistant")]
    extended.register(app, {"state": {"svc": make_service()}, "ledger": Ledger(tmp_path / "l.db"),
                            "require": lambda *roles: (lambda: {"username": "danny", "role": "planner"}),
                            "decisions": lambda: {}, "all_roles": ("planner",), "chats": chats, "llm": FakeLlm(),
                            "store_dir": tmp_path})
    client = TestClient(app)
    r = client.post("/api/v1/assistant/stream", json={"question": "How big is the queue?"})
    assert r.status_code == 200 and "has 2 items" in r.text and '"done": true' in r.text
    hist = client.get("/api/v1/assistant/history").json()
    assert [m["role"] for m in hist["messages"]] == ["user", "assistant"] and hist["enabled"] is True
    assert client.post("/api/v1/assistant/stream", json={"question": ""}).status_code == 422
