"""Co-Pilot daily-task hub: S&OP fast tasks with live attention counts and urgency pings.

Each task is a small deterministic analysis over the live cycle data. GET /api/v1/tasks
returns the registry with live attention counts; POST /api/v1/tasks/{id}/run runs one task,
returns a plain-language summary + navigation actions, and emits an in-app notification
("ping") when the findings are urgent. The same summaries feed the assistant context so
every detail is reachable over the chatbot.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd
from fastapi import Depends, HTTPException

from app.data.loaders import load
from app.models.enums import Role
from app.rules import otif

log = logging.getLogger("sop.tasks")

RHYTHM_LABELS = {
    "soe_daily": "S&OE daily",
    "sop_weeks_1_2": "S&OP weeks 1-2",
    "sop_week_3": "S&OP week 3",
    "sop_week_4": "S&OP week 4",
}

CHANNELS = ("all", "amazon", "b2b", "d2c")
PRIORITIES = ("high", "medium", "low")

# Attention badges are recomputed at most every ATTENTION_TTL seconds; running a task is always fresh.
ATTENTION_TTL = 300.0
_attention_cache: Dict[str, Any] = {"at": 0.0, "value": None}


def _fmt(v: Any, digits: int = 0) -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "-"
    return f"{v:,.{digits}f}"


def _gbp(v: Any) -> str:
    return "£" + _fmt(v)


# ------------------------------------------------------------------ live attention per task
def _amazon_po_attention(svc) -> Dict[str, Any]:
    """Open Amazon PO lines vs ATP (sellable on-hand + open PO)."""
    try:
        ob = load("orderbook")
        ob = ob[ob["channel"] == "Amazon"]
        open_ = ob[(ob["cancellation_status"].isna() | (ob["cancellation_status"] != "cancelled_completely")) &
                   (ob["delivered_quantity"].fillna(0) < ob["ordered_quantity"].fillna(0))]
        inv = load("amazon_inventory")
        latest = inv[inv["date"] >= inv["date"].max() - pd.Timedelta(days=7)] \
            .sort_values(["item_id", "date"]).groupby("item_id").tail(1)
        atp = latest.set_index("item_id")["sellable_on_hand_inventory_units"].fillna(0) + \
            latest.set_index("item_id")["open_purchase_order_units"].fillna(0)
        remaining = (open_["ordered_quantity"].fillna(0) - open_["delivered_quantity"].fillna(0)).clip(lower=0)
        cov = open_["item_id"].map(atp) / remaining.replace(0, np.nan)
        deficit = int((cov < 1.0).sum())
        return {"attention": deficit, "open_lines": int(len(open_)), "atp_shortfall_units": int(remaining[cov < 1.0].sum())}
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("amazon_po task failed: %s", exc)
        return {"attention": 0, "open_lines": 0, "atp_shortfall_units": 0}


def _buybox_attention(svc) -> Dict[str, Any]:
    """A-item Amazon ASINs with a >25% glance-view drop over 3 consecutive days."""
    try:
        tr = load("amazon_traffic")
        recent = tr[tr["date"] >= tr["date"].max() - pd.Timedelta(days=10)].sort_values(["item_id", "date"])
        items = load("items")
        a_items = set(items.loc[items["abc_category"] == "A", "item_id"])
        last = recent.groupby("item_id").tail(3).groupby("item_id")["glance_views"].mean()
        prior = recent.groupby("item_id").tail(6).groupby("item_id")["glance_views"].apply(lambda s: s.iloc[:3].mean())
        drop = (prior - last) / prior.replace(0, np.nan)
        flagged = [int(i) for i in drop.index if i in a_items and drop[i] > 0.25]
        return {"attention": len(flagged), "flagged_items": flagged[:10]}
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("buybox task failed: %s", exc)
        return {"attention": 0, "flagged_items": []}


def _promo_attention(svc) -> Dict[str, Any]:
    """Upcoming promotions whose uplift exceeds 3x the item's historical monthly average."""
    try:
        promos = load("promotions")
        ts = load("target_time_series")
        hist = ts[ts["date"] < pd.Timestamp(svc.cutoff)].groupby("item_id")["target"].mean()
        upcoming = promos[promos["date_from"] >= pd.Timestamp(svc.cutoff)].copy()
        if upcoming.empty:
            return {"attention": 0, "flagged": []}
        upcoming["hist_avg"] = upcoming["item_id"].map(hist).fillna(0.0)
        upcoming["uplift"] = upcoming["forecast_units"] / upcoming["hist_avg"].clip(lower=1.0)
        flagged = upcoming[upcoming["uplift"] > 3.0]
        return {"attention": int(len(flagged)), "flagged": int(flagged["promotion_id"].nunique())}
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("promo task failed: %s", exc)
        return {"attention": 0, "flagged": 0}


