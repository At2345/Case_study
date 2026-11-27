"""n8n integration surface: outbound webhook node (SSRF guard), API keys for machine clients, polling filter."""
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.workflows import Engine, Workflow, default_registry
from app.workflows import webhooks
from app.workflows.webhooks import WebhookError, check_url, post_json
from tests.test_api import auth, env  # noqa: F401  (fixture reuse)
from tests.test_workflows import DEEP, MILD, T0, rescue_playbook

PUBLIC = lambda host, port: ["93.184.216.34"]  # noqa: E731
PRIVATE = lambda host, port: ["10.0.0.7"]  # noqa: E731
LOOPBACK = lambda host, port: ["127.0.0.1", "::1"]  # noqa: E731


# ---------------------------------------------------------------- SSRF guard
def test_check_url_rules():
    check_url("https://n8n.example.com/webhook/abc", PUBLIC, allow_private=False)
    for bad in ("ftp://x.com/a", "https://user:pw@x.com/a", "https:///nohost"):
        with pytest.raises(WebhookError):
            check_url(bad, PUBLIC, allow_private=False)
    for resolver in (PRIVATE, LOOPBACK):
        with pytest.raises(WebhookError, match="non-public"):
            check_url("http://n8n.internal:5678/webhook/x", resolver, allow_private=False)
    check_url("http://localhost:5678/webhook/x", LOOPBACK, allow_private=True)  # self-hosted opt-in


def test_check_url_reads_env(monkeypatch):
    monkeypatch.setenv("WORKFLOW_ALLOW_PRIVATE_WEBHOOKS", "true")
    check_url("http://localhost:5678/webhook/x", LOOPBACK)
    monkeypatch.setenv("WORKFLOW_ALLOW_PRIVATE_WEBHOOKS", "false")
    with pytest.raises(WebhookError):
        check_url("http://localhost:5678/webhook/x", LOOPBACK)


# ---------------------------------------------------------------- HTTP behaviour
def _transport(handler):
    return httpx.MockTransport(handler)


def test_post_json_sends_idempotency_key_and_secret(monkeypatch):
    seen = {}

    def handler(request):
        seen["headers"], seen["body"] = dict(request.headers), json.loads(request.content)
        return httpx.Response(200, json={"approved": True})

    monkeypatch.setenv("WF_SECRET_N8N", "s3cret-value")
    out = post_json("https://n8n.example.com/webhook/a", {"x": 1}, "run1:call", secret_ref="N8N",
                    transport=_transport(handler), resolver=PUBLIC)
    assert out == {"status_code": 200, "response": {"approved": True}, "truncated": False}
    assert seen["headers"]["idempotency-key"] == "run1:call"
    assert seen["headers"]["x-workflow-secret"] == "s3cret-value"
    assert seen["body"] == {"x": 1}


def test_post_json_errors_and_limits(monkeypatch):
    monkeypatch.delenv("WF_SECRET_MISSING", raising=False)
    url = "https://n8n.example.com/webhook/a"
    with pytest.raises(WebhookError, match="not configured"):
        post_json(url, {}, "k", secret_ref="MISSING", transport=_transport(lambda r: httpx.Response(200)), resolver=PUBLIC)
    with pytest.raises(WebhookError, match="HTTP 500"):
        post_json(url, {}, "k", transport=_transport(lambda r: httpx.Response(500, text="boom")), resolver=PUBLIC)
    redirect = lambda r: httpx.Response(302, headers={"Location": "http://169.254.169.254/"})  # noqa: E731
    with pytest.raises(WebhookError, match="redirect"):
        post_json(url, {}, "k", transport=_transport(redirect), resolver=PUBLIC)
    big = post_json(url, {}, "k", transport=_transport(lambda r: httpx.Response(200, content=b"x" * 200_000)), resolver=PUBLIC)
    assert big["truncated"] is True and len(big["response"]) == webhooks.MAX_RESPONSE_BYTES
    text = post_json(url, {}, "k", transport=_transport(lambda r: httpx.Response(200, text="ok")), resolver=PUBLIC)
    assert text["response"] == "ok"
    with pytest.raises(WebhookError, match="non-public"):
        post_json("http://10.1.2.3/webhook", {}, "k", transport=_transport(lambda r: httpx.Response(200)), resolver=PRIVATE)


# ---------------------------------------------------------------- node in a playbook
def n8n_playbook():
    return Workflow.model_validate({
        "id": "ask-n8n", "name": "Ask n8n", "trigger": "start",
        "nodes": [
            {"id": "start", "type": "trigger.manual"},
            {"id": "call", "type": "action.n8n_webhook", "params": {"url": "https://n8n.example.com/webhook/ok"}},
            {"id": "check", "type": "logic.if", "params": {"left": "$nodes.call.response.approved", "op": "==", "right": True}},
            {"id": "yes", "type": "action.notify", "params": {"message": "n8n approved"}},
        ],
        "edges": [{"source": "start", "target": "call"}, {"source": "call", "target": "check"},
                  {"source": "check", "target": "yes", "port": "true"}],
    })


