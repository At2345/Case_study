"""Playbook template library and the give-to-get cross-tenant benchmark."""
from datetime import timedelta

import pytest

from app.workflows import templates, webhooks
from app.workflows.service import WorkflowService
from tests.test_api import auth, env  # noqa: F401  (fixture reuse)
from tests.test_workflows import DEEP, MILD, T0

STOCK_URL = "https://n8n.example.com/webhook/stock"


# ---------------------------------------------------------------- rendering
def test_render_applies_typed_parameters():
    spec = templates.render("rescue-basic", "my-rescue", {"budget_eur": "1500", "transfer_to": "IT"})
    assert spec["id"] == "my-rescue" and spec["budget_eur"] == 1500.0
    assert spec["template"] == {"id": "rescue-basic", "version": 1}
    transfer = next(n for n in spec["nodes"] if n["id"] == "transfer")
    assert transfer["params"]["to_marketplace"] == "IT"
    tell = next(n for n in spec["nodes"] if n["id"] == "tell")
    assert tell["params"]["message"] == "Rescue action done for IT plan."  # in-string placeholder
    assert "{{" not in str(spec)


def test_render_rejects_bad_parameters():
    with pytest.raises(ValueError, match="required"):
        templates.render("rescue-measured", "x")
    with pytest.raises(ValueError, match="http"):
        templates.render("rescue-measured", "x", {"stock_webhook_url": "ftp://nope"})
    with pytest.raises(ValueError, match="must be a number"):
        templates.render("rescue-basic", "x", {"budget_eur": "lots"})
    with pytest.raises(ValueError, match="unknown"):
        templates.render("rescue-basic", "x", {"colour": "red"})
    with pytest.raises(KeyError):
        templates.render("nope", "x")


def test_every_template_installs_validates_and_dry_runs(tmp_path):
    svc = WorkflowService(tmp_path)
    sample = {"url": STOCK_URL, "string": "conn-1", "number": 1, "integer": 1}
    for tid in templates.TEMPLATES:
        required = {p["name"]: sample[p["type"]] for p in templates.TEMPLATES[tid]["params"] if p["required"]}
        wf = svc.install_template("acme", tid, f"t-{tid}", required)
        assert wf.template["id"] == tid and wf.version == 1
        run = svc.start("acme", wf.id, templates.public(templates.TEMPLATES[tid])["example_payload"], "danny",
                        now=T0, dry_run=True)
        assert run["status"] in ("SUCCEEDED", "WAITING", "WAITING_APPROVAL"), (tid, run["error"])
    with pytest.raises(ValueError, match="already exists"):
        svc.install_template("acme", "rescue-basic", "t-rescue-basic")


# ---------------------------------------------------------------- benchmark
@pytest.fixture
def fleet(tmp_path, monkeypatch):
    """Four tenants run the measured template; three share their results."""
    def fake_post(url, body, idempotency_key, secret_ref=None, timeout_s=10.0):
        return {"status_code": 200, "response": {"units_on_hand": 50, "units_sold": 950}, "truncated": False}

    monkeypatch.setattr(webhooks, "post_json", fake_post)
    svc = WorkflowService(tmp_path)
    for tenant, shares in (("acme", True), ("globex", True), ("initech", True), ("umbrella", False)):
        svc.install_template(tenant, "rescue-measured", "rescue", {"stock_webhook_url": STOCK_URL})
        run = svc.start(tenant, "rescue", MILD, "planner", now=T0)
        svc.advance(tenant, run["run_id"], now=T0 + timedelta(days=60))
        svc.update_settings(tenant, shares)
    return svc


def by_id(rows):
    return {r["template"]["id"]: r for r in rows}


def test_pooled_benchmark_for_sharing_tenants(fleet):
    acme = by_id(fleet.template_benchmark("acme", min_tenants=3))
    measured = acme["rescue-measured"]
    # Each run: spend 160, loss avoided (200 - 50) x 8 x 0.75 = 900.
    assert measured["yours"] == {"playbooks": 1, "runs": 1, "measured_runs": 1, "spent_eur": 160.0,
                                 "value_eur": 900.0, "net_eur": 740.0, "value_per_eur": 5.62}
    assert measured["pooled_status"] == "available"
    assert measured["pooled"]["tenants"] == 3 and measured["pooled"]["runs"] == 3  # umbrella excluded
    assert measured["pooled"]["value_eur"] == 2700.0
    assert acme["rescue-basic"]["yours"] is None and acme["rescue-basic"]["pooled_status"] == "insufficient_data"


def test_give_to_get_and_minimum_group_size(fleet):
    umbrella = by_id(fleet.template_benchmark("umbrella", min_tenants=3))["rescue-measured"]
    assert umbrella["pooled_status"] == "share_to_see" and umbrella["pooled"] is None
    assert umbrella["yours"]["runs"] == 1  # own results are always visible

    fleet.update_settings("initech", False)  # only two sharers left
    acme = by_id(fleet.template_benchmark("acme", min_tenants=3))["rescue-measured"]
    assert acme["pooled_status"] == "insufficient_data" and acme["pooled"] is None
    assert "tenants" not in acme["yours"]


def test_template_link_survives_edits(fleet):
    wf = fleet.get("acme", "rescue")
    edited = wf.model_dump()
    edited["budget_eur"] = 3000
    saved = fleet.save("acme", edited)
    assert saved.version == 2 and saved.template == {"id": "rescue-measured", "version": 1}
    assert by_id(fleet.template_benchmark("acme"))["rescue-measured"]["yours"]["runs"] == 1


# ---------------------------------------------------------------- API
def test_template_api(env):  # noqa: F811
    client, _, _ = env
    planner, admin, auditor = auth(client), auth(client, "root"), auth(client, "ada")
    listed = client.get("/api/v1/workflows-templates", headers=auditor).json()
    assert listed["sharing"] is False and listed["min_tenants"] == 3
    assert {t["template"]["id"] for t in listed["templates"]} == set(templates.TEMPLATES)
    assert all(t["pooled_status"] == "share_to_see" for t in listed["templates"])
    params = next(t for t in listed["templates"] if t["template"]["id"] == "rescue-measured")["template"]["params"]
    assert next(p for p in params if p["name"] == "stock_webhook_url")["required"] is True

    url = "/api/v1/workflows-templates/rescue-basic/install"
    assert client.post(url, headers=auditor, json={"workflow_id": "r1"}).status_code == 403
    made = client.post(url, headers=planner, json={"workflow_id": "r1", "name": "DE rescue", "params": {"transfer_to": "IT"}})
    assert made.status_code == 201 and made.json()["template"]["id"] == "rescue-basic" and made.json()["name"] == "DE rescue"
    assert client.post(url, headers=planner, json={"workflow_id": "r1"}).status_code == 422  # exists
    assert client.post("/api/v1/workflows-templates/nope/install", headers=planner, json={"workflow_id": "r2"}).status_code == 404
    bad = client.post("/api/v1/workflows-templates/rescue-measured/install", headers=planner, json={"workflow_id": "r3"})
    assert bad.status_code == 422 and "required" in bad.json()["detail"]
    run = client.post("/api/v1/workflows/r1/runs", headers=planner, json={"payload": DEEP}).json()
    assert run["status"] == "WAITING_APPROVAL"

    assert client.put("/api/v1/admin/workflows-settings", headers=planner, json={"share_benchmarks": True}).status_code == 403
    assert client.put("/api/v1/admin/workflows-settings", headers=admin, json={"share_benchmarks": True}).json() == {"share_benchmarks": True}
    assert client.get("/api/v1/workflows-templates", headers=planner).json()["sharing"] is True