def _attention(svc, decisions: Dict[str, Any], store_dir) -> Dict[str, Dict[str, Any]]:
    """Live attention per task id."""
    s = svc.summary(decisions, committed=False, fva=None)
    queue = svc.top_queue(decisions, 20)
    o = otif.from_plan(svc, svc.consensus(decisions), otif.load_terms(store_dir))
    ss = svc.extras.get("safety_meta") or {}
    po = _amazon_po_attention(svc)
    bb = _buybox_attention(svc)
    promo = _promo_attention(svc)
    return {
        "amazon_po_confirmations": {"attention": po["attention"], "urgent": po["attention"] > 0,
                                    "detail": po},
        "otif_exposure": {"attention": int(o.get("items_at_risk", len(o.get("rows", [])))),
                          "urgent": float(o.get("total_penalty_gbp") or 0.0) > 0,
                          "detail": {"penalty_gbp": o.get("total_penalty_gbp"), "items": len(o.get("rows", []))}},
        "buybox_watch": {"attention": bb["attention"], "urgent": bb["attention"] > 0, "detail": bb},
        "exception_queue": {"attention": int(s.get("exceptions_requiring_review") or len(queue)),
                            "urgent": int(s.get("exceptions_requiring_review") or 0) > 0,
                            "detail": {"queue": int(s.get("exceptions_requiring_review") or 0)}},
        "data_quality": {"attention": len(s.get("quality_warnings") or []),
                         "urgent": len(s.get("quality_warnings") or []) > 0,
                         "detail": {"warnings": len(s.get("quality_warnings") or [])}},
        "promo_sanity": {"attention": promo["attention"], "urgent": promo["attention"] > 0, "detail": promo},
        "safety_stock": {"attention": int((ss.get("a_items") or {}).get("items") or 0), "urgent": False,
                         "detail": ss},
        "value_at_risk": {"attention": len(queue),
                          "urgent": float(s.get("planned_value_gbp") or 0.0) < float(s.get("baseline_value_gbp") or 0.0),
                          "detail": {"planned_gbp": s.get("planned_value_gbp"), "baseline_gbp": s.get("baseline_value_gbp")}},
        "scenario_whatif": {"attention": 0, "urgent": False, "detail": {}},
        "consensus_commit": {"attention": int(s.get("pending_decisions") or 0),
                             "urgent": int(s.get("pending_decisions") or 0) > 0,
                             "detail": {"pending": int(s.get("pending_decisions") or 0),
                                        "decided": int(s.get("decided") or 0),
                                        "committed": bool(s.get("committed"))}},
        "executive_briefing": {"attention": 0, "urgent": False, "detail": {}},
        "fva_autopilot": {"attention": 0, "urgent": False, "detail": {}},
    }


# ------------------------------------------------------------------ registry
def _attention_cached(svc, decisions: Dict[str, Any], store_dir) -> Dict[str, Dict[str, Any]]:
    """Attention counts with a short TTL so the hub and the assistant stay cheap."""
    now = time.monotonic()
    if _attention_cache["value"] is not None and now - _attention_cache["at"] < ATTENTION_TTL:
        return _attention_cache["value"]
    value = _attention(svc, decisions, store_dir)
    _attention_cache.update({"at": now, "value": value})
    return value


