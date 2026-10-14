from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from app.api.service import DataService, clean
from app.config import settings
from app.forecasting import dynamic as dynamic_forecast
from app.rules import autopilot as autopilot_rules
from app.rules import otif

ARTIFACT = "cycle_agent_actions.json"


def build(svc: Optional[DataService] = None, decisions: Optional[Dict] = None, reports_dir: Optional[Path] = None) -> Dict[str, Any]:
    svc = svc or DataService.from_reports(reports_dir or settings.reports_dir)
    decisions = decisions or {}
    queue = svc.top_queue(decisions, 10)
    auto = autopilot_rules.recommendations(svc, decisions, "channel", [])
    phasing = svc.phasing_summary(decisions)["rows"]
    otif_rows = otif.from_plan(svc, svc.consensus(decisions), otif.load_terms(settings.store_dir))
    forecast = _dynamic_forecast_candidates(svc, queue[:5])
    scenario_prompts = _scenario_prompts(queue, auto)
    actions = []
    actions.extend(_risk_actions(queue[:5]))
    actions.extend(_autopilot_actions(auto[:3]))
    actions.extend(_forecast_actions(forecast))
    actions.extend(_phasing_actions(phasing))
    actions.extend(_otif_actions(otif_rows["rows"][:3]))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cutoff": svc.cutoff,
        "first_month": svc.first_month,
        "summary": {
            "top_risks": len(queue),
            "autopilot_actions": len(auto),
            "dynamic_forecast_candidates": len(forecast),
            "scenario_prompts": len(scenario_prompts),
            "otif_items_at_risk": len(otif_rows["rows"]),
            "otif_penalty_gbp": clean(otif_rows["total_penalty_gbp"]),
        },
        "actions": actions,
        "top_risks": queue,
        "autopilot": auto,
        "dynamic_forecasts": forecast,
        "scenario_prompts": scenario_prompts,
    }


def write(reports_dir: Optional[Path] = None) -> Dict[str, Any]:
    reports_dir = Path(reports_dir or settings.reports_dir)
    payload = build(reports_dir=reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / ARTIFACT).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return payload


def read(reports_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    path = Path(reports_dir or settings.reports_dir) / ARTIFACT
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _risk_actions(queue: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for r in queue:
        out.append({
            "id": f"risk:{r['item_id']}",
            "type": "review_exception",
            "priority": "high" if abs(float(r.get("value_delta_gbp") or 0)) >= 10_000 else "medium",
            "title": f"Review item {r['item_id']} ({r['risk_type']})",
            "detail": f"{r['product_id']} in {r['channel']} has value delta GBP {round(float(r.get('value_delta_gbp') or 0), 0)}.",
            "item_id": r["item_id"],
            "product_id": r["product_id"],
        })
    return out


def _autopilot_actions(auto: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{
        "id": f"autopilot:{a['id']}",
        "type": "apply_autopilot",
        "priority": "medium",
        "title": f"Autopilot {a['eligible_items']} low-risk {a['value']} item(s)",
        "detail": a["reason"],
        "segment": a["segment"],
        "value": a["value"],
        "eligible_items": a["eligible_items"],
    } for a in auto if a.get("eligible_items", 0) > 0]


def _dynamic_forecast_candidates(svc: DataService, queue: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for r in queue:
        try:
            fc = dynamic_forecast.item_forecast(svc, int(r["item_id"]), "auto")
        except Exception:
            continue
        first = fc["rows"][0]
        delta = first.get("delta_to_current")
        if delta is None or abs(float(delta)) < 1:
            continue
        out.append({
            "item_id": fc["item_id"],
            "product_id": fc["product_id"],
            "method": fc["method"],
            "month": first["month"],
            "dynamic": first["dynamic"],
            "current_final": first["current_final"],
            "delta_to_current": first["delta_to_current"],
        })
    return out


def _forecast_actions(forecast: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{
        "id": f"forecast:{r['item_id']}",
        "type": "recompute_forecast",
        "priority": "medium" if abs(float(r["delta_to_current"])) < 100 else "high",
        "title": f"Recompute forecast for item {r['item_id']}",
        "detail": f"{r['method']} suggests {round(float(r['dynamic'] or 0), 0)} units vs current {round(float(r['current_final'] or 0), 0)} for {r['month']}.",
        "item_id": r["item_id"],
        "method": r["method"],
    } for r in forecast]


def _phasing_actions(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    gaps = [r for r in rows if float(r.get("shift") or 0) < -100]
    gaps = sorted(gaps, key=lambda r: float(r.get("shift") or 0))[:3]
    return [{
        "id": f"phasing:{r['month']}:{r['channel']}",
        "type": "review_phasing",
        "priority": "medium",
        "title": f"Review {r['channel']} delivery phasing",
        "detail": f"Expected delivery is {round(abs(float(r['shift'])), 0)} units below demand in {r['month']}.",
        "month": r["month"],
        "channel": r["channel"],
    } for r in gaps]


def _otif_actions(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{
        "id": f"otif:{r['item_id']}:{r['month']}",
        "type": "mitigate_otif",
        "priority": "high",
        "title": f"Mitigate OTIF exposure for item {r['item_id']}",
        "detail": f"Penalty exposure GBP {round(float(r.get('penalty_gbp') or 0), 0)} in {r['month']}.",
        "item_id": r["item_id"],
        "month": r["month"],
    } for r in rows]


def _scenario_prompts(queue: List[Dict[str, Any]], auto: List[Dict[str, Any]]) -> List[str]:
    prompts = []
    if queue:
        ch = queue[0]["channel"]
        prompts.append(f"Create a downside scenario reducing {ch} demand by 10% next month for the highest-risk products")
    for a in auto[:2]:
        prompts.append(f"Create an autopilot adoption scenario for {a['value']} with low-risk items accepted automatically")
    return prompts
