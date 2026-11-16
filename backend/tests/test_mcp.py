# MCP configuration: registry CRUD, RBAC, test connection, tool calls, audit log
import os

import requests

BASE = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/") + "/api/v1"
CREDS = {"danny": os.environ.get("TEST_PLANNER_PASSWORD", "PlannerPass2026!"),
         "admin": os.environ.get("TEST_ADMIN_PASSWORD", "AdminPass2026!"),
         "auditor": os.environ.get("TEST_AUDITOR_PASSWORD", "AuditorPass2026!")}
_tok = {}

UNREACHABLE = "http://127.0.0.1:1/mcp"


def h(user):
    if user not in _tok:
        r = requests.post(f"{BASE}/auth/login", json={"username": user, "password": CREDS[user]}, timeout=30)
        assert r.status_code == 200, r.text
        _tok[user] = r.json()["access_token"]
    return {"Authorization": f"Bearer {_tok[user]}"}


def _server(server_id="erp_sap", **over):
    body = {"id": server_id, "name": "SAP ERP", "kind": "erp", "transport": "streamable_http",
            "url": UNREACHABLE, "headers": {"Authorization": "Bearer sk-secret123456"}, "enabled": True,
            "tools": ["get_stock_levels"], "timeout_s": 30}
    body.update(over)
    return body


def _cleanup(server_id):
    requests.delete(f"{BASE}/mcp/servers/{server_id}", headers=h("admin"), timeout=30)