_META = [
    {"id": "amazon_po_confirmations", "title": "Amazon PO confirmations",
     "description": "Confirm open Amazon POs inside the 24h window; flag ATP deficits before chargebacks.",
     "rhythm": "soe_daily", "channel": "amazon", "priority": "high", "kind": "run"},
    {"id": "otif_exposure", "title": "OTIF exposure",
     "description": "B2B order-book months the consensus plan under-covers; penalty £ at risk.",
     "rhythm": "soe_daily", "channel": "b2b", "priority": "high", "kind": "run"},
    {"id": "buybox_watch", "title": "Buy Box & glance-view watch",
     "description": "A-item ASINs with >25% glance-view drop over 3 days or OOS suppression.",
     "rhythm": "soe_daily", "channel": "amazon", "priority": "high", "kind": "run"},
    {"id": "exception_queue", "title": "Exception queue review",
     "description": "Largest value-at-risk exceptions still requiring a planner decision.",
     "rhythm": "sop_weeks_1_2", "channel": "all", "priority": "high", "kind": "run"},
    {"id": "data_quality", "title": "Data-quality gate",
     "description": "Schema and completeness warnings that could invalidate the baseline.",
     "rhythm": "sop_weeks_1_2", "channel": "all", "priority": "high", "kind": "run"},
    {"id": "promo_sanity", "title": "Promo sanity check",
     "description": "Upcoming promotions with uplifts >3x historical average, flagged for justification.",
     "rhythm": "sop_weeks_1_2", "channel": "all", "priority": "medium", "kind": "run"},
    {"id": "safety_stock", "title": "Safety stock & bands",
     "description": "Dynamic vs static safety stock, holding-cost reduction and band coverage.",
     "rhythm": "sop_week_3", "channel": "all", "priority": "medium", "kind": "run"},
    {"id": "value_at_risk", "title": "Value at risk",
     "description": "Planned vs baseline £ value; the exceptions that move the P&L.",
     "rhythm": "sop_week_3", "channel": "all", "priority": "high", "kind": "run"},
    {"id": "scenario_whatif", "title": "Scenario what-if",
     "description": "One-click simulation of promo shifts, constraints or price changes.",
     "rhythm": "sop_week_3", "channel": "all", "priority": "medium", "kind": "run"},
    {"id": "consensus_commit", "title": "Consensus commit readiness",
     "description": "Pending vs decided items; is the plan ready to commit for the cycle?",
     "rhythm": "sop_week_4", "channel": "all", "priority": "high", "kind": "run"},
    {"id": "executive_briefing", "title": "Executive briefing",
     "description": "Download the monthly executive S&OP brief (PDF).",
     "rhythm": "sop_week_4", "channel": "all", "priority": "medium", "kind": "download"},
    {"id": "fva_autopilot", "title": "FVA & autopilot",
     "description": "Forecast-value-add scorecard and low-risk ML-acceptance recommendations.",
     "rhythm": "sop_week_4", "channel": "all", "priority": "medium", "kind": "run"},
]


def registry(svc, decisions: Dict[str, Any], store_dir) -> List[Dict[str, Any]]:
    """Static task metadata merged with live attention counts."""
    att = _attention_cached(svc, decisions, store_dir)
    out = []
    for m in _META:
        a = att[m["id"]]
        out.append({**m, "attention": a["attention"], "urgent": a["urgent"], "detail": a["detail"]})
    return out


# Keywords that map an "explain <task>" question to a task (checked before generic intents).
_TASK_KEYWORDS: Dict[str, tuple] = {
    "amazon_po_confirmations": ("amazon po", "po confirmation", "po confirmations", "purchase order"),
    "otif_exposure": ("otif",),
    "buybox_watch": ("buy box", "buybox", "glance view", "glance"),
    "exception_queue": ("exception queue",),
    "data_quality": ("data quality", "quality gate"),
    "promo_sanity": ("promo sanity", "promotion sanity", "promo", "promotion"),
    "safety_stock": ("safety stock",),
    "value_at_risk": ("value at risk",),
    "scenario_whatif": ("scenario what-if", "scenario what if", "scenario"),
    "consensus_commit": ("consensus commit", "consensus"),
    "executive_briefing": ("executive briefing", "briefing"),
    "fva_autopilot": ("fva", "autopilot"),
}


def task_for_question(question: str) -> Optional[Dict[str, Any]]:
    """The task whose title the question asks about, or None."""
    q = question.lower()
    for m in _META:
        if any(k in q for k in _TASK_KEYWORDS[m["id"]]):
            return m
    return None


def urgent_tasks(svc, decisions: Dict[str, Any], store_dir, n: int = 3) -> List[Dict[str, Any]]:
    """Top urgent tasks for the assistant context (chatbot reachability)."""
    return [t for t in registry(svc, decisions, store_dir) if t["urgent"]][:n]


