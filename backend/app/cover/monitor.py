"""Scheduler hook for pilot cover policies: settlement and sales-snapshot reminders.

- A policy whose window has ended is announced to admins once ("settle this policy").
- An active policy without a sales snapshot for SNAPSHOT_DUE_DAYS (counted from the start or the last
  snapshot) reminds planners, at most once per SNAPSHOT_DUE_DAYS, so sell-through monitoring and rescue
  triggering do not silently stop when an ERP or n8n feed breaks.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.cover.policies import PolicyStore, TENANT_RE, _dt, effective_status

log = logging.getLogger("sop.cover.monitor")
SNAPSHOT_DUE_DAYS = 14


def check_policies(store: PolicyStore, notes: Any, now: Optional[datetime] = None) -> Dict[str, int]:
    now = now or datetime.now(timezone.utc)
    counts = {"settlement_due": 0, "snapshot_due": 0}
    if notes is None or not store.root.exists():
        return counts
    for tenant_dir in store.root.iterdir():
        if not tenant_dir.is_dir() or not TENANT_RE.match(tenant_dir.name):
            continue
        tenant = tenant_dir.name
        with store.lock():
            for policy in store.list(tenant):
                try:
                    if _check_one(policy, notes, now, counts):
                        store.save(tenant, policy)
                except Exception:  # one broken policy must not stop the others
                    log.exception("cover monitor failed for policy %s", policy.get("id"))
    return counts


def _check_one(policy: Dict[str, Any], notes: Any, now: datetime, counts: Dict[str, int]) -> bool:
    status = effective_status(policy, now)
    if status == "AWAITING_SETTLEMENT" and not policy.get("settlement_notified_at"):
        notes.add(["admin"], "cover", f"Settle cover for {policy['po_id']}",
                  f"The {policy['horizon_days']}-day window ended on {policy['ends_at'][:10]}. "
                  "Enter the final unsold units to settle.", "cover", policy["id"])
        policy["settlement_notified_at"] = now.isoformat()
        counts["settlement_due"] += 1
        return True
    if status != "ACTIVE":
        return False
    last_seen = _dt(policy["observations"][-1]["at"]) if policy["observations"] else _dt(policy["starts_at"])
    reminded = policy.get("snapshot_reminded_at")
    due = now - last_seen >= timedelta(days=SNAPSHOT_DUE_DAYS)
    recently_reminded = reminded and now - _dt(reminded) < timedelta(days=SNAPSHOT_DUE_DAYS)
    if due and not recently_reminded:
        days = (now - last_seen).days
        notes.add(["planner", "admin"], "cover", f"Sales snapshot due for {policy['po_id']}",
                  f"No sales snapshot for {days} days. Sell-through monitoring and rescue triggers are paused "
                  "until one arrives.", "cover", policy["id"])
        policy["snapshot_reminded_at"] = now.isoformat()
        counts["snapshot_due"] += 1
        return True
    return False
