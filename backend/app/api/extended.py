"""Phase 2-4 endpoints: OTIF, disaggregation, safety stock, pipelines, orchestration, assistant, briefing, autopilot."""
from __future__ import annotations

import json
import logging
import threading
from typing import Any, Callable, Dict, List, Optional

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.service import clean
from app.evaluation.disaggregate import METHODS, disaggregate
from app.evaluation.fva import negative_fva_streaks, override_fva
from app.models.enums import Role
from app.rules import otif

log = logging.getLogger("sop.api.ext")

ASSISTANT_PROMPT = (
    "You are the S&OP co-pilot for a UK consumer-goods demand planning team. Answer the planner's question using only "
    "the JSON context provided with each question (current cycle summary, exception queue, order-book phasing, OTIF "
    "risk, safety stock, data-quality warnings, FVA). Quote numbers as they appear, use GBP and units, and say plainly "
    "when the context does not contain the answer. Be concise: at most 6 sentences or a short list."
)


class DisaggregateRequest(BaseModel):
    product_id: str = Field(..., min_length=1, max_length=64)
    total: float = Field(..., ge=0, le=100_000_000)
    month: Optional[str] = None
    method: str = Field("mint_diag", pattern="^(mint_diag|proportional|ols)$")


class AssistantRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000)