def test_webhook_node_feeds_next_step(monkeypatch):
    calls = []

    def fake_post(url, body, idempotency_key, secret_ref=None, timeout_s=10.0):
        calls.append((url, idempotency_key, body["payload"]))
        return {"status_code": 200, "response": {"approved": True}, "truncated": False}

    monkeypatch.setattr(webhooks, "post_json", fake_post)
    eng = Engine(default_registry())
    state = eng.start(n8n_playbook(), {"sku": "MAT"}, now=T0, run_id="r1")
    assert state["status"] == "SUCCEEDED" and "yes" in state["outputs"]
    assert calls == [("https://n8n.example.com/webhook/ok", "r1:call", {"sku": "MAT"})]

    dry = eng.start(n8n_playbook(), {"sku": "MAT"}, now=T0, dry_run=True)
    assert dry["outputs"]["call"]["simulated"] is True and len(calls) == 1  # dry run never calls out


def test_webhook_failure_fails_run(monkeypatch):
    def failing(*a, **k):
        raise WebhookError("webhook returned HTTP 500: boom")

    monkeypatch.setattr(webhooks, "post_json", failing)
    state = Engine(default_registry()).start(n8n_playbook(), {}, now=T0)
    assert state["status"] == "FAILED" and "HTTP 500" in state["error"]


# ---------------------------------------------------------------- API keys + polling, through the real app
def test_api_key_lifecycle_and_scope(env):  # noqa: F811
    client, _, _ = env
    admin, planner = auth(client, "root"), auth(client)
    assert client.post("/api/v1/admin/api-keys", headers=planner, json={"name": "n8n"}).status_code == 403
    assert client.post("/api/v1/admin/api-keys", headers=admin, json={"name": "x", "role": "admin"}).status_code == 422

    created = client.post("/api/v1/admin/api-keys", headers=admin, json={"name": "n8n", "role": "planner"})
    assert created.status_code == 201
    key = created.json()["key"]
    assert key.startswith("sopk_") and "hash" not in created.json()
    listed = client.get("/api/v1/admin/api-keys", headers=admin).json()["keys"]
    assert [k["name"] for k in listed] == ["n8n"] and all("key" not in k and "hash" not in k for k in listed)

    h = {"Authorization": f"Bearer {key}"}
    assert client.get("/api/v1/workflows/node-types", headers=h).status_code == 200
    assert client.get("/api/v1/summary", headers=h).status_code == 403  # keys only reach workflow endpoints
    assert client.get("/api/v1/admin/api-keys", headers=h).status_code == 403
    assert client.post("/api/v1/workflows", headers=h, json=rescue_playbook().model_dump()).status_code == 201
    run = client.post("/api/v1/workflows/rescue-de/runs", headers=h, json={"payload": DEEP}).json()
    assert run["started_by"] == "apikey:n8n" and run["status"] == "WAITING_APPROVAL"
    approved = client.post(f"/api/v1/workflow-runs/{run['run_id']}/approve", headers=h,
                           json={"node": "transfer", "approved": True})
    assert approved.json()["status"] == "SUCCEEDED"  # e.g. an approval collected in Slack by n8n

    auditor_key = client.post("/api/v1/admin/api-keys", headers=admin, json={"name": "bi", "role": "auditor"}).json()
    ha = {"Authorization": f"Bearer {auditor_key['key']}"}
    assert client.get("/api/v1/workflow-runs", headers=ha).status_code == 200
    assert client.post("/api/v1/workflows/rescue-de/runs", headers=ha, json={}).status_code == 403

    key_id = created.json()["id"]
    assert client.delete(f"/api/v1/admin/api-keys/{key_id}", headers=admin).status_code == 200
    assert client.get("/api/v1/workflows", headers=h).status_code == 401
    assert client.delete(f"/api/v1/admin/api-keys/{key_id}", headers=admin).status_code == 404
    bogus = {"Authorization": "Bearer sopk_000000000000_" + "a" * 30}
    assert client.get("/api/v1/workflows", headers=bogus).status_code == 401


def test_polling_filter_returns_changes_oldest_first(env):  # noqa: F811
    client, _, _ = env
    planner = auth(client)
    client.post("/api/v1/workflows", headers=planner, json=rescue_playbook().model_dump())
    before = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    first = client.post("/api/v1/workflows/rescue-de/runs", headers=planner, json={"payload": MILD}).json()
    second = client.post("/api/v1/workflows/rescue-de/runs", headers=planner, json={"payload": DEEP}).json()

    changed = client.get("/api/v1/workflow-runs", headers=planner, params={"updated_after": before}).json()["runs"]
    assert [r["run_id"] for r in changed] == [first["run_id"], second["run_id"]]
    cursor = changed[-1]["updated_at"]
    assert client.get("/api/v1/workflow-runs", headers=planner, params={"updated_after": cursor}).json()["runs"] == []
    waiting = client.get("/api/v1/workflow-runs", headers=planner,
                         params={"updated_after": before, "status": "WAITING_APPROVAL"}).json()["runs"]
    assert [r["run_id"] for r in waiting] == [second["run_id"]]