# ------------------------------------------------------------------ run one task
def run(svc, decisions: Dict[str, Any], store_dir, task_id: str) -> Dict[str, Any]:
    """Execute one task: plain-language summary + navigation actions + detail rows.

    Only the data each task actually needs is computed: OTIF is the most expensive
    step (~20s) and is limited to otif_exposure; the exception queue is limited to
    the two tasks that rank it.
    """
    s = svc.summary(decisions, committed=False, fva=None)
    ss = svc.extras.get("safety_meta") or {}
    queue: Optional[List[Dict[str, Any]]] = None
    o: Optional[Dict[str, Any]] = None

    if task_id == "amazon_po_confirmations":
        d = _amazon_po_attention(svc)
        return {"summary": f"{d['open_lines']} open Amazon PO lines; {d['attention']} have ATP coverage below 100% "
                           f"({_fmt(d['atp_shortfall_units'])} units short). Confirm covered lines now; partial-confirm "
                           f"deficits before the 24h cutoff to avoid chargebacks.",
                "attention": d["attention"], "urgent": d["attention"] > 0,
                "actions": [{"type": "review_phasing", "title": "Review supply & phasing",
                             "detail": "Open Supply & Risk for delivery phasing review."}]}
    if task_id == "otif_exposure":
        o = otif.from_plan(svc, svc.consensus(decisions), otif.load_terms(store_dir))
        rows = o.get("rows", [])
        top = rows[0] if rows else None
        actions = []
        if top:
            actions.append({"type": "review_exception", "item_id": int(top["item_id"]),
                            "title": f"Mitigate OTIF item {top['item_id']}",
                            "detail": f"Penalty {_gbp(top.get('penalty_gbp'))}, shortfall {_fmt(top.get('shortfall_qty'))} units."})
        return {"summary": f"OTIF exposure {_gbp(o.get('total_penalty_gbp'))} across {len(rows)} B2B items "
                           f"under-covered by the consensus plan.",
                "attention": len(rows), "urgent": float(o.get("total_penalty_gbp") or 0.0) > 0, "actions": actions}
    if task_id == "buybox_watch":
        d = _buybox_attention(svc)
        return {"summary": f"{d['attention']} A-item ASINs show a >25% glance-view drop over 3 consecutive days "
                           f"or OOS suppression. Alert marketing before demand shifts.",
                "attention": d["attention"], "urgent": d["attention"] > 0,
                "actions": [{"type": "review_exception", "item_id": d["flagged_items"][0],
                             "title": "Review first flagged ASIN",
                             "detail": "Open the item in the workbench."}] if d["flagged_items"] else []}
    if task_id == "exception_queue":
        queue = svc.top_queue(decisions, 20)
        ranked = sorted(queue, key=lambda r: abs(float(r.get("value_delta_gbp") or 0.0)), reverse=True)
        actions = [{"type": "review_exception", "item_id": int(r["item_id"]),
                    "title": f"Review item {r['item_id']}",
                    "detail": f"{_gbp(r.get('value_delta_gbp'))} value delta, {r.get('risk_type')} risk."}
                   for r in ranked[:3]]
        actions.append({"type": "queue_filter", "filters": {"state": "EXCEPTION_REVIEW"},
                        "title": "Open the exception queue", "detail": "Workbench filtered to EXCEPTION_REVIEW."})
        return {"summary": f"{int(s.get('exceptions_requiring_review') or len(queue))} exceptions need review; "
                           f"top value at risk {_gbp(ranked[0].get('value_delta_gbp')) if ranked else '-'} on item "
                           f"{ranked[0]['item_id'] if ranked else '-'}.",
                "attention": int(s.get("exceptions_requiring_review") or len(queue)),
                "urgent": int(s.get("exceptions_requiring_review") or 0) > 0, "actions": actions}
    if task_id == "data_quality":
        w = s.get("quality_warnings") or []
        return {"summary": f"{len(w)} data-quality warnings in this cycle." + ("" if not w else " Check the gate before trusting the baseline."),
                "attention": len(w), "urgent": len(w) > 0, "actions": []}
    if task_id == "promo_sanity":
        d = _promo_attention(svc)
        return {"summary": f"{d['flagged']} upcoming promotions exceed 3x the item's historical monthly average "
                           f"and need commercial justification.",
                "attention": d["attention"], "urgent": d["attention"] > 0, "actions": []}
    if task_id == "safety_stock":
        a = ss.get("a_items") or {}
        return {"summary": f"Dynamic safety stock cuts A-item holding cost by "
                           f"{_fmt((a.get('reduction_pct') or 0.0) * 100, 1)}% "
                           f"({_gbp(a.get('holding_static_gbp'))} -> {_gbp(a.get('holding_dynamic_gbp'))}).",
                "attention": int(a.get("items") or 0), "urgent": False,
                "actions": [{"type": "review_phasing", "title": "Review safety stock",
                             "detail": "Open Supply & Risk for bands and stock."}]}
    if task_id == "value_at_risk":
        queue = svc.top_queue(decisions, 20)
        planned, baseline = s.get("planned_value_gbp"), s.get("baseline_value_gbp")
        delta = None
        if planned is not None and baseline is not None:
            delta = float(planned) - float(baseline)
        ranked = sorted(queue, key=lambda r: abs(float(r.get("value_delta_gbp") or 0.0)), reverse=True)
        actions = [{"type": "review_exception", "item_id": int(r["item_id"]),
                    "title": f"Review item {r['item_id']}",
                    "detail": f"{_gbp(r.get('value_delta_gbp'))} value delta."} for r in ranked[:3]]
        return {"summary": f"Planned value {_gbp(planned)} vs baseline {_gbp(baseline)} "
                           f"({_gbp(delta)} net). {len(queue)} exceptions carry the value at risk.",
                "attention": len(queue), "urgent": delta is not None and delta < 0, "actions": actions}
    if task_id == "scenario_whatif":
        return {"summary": "Open the scenario engine to simulate promo shifts, supply constraints or price changes "
                           "and see the impact on revenue, fill rate and safety stock.",
                "attention": 0, "urgent": False,
                "actions": [{"type": "scenario_prompt",
                             "prompt": "What is the impact of a 3-week delay from the primary packaging supplier on revenue, OTIF and safety stock?",
                             "title": "Open scenario what-if",
                             "detail": "Preloads a supply-delay scenario."}]}
    if task_id == "consensus_commit":
        pending, decided = s.get("pending_decisions"), s.get("decided")
        committed = bool(s.get("committed"))
        state = "committed" if committed else f"{_fmt(s.get('pending_decisions'))} items still pending"
        return {"summary": f"Consensus readiness: {_fmt(s.get('decided'))} items decided, {_fmt(s.get('pending_decisions'))} pending. "
                           f"Cycle is {state}.",
                "attention": int(s.get('pending_decisions') or 0), "urgent": int(s.get('pending_decisions') or 0) > 0, "actions": []}
    if task_id == "executive_briefing":
        return {"summary": "Download the monthly executive S&OP brief (PDF): waterfall, top-5 risks and commentary.",
                "attention": 0, "urgent": False, "download": "briefing", "actions": []}
    if task_id == "fva_autopilot":
        return {"summary": "FVA scorecard and autopilot recommendations are ready to review on the FVA & audit page.",
                "attention": 0, "urgent": False,
                "actions": [{"type": "apply_autopilot", "title": "Review autopilot actions",
                             "detail": "Open FVA & audit to apply low-risk ML-acceptance decisions."}]}
    raise HTTPException(status_code=404, detail=f"Unknown task {task_id}")


