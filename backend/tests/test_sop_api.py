"""S&OP API regression tests (public URL). Does NOT commit the cycle; does NOT run the monthly cycle."""
import json
import os
import time

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
API = f"{BASE_URL}/api/v1"
CREDS = {"planner": ("danny", os.environ.get("TEST_PLANNER_PASSWORD", "PlannerPass2026!")),
         "admin": ("admin", os.environ.get("TEST_ADMIN_PASSWORD", "AdminPass2026!")),
         "auditor": ("auditor", os.environ.get("TEST_AUDITOR_PASSWORD", "AuditorPass2026!"))}


def _login(role):
    u, p = CREDS[role]
    r = requests.post(f"{API}/auth/login", json={"username": u, "password": p}, timeout=30)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="session")
def tokens():
    return {k: _login(k)["access_token"] for k in CREDS}


def h(tokens, role):
    return {"Authorization": f"Bearer {tokens[role]}"}


# Health / auth
class TestAuth:
    def test_health(self):
        r = requests.get(f"{API}/health", timeout=30)
        assert r.status_code == 200 and r.json()["status"] == "healthy"
        assert r.json()["cutoff"] == "2026-02-01"

    def test_login_roles(self):
        for role in CREDS:
            d = _login(role)
            assert d["role"] == role and d["access_token"]

    def test_bad_login(self):
        r = requests.post(f"{API}/auth/login", json={"username": "TEST_nouser", "password": "x"}, timeout=30)
        assert r.status_code == 401

    def test_unauth_401(self):
        assert requests.get(f"{API}/summary", timeout=30).status_code == 401
        assert requests.post(f"{API}/override", json={}, timeout=30).status_code in (401, 422)


# Core workbench
class TestWorkbench:
    def test_summary_and_exceptions(self, tokens):
        s = requests.get(f"{API}/summary", headers=h(tokens, "planner"), timeout=60).json()
        assert s["exceptions_requiring_review"] == 74, s
        e = requests.get(f"{API}/exceptions?limit=500", headers=h(tokens, "planner"), timeout=60).json()
        items = e.get("items") or e.get("rows")
        assert e.get("total", len(items)) == 74

    def test_item_detail_band(self, tokens):
        e = requests.get(f"{API}/exceptions?limit=1", headers=h(tokens, "planner"), timeout=60).json()
        iid = (e.get("items") or e.get("rows"))[0]["item_id"]
        d = requests.get(f"{API}/items/{iid}", headers=h(tokens, "planner"), timeout=60)
        assert d.status_code == 200
        txt = d.text
        assert "p10" in txt.lower() and "p90" in txt.lower()
        pytest.item_id = iid

    def test_narrative(self, tokens):
        iid = getattr(pytest, "item_id", None) or 1
        r = requests.post(f"{API}/items/{iid}/narrative", headers=h(tokens, "planner"), timeout=120)
        assert r.status_code == 200, r.text
        d = r.json()
        print("narrative:", {k: (v[:120] if isinstance(v, str) else v) for k, v in d.items()})
        assert d["enabled"] is True

    def test_auditor_cannot_override(self, tokens):
        r = requests.post(f"{API}/override", headers=h(tokens, "auditor"),
                          json={"item_id": 1, "override_quantity": 1, "reason_code": "X"}, timeout=30)
        assert r.status_code == 403

    def test_override_reason_required(self, tokens):
        iid = getattr(pytest, "item_id", None)
        r = requests.post(f"{API}/override", headers=h(tokens, "planner"),
                          json={"item_id": iid, "override_quantity": 123, "reason_code": ""}, timeout=30)
        assert r.status_code == 422
        assert "Reason code is required" in r.text

    def test_metrics(self):
        r = requests.get(f"{API}/metrics", timeout=30)
        assert r.status_code == 200 and "sop_http_requests_total" in r.text and "sop_pending_exceptions" in r.text

    def test_fva_and_verify(self, tokens):
        for g in ("planner_id", "reason_code"):
            assert requests.get(f"{API}/fva?group_by={g}", headers=h(tokens, "auditor"), timeout=60).status_code == 200
        a = requests.get(f"{API}/fva/autopilot", headers=h(tokens, "auditor"), timeout=60)
        assert a.status_code == 200 and a.json()["candidates"] == []
        assert requests.get(f"{API}/audit/verify", headers=h(tokens, "planner"), timeout=30).status_code == 403
        v = requests.get(f"{API}/audit/verify", headers=h(tokens, "auditor"), timeout=60).json()
        assert v["ok"] is True