def _round(node: Any) -> Any:
    if isinstance(node, float):
        return round(node, 1)
    if isinstance(node, dict):
        return {k: _round(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_round(v) for v in node]
    return node


def register(app: FastAPI, ctx: Dict[str, Any]) -> None:
    state, ledger, require = ctx["state"], ctx["ledger"], ctx["require"]
    decisions: Callable[[], Dict] = ctx["decisions"]
    all_roles, admin = ctx["all_roles"], Role.ADMIN.value
    runs, chats, llm = ctx.get("runs"), ctx.get("chats"), ctx.get("llm")
    store_dir, reload_fn = ctx["store_dir"], ctx.get("reload_fn")

    # ------------------------------------------------------------------ OTIF (F-B2B-03)
    def otif_rows() -> Dict[str, Any]:
        svc = state["svc"]
        cfg = otif.load_terms(store_dir)
        cons = svc.consensus(decisions())
        ph = svc.phasing_items
        if ph.empty:
            return {"terms": cfg, "rows": [], "total_penalty_gbp": 0.0}
        ph = ph.assign(month=pd.to_datetime(ph["month"]).dt.strftime("%Y-%m-%d"))
        j = cons.merge(ph[["item_id", "month", "booked_qty"]], on=["item_id", "month"], how="inner")
        price = svc.triage["sales_price"]
        rows = [{"item_id": int(r.item_id), "product_id": r.product_id, "channel": r.channel, "month": r.month,
                 "booked": float(r.booked_qty), "plan": float(r.consensus_qty), "sales_price": float(price.get(r.item_id, 0.0))}
                for r in j.itertuples()]
        risk = otif.check(rows, cfg)
        return {"terms": cfg, "rows": risk, "total_penalty_gbp": float(sum(r["penalty_gbp"] for r in risk))}

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

    # ------------------------------------------------------------------ inventory (Phase 3)
    @app.get("/api/v1/inventory/safety-stock")
    def safety_stock(channel: Optional[str] = None, abc_category: Optional[str] = None,
                     limit: int = Query(25, ge=1, le=500), user=Depends(require(*all_roles))):
        ex = state["svc"].extras
        if ex.get("safety") is None:
            return {"available": False, "summary": None, "coverage": [], "items": []}
        d = ex["safety"]
        if channel:
            d = d[d["channel"] == channel]
        if abc_category:
            d = d[d["abc_category"] == abc_category]
        d = d.assign(saving=d["holding_static_gbp"] - d["holding_dynamic_gbp"]).sort_values("saving", ascending=False)
        cov = ex.get("coverage")
        return {"available": True, "summary": ex.get("safety_meta"),
                "coverage": [] if cov is None else [{k: clean(v) for k, v in r.items()} for r in cov.to_dict("records")],
                "total": int(len(d)), "items": [{k: clean(v) for k, v in r.items()} for r in d.head(limit).to_dict("records")]}

    # ------------------------------------------------------------------ Phase 2 Amazon daily pipeline
    @app.get("/api/v1/pipeline/amazon")
    def amazon_pipeline(user=Depends(require(*all_roles))):
        ex = state["svc"].extras
        wr = ex.get("weekly_risk")
        top = [] if wr is None else [{k: clean(v) for k, v in r.items()} for r in
                                     wr[wr["surge"]].sort_values("gv_last14", ascending=False).head(15).to_dict("records")]
        return {"meta": ex.get("amazon_meta"), "top_surges": top}

    # ------------------------------------------------------------------ orchestration (Phase 4)
    lock = threading.Lock()

    def _run_job(run_id: str, kind: str) -> None:
        try:
            if kind == "amazon_daily":
                from app.orchestration.graph import amazon_daily
                runs.step(run_id, {"node": "amazon_daily", "status": "ok", "detail": amazon_daily()})
                status = "SUCCEEDED"
            else:
                from app.orchestration.graph import run_cycle
                status = run_cycle(on_step=lambda s: runs.step(run_id, s)).get("status", "FAILED")
            if status != "FAILED" and reload_fn is not None:
                state["svc"] = reload_fn()
            runs.finish(run_id, status)
        except Exception as exc:
            log.exception("pipeline run failed")
            runs.finish(run_id, "FAILED", error=f"{type(exc).__name__}: {exc}")
        finally:
            lock.release()

    def _start(kind: str, user: Dict[str, str]) -> Dict[str, Any]:
        if runs is None:
            raise HTTPException(status_code=503, detail="Run store (MongoDB) is not configured.")
        if not lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="A pipeline run is already in progress.")
        run_id = runs.start(kind, user["username"])
        threading.Thread(target=_run_job, args=(run_id, kind), daemon=True).start()
        return {"run_id": run_id, "status": "RUNNING", "kind": kind}

    @app.post("/api/v1/admin/cycles/run")
    def run_cycle_endpoint(user=Depends(require(admin))):
        return _start("monthly_cycle", user)

    @app.post("/api/v1/admin/pipeline/amazon")
    def run_amazon_endpoint(user=Depends(require(admin))):
        return _start("amazon_daily", user)

    @app.get("/api/v1/cycles/runs")
    def list_runs(limit: int = Query(20, ge=1, le=100), user=Depends(require(*all_roles))):
        if runs is None:
            return {"runs": [], "nodes": []}
        from app.orchestration.graph import NODES
        return {"runs": runs.recent(limit), "nodes": [n for n, _ in NODES] + ["human_review"]}

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
        return {"segment": segment, "min_cycles": min_cycles, "candidates": cands,
                "per_cycle": [{k: clean(v) for k, v in r.items()} for r in per.to_dict("records")] if not per.empty else [],
                "note": "Segments whose planner overrides destroyed value (negative FVA) in each of the last cycles move to autopilot."}

    # ------------------------------------------------------------------ conversational assistant
    def context() -> Dict[str, Any]:
        svc, dec = state["svc"], decisions()
        s = svc.summary(dec, committed=ledger.commit_for(svc.cutoff) is not None, fva=None)
        ph = svc.phasing_summary(dec)["rows"]
        o = otif_rows()
        ss = svc.extras.get("safety_meta") or {}
        return {
            "cycle": {k: s[k] for k in ("cutoff", "first_month", "total_catalog_skus", "in_scope_skus", "autopilot_approved_skus",
                                        "exceptions_requiring_review", "planner_led_skus", "pending_decisions", "decided",
                                        "planned_value_gbp", "baseline_value_gbp", "critical_alerts", "committed")},
            "quality_warnings": s["quality_warnings"],
            "exception_queue_top": svc.top_queue(20),
            "phasing_first_month": [r for r in ph if r["month"] == svc.first_month],
            "otif": {"items_at_risk": len(o["rows"]), "total_penalty_gbp": o["total_penalty_gbp"], "top": o["rows"][:5]},
            "safety_stock": {k: ss.get(k) for k in ("a_items", "all_items", "target_reduction_pct")} if ss else None,
            "amazon_pipeline": {k: (svc.extras.get("amazon_meta") or {}).get(k) for k in ("as_of", "surge_items", "total_seconds")},
        }

    def _chat_store() -> Any:
        if chats is None:
            raise HTTPException(status_code=503, detail="Assistant store (MongoDB) is not configured.")
        return chats

    @app.get("/api/v1/assistant/history")
    def assistant_history(user=Depends(require(*all_roles))):
        return {"enabled": llm is not None and hasattr(llm, "stream"), "messages": _chat_store().history(user["username"])}

    @app.delete("/api/v1/assistant/history")
    def assistant_clear(user=Depends(require(*all_roles))):
        _chat_store().clear(user["username"])
        return {"cleared": True}

    @app.post("/api/v1/assistant/stream")
    async def assistant_stream(payload: AssistantRequest, user=Depends(require(*all_roles))):
        store = _chat_store()
        if llm is None or not hasattr(llm, "stream"):
            raise HTTPException(status_code=503, detail="The language model is not configured.")
        history = [(m["role"], m["text"]) for m in store.history(user["username"], 8)]
        prompt = f"CONTEXT JSON:\n{json.dumps(_round(context()), default=str)}\n\nQUESTION: {payload.question}"
        store.add(user["username"], "user", payload.question)

        async def events():
            parts: List[str] = []
            try:
                async for chunk in llm.stream(ASSISTANT_PROMPT, history, prompt):
                    parts.append(chunk)
                    yield f"data: {json.dumps({'delta': chunk})}\n\n"
                store.add(user["username"], "assistant", "".join(parts))
                yield f"data: {json.dumps({'done': True})}\n\n"
            except Exception as exc:
                log.warning("assistant failed: %s", type(exc).__name__)
                yield f"data: {json.dumps({'error': 'The language model could not be reached.'})}\n\n"

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
