from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

MAX_AUTO_ITEMS = 10
LOW_RISK_VAR_GBP = 5_000
BLOCKED_RISKS = {"CRITICAL_AMAZON_BUYBOX_ALERT", "TERMINAL_OBSOLESCENCE_RISK", "NPI_ANALOGY_GAP"}


def recommendations(svc, decisions: Dict, segment: str, fva_candidates: List[str]) -> List[Dict[str, Any]]:
    rows = _pending_frame(svc, decisions)
    out = []
    for value, group in rows.groupby(segment, dropna=False):
        value = str(value)
        low = group[(group["var_gbp"].fillna(0) <= LOW_RISK_VAR_GBP) & ~group["risk_type"].isin(BLOCKED_RISKS)]
        low = low.sort_values("var_gbp", ascending=True).head(MAX_AUTO_ITEMS)
        fva_backed = value in set(fva_candidates)
        if low.empty and not fva_backed:
            continue
        candidate_ids = [int(i) for i in low["item_id"].tolist()]
        action_type = "promote_segment" if fva_backed else "auto_accept_low_risk"
        confidence = 0.85 if fva_backed else min(0.75, 0.45 + 0.03 * len(candidate_ids))
        out.append({
            "id": f"{segment}:{value}",
            "segment": segment,
            "value": value,
            "action": action_type,
            "mode": "auto_apply" if candidate_ids else "observe",
            "confidence": round(confidence, 2),
            "pending_items": int(len(group)),
            "eligible_items": len(candidate_ids),
            "item_ids": candidate_ids,
            "estimated_value_at_risk_gbp": float(low["var_gbp"].fillna(0).sum()) if candidate_ids else 0.0,
            "reason": _reason(value, action_type, len(group), candidate_ids, fva_backed),
        })
    return sorted(out, key=lambda r: (r["action"] != "promote_segment", -r["eligible_items"], -r["confidence"]))


def eligible_items(svc, decisions: Dict, segment: str, value: str) -> List[int]:
    rows = _pending_frame(svc, decisions)
    group = rows[rows[segment].astype(str) == str(value)]
    low = group[(group["var_gbp"].fillna(0) <= LOW_RISK_VAR_GBP) & ~group["risk_type"].isin(BLOCKED_RISKS)]
    return [int(i) for i in low.sort_values("var_gbp", ascending=True).head(MAX_AUTO_ITEMS)["item_id"].tolist()]


def _pending_frame(svc, decisions: Dict) -> pd.DataFrame:
    decided = {i for (i, month) in decisions if month == svc.first_month}
    frame = svc.triage.reset_index(drop=True).copy()
    frame = frame[(frame["state"] == "EXCEPTION_REVIEW") & ~frame["item_id"].isin(decided)]
    return frame


def _reason(value: str, action_type: str, pending: int, ids: List[int], fva_backed: bool) -> str:
    if action_type == "promote_segment":
        return f"{value} has a negative-FVA streak; the agent can auto-accept ML recommendations for {len(ids)} low-risk pending item(s) and keep higher-risk items in review."
    return f"{value} has {pending} pending exception(s); {len(ids)} low-value, non-critical item(s) are eligible for autonomous ML acceptance."
