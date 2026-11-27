"""Cover policies (pilot): bind a quote, monitor sell-through, trigger rescue playbooks, settle claims.

Lifecycle: ACTIVE -> (window ends) AWAITING_SETTLEMENT -> CLAIMED | CLOSED_NO_CLAIM, or CANCELLED.
Sell-through is compared with a linear expected-sales curve over the window; when sales fall behind by
more than `rescue_gap_threshold`, the linked rescue playbook is started (not in the first
MIN_DAYS_BEFORE_RESCUE days, at most once per COOLDOWN_DAYS). Settlement applies the policy's own cap,
excess and per-unit payout to the final unsold count.

"pilot": these are internal commitments for testing the model, not insurance contracts.
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.cover.experience import experience

TENANT_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
POLICY_RE = re.compile(r"^[a-f0-9]{12}$")
DEFAULT_RESCUE_GAP = -0.20
MIN_DAYS_BEFORE_RESCUE = 14
COOLDOWN_DAYS = 30
CLOSED = ("CLAIMED", "CLOSED_NO_CLAIM", "CANCELLED")


class PolicyError(ValueError):
    pass


def _dt(value: Any) -> datetime:
    d = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def new_policy(quote: Dict[str, Any], bound_by: str, now: datetime, marketplace: Optional[str] = None,
               rescue_playbook_id: Optional[str] = None,
               rescue_gap_threshold: float = DEFAULT_RESCUE_GAP) -> Dict[str, Any]:
    if not quote.get("offered"):
        raise PolicyError("this quote was declined; it cannot be bound")
    if _dt(quote["valid_until"]) < now:
        raise PolicyError("this quote has expired; request a new one")
    if not -0.9 <= rescue_gap_threshold < 0:
        raise PolicyError("rescue_gap_threshold must be between -0.9 and 0")
    horizon = int(quote["terms"]["horizon_days"])
    monthly = quote.get("demand", {}).get("monthly_means") or []
    total = sum(monthly)
    weights = [round(m / total, 6) for m in monthly] if total > 0 else None
    return {
        "sales_curve": weights,  # monthly share of expected sales (seasonal); None = straight line
        "id": uuid.uuid4().hex[:12], "pilot": True, "status": "ACTIVE",
        "quote_id": quote["id"], "po_id": quote["po_id"], "sku": quote.get("sku") or quote["po_id"],
        "item_id": quote.get("item_id"), "marketplace": marketplace,
        "units": quote["units"], "covered_units": quote["covered_units"], "excluded_units": quote["excluded_units"],
        "deductible_units": quote["deductible_units"], "payout_per_unit_eur": quote["payout_per_unit_eur"],
        "unit_landed_cost_eur": quote["unit_landed_cost_eur"], "premium_eur": quote["premium_eur"],
        "demand_mean_units": quote["demand"]["mean_units"], "horizon_days": horizon,
        # What the quote expected, kept for experience rating (actual vs expected at settlement).
        "expected_paid_units": quote.get("expected_paid_units"), "expected_loss_eur": quote.get("expected_loss_eur"),
        "max_payout_eur": round(max(quote["covered_units"] - quote["deductible_units"], 0) * quote["payout_per_unit_eur"], 2),
        "starts_at": now.isoformat(), "ends_at": (now + timedelta(days=horizon)).isoformat(),
        "rescue_playbook_id": rescue_playbook_id, "rescue_gap_threshold": rescue_gap_threshold,
        "rescue_runs": [], "observations": [], "claim": None,
        "bound_by": bound_by, "bound_at": now.isoformat(),
    }


def effective_status(policy: Dict[str, Any], now: datetime) -> str:
    if policy["status"] == "ACTIVE" and now >= _dt(policy["ends_at"]):
        return "AWAITING_SETTLEMENT"
    return policy["status"]


def expected_share(policy: Dict[str, Any], elapsed_days: float) -> float:
    """Share of window demand expected by `elapsed_days`: seasonal (monthly weights) or a straight line."""
    horizon = float(policy["horizon_days"])
    weights = policy.get("sales_curve")
    if not weights:
        return min(max(elapsed_days / horizon, 0.0), 1.0)
    seg = horizon / len(weights)
    full = int(min(elapsed_days, horizon) // seg)
    share = sum(weights[:full])
    if full < len(weights):
        share += weights[full] * (elapsed_days - full * seg) / seg
    return min(max(share, 0.0), 1.0)


def assess(policy: Dict[str, Any], units_sold_to_date: float, as_of: datetime) -> Dict[str, Any]:
    """Sell-through against the expected sales curve over the cover window."""
    horizon = policy["horizon_days"]
    elapsed = min(max((as_of - _dt(policy["starts_at"])).total_seconds() / 86400, 0.0), float(horizon))
    expected = policy["demand_mean_units"] * expected_share(policy, elapsed)
    gap = (units_sold_to_date / expected - 1) if expected > 0 else 0.0
    return {"elapsed_days": round(elapsed, 1), "days_to_end": round(horizon - elapsed, 1),
            "expected_sold_to_date": round(expected, 1), "units_sold_to_date": units_sold_to_date,
            "gap": round(gap, 4), "curve": "seasonal" if policy.get("sales_curve") else "linear"}


def rescue_due(policy: Dict[str, Any], assessment: Dict[str, Any], as_of: datetime) -> Optional[str]:
    """Return None when a rescue should start now, otherwise the reason it should not."""
    if policy["status"] != "ACTIVE" or effective_status(policy, as_of) != "ACTIVE":
        return "policy is not active"
    if not policy.get("rescue_playbook_id"):
        return "no rescue playbook linked"
    if assessment["elapsed_days"] < MIN_DAYS_BEFORE_RESCUE:
        return f"too early (first {MIN_DAYS_BEFORE_RESCUE} days are not assessed)"
    if assessment["gap"] > policy["rescue_gap_threshold"]:
        return "sell-through is within tolerance"
    if policy["rescue_runs"]:
        last = _dt(policy["rescue_runs"][-1]["at"])
        if (as_of - last).days < COOLDOWN_DAYS:
            return f"a rescue ran on {last.date()}; waiting {COOLDOWN_DAYS} days between rescues"
    return None


def settle(policy: Dict[str, Any], units_unsold: float, settled_by: str, now: datetime) -> Dict[str, Any]:
    if policy["status"] in CLOSED:
        raise PolicyError(f"policy is already {policy['status']}")
    if now < _dt(policy["ends_at"]):
        raise PolicyError(f"the cover window ends on {policy['ends_at'][:10]}; settle after that")
    if not 0 <= units_unsold <= policy["units"]:
        raise PolicyError("units_unsold must be between 0 and the ordered units")
    covered_unsold = max(units_unsold - policy["excluded_units"], 0)   # units above the cap are unsold first
    paid_units = max(covered_unsold - policy["deductible_units"], 0)
    payout = round(paid_units * policy["payout_per_unit_eur"], 2)
    claim = {"units_unsold": units_unsold, "covered_unsold_units": covered_unsold, "paid_units": paid_units,
             "payout_eur": payout, "settled_by": settled_by, "settled_at": now.isoformat()}
    policy["claim"] = claim
    policy["status"] = "CLAIMED" if payout > 0 else "CLOSED_NO_CLAIM"
    return claim


def portfolio(policies: List[Dict[str, Any]], now: datetime) -> Dict[str, Any]:
    by_status: Dict[str, int] = {}
    for p in policies:
        s = effective_status(p, now)
        by_status[s] = by_status.get(s, 0) + 1
    settled = [p for p in policies if p["status"] in ("CLAIMED", "CLOSED_NO_CLAIM")]
    premium_settled = sum(p["premium_eur"] for p in settled)
    claims = sum(p["claim"]["payout_eur"] for p in settled)
    open_ = [p for p in policies if p["status"] == "ACTIVE"]
    return {
        "policies": len(policies), "by_status": by_status,
        "premium_written_eur": round(sum(p["premium_eur"] for p in policies if p["status"] != "CANCELLED"), 2),
        "claims_paid_eur": round(claims, 2),
        "loss_ratio": round(claims / premium_settled, 3) if premium_settled else None,
        "open_max_exposure_eur": round(sum(p["max_payout_eur"] for p in open_), 2),
        "rescue_runs": sum(len(p["rescue_runs"]) for p in policies),
        "experience": experience(policies),
    }


class PolicyStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._lock = threading.RLock()

    def _dir(self, tenant: str) -> Path:
        if not TENANT_RE.match(tenant or ""):
            raise ValueError("invalid tenant id")
        return self.root / tenant / "policies"

    def save(self, tenant: str, policy: Dict[str, Any]) -> Dict[str, Any]:
        folder = self._dir(tenant)
        with self._lock:
            folder.mkdir(parents=True, exist_ok=True)
            tmp = folder / f"{policy['id']}.json.tmp"
            tmp.write_text(json.dumps(policy, indent=2, default=str), encoding="utf-8")
            tmp.replace(folder / f"{policy['id']}.json")
        return policy

    def get(self, tenant: str, policy_id: str) -> Dict[str, Any]:
        path = self._dir(tenant) / f"{policy_id}.json"
        if not POLICY_RE.match(policy_id or "") or not path.exists():
            raise KeyError(f"policy {policy_id} not found")
        return json.loads(path.read_text(encoding="utf-8"))

    def list(self, tenant: str) -> List[Dict[str, Any]]:
        folder = self._dir(tenant)
        if not folder.exists():
            return []
        rows = [json.loads(p.read_text(encoding="utf-8")) for p in folder.glob("*.json")]
        return sorted(rows, key=lambda p: p["bound_at"], reverse=True)

    def lock(self):
        return self._lock
