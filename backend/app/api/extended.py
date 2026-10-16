"""Phase 2-4 endpoints: OTIF, disaggregation, safety stock, pipelines, orchestration, assistant, briefing, autopilot."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.service import clean
from app.config import settings
from app.evaluation.disaggregate import METHODS, disaggregate
from app.evaluation.fva import negative_fva_streaks, override_fva
from app.forecasting import dynamic as dynamic_forecast
from app.models.enums import ReasonCode, Role
from app.narrative.concepts import concept_answer
from app.narrative.guardrails import NUMBER as _NUMBER_RE
from app.narrative.guardrails import _allowed_numbers
from app.orchestration import agent_actions
from app.retrieval.hybrid import CHANNELS, _structured_intent, retrieve
from app.rules import autopilot as autopilot_rules
from app.rules import otif


def _step_summaries(steps: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Attach a plain-language summary to each step so UIs never render raw detail JSON."""
    from app.orchestration.jobs import _node_summary
    out = []
    for s in steps or []:
        step = dict(s)
        if step.get("detail") is not None:
            step["summary"] = _node_summary(step.get("node"), step.get("detail"))
        out.append(step)
    return out


log = logging.getLogger("sop.api.ext")

GENERAL_KNOWLEDGE_PROMPT = (
    "You are answering a demand planner's question using your own general knowledge. "
    "The project's data does not contain the answer, so do not claim it does. "
    "Clearly label the answer as general knowledge, not from the project's data. "
    "Never invent project-specific figures (item ids, quantities, cutoffs, FVA or OTIF numbers). "
    "If your knowledge may be dated, say so (for example 'as of my knowledge cutoff'). "
    "Answer in 2-4 plain sentences. No markdown, no lists, no greetings."
)


ASSISTANT_PROMPT = (
    "You are the S&OP co-pilot for a UK consumer-goods demand planning team. Answer the planner's question using only "
    "the JSON context provided with each question (current cycle summary, exception queue, order-book phasing, OTIF "
    "risk, safety stock, data-quality warnings, FVA). Quote numbers as they appear, use GBP and units, and say plainly "
    "when the context does not contain the answer. In the exception queue, current_final is the plan after planner decisions. "
    "Answer in plain text without markdown (no asterisks or headings); short numbered lines are fine. At most 6 sentences."
)


class DisaggregateRequest(BaseModel):
    product_id: str = Field(..., min_length=1, max_length=64)
    total: float = Field(..., ge=0, le=100_000_000)
    month: Optional[str] = None
    method: str = Field("mint_diag", pattern="^(mint_diag|proportional|ols)$")


class AssistantRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000)


class AutopilotApplyRequest(BaseModel):
    segment: str = Field("channel", pattern="^(channel|abc_category)$")
    value: str = Field(..., min_length=1, max_length=64)


class RerunNodeRequest(BaseModel):
    run_id: str = Field(..., min_length=1, max_length=64)
    node: str = Field(..., min_length=1, max_length=64)


def _unverifiable_numbers(text: str, ctx: Dict[str, Any]) -> List[str]:
    """Numbers in the assistant text that do not appear anywhere in the cycle context."""
    allowed = _allowed_numbers(_round(ctx), set())
    out = []
    for token in _NUMBER_RE.findall(text or ""):
        value = float(token.replace(",", ""))
        if not any(abs(value - a) < 1e-6 for a in allowed):
            out.append(token)
    return out


def _round(node: Any) -> Any:
    if isinstance(node, float):
        return round(node, 1)
    if isinstance(node, dict):
        return {k: _round(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_round(v) for v in node]
    return node


def _fmt_num(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):,.0f}"
    except (TypeError, ValueError):
        return str(value)