class TestRegistry:
    def test_create_requires_admin(self):
        r = requests.post(f"{BASE}/mcp/servers", json=_server("erp_planner"), headers=h("danny"), timeout=30)
        assert r.status_code == 403
        _cleanup("erp_planner")

    def test_create_and_list_masks_secrets(self):
        _cleanup("erp_sap")
        r = requests.post(f"{BASE}/mcp/servers", json=_server(), headers=h("admin"), timeout=30)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["id"] == "erp_sap" and d["kind_label"] == "ERP"
        assert d["headers"]["Authorization"] == "Be****3456"  # masked, never the raw token
        r = requests.get(f"{BASE}/mcp/servers", headers=h("auditor"), timeout=30)
        assert r.status_code == 200
        ids = {s["id"] for s in r.json()["servers"]}
        assert "erp_sap" in ids
        assert "kinds" in r.json() and "erp" in r.json()["kinds"]
        _cleanup("erp_sap")

    def test_duplicate_409(self):
        _cleanup("erp_dup")
        requests.post(f"{BASE}/mcp/servers", json=_server("erp_dup"), headers=h("admin"), timeout=30)
        r = requests.post(f"{BASE}/mcp/servers", json=_server("erp_dup"), headers=h("admin"), timeout=30)
        assert r.status_code == 409
        _cleanup("erp_dup")

    def test_invalid_payload_422(self):
        for body in (_server("Bad ID"), _server("erp_bad", kind="sap"), _server("erp_bad", url="ftp://x"),
                     _server("erp_bad", timeout_s=0)):
            r = requests.post(f"{BASE}/mcp/servers", json=body, headers=h("admin"), timeout=30)
            assert r.status_code == 422, (body, r.status_code)
        _cleanup("Bad ID")
        _cleanup("erp_bad")

    def test_update_and_delete(self):
        _cleanup("erp_upd")
        requests.post(f"{BASE}/mcp/servers", json=_server("erp_upd"), headers=h("admin"), timeout=30)
        r = requests.put(f"{BASE}/mcp/servers/erp_upd", headers=h("admin"),
                         json={"name": "SAP ERP PROD", "enabled": False, "headers": {"X-API-Key": "k-abcdef123456"}}, timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert d["name"] == "SAP ERP PROD" and d["enabled"] is False
        assert d["headers"]["Authorization"] == "Be****3456"  # merged, still masked
        assert d["headers"]["X-API-Key"] == "k-****3456"
        r = requests.delete(f"{BASE}/mcp/servers/erp_upd", headers=h("admin"), timeout=30)
        assert r.status_code == 200
        r = requests.get(f"{BASE}/mcp/servers/erp_upd", headers=h("admin"), timeout=30)
        assert r.status_code == 404

    def test_unknown_404(self):
        r = requests.get(f"{BASE}/mcp/servers/nope", headers=h("admin"), timeout=30)
        assert r.status_code == 404
        r = requests.put(f"{BASE}/mcp/servers/nope", headers=h("admin"), json={"name": "x"}, timeout=30)
        assert r.status_code == 404
        r = requests.delete(f"{BASE}/mcp/servers/nope", headers=h("admin"), timeout=30)
        assert r.status_code == 404


class TestConnection:
    def test_test_connection_fails_gracefully(self):
        _cleanup("erp_down")
        requests.post(f"{BASE}/mcp/servers", json=_server("erp_down", timeout_s=2), headers=h("admin"), timeout=30)
        r = requests.post(f"{BASE}/mcp/servers/erp_down/test", headers=h("admin"), timeout=30)
        assert r.status_code == 502
        assert r.json()["detail"].startswith(("timeout:", "server_error:"))
        r = requests.get(f"{BASE}/mcp/servers/erp_down/tools", headers=h("danny"), timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert d["status"] == "failed" and d["last_error"]
        _cleanup("erp_down")

    def test_planner_cannot_test(self):
        _cleanup("erp_pt")
        requests.post(f"{BASE}/mcp/servers", json=_server("erp_pt"), headers=h("admin"), timeout=30)
        r = requests.post(f"{BASE}/mcp/servers/erp_pt/test", headers=h("danny"), timeout=30)
        assert r.status_code == 403
        _cleanup("erp_pt")


class TestCall:
    def test_call_disabled_409(self):
        _cleanup("erp_off")
        requests.post(f"{BASE}/mcp/servers", json=_server("erp_off", enabled=False), headers=h("admin"), timeout=30)
        r = requests.post(f"{BASE}/mcp/servers/erp_off/call", headers=h("danny"),
                          json={"tool": "get_stock_levels", "arguments": {}}, timeout=30)
        assert r.status_code == 409
        _cleanup("erp_off")

    def test_call_not_allowlisted_403(self):
        _cleanup("erp_al")
        requests.post(f"{BASE}/mcp/servers", json=_server("erp_al"), headers=h("admin"), timeout=30)
        r = requests.post(f"{BASE}/mcp/servers/erp_al/call", headers=h("danny"),
                          json={"tool": "get_open_purchase_orders", "arguments": {}}, timeout=30)
        assert r.status_code == 403
        _cleanup("erp_al")

    def test_call_unreachable_502_and_audited(self):
        _cleanup("erp_call")
        requests.post(f"{BASE}/mcp/servers", json=_server("erp_call", timeout_s=2), headers=h("admin"), timeout=30)
        r = requests.post(f"{BASE}/mcp/servers/erp_call/call", headers=h("danny"),
                          json={"tool": "get_stock_levels", "arguments": {}}, timeout=30)
        assert r.status_code == 502
        r = requests.get(f"{BASE}/mcp/calls", headers=h("auditor"), timeout=30)
        assert r.status_code == 200
        calls = r.json()["calls"]
        assert any(c["server_id"] == "erp_call" and c["tool"] == "get_stock_levels" and c["ok"] is False for c in calls)
        _cleanup("erp_call")

    def test_auditor_cannot_call(self):
        _cleanup("erp_aud")
        requests.post(f"{BASE}/mcp/servers", json=_server("erp_aud"), headers=h("admin"), timeout=30)
        r = requests.post(f"{BASE}/mcp/servers/erp_aud/call", headers=h("auditor"),
                          json={"tool": "get_stock_levels", "arguments": {}}, timeout=30)
        assert r.status_code == 403
        _cleanup("erp_aud")


class TestWriteToolGating:
    def test_write_tool_planner_403_admin_attempts(self):
        _cleanup("erp_write")
        requests.post(f"{BASE}/mcp/servers",
                      json=_server("erp_write", tools=["push_forecast"], timeout_s=2), headers=h("admin"), timeout=30)
        r = requests.post(f"{BASE}/mcp/servers/erp_write/call", headers=h("danny"),
                          json={"tool": "push_forecast", "arguments": {"item_id": 100001, "month": "2026-03-01", "units": 10}}, timeout=30)
        assert r.status_code == 403  # write tools are admin-only
        r = requests.post(f"{BASE}/mcp/servers/erp_write/call", headers=h("admin"),
                          json={"tool": "push_forecast", "arguments": {"item_id": 100001, "month": "2026-03-01", "units": 10}}, timeout=30)
        assert r.status_code == 502  # admin passes the gate; the unreachable server fails the call
        r = requests.get(f"{BASE}/mcp/calls", headers=h("auditor"), timeout=30)
        calls = r.json()["calls"]
        assert any(c["server_id"] == "erp_write" and c["tool"] == "push_forecast" and c["ok"] is False for c in calls)
        _cleanup("erp_write")

    def test_intent_never_maps_to_write_tools(self):
        from app.mcp.intent import KINDS
        from app.mcp.registry import is_write_tool
        for kind, spec in KINDS.items():
            for keys, tool in spec["tools"]:
                assert not is_write_tool(kind, tool), f"{kind}/{tool} is a write tool and must not be assistant-mapped"


class TestAssistantMCP:
    """Requires the demo ERP MCP server on http://127.0.0.1:9101 (python -m app.mcp.demo --kind erp --port 9101)."""

    def _stream(self, question, user="danny"):
        r = requests.post(f"{BASE}/assistant/stream", headers=h(user), json={"question": question},
                          timeout=90, stream=True)
        assert r.status_code == 200
        return "".join(line.decode("utf-8", "replace") for line in r.iter_lines() if line)

    def test_mcp_evidence_source(self):
        _cleanup("erp_demo")
        _cleanup("erp_ai")
        requests.post(f"{BASE}/mcp/servers",
                      json=_server("erp_ai", url="http://127.0.0.1:9101/", tools=["get_open_purchase_orders"]),
                      headers=h("admin"), timeout=30)
        body = self._stream("What open purchase orders are in SAP?")
        assert '"kind": "mcp"' in body
        assert "mcp:erp_ai" in body and "get_open_purchase_orders" in body
        _cleanup("erp_ai")

    def test_mcp_failure_deterministic(self):
        _cleanup("erp_demo")
        _cleanup("erp_down2")
        requests.post(f"{BASE}/mcp/servers", json=_server("erp_down2", timeout_s=2, tools=["get_open_purchase_orders"]), headers=h("admin"), timeout=30)
        body = self._stream("What open purchase orders are in SAP?")
        assert "is unavailable" in body
        assert '"kind": "mcp"' in body  # the failed attempt is still surfaced as a source
        _cleanup("erp_down2")
