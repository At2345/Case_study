"""Monthly cycle as a LangGraph StateGraph (Section 7 orchestration state machine).

TRIGGER -> INGEST/VALIDATE -> AMAZON_DAILY -> BASE_INFERENCE -> RECONCILE -> PHASING -> PROMO_LIFECYCLE_RULES/TRIAGE
-> BANDS/SAFETY_STOCK -> NARRATIVE -> HUMAN_REVIEW (interrupt). Commit happens in the workbench.
Each node is guarded; a failed guard halts the run and nothing after it is published.
"""
from __future__ import annotations

import contextlib
import io
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, TypedDict

import numpy as np
import pandas as pd

from app.config import settings

QUEUE_BOUNDS = (10, 200)
COHERENCE_TOL = 1e-6


class GuardError(RuntimeError):
    pass


class CycleState(TypedDict, total=False):
    run_id: str
    steps: List[Dict[str, Any]]
    failed: bool
    status: str


def _quiet(fn: Callable[[], Any]) -> Any:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return fn()


# ---------------------------------------------------------------- node bodies (return a short detail dict)
def ingest_validate() -> Dict[str, Any]:
    from app.data.loaders import row_counts, validate_all
    issues = {k: v for k, v in validate_all().items() if v}
    if issues:
        raise GuardError(f"schema contract violations: {issues}")
    counts = row_counts()
    return {"tables": len(counts), "rows": int(sum(counts.values()))}


def amazon_daily() -> Dict[str, Any]:
    from app.pipeline.daily_amazon import run
    r = run()
    worst = min(c["match_rate"] for c in r["regression"]["columns"].values() if c["match_rate"] is not None)
    if worst < 0.99:
        raise GuardError(f"daily->monthly regression match rate {worst:.3f} < 0.99")
    if not r["sla_ok"]:
        raise GuardError(f"NF-04 SLA breached: {r['total_seconds']}s")
    return {"rows_in": r["rows_in"], "seconds": r["total_seconds"], "surge_items": r["surge_items"], "min_match_rate": worst}


def base_inference() -> Dict[str, Any]:
    from app.forecasting import report
    _quiet(report.main)
    live = pd.read_parquet(settings.reports_dir / "live_forecast_complete.parquet", columns=["item_id", "final", "source"])
    if (live["final"].dropna() < 0).any():
        raise GuardError("negative forecasts")
    return {"rows": int(len(live)), "items": int(live["item_id"].nunique()),
            "fallback_rows": int(live["source"].astype(str).str.startswith(("fallback", "routed")).sum())}


def reconcile() -> Dict[str, Any]:
    from app.data.loaders import load
    live = pd.read_parquet(settings.reports_dir / "live_forecast_complete.parquet", columns=["item_id", "date", "final"])
    live = live.merge(load("baseline_mapping"), on="item_id", how="left")
    if live["level_id"].isna().any():
        raise GuardError("items without a product mapping")
    b = live["final"].fillna(0.0)
    top = live.assign(b=b).groupby(["level_id", "date"])["b"].sum()
    resid = float(np.abs(top.sum() - b.sum()))
    if resid > COHERENCE_TOL * max(1.0, b.sum()):
        raise GuardError(f"coherence residual {resid}")
    return {"products": int(live["level_id"].nunique()), "coherence_residual": resid}


def phasing() -> Dict[str, Any]:
    from app.phasing import run as ph
    _quiet(ph.main)
    return {"booked_rows": int(len(pd.read_parquet(settings.reports_dir / "phasing_items.parquet")))}


def rules_triage() -> Dict[str, Any]:
    from app.triage import run as tr
    _quiet(tr.main)
    t = pd.read_parquet(settings.reports_dir / "triage_live.parquet", columns=["state", "final_h"])
    q = int((t["state"] == "EXCEPTION_REVIEW").sum())
    if t["final_h"].isna().any():
        raise GuardError("NaN in final plan")
    if not QUEUE_BOUNDS[0] <= q <= QUEUE_BOUNDS[1]:
        raise GuardError(f"queue size {q} outside sanity bounds {QUEUE_BOUNDS}")
    return {"queue": q, "touchless": int((t["state"] == "AUTO_APPROVED").sum()), "planner_led": int((t["state"] == "PLANNER_LED").sum())}


def bands_safety() -> Dict[str, Any]:
    from app.forecasting import quantiles
    from app.inventory import safety_stock
    cov = _quiet(quantiles.main)["coverage"]
    ss = _quiet(safety_stock.main)
    return {"band_coverage": cov, "a_item_holding_reduction": ss["a_items"]["reduction_pct"]}


def narrative() -> Dict[str, Any]:
    t = pd.read_parquet(settings.reports_dir / "triage_live.parquet", columns=["state", "narrative"])
    q = t[t["state"] == "EXCEPTION_REVIEW"]
    missing = int((q["narrative"].fillna("") == "").sum())
    if missing:
        raise GuardError(f"{missing} queued items without a narrative")
    return {"narratives": int(len(q)), "mode": "deterministic; LLM on demand per item"}


NODES = [
    ("ingest_validate", ingest_validate), ("amazon_daily", amazon_daily), ("base_inference", base_inference),
    ("reconcile", reconcile), ("phasing", phasing), ("rules_triage", rules_triage),
    ("bands_safety", bands_safety), ("narrative", narrative),
]


def build_graph(on_step: Optional[Callable[[Dict[str, Any]], None]] = None, nodes=None):
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, StateGraph

    nodes = nodes or NODES
    g = StateGraph(CycleState)

    def wrap(name: str, fn: Callable[[], Dict[str, Any]]):
        def node(state: CycleState) -> CycleState:
            t0 = time.perf_counter()
            step = {"node": name, "status": "ok"}
            try:
                step["detail"] = fn()
            except Exception as exc:  # guard or crash: halt the graph
                step.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            step["seconds"] = round(time.perf_counter() - t0, 2)
            steps = list(state.get("steps", [])) + [step]
            if on_step:
                on_step(step)
            return {"steps": steps, "failed": step["status"] == "failed",
                    "status": "FAILED" if step["status"] == "failed" else f"DONE_{name.upper()}"}
        return node

    for name, fn in nodes:
        g.add_node(name, wrap(name, fn))

    def human_review(state: CycleState) -> CycleState:
        return {"status": "AWAITING_REVIEW"}

    g.add_node("human_review", human_review)
    g.set_entry_point(nodes[0][0])
    names = [n for n, _ in nodes] + ["human_review"]
    for cur, nxt in zip(names[:-1], names[1:]):
        g.add_conditional_edges(cur, lambda s, nxt=nxt: END if s.get("failed") else nxt, {nxt: nxt, END: END})
    g.add_edge("human_review", END)
    return g.compile(checkpointer=MemorySaver(), interrupt_before=["human_review"])


def run_cycle(on_step=None, nodes=None) -> CycleState:
    graph = build_graph(on_step, nodes)
    run_id = uuid.uuid4().hex
    cfg = {"configurable": {"thread_id": run_id}}
    graph.invoke({"run_id": run_id, "steps": [], "failed": False, "status": "STARTED"}, cfg)
    snap = graph.get_state(cfg)
    state = dict(snap.values)
    if not state.get("failed") and "human_review" in (snap.next or ()):
        state["status"] = "AWAITING_REVIEW"
    return state