def _fmt_gbp(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"£{float(value):,.0f}"
    except (TypeError, ValueError):
        return str(value)


def _det_branch(question: str) -> Optional[str]:
    """Which deterministic_answer branch would fire for this question, or None for the generic lead.
    Uses the same precision rules as the retrieval router so the fallback never answers off-topic."""
    intent = _structured_intent(question)
    if intent:
        return intent[0]
    q = question.lower()
    if any(word in q for word in ("amazon", "surge", "glance")):
        return "amazon"
    if any(word in q for word in ("pending", "decided", "cycle", "summary", "status")):
        return "cycle"
    return None


def _deterministic_or_honest(question: str, ctx: Dict[str, Any]) -> str:
    """Deterministic answer when the question maps to a known branch; otherwise an honest
    'not in project data' message instead of an off-topic keyword answer."""
    if _det_branch(question) is None:
        return "The project data does not contain this answer, and the knowledge-base fallback is unavailable."
    return deterministic_answer(question, ctx)


def deterministic_answer(question: str, ctx: Dict[str, Any]) -> str:
    branch = _det_branch(question)
    q = question.lower()
    cycle = ctx.get("cycle") or {}
    queue = ctx.get("exception_queue_top") or []
    quality = ctx.get("quality_warnings") or []
    otif_ctx = ctx.get("otif") or {}
    phasing = ctx.get("phasing_first_month") or []
    safety = ctx.get("safety_stock") or {}
    amazon = ctx.get("amazon_pipeline") or {}
    if branch == "otif":
        top = otif_ctx.get("top") or []
        rows = [f"Item {r.get('item_id')}: penalty {_fmt_gbp(r.get('penalty_gbp'))}, shortfall {_fmt_num(r.get('shortfall_qty'))} units." for r in top[:3]]
        return "OTIF exposure: " + _fmt_num(otif_ctx.get("items_at_risk")) + " item-months at risk, estimated penalty " + _fmt_gbp(otif_ctx.get("total_penalty_gbp")) + (" Top drivers: " + " ".join(rows) if rows else ".")
    if branch == "quality":
        if not quality:
            return "No data-quality warnings are present in the current cycle context."
        return "Data-quality warnings: " + "; ".join(str(w) for w in quality[:5])
    if branch == "phasing":
        if not phasing:
            return "No phasing rows are available for the first forecast month in the current context."
        rows = [f"{r.get('channel')}: demand {_fmt_num(r.get('demand'))}, expected delivery {_fmt_num(r.get('expected_delivery'))}, shift {_fmt_num(r.get('shift'))}." for r in phasing[:6]]
        return "First-month phasing: " + " ".join(rows)
    if branch == "safety":
        if not safety:
            return "Safety-stock outputs are not available yet for this cycle."
        return "Safety stock summary: A items " + str(safety.get("a_items")) + "; all items " + str(safety.get("all_items")) + "; target reduction " + str(safety.get("target_reduction_pct")) + "."
    if branch == "amazon":
        return "Amazon pipeline context: as of " + str(amazon.get("as_of")) + ", surge items " + _fmt_num(amazon.get("surge_items")) + ", runtime " + str(amazon.get("total_seconds")) + " seconds."
    if branch == "exception":
        if not queue:
            return "There are no exception-queue rows in the current context."
        ranked = sorted(queue, key=lambda r: abs(float(r.get("value_delta_gbp") or 0.0)), reverse=True)
        rows = [f"Item {r.get('item_id')} ({r.get('product_id')}, {r.get('channel')}): value delta {_fmt_gbp(r.get('value_delta_gbp'))}, risk {r.get('risk_type')}, current final {_fmt_num(r.get('current_final'))}." for r in ranked[:3]]
        return "Top exceptions: " + " ".join(rows)
    if branch == "fva":
        fva = ctx.get("fva")
        if not fva:
            return "No matured overrides yet, so there is no FVA position to report for this cycle."
        return f"FVA: {fva['matured_overrides']} matured overrides; planner overrides are {fva['fva_pts']:+.1f} points versus the ML baseline (positive means the overrides added value)."
    if branch == "cycle":
        return "Current cycle " + str(cycle.get("cutoff")) + " plans from " + str(cycle.get("first_month")) + ". In-scope SKUs: " + _fmt_num(cycle.get("in_scope_skus")) + "; exceptions: " + _fmt_num(cycle.get("exceptions_requiring_review")) + "; pending decisions: " + _fmt_num(cycle.get("pending_decisions")) + "; planned value: " + _fmt_gbp(cycle.get("planned_value_gbp")) + "."
    if branch == "top_products":
        channel = next((ch for ch in CHANNELS if ch in q), None)
        rows = (ctx.get("top_products") or {}).get(channel or "all") or []
        if not rows:
            return "No monthly demand rows are available to rank best-selling products in the current context."
        return "Best-selling items by planned demand: " + " ".join(
            f"Item {r['item_id']} ({r['channel']}): {_fmt_num(r['demand'])} units." for r in rows[:5])
    lead = "Current cycle " + str(cycle.get("cutoff")) + " has " + _fmt_num(cycle.get("pending_decisions")) + " pending decisions and planned value " + _fmt_gbp(cycle.get("planned_value_gbp")) + "."
    top = ""
    if queue:
        r = queue[0]
        top = " The largest queued exception is item " + str(r.get("item_id")) + " with value delta " + _fmt_gbp(r.get("value_delta_gbp")) + " and risk " + str(r.get("risk_type")) + "."
    return lead + top + " Ask about exceptions, OTIF, phasing, safety stock, data quality, or cycle status for a more specific answer."


def suggested_actions(question: str, ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Action-aware planner shortcuts derived from the question and live cycle context."""
    q = question.lower()
    queue = ctx.get("exception_queue_top") or []
    otif_ctx = ctx.get("otif") or {}
    actions: List[Dict[str, Any]] = []
    if any(word in q for word in ("exception", "risk", "value", "top", "why", "review")) and queue:
        ranked = sorted(queue, key=lambda r: abs(float(r.get("value_delta_gbp") or 0.0)), reverse=True)
        for r in ranked[:2]:
            actions.append({"type": "review_exception", "item_id": int(r["item_id"]),
                            "title": f"Review item {r['item_id']}",
                            "detail": f"{_fmt_gbp(r.get('value_delta_gbp'))} value delta, {r.get('risk_type')} risk."})
    if any(word in q for word in ("otif", "fine", "penalty", "exposure")) and otif_ctx.get("top"):
        top = otif_ctx["top"][0]
        actions.append({"type": "review_exception", "item_id": int(top["item_id"]),
                        "title": f"Mitigate OTIF item {top['item_id']}",
                        "detail": f"Penalty {_fmt_gbp(top.get('penalty_gbp'))}, shortfall {_fmt_num(top.get('shortfall_qty'))} units."})
    if any(word in q for word in ("autopilot", "fva", "accept")):
        actions.append({"type": "apply_autopilot", "title": "Review autopilot actions",
                        "detail": "Open FVA & audit to apply low-risk ML-acceptance decisions."})
    if any(word in q for word in ("scenario", "what-if", "what if", "downside", "upside")):
        actions.append({"type": "scenario_prompt", "prompt": question, "title": "Run this as a scenario",
                        "detail": "Open Scenarios with your question preloaded as a generation prompt."})
    if any(word in q for word in ("phasing", "delivery", "deliveries", "supply")):
        actions.append({"type": "review_phasing", "title": "Review supply & phasing",
                        "detail": "Open Supply & Risk for delivery phasing review."})
    if any(word in q for word in ("operation", "cycle", "run", "pipeline", "agent", "job", "orchestration")) and ctx.get("operations"):
        op = ctx["operations"]
        actions.append({"type": "view_operation", "run_id": op["run_id"],
                        "title": f"View latest {op['kind'].replace('_', ' ')} run",
                        "detail": f"Status {op['status']}, {len(op['steps'])} steps."})
    if any(word in q for word in ("product", "item")) and queue:
        top = sorted(queue, key=lambda r: abs(float(r.get("value_delta_gbp") or 0.0)), reverse=True)[0]
        actions.append({"type": "review_exception", "item_id": int(top["item_id"]),
                        "title": f"View item {top['item_id']} details",
                        "detail": f"{_fmt_gbp(top.get('value_delta_gbp'))} value delta, {top.get('risk_type')} risk."})
    return actions


def register(app: FastAPI, ctx: Dict[str, Any]) -> None:
    state, ledger, require = ctx["state"], ctx["ledger"], ctx["require"]
    decisions: Callable[[], Dict] = ctx["decisions"]
    all_roles, admin = ctx["all_roles"], Role.ADMIN.value
    write_roles = (Role.PLANNER.value, Role.ADMIN.value)
    runs, chats, llm = ctx.get("runs"), ctx.get("chats"), ctx.get("llm")
    store_dir, reload_fn = ctx["store_dir"], ctx.get("reload_fn")

    # ------------------------------------------------------------------ OTIF (F-B2B-03)
    def otif_rows() -> Dict[str, Any]:
        svc = state["svc"]
        return otif.from_plan(svc, svc.consensus(decisions()), otif.load_terms(store_dir))

    @app.get("/api/v1/otif")
    def otif_risk(limit: int = Query(50, ge=1, le=1000), user=Depends(require(*all_roles))):
        out = otif_rows()
        return {**out, "items_at_risk": len(out["rows"]), "rows": out["rows"][:limit],
                "note": "Customer OTIF clauses are not in the data (Q4): terms are configurable assumptions."}

    # ------------------------------------------------------------------ disaggregation (F-SOP-01)
    @app.post("/api/v1/disaggregate")
    def disaggregate_total(payload: DisaggregateRequest, user=Depends(require(*all_roles))):
        svc = state["svc"]
        month = payload.month or svc.first_month
        items = svc.product_items(payload.product_id)
        if items.empty:
            raise HTTPException(status_code=404, detail=f"No active items for product {payload.product_id}.")
        cons = svc.consensus(decisions())
        cur = cons[(cons["product_id"] == payload.product_id) & (cons["month"] == month)]
        if cur.empty:
            raise HTTPException(status_code=404, detail=f"No plan for {payload.product_id} in {month}.")
        ids = [int(i) for i in cur["item_id"]]
        plan = cur["consensus_qty"].astype(float).tolist()
        sig = [svc.sigma(i, month) or 0.0 for i in ids]
        new = disaggregate(plan, payload.total, sig, payload.method)
        rows = [{"item_id": i, "channel": ch, "current": p, "sigma": s, "proposed": float(n), "change": float(n - p)}
                for i, ch, p, s, n in zip(ids, cur["channel"], plan, sig, new)]
        return {"product_id": payload.product_id, "month": month, "method": payload.method, "methods": list(METHODS),
                "current_total": float(sum(plan)), "target_total": payload.total, "rows": rows}

    # ------------------------------------------------------------------ dynamic forecasting
    @app.get("/api/v1/forecast/items/{item_id}")
    def dynamic_item_forecast(item_id: int, method: str = Query("auto"), user=Depends(require(*all_roles))):
        try:
            return dynamic_forecast.item_forecast(state["svc"], item_id, method)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc).strip("'"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.get("/api/v1/forecast/items/{item_id}/agentic")
    def agentic_item_forecast(item_id: int, method: str = Query("auto"), user=Depends(require(*all_roles))):
        """Research -> Process -> Forecast -> Inform trace for one item (read-only compute)."""
        from app.forecasting import agent as forecast_agent
        try:
            return forecast_agent.run(state["svc"], item_id, method, llm,
                                      history=ledger.overrides(cutoff=state["svc"].cutoff, item_id=item_id, limit=3))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc).strip("'"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    # ------------------------------------------------------------------ inventory (Phase 3)
    def coverage_history() -> List[Dict[str, Any]]:
        """Band coverage per monthly-cycle run (from the bands_safety node detail), newest first."""
        if runs is None:
            return []
        out = []
        for r in runs.recent(12):
            for s in r.get("steps", []):
                if s.get("node") == "bands_safety" and s.get("status") == "ok":
                    d = s.get("detail") or {}
                    if d.get("band_coverage") is not None:
                        out.append({"started_at": r.get("started_at"), "band_coverage": float(d["band_coverage"])})
                    break
        return out

    @app.get("/api/v1/inventory/safety-stock")
    def safety_stock(channel: Optional[str] = None, abc_category: Optional[str] = None,
                     limit: int = Query(25, ge=1, le=500), user=Depends(require(*all_roles))):
        ex = state["svc"].extras
        if ex.get("safety") is None:
            return {"available": False, "summary": None, "coverage": [], "coverage_history": [], "items": []}
        d = ex["safety"]
        if channel:
            d = d[d["channel"] == channel]
        if abc_category:
            d = d[d["abc_category"] == abc_category]
        d = d.assign(saving=d["holding_static_gbp"] - d["holding_dynamic_gbp"]).sort_values("saving", ascending=False)
        cov = ex.get("coverage")
        return {"available": True, "summary": ex.get("safety_meta"),
                "coverage": [] if cov is None else [{k: clean(v) for k, v in r.items()} for r in cov.to_dict("records")],
                "coverage_history": coverage_history(),
                "total": int(len(d)), "items": [{k: clean(v) for k, v in r.items()} for r in d.head(limit).to_dict("records")]}

    @app.get("/api/v1/supply/risk-flags")
    def supply_risk_flags(user=Depends(require(*all_roles))):
        """Per-channel counts of the supply risk flags, with deep-link filters for the workbench queue."""
        t = state["svc"].triage
        t = t[t["in_scope"]]
        flags = (t.groupby(["channel", "risk_type"]).size().reset_index(name="count")
                 .sort_values(["channel", "count"], ascending=[True, False]))
        rows = [{"channel": str(r.channel), "risk_type": str(r.risk_type), "count": int(r.count)}
                for r in flags.itertuples()]
        return {"cutoff": state["svc"].cutoff, "rows": rows,
                "note": "Deep links open the workbench queue filtered to that channel and risk flag."}

    # ------------------------------------------------------------------ orchestration (Phase 4)
    jobs = ctx.get("jobs")

    def _start(kind: str, user: Dict[str, str]) -> Dict[str, Any]:
        if jobs is None:
            raise HTTPException(status_code=503, detail="Run store (MongoDB) is not configured.")
        try:
            return jobs.start(kind, user["username"])
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @app.post("/api/v1/admin/cycles/run")
    def run_cycle_endpoint(user=Depends(require(admin))):
        return _start("monthly_cycle", user)

    @app.get("/api/v1/cycles/runs")
    def list_runs(limit: int = Query(20, ge=1, le=100), user=Depends(require(*all_roles))):
        if runs is None:
            return {"runs": [], "nodes": []}
        from app.orchestration.graph import NODES
        recent = [{**r, "steps": _step_summaries(r.get("steps"))} for r in runs.recent(limit)]
        return {"runs": recent, "nodes": [n for n, _ in NODES] + ["human_review"]}

    @app.get("/api/v1/cycles/runs/{run_id}")
    def run_detail(run_id: str, user=Depends(require(*all_roles))):
        """One run with its per-node steps plus the current data-quality gate warnings."""
        if runs is None:
            raise HTTPException(status_code=503, detail="Run store (MongoDB) is not configured.")
        run = runs.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return {**run, "steps": _step_summaries(run.get("steps")),
                "quality_warnings": state["svc"].meta.get("quality_warnings", [])}

    @app.post("/api/v1/admin/cycles/rerun-node")
    def rerun_node(payload: RerunNodeRequest, user=Depends(require(admin))):
        """Re-execute one failed node of a run instead of restarting the whole cycle."""
        if jobs is None:
            raise HTTPException(status_code=503, detail="Run store (MongoDB) is not configured.")
        if jobs.busy():
            raise HTTPException(status_code=409, detail="A pipeline run is already in progress.")
        run = runs.get(payload.run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        failed = [s for s in run.get("steps", []) if s.get("node") == payload.node and s.get("status") == "failed"]
        if not failed:
            raise HTTPException(status_code=409, detail=f"Node {payload.node} is not failed in this run.")
        from app.orchestration.graph import NODES
        node_fn = dict(NODES).get(payload.node)
        if node_fn is None:
            raise HTTPException(status_code=422, detail=f"Unknown node {payload.node}.")
        t0 = time.perf_counter()
        step = {"node": payload.node, "status": "ok", "rerun": True}
        try:
            step["detail"] = node_fn()
        except Exception as exc:
            step.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        step["seconds"] = round(time.perf_counter() - t0, 2)
        runs.step(payload.run_id, step)
        runs.finish(payload.run_id, "FAILED", error=None if step["status"] == "ok" else step.get("error"))
        return {"run_id": payload.run_id, "node": payload.node, "step": step,
                "note": "The node was re-run in isolation; downstream nodes still need a fresh cycle run."}

    @app.get("/api/v1/cycles/agent-actions")
    def cycle_agent_actions(user=Depends(require(*all_roles))):
        payload = agent_actions.read()
        if payload is None:
            payload = agent_actions.build(state["svc"], decisions())
        return payload

    # ------------------------------------------------------------------ autopilot promotion rule
    @app.get("/api/v1/fva/autopilot")
    def autopilot(segment: str = Query("channel", pattern="^(channel|abc_category)$"), min_cycles: int = Query(3, ge=1, le=12),
                  user=Depends(require(*all_roles))):
        svc = state["svc"]
        rows = ledger.overrides()
        per = pd.DataFrame()
        if rows:
            ov = pd.DataFrame(rows).merge(svc.triage[["item_id", segment]].reset_index(drop=True), on="item_id", how="left")
            per = override_fva(ov.rename(columns={segment: "segment"}), svc.actuals, group_by=("cutoff", "segment"))
        cands = negative_fva_streaks(per, min_cycles) if not per.empty else []
        actions = autopilot_rules.recommendations(svc, decisions(), segment, [str(c) for c in cands])
        return {"segment": segment, "min_cycles": min_cycles, "candidates": cands, "actions": actions,
                "per_cycle": [{k: clean(v) for k, v in r.items()} for r in per.to_dict("records")] if not per.empty else [],
                "note": "Segments whose planner overrides destroyed value move to autopilot; the agent can also auto-accept low-risk current-cycle exceptions."}

    @app.post("/api/v1/fva/autopilot/apply")
    def autopilot_apply(payload: AutopilotApplyRequest, request: Request, user=Depends(require(*write_roles))):
        svc = state["svc"]
        if ledger.commit_for(svc.cutoff):
            raise HTTPException(status_code=409, detail="Cycle already committed; autopilot actions are closed.")
        ids = autopilot_rules.eligible_items(svc, decisions(), payload.segment, payload.value)
        if not ids:
            raise HTTPException(status_code=409, detail="No currently eligible low-risk pending items for this autopilot action.")
        applied = []
        for item_id in ids:
            row = svc.item_row(item_id)
            if row is None:
                continue
            baseline, recommended = svc.month_values(item_id, svc.first_month)
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(), "item_id": item_id, "cutoff": svc.cutoff,
                "target_month": svc.first_month, "baseline_quantity": baseline, "ml_recommended_quantity": recommended,
                "submitted_quantity": recommended, "variance_to_ml": 0.0, "financial_impact_gbp": 0.0,
                "reason_code": ReasonCode.ML_ACCEPTED.value,
                "planner_comment": f"Autopilot agent accepted ML output for {payload.segment}={payload.value}.",
                "planner_id": f"autopilot_agent:{user['username']}",
                "ip_address": request.client.host if request.client else None,
                "session_id": request.headers.get("X-Session-Id") or user["jti"],
                "tier": row["tier"], "risk_type": row["risk_type"],
            }
            record["id"] = ledger.append_override(record)
            applied.append(record)
        return {"segment": payload.segment, "value": payload.value, "applied": len(applied), "item_ids": [r["item_id"] for r in applied], "records": applied}

    # ------------------------------------------------------------------ conversational assistant
    def context() -> Dict[str, Any]:
        svc, dec = state["svc"], decisions()
        s = svc.summary(dec, committed=ledger.commit_for(svc.cutoff) is not None, fva=None)
        ph = svc.phasing_summary(dec)["rows"]
        o = otif_rows()
        ss = svc.extras.get("safety_meta") or {}
        df = override_fva(pd.DataFrame(ledger.overrides(cutoff=svc.cutoff)), svc.actuals, group_by=("planner_id",))
        fva = None
        if not df.empty:
            vol = float(df["volume"].sum())
            if vol > 0:
                fpe_ml = float((df["fpe_ml"] * df["volume"]).sum() / vol)
                fpe_pl = float((df["fpe_planner"] * df["volume"]).sum() / vol)
                fva = {"matured_overrides": int(df["n_overrides"].sum()), "fva_pts": fpe_ml - fpe_pl}
        ops = None
        if runs is not None:
            recent = runs.recent(1)
            if recent:
                r = recent[0]
                ops = {"run_id": r.get("id"), "status": r.get("status"), "kind": r.get("kind"),
                       "started_at": r.get("started_at"),
                       "steps": [{"node": s.get("node"), "status": s.get("status"), "seconds": s.get("seconds")}
                                  for s in r.get("steps", [])]}
        return {
            "cycle": {k: s[k] for k in ("cutoff", "first_month", "total_catalog_skus", "in_scope_skus", "autopilot_approved_skus",
                                        "exceptions_requiring_review", "planner_led_skus", "pending_decisions", "decided",
                                        "planned_value_gbp", "baseline_value_gbp", "critical_alerts", "committed")},
            "quality_warnings": s["quality_warnings"],
            "exception_queue_top": svc.top_queue(dec, 20),
            "phasing_first_month": [r for r in ph if r["month"] == svc.first_month],
            "otif": {"items_at_risk": len(o["rows"]), "total_penalty_gbp": o["total_penalty_gbp"], "top": o["rows"][:5]},
            "safety_stock": {k: ss.get(k) for k in ("a_items", "all_items", "target_reduction_pct")} if ss else None,
            "fva": fva,
            "operations": ops,
            "top_products": {"all": svc.top_items_by_demand(None, svc.first_month, 5),
                              "amazon": svc.top_items_by_demand("amazon", svc.first_month, 5),
                              "b2b": svc.top_items_by_demand("b2b", svc.first_month, 5),
                              "d2c": svc.top_items_by_demand("d2c", svc.first_month, 5)},
        }

    def _chat_store() -> Any:
        if chats is None:
            raise HTTPException(status_code=503, detail="Assistant store (MongoDB) is not configured.")
        return chats

    @app.get("/api/v1/assistant/history")
    def assistant_history(user=Depends(require(*all_roles))):
        return {"enabled": True, "messages": _chat_store().history(user["username"])}

    @app.get("/api/v1/assistant/providers")
    def assistant_providers(user=Depends(require(*all_roles))):
        """Configured LLM providers and their last call status (e.g. Gemini 429 quota, GLM errors)."""
        from app.narrative.llm import PROVIDER_STATUS
        return {"providers": PROVIDER_STATUS}

    @app.delete("/api/v1/assistant/history")
    def assistant_clear(user=Depends(require(*all_roles))):
        _chat_store().clear(user["username"])
        return {"cleared": True}

    @app.post("/api/v1/assistant/stream")
    async def assistant_stream(payload: AssistantRequest, user=Depends(require(*all_roles))):
        store = _chat_store()
        c = context()
        history = [(m["role"], m["text"]) for m in store.history(user["username"], 8)]
        retrieval = retrieve(payload.question, state["svc"], ledger, settings.hybrid_search)
        store.add(user["username"], "user", payload.question)
        actions = suggested_actions(payload.question, c)

        async def events():
            parts: List[str] = []
            deterministic = False
            try:
                yield f"data: {json.dumps({'tier': retrieval.tier})}\n\n"
                if retrieval.sources:
                    yield f"data: {json.dumps({'sources': [{'kind': s.kind, 'title': s.title, 'snippet': s.snippet[:160]} for s in retrieval.sources]})}\n\n"
                try:
                    if retrieval.tier == "general_knowledge":
                        concept = concept_answer(payload.question)
                        if llm is not None and hasattr(llm, "stream"):
                            prompt = payload.question
                            if concept:
                                prompt = f"REFERENCE MATERIAL (ground your answer in it):\n{concept}\n\nQUESTION: {payload.question}"
                            async for chunk in llm.stream(GENERAL_KNOWLEDGE_PROMPT, history, prompt):
                                parts.append(chunk)
                                yield f"data: {json.dumps({'delta': chunk})}\n\n"
                        else:
                            answer = concept or "The project data does not contain this answer, and no knowledge-base fallback is configured."
                            deterministic = True
                            parts.append(answer)
                            yield f"data: {json.dumps({'delta': answer})}\n\n"
                    else:
                        evidence = "\n".join(f"[{s.kind}] {s.title}: {s.snippet}" for s in retrieval.sources if s.kind in ("docs", "ledger"))
                        prompt = f"CONTEXT JSON:\n{json.dumps(_round(c), default=str)}\n\n"
                        if evidence:
                            prompt += f"PROJECT EVIDENCE:\n{evidence}\n\n"
                        prompt += f"QUESTION: {payload.question}"
                        if llm is not None and hasattr(llm, "stream"):
                            async for chunk in llm.stream(ASSISTANT_PROMPT, history, prompt):
                                parts.append(chunk)
                                yield f"data: {json.dumps({'delta': chunk})}\n\n"
                        else:
                            answer = _deterministic_or_honest(payload.question, c)
                            deterministic = True
                            parts.append(answer)
                            yield f"data: {json.dumps({'delta': answer})}\n\n"
                except Exception as exc:
                    log.warning("assistant llm failed (%s), using deterministic fallback", type(exc).__name__)
                    if retrieval.tier == "general_knowledge":
                        answer = concept_answer(payload.question) or "The knowledge-base fallback is temporarily unavailable, and the project data does not contain this answer."
                    else:
                        answer = _deterministic_or_honest(payload.question, c)
                    deterministic = True
                    parts.append(answer)
                    yield f"data: {json.dumps({'delta': answer})}\n\n"
                if actions:
                    yield f"data: {json.dumps({'actions': actions})}\n\n"
                text = "".join(parts)
                # Deterministic answers are by construction from cycle data, so the number check is skipped;
                # general-knowledge answers are exempt from the cycle-data check.
                bad = [] if deterministic or retrieval.tier != "project" else _unverifiable_numbers(text, c)
                if bad:
                    note = f" Note: {', '.join(bad[:3])} could not be verified against the cycle data."
                    text += note
                    yield f"data: {json.dumps({'delta': note})}\n\n"
                store.add(user["username"], "assistant", text)
                yield f"data: {json.dumps({'done': True})}\n\n"
            except Exception as exc:
                log.warning("assistant failed: %s", type(exc).__name__)
                yield f"data: {json.dumps({'error': 'The assistant could not answer this question.'})}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ------------------------------------------------------------------ executive briefing
    @app.get("/api/v1/export/briefing.pdf")
    def briefing(user=Depends(require(*all_roles))):
        from app.api.briefing import build_pdf
        svc = state["svc"]
        pdf = build_pdf(context(), svc.extras.get("safety_meta"), otif_rows())
        return Response(pdf, media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="sop_briefing_{svc.cutoff}.pdf"'})