def register(app, ctx: Dict[str, Any]) -> None:
    state, require, all_roles = ctx["state"], ctx["require"], ctx["all_roles"]
    decisions: Callable[[], Dict] = ctx["decisions"]
    notes = ctx.get("notes")
    store_dir = ctx["store_dir"]

    def _warm() -> None:
        """Preload the task tables in the background so the first hub request is fast."""
        try:
            registry(state["svc"], decisions(), store_dir)
            log.info("task hub warmed")
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("task hub warm failed: %s", exc)

    threading.Thread(target=_warm, daemon=True).start()

    @app.get("/api/v1/tasks")
    def list_tasks(user=Depends(require(*all_roles))):
        return {"rhythms": RHYTHM_LABELS, "channels": CHANNELS, "priorities": PRIORITIES,
                "tasks": registry(state["svc"], decisions(), store_dir)}

    @app.post("/api/v1/tasks/{task_id}/run")
    def run_task(task_id: str, user=Depends(require(*all_roles))):
        result = run(state["svc"], decisions(), store_dir, task_id)
        if result.get("urgent") and notes is not None:
            meta = next((t for t in _META if t["id"] == task_id), {})
            notes.add([Role.PLANNER.value, Role.ADMIN.value], "task",
                      f"Urgent: {meta.get('title', task_id)}",
                      f"{result['summary']} ({result.get('attention', 0)} items need attention).",
                      "copilot", "")
        return result