# Phase 2-4 extended
class TestExtended:
    def test_safety_stock(self, tokens):
        r = requests.get(f"{API}/inventory/safety-stock", headers=h(tokens, "planner"), timeout=60)
        assert r.status_code == 200 and r.json()["available"] is True and r.json()["items"]
        ch = r.json()["items"][0]["channel"]
        f = requests.get(f"{API}/inventory/safety-stock?channel={ch}", headers=h(tokens, "planner"), timeout=60).json()
        assert all(i["channel"] == ch for i in f["items"])

    def test_otif(self, tokens):
        r = requests.get(f"{API}/otif", headers=h(tokens, "planner"), timeout=60)
        assert r.status_code == 200
        d = r.json()
        assert "items_at_risk" in d and "total_penalty_gbp" in d
        assert "by_customer" in d  # customer dimension does not break the channel view
        if d["by_customer"]:
            assert all("customer_id" in c and "penalty_gbp" in c for c in d["by_customer"])

    def test_supply_risk_flags(self, tokens):
        r = requests.get(f"{API}/supply/risk-flags", headers=h(tokens, "planner"), timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert d["rows"] and all("channel" in f and "risk_type" in f and "count" in f for f in d["rows"])

    def test_scenario_promote(self, tokens):
        iid = getattr(pytest, "item_id", None) or 106205
        s = requests.get(f"{API}/summary", headers=h(tokens, "planner"), timeout=30).json()
        m1 = s["first_month"]
        d = requests.get(f"{API}/items/{iid}", headers=h(tokens, "planner"), timeout=60).json()
        # Base the edit on the current consensus so an existing override cannot cancel the change.
        cur = next(p for p in d["plan"] if p["month"] == m1)["consensus"]
        body = {"name": "TEST promote", "description": "regression",
                "edits": [{"type": "item", "item_id": iid, "month": m1, "qty": cur + 100}]}
        created = requests.post(f"{API}/scenarios", headers=h(tokens, "planner"), json=body, timeout=30).json()
        assert requests.post(f"{API}/scenarios/{created['id']}/promote", headers=h(tokens, "auditor"),
                             timeout=30).status_code == 403
        r = requests.post(f"{API}/scenarios/{created['id']}/promote", headers=h(tokens, "planner"), timeout=60)
        assert r.status_code == 200, r.text
        assert r.json()["promoted"] >= 1
        ov = requests.get(f"{API}/overrides?limit=200", headers=h(tokens, "planner"), timeout=30).json()
        assert any(o["item_id"] == iid and "TEST promote" in (o.get("planner_comment") or "") for o in ov)
        requests.delete(f"{API}/scenarios/{created['id']}", headers=h(tokens, "planner"), timeout=30)

    def test_disaggregate(self, tokens):
        iid = getattr(pytest, "item_id", None)
        d = requests.get(f"{API}/items/{iid}", headers=h(tokens, "planner"), timeout=60).json()
        pid = d["item"]["product_id"]
        for m in ("mint_diag", "proportional", "ols"):
            r = requests.post(f"{API}/disaggregate", headers=h(tokens, "planner"),
                              json={"product_id": pid, "total": 1000, "method": m}, timeout=60)
            assert r.status_code == 200, r.text
            tot = sum(x["proposed"] for x in r.json()["rows"])
            assert abs(tot - 1000) < 1.0, (m, tot)
        bad = requests.post(f"{API}/disaggregate", headers=h(tokens, "planner"),
                            json={"product_id": "TEST_nope", "total": 10}, timeout=30)
        assert bad.status_code == 404
        badm = requests.post(f"{API}/disaggregate", headers=h(tokens, "planner"),
                             json={"product_id": pid, "total": 10, "method": "foo"}, timeout=30)
        assert badm.status_code == 422

    def test_agentic_forecast(self, tokens):
        iid = getattr(pytest, "item_id", None) or 106205
        r = requests.get(f"{API}/forecast/items/{iid}/agentic", headers=h(tokens, "planner"), timeout=180)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["stages"] == ["research", "process", "forecast", "inform"]
        assert d["research"]["history_points"] > 0 and "nonzero_share" in d["research"]
        assert d["process"]["chosen_method"] and d["process"]["method_scores"]
        assert d["forecast"]["rows"] and d["forecast"]["rows"][0]["dynamic"] is not None
        assert d["inform"]["text"] and d["inform"]["source"] in ("llm", "deterministic")
        assert d["actions"] and any(a["type"] == "use_dynamic_forecast" for a in d["actions"])
        bad = requests.get(f"{API}/forecast/items/99999999/agentic", headers=h(tokens, "planner"), timeout=30)
        assert bad.status_code == 404

    def test_cycle_runs(self, tokens):
        runs = requests.get(f"{API}/cycles/runs", headers=h(tokens, "planner"), timeout=60)
        assert runs.status_code == 200 and "human_review" in runs.json()["nodes"]
        if runs.json()["runs"]:
            rid = runs.json()["runs"][0]["id"]
            d = requests.get(f"{API}/cycles/runs/{rid}", headers=h(tokens, "planner"), timeout=30)
            assert d.status_code == 200 and "steps" in d.json() and "quality_warnings" in d.json()

    def test_rerun_node_rbac(self, tokens):
        assert requests.post(f"{API}/admin/cycles/rerun-node", headers=h(tokens, "planner"),
                             json={"run_id": "x", "node": "ingest_validate"}, timeout=30).status_code == 403
        runs = requests.get(f"{API}/cycles/runs", headers=h(tokens, "admin"), timeout=30).json()["runs"]
        rid = runs[0]["id"] if runs else "000000000000000000000000"
        r = requests.post(f"{API}/admin/cycles/rerun-node", headers=h(tokens, "admin"),
                          json={"run_id": rid, "node": "ingest_validate"}, timeout=60)
        assert r.status_code in (404, 409)  # no failed node to re-run in a healthy cycle

    def test_run_cycle_rbac(self, tokens):
        assert requests.post(f"{API}/admin/cycles/run", headers=h(tokens, "planner"), timeout=30).status_code == 403

    def test_briefing_and_csv(self, tokens):
        r = requests.get(f"{API}/export/briefing.pdf", headers=h(tokens, "planner"), timeout=120)
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/pdf") and r.content[:4] == b"%PDF"
        c = requests.get(f"{API}/export/consensus", headers=h(tokens, "planner"), timeout=60)
        assert c.status_code == 200 and "text/csv" in c.headers["content-type"]

    def test_assistant_stream_and_history(self, tokens):
        hd = h(tokens, "auditor")
        requests.delete(f"{API}/assistant/history", headers=hd, timeout=30)
        r = requests.post(f"{API}/assistant/stream", headers=hd, json={"question": "How many exceptions need review?"},
                          stream=True, timeout=120)
        assert r.status_code == 200
        text, done, err = "", False, None
        for line in r.iter_lines(decode_unicode=True):
            if line and line.startswith("data: "):
                ev = json.loads(line[6:])
                text += ev.get("delta", "")
                done = done or ev.get("done", False)
                err = err or ev.get("error")
        print("assistant:", text[:300], err)
        assert done and text and not err
        hist = requests.get(f"{API}/assistant/history", headers=hd, timeout=30).json()["messages"]
        assert len(hist) == 2
        assert requests.delete(f"{API}/assistant/history", headers=hd, timeout=30).json()["cleared"]
        assert requests.get(f"{API}/assistant/history", headers=hd, timeout=30).json()["messages"] == []

    def test_assistant_fva(self, tokens):
        """The assistant context now carries the FVA position; the deterministic path answers FVA questions."""
        r = requests.post(f"{API}/assistant/stream", headers=h(tokens, "planner"),
                          json={"question": "What is the FVA position?"}, stream=True, timeout=120)
        assert r.status_code == 200
        text = "".join(json.loads(line[6:]).get("delta", "")
                       for line in r.iter_lines(decode_unicode=True) if line and line.startswith("data: "))
        assert "fva" in text.lower()

    def test_assistant_hybrid_tiers(self, tokens):
        """Project-data questions are tier=project; out-of-scope questions fall back per HYBRID_SEARCH."""
        for q, expected in [("What is the OTIF exposure?", "project"),
                            ("What is the tallest mountain in the world?", None)]:
            r = requests.post(f"{API}/assistant/stream", headers=h(tokens, "planner"),
                              json={"question": q}, stream=True, timeout=120)
            assert r.status_code == 200
            tier, done, err = None, False, None
            for line in r.iter_lines(decode_unicode=True):
                if line and line.startswith("data: "):
                    ev = json.loads(line[6:])
                    tier = ev.get("tier", tier)
                    done = done or ev.get("done", False)
                    err = err or ev.get("error")
            assert done and not err
            assert tier in ("project", "general_knowledge", "none")
            if expected:
                assert tier == expected

    def test_assistant_providers(self, tokens):
        """The provider health endpoint lists configured LLM providers and their last status."""
        r = requests.get(f"{API}/assistant/providers", headers=h(tokens, "planner"), timeout=30)
        assert r.status_code == 200
        d = r.json()["providers"]
        assert isinstance(d, dict) and "GlmClient" in d
        assert d["GlmClient"]["configured"] is True

    def test_assistant_concept_answer(self, tokens):
        """A concept question gets a referenced answer from the local knowledge base, not the docs."""
        r = requests.post(f"{API}/assistant/stream", headers=h(tokens, "planner"),
                          json={"question": "What is Croston's method in demand forecasting?"},
                          stream=True, timeout=120)
        assert r.status_code == 200
        tier, sources, text, done, err = None, [], "", False, None
        for line in r.iter_lines(decode_unicode=True):
            if line and line.startswith("data: "):
                ev = json.loads(line[6:])
                tier = ev.get("tier", tier)
                if ev.get("sources"):
                    sources = ev["sources"]
                text += ev.get("delta", "")
                done = done or ev.get("done", False)
                err = err or ev.get("error")
        assert done and not err
        assert tier == "general_knowledge"
        assert all(s["kind"] != "docs" for s in sources)  # no misleading project-doc sources
        assert "Croston" in text and "unavailable" not in text  # LLM paraphrase or local KB, never the dead-end message

    def test_fva_pending(self, tokens):
        """The FVA scorecard reports matured (scored) and pending overrides so it is never blank."""
        d = requests.get(f"{API}/fva", headers=h(tokens, "planner"), timeout=60).json()
        assert "pending_overrides" in d and "pending" in d
        assert d["matured_overrides"] >= 1  # demo seed provides scored historical overrides
        assert d["pending_overrides"] >= 1  # current-cycle decisions await the target month's actuals
        assert all("n_overrides" in p and "volume" in p for p in d["pending"])

    def test_assistant_view_operation(self, tokens):
        """An operation/cycle question returns a view_operation action with the latest run id."""
        r = requests.post(f"{API}/assistant/stream", headers=h(tokens, "planner"),
                          json={"question": "How did the last monthly cycle run go?"}, stream=True, timeout=120)
        assert r.status_code == 200
        actions = []
        for line in r.iter_lines(decode_unicode=True):
            if line and line.startswith("data: "):
                ev = json.loads(line[6:])
                if ev.get("actions"):
                    actions = ev["actions"]
        assert any(a["type"] == "view_operation" and a.get("run_id") for a in actions)

    def test_agent_work_notifications(self, tokens):
        """Each agent step of a cycle run surfaces as a notification with a work summary and run link."""
        d = requests.get(f"{API}/notifications", headers=h(tokens, "planner"), timeout=30).json()
        items = d.get("items", [])
        agent = [n for n in items if n.get("kind") == "agent_work"]
        assert agent, "no agent_work notifications; run the monthly cycle first"
        assert all(n.get("run_id") and n.get("link") == "operations" and n.get("body") for n in agent)
        assert any("Agent:" in n.get("title", "") for n in agent)


# Unit tests for the Tier 0 generative-content enrichment (no live API needed)
class TestTier0Enrichment:
    def test_telemetry_prior_decisions(self):
        from app.narrative.telemetry import build_telemetry
        detail = {
            "item": {"item_id": 103125, "channel": "Amazon", "phase": "GROWTH",
                     "risk_type": "CRITICAL_AMAZON_BUYBOX_ALERT", "risk_flags": ["BUYBOX"],
                     "sales_price": 12.5, "value_delta_gbp": 25000},
            "plan": [{"month": "2026-03-01", "baseline": 1200, "ml": 1300, "consensus": 1450}],
            "telemetry": {"on_hand_units": 180, "out_of_stock_rate_28d_pct": 0.125},
            "first_month": "2026-03-01",
        }
        history = [
            {"target_month": "2026-02-01", "submitted_quantity": 1200, "reason_code": "PROMO_SUPPORT",
             "planner_comment": "Buy-box push"},
            {"target_month": "2026-01-01", "submitted_quantity": 1100, "reason_code": "DEMAND_SHIFT",
             "planner_comment": None},
        ]
        tele = build_telemetry(detail, [], history=history)
        assert len(tele["prior_decisions"]) == 2
        assert tele["prior_decisions"][0] == {"month": "2026-02-01", "submitted_units": 1200,
                                              "reason": "PROMO_SUPPORT", "comment": "Buy-box push"}
        assert tele["prior_decisions"][1]["reason"] == "DEMAND_SHIFT"

    def test_inform_guardrail_fallback(self):
        """An LLM brief quoting a number outside the payload must fall back to the deterministic brief."""
        from app.forecasting import agent as forecast_agent

        class FakeLlm:
            def generate(self, system, user):
                return "The forecast plans 99,999 units next month."

        class FakeSvc:
            def item_detail(self, item_id, decisions):
                return {"plan": [{"p10": 1100, "p90": 1900}]}

        forecast = {"item_id": 103125, "rows": [{"month": "2026-03-01", "dynamic": 1450, "delta_to_current": 250}]}
        processed = {"chosen_method": "combo_sn_ma12", "chosen_reason": "holdout",
                     "method_scores": [{"method": "combo_sn_ma12", "wape": 18.2}]}
        research = {"channel": "Amazon", "phase": "GROWTH", "booked_m1": 900, "promo_overlap": [],
                    "history_points": 18, "nonzero_share": 0.9}
        out = forecast_agent._inform(FakeSvc(), 103125, None, forecast, processed, research, FakeLlm())
        assert out["source"] == "deterministic" and out["text"] == out["brief"]

    def test_unverifiable_numbers(self):
        from app.api.extended import _unverifiable_numbers
        ctx = {"cycle": {"cutoff": "2026-02-01", "exceptions_requiring_review": 74},
               "fva": {"matured_overrides": 5, "fva_pts": 1.2}}
        assert _unverifiable_numbers("74 exceptions and 5 overrides", ctx) == []
        assert _unverifiable_numbers("74 exceptions and 999 overrides", ctx) == ["999"]

class TestHybridRetrieval:
    def test_structured_intent(self):
        from app.retrieval.hybrid import _structured_intent
        assert _structured_intent("What is the OTIF exposure?") == ("otif", None)
        assert _structured_intent("How big is the penalty?") == ("otif", None)
        assert _structured_intent("What is the tallest mountain in the world?") is None

    def test_intent_precision(self):
        """Channel words alone never trigger phasing; ranking questions map to top_products."""
        from app.retrieval.hybrid import _structured_intent
        assert _structured_intent("Can you research the best selling products in Amazon?") == ("top_products", "amazon")
        assert _structured_intent("What are the top items by Amazon demand?") == ("top_products", "amazon")
        assert _structured_intent("How do B2B demand and expected deliveries differ next month?") == ("phasing", "b2b")
        assert _structured_intent("What is the delivery phasing for next month?") == ("phasing", None)
        assert _structured_intent("What is the Amazon surge situation?") is None
        assert _structured_intent("Which items have the highest value at risk?") == ("exception", None)

    def test_lexical_hits_docs(self):
        from app.retrieval.hybrid import _lexical_hits

        class FakeLedger:
            def overrides(self, limit=None):
                return []

        hits = _lexical_hits("How does the monthly planning cycle work?", FakeLedger())
        assert hits and hits[0].kind == "docs" and hits[0].score >= 2

    def test_retrieve_gating(self):
        from app.retrieval.hybrid import retrieve

        class FakeSvc:
            def item_row(self, item_id):
                return None

        class FakeLedger:
            def overrides(self, limit=None):
                return []

        svc, ledger = FakeSvc(), FakeLedger()
        assert retrieve("What is the OTIF exposure?", svc, ledger, "tier1").tier == "project"
        assert retrieve("What is the tallest mountain in the world?", svc, ledger, "tier1").tier == "none"
        assert retrieve("What is the tallest mountain in the world?", svc, ledger, "tier2").tier == "general_knowledge"
        assert retrieve("What is the tallest mountain in the world?", svc, ledger, "off").tier == "none"
        r = retrieve("Can you research the best selling products in Amazon?", svc, ledger, "tier1")
        assert r.tier == "project" and r.sources[0].title == "top_products:amazon"

    def test_concept_routing(self):
        """Concept questions go to general_knowledge with a concept source, never the project docs."""
        from app.retrieval.hybrid import retrieve

        class FakeSvc:
            def item_row(self, item_id):
                return None

        class FakeLedger:
            def overrides(self, limit=None):
                return []

        r = retrieve("What is Croston's method in demand forecasting?", FakeSvc(), FakeLedger(), "tier1")
        assert r.tier == "general_knowledge"
        assert r.sources and r.sources[0].kind == "concept" and r.sources[0].title == "Croston's method"
        # project intents still win over concept keywords (e.g. "otif")
        assert retrieve("What is the OTIF exposure?", FakeSvc(), FakeLedger(), "tier1").tier == "project"


# Unit tests for the LLM failover chain (Gemini -> GLM -> local)
class TestLlmFailover:
    def test_failover_falls_through(self):
        import asyncio

        from app.narrative.llm import FailoverClient

        class Boom:
            def generate(self, system, user):
                raise RuntimeError("quota exhausted")

            async def stream(self, system, history, user):
                raise RuntimeError("quota exhausted")
                yield  # pragma: no cover

        class Ok:
            def generate(self, system, user):
                return "deepseek answer"

            async def stream(self, system, history, user):
                yield "deepseek answer"

        fc = FailoverClient([Boom(), Ok()])
        assert fc.generate("s", "u") == "deepseek answer"

        async def collect():
            return "".join([c async for c in fc.stream("s", [], "u")])

        assert asyncio.run(collect()) == "deepseek answer"

    def test_failover_all_fail(self):
        from app.narrative.llm import FailoverClient

        class Boom:
            def generate(self, system, user):
                raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            FailoverClient([Boom()]).generate("s", "u")

    def test_glm_messages(self):
        from app.narrative.client import GlmClient

        c = GlmClient(api_key="k")
        assert c._messages("sys", "usr") == [{"role": "system", "content": "sys"}, {"role": "user", "content": "usr"}]

    def test_glm_generate(self):
        import httpx

        from app.narrative.client import GlmClient

        def handler(request):
            assert request.headers["authorization"] == "Bearer k"
            return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

        c = GlmClient(api_key="k", transport=httpx.MockTransport(handler))
        assert c.generate("s", "u") == "hi"

    def test_glm_stream_sse(self):
        import asyncio

        import httpx

        from app.narrative.client import GlmClient

        def handler(request):
            body = ('data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
                    'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
                    "data: [DONE]\n\n")
            return httpx.Response(200, text=body)

        c = GlmClient(api_key="k", transport=httpx.MockTransport(handler))

        async def collect():
            return "".join([chunk async for chunk in c.stream("s", [], "u")])

        assert asyncio.run(collect()) == "Hello"


# Unit tests for the honest-fallback gate (never answer off-topic when the LLM chain is down)
class TestHonestFallback:
    def test_det_branch(self):
        from app.api.extended import _det_branch
        assert _det_branch("What is the OTIF exposure?") == "otif"
        assert _det_branch("Can you research the best selling products in Amazon?") == "top_products"
        assert _det_branch("What is the tallest mountain in the world?") is None

    def test_honest_message(self):
        from app.api.extended import _deterministic_or_honest
        out = _deterministic_or_honest("What is the tallest mountain in the world?", {})
        assert "does not contain this answer" in out

    def test_top_products_deterministic(self):
        from app.api.extended import deterministic_answer
        ctx = {"top_products": {"amazon": [{"item_id": 103125, "channel": "amazon", "demand": 5000.0}]}}
        out = deterministic_answer("Can you research the best selling products in Amazon?", ctx)
        assert "103125" in out and "5,000" in out


# Unit tests for the local concept knowledge base (answers general-knowledge questions offline)
class TestConceptKnowledgeBase:
    def test_lookup_croston(self):
        from app.narrative.concepts import concept_answer, lookup_concept
        assert lookup_concept("What is Croston's method in demand forecasting?")["title"] == "Croston's method"
        out = concept_answer("What is Croston's method in demand forecasting?")
        assert "exponential smoothing" in out and "1972" in out and "Reference:" in out
        assert concept_answer("What is the tallest mountain in the world?") is None

    def test_lookup_other_concepts(self):
        from app.narrative.concepts import concept_answer
        assert "Reference:" in concept_answer("Explain exponential smoothing.")
        assert "Reference:" in concept_answer("What is forecast value added?")
        assert "Reference:" in concept_answer("How is WAPE calculated?")
