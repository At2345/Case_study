"""Demo seed: historical planner overrides so the FVA scorecard has matured, scored rows.

Actuals are monthly and end at the cycle cutoff, so overrides targeting the planning
horizon cannot be scored until the target month closes. For the demo, this module
appends planner overrides for past months (which DO have actuals) into the insert-only
ledger; the existing FVA engine then scores them exactly like real decisions.

The seeded rows are tagged with session_id="demo-seed" and are constructed so the
planner's override lands closer to the actual than the ML forecast (positive FVA).

Idempotent: it skips when demo-seed rows already exist for the current cutoff, so
restarting the app never duplicates rows. Run standalone with:
    python -m app.seed_demo_fva            # seed if missing
    python -m app.seed_demo_fva --reset    # remove demo-seed rows, re-chain, re-seed
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from app.api.service import DataService
from app.config import settings
from app.evaluation.fva import override_fva
from app.store.ledger import GENESIS, OVERRIDE_FIELDS, Ledger, _row_hash

log = logging.getLogger("sop.seed")

MARKER = "demo-seed"
REASONS = [
    "Commercial Intelligence (Account Feedback)",
    "Retailer Promotion Timing Shift",
    "Amazon Promotion Shift / Cancellation",
    "Competitor Stockout Capture",
]


def _has_demo_rows(ledger: Ledger, cutoff: str) -> bool:
    return any(r.get("session_id") == MARKER for r in ledger.overrides(cutoff=cutoff))


def seed_demo_fva(ledger: Ledger, svc: DataService, planner_id: str = "danny",
                  months: int = 3, per_month: int = 4, seed: int = 42) -> int:
    """Append historical overrides for months that have actuals; returns rows appended (0 if skipped)."""
    if _has_demo_rows(ledger, svc.cutoff):
        log.info("FVA demo seed skipped: demo-seed rows already present for cutoff %s", svc.cutoff)
        return 0

    act = svc.actuals.dropna(subset=["target"]).copy()
    if act.empty:
        log.warning("FVA demo seed skipped: no actuals available")
        return 0
    act["date"] = pd.to_datetime(act["date"])
    cutoff = pd.Timestamp(svc.cutoff)
    periods = sorted(act["date"].dt.to_period("M").unique())[-months:]

    triage = svc.triage
    price = triage.set_index("item_id")["sales_price"].to_dict()
    tier = triage.set_index("item_id")["tier"].to_dict()
    risk = triage.set_index("item_id")["risk_type"].to_dict()

    rng = np.random.default_rng(seed)
    appended = 0
    for period in periods:
        month = period.to_timestamp()
        if month >= cutoff:
            continue
        pool = act[act["date"].dt.to_period("M") == period]
        pool = pool[pool["target"] > 0]
        if pool.empty:
            continue
        chosen = pool.sample(n=min(per_month, len(pool)), random_state=rng)
        for _, r in chosen.iterrows():
            item_id = int(r["item_id"])
            actual = float(r["target"])
            ml = max(0.0, actual * (1 + float(rng.normal(0, 0.12))))
            baseline = max(0.0, actual * (1 + float(rng.normal(0.20, 0.10))))
            submitted = max(0.0, actual * (1 + float(rng.normal(0, 0.02))))
            record: Dict[str, Any] = {
                "timestamp": (month - pd.DateOffset(months=1)).strftime("%Y-%m-%dT09:00:00"),
                "item_id": item_id,
                "cutoff": svc.cutoff,
                "target_month": month.strftime("%Y-%m-01"),
                "baseline_quantity": round(baseline, 1),
                "ml_recommended_quantity": round(ml, 1),
                "submitted_quantity": round(submitted, 1),
                "variance_to_ml": round(submitted - ml, 1),
                "financial_impact_gbp": round((submitted - ml) * price.get(item_id, 0.0), 2),
                "reason_code": REASONS[appended % len(REASONS)],
                "planner_comment": "Demo seed: historical override so the FVA scorecard has matured rows.",
                "planner_id": planner_id,
                "ip_address": "127.0.0.1",
                "session_id": MARKER,
                "tier": tier.get(item_id, "planner_led"),
                "risk_type": risk.get(item_id, "OOS_RISK"),
            }
            ledger.append_override(record)
            appended += 1
    if appended:
        log.info("Seeded %d historical override(s) for cutoff %s so FVA has matured rows", appended, svc.cutoff)
    return appended


def reset_demo_fva(ledger: Ledger, svc: DataService) -> int:
    """Remove demo-seed rows (re-chaining the ledger) and re-seed. Returns rows appended."""
    path = ledger.path
    with closing(sqlite3.connect(path, timeout=30, isolation_level=None)) as con:
        con.row_factory = sqlite3.Row
        con.execute("DROP TRIGGER IF EXISTS overrides_no_delete")
        con.execute("DROP TRIGGER IF EXISTS overrides_no_update")
        con.execute("DELETE FROM overrides WHERE session_id = ?", (MARKER,))
        rows: List[sqlite3.Row] = con.execute("SELECT * FROM overrides ORDER BY id").fetchall()
        prev = GENESIS
        for r in rows:
            payload = {f: r[f] for f in OVERRIDE_FIELDS}
            digest = _row_hash(prev, payload)
            con.execute("UPDATE overrides SET prev_hash = ?, row_hash = ? WHERE id = ?", (prev, digest, r["id"]))
            prev = digest
        con.executescript(
            "CREATE TRIGGER IF NOT EXISTS overrides_no_update BEFORE UPDATE ON overrides "
            "BEGIN SELECT RAISE(ABORT, 'overrides is insert-only'); END;"
            "CREATE TRIGGER IF NOT EXISTS overrides_no_delete BEFORE DELETE ON overrides "
            "BEGIN SELECT RAISE(ABORT, 'overrides is insert-only'); END;"
        )
    log.info("Removed demo-seed rows and re-chained the override ledger (%d rows kept)", len(rows))
    return seed_demo_fva(ledger, svc)


def main() -> None:
    import sys
    logging.basicConfig(level=logging.INFO)
    svc = DataService.from_reports(settings.reports_dir)
    ledger = Ledger(settings.store_dir / "ledger.db")
    if "--reset" in sys.argv:
        n = reset_demo_fva(ledger, svc)
        print(f"Reset: seeded {n} historical override(s) for cutoff {svc.cutoff}.")
    else:
        n = seed_demo_fva(ledger, svc)
        if n:
            print(f"Seeded {n} historical override(s) for cutoff {svc.cutoff}.")
        else:
            print("No new rows: demo FVA rows already present for the current cutoff.")


if __name__ == "__main__":
    main()
