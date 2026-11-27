"""Playbook MCP server: protocol handling, agent guardrails, and the HTTP endpoint with API keys."""
import pytest

from app.mcp import client as mcp_client
from app.workflows.mcp_server import LATEST_VERSION, PlaybookMcpServer
from app.workflows.runners import LocalRunner
from app.workflows.service import WorkflowService
from tests.test_api import auth, env  # noqa: F401  (fixture reuse)
from tests.test_workflows import DEEP, MILD, rescue_playbook

PLANNER = {"username": "danny", "role": "planner", "tenant": "acme"}
AUDITOR = {"username": "ada", "role": "auditor", "tenant": "acme"}


@pytest.fixture
def svc(tmp_path):
    s = WorkflowService(tmp_path)
    s.save("acme", rescue_playbook().model_dump())
    return s


def rpc(server, method, params=None, user=PLANNER, req_id=1):
    msg = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        msg["params"] = params
    return server.handle(msg, user)


def call(server, name, args=None, user=PLANNER):
    return rpc(server, "tools/call", {"name": name, "arguments": args or {}}, user)


# ---------------------------------------------------------------- protocol
def test_initialize_and_basic_methods(svc):
    server = PlaybookMcpServer(svc, LocalRunner(svc))
    init = rpc(server, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t"}})
    assert init["result"]["protocolVersion"] == "2025-03-26"
    assert init["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert rpc(server, "initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"] == LATEST_VERSION
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, PLANNER) is None
    assert rpc(server, "ping")["result"] == {}
    assert rpc(server, "resources/list")["error"]["code"] == -32601
    assert server.handle({"id": 1, "method": "ping"}, PLANNER)["error"]["code"] == -32600  # missing jsonrpc
    assert server.handle(["not", "an", "object"], PLANNER)["error"]["code"] == -32600


def test_tool_visibility_by_role_and_approval_flag(svc):
    default = PlaybookMcpServer(svc, LocalRunner(svc))
    names = lambda server, user: [t["name"] for t in rpc(server, "tools/list", {}, user)["result"]["tools"]]  # noqa: E731
    assert names(default, PLANNER) == ["list_playbooks", "get_playbook", "list_runs", "get_run",
                                       "get_playbook_performance", "list_templates", "draft_playbook",
                                       "record_outcome", "start_run"]
    assert names(default, AUDITOR) == ["list_playbooks", "get_playbook", "list_runs", "get_run",
                                       "get_playbook_performance", "list_templates"]
    approvals = PlaybookMcpServer(svc, LocalRunner(svc), allow_approvals=True)
    assert "decide_step" in names(approvals, PLANNER) and "decide_step" not in names(approvals, AUDITOR)

    tools = {t["name"]: t for t in rpc(default, "tools/list", {})["result"]["tools"]}
    assert tools["get_run"]["annotations"]["readOnlyHint"] is True
    assert tools["start_run"]["annotations"] == {"readOnlyHint": False, "destructiveHint": True,
                                                 "idempotentHint": False, "openWorldHint": False}
    schema = tools["start_run"]["inputSchema"]
    assert schema["required"] == ["playbook_id"] and schema["properties"]["dry_run"]["default"] is True
    assert schema["additionalProperties"] is False


def test_start_run_is_dry_by_default(svc):
    server = PlaybookMcpServer(svc, LocalRunner(svc))
    res = call(server, "start_run", {"playbook_id": "rescue-de", "payload": MILD})["result"]
    run = res["structuredContent"]
    assert res["isError"] is False and run["dry_run"] is True and run["spent_eur"] == 0.0
    assert run["started_by"] == "danny"
    listed = call(server, "list_runs", {"playbook_id": "rescue-de"})["result"]["structuredContent"]["runs"]
    assert [r["run_id"] for r in listed] == [run["run_id"]]
    playbooks = call(server, "list_playbooks")["result"]["structuredContent"]["playbooks"]
    assert playbooks == [{"id": "rescue-de", "name": "Rescue slow batches in DE", "version": 1}]


def test_real_run_with_approval_through_mcp(svc):
    server = PlaybookMcpServer(svc, LocalRunner(svc), allow_approvals=True)
    run = call(server, "start_run", {"playbook_id": "rescue-de", "payload": DEEP, "dry_run": False})["result"]["structuredContent"]
    assert run["status"] == "WAITING_APPROVAL" and run["pending"]["node"] == "transfer"
    wrong = call(server, "decide_step", {"run_id": run["run_id"], "node": "cut_price", "approved": True})["result"]
    assert wrong["isError"] is True and "not awaiting approval" in wrong["content"][0]["text"]
    done = call(server, "decide_step", {"run_id": run["run_id"], "node": "transfer", "approved": True,
                                        "note": "agent asked a human first"})["result"]["structuredContent"]
    assert done["status"] == "SUCCEEDED" and done["outputs"]["transfer"]["units"] == 600


def test_errors_are_reported_the_mcp_way(svc):
    server = PlaybookMcpServer(svc, LocalRunner(svc))
    assert call(server, "nope")["error"]["code"] == -32602
    bad = call(server, "start_run", {"playbook_id": "rescue-de", "surprise": 1})["error"]
    assert bad["code"] == -32602 and "surprise" in bad["message"]
    missing = call(server, "get_run", {"run_id": "doesnotexist"})["result"]
    assert missing["isError"] is True and missing["content"][0]["text"].startswith("Not found")
    assert call(server, "start_run", {"playbook_id": "rescue-de"}, user=AUDITOR)["error"]["code"] == -32602
    # decide_step is hidden (not merely refused) while approvals are off
    assert call(server, "decide_step", {"run_id": "x", "node": "y", "approved": True})["error"]["code"] == -32602


# ---------------------------------------------------------------- HTTP endpoint through the real app
def _api_key(client, role="planner"):
    r = client.post("/api/v1/admin/api-keys", headers=auth(client, "root"), json={"name": f"agent-{role}", "role": role})
    return r.json()["key"]


def test_repo_mcp_client_talks_to_playbook_server(env, monkeypatch):  # noqa: F811
    client, _, _ = env
    key = _api_key(client)
    client.post("/api/v1/workflows", headers=auth(client), json=rescue_playbook().model_dump())

    def via_testclient(url, json=None, headers=None, timeout=None):
        return client.post(url.replace("http://testserver", ""), json=json, headers=headers)

    monkeypatch.setattr(mcp_client.httpx, "post", via_testclient)
    config = {"url": "http://testserver/api/v1/workflows/mcp", "headers": {"Authorization": f"Bearer {key}"}}
    tools = [t["name"] for t in mcp_client.list_tools(config)]
    assert "start_run" in tools and "decide_step" not in tools
    result = mcp_client.call_tool(config, "start_run", {"playbook_id": "rescue-de", "payload": MILD})
    assert '"dry_run": true' in mcp_client.tool_text(result)


def test_http_endpoint_rules(env):  # noqa: F811
    client, _, _ = env
    h = {"Authorization": f"Bearer {_api_key(client)}"}
    url = "/api/v1/workflows/mcp"
    note = client.post(url, headers=h, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert note.status_code == 202 and note.content == b""
    assert client.get(url, headers=h).status_code == 405
    assert client.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 401
    evil = client.post(url, headers={**h, "Origin": "https://evil.example"}, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert evil.status_code == 403
    bad_version = client.post(url, headers={**h, "MCP-Protocol-Version": "1999-01-01"},
                              json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert bad_version.status_code == 400
    batch = client.post(url, headers=h, json=[{"jsonrpc": "2.0", "id": 1, "method": "ping"}]).json()
    assert batch["error"]["code"] == -32600
    parse = client.post(url, headers={**h, "Content-Type": "application/json"}, content=b"{not json").json()
    assert parse["error"]["code"] == -32700

    auditor = {"Authorization": f"Bearer {_api_key(client, 'auditor')}"}
    listed = client.post(url, headers=auditor, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).json()
    assert "start_run" not in [t["name"] for t in listed["result"]["tools"]]

    reserved = client.post("/api/v1/workflows", headers=auth(client), json=rescue_playbook(id="mcp").model_dump())
    assert reserved.status_code == 422 and "reserved" in reserved.json()["detail"]
