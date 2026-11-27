"""Recovery marketplace: the platform resells stock it paid claims on, and learns its real recovery rate.

A lot is created when a claim pays units. Buyers bid a price per unit for the whole lot through a channel
(b2b, outlet, donation; donation keeps unsold goods out of destruction, as the EU ESPR ban requires for
apparel and footwear). An admin awards the best bid at or above the reserve; the proceeds are recorded.

recovery_experience(): observed rate = sum(proceeds) / sum(units x landed cost) over sold lots, blended
with the default assumption by credibility Z = min(1, sqrt(lots / 30)). New quotes use the blended rate.
"""
from __future__ import annotations

import json
import math
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.cover.policies import TENANT_RE

LOT_RE = re.compile(r"^[a-f0-9]{12}$")
CHANNELS = ("b2b", "outlet", "donation")
DEFAULT_RESERVE_PCT = 0.10
FULL_CREDIBILITY_LOTS = 30


class LotError(ValueError):
    pass


def new_lot(policy: Dict[str, Any], now: datetime, reserve_pct: float = DEFAULT_RESERVE_PCT) -> Dict[str, Any]:
    units = float(policy["claim"]["paid_units"])
    if units <= 0:
        raise LotError("no paid units to recover")
    cost = float(policy["unit_landed_cost_eur"])
    return {
        "id": uuid.uuid4().hex[:12], "status": "OPEN", "policy_id": policy["id"], "po_id": policy["po_id"],
        "sku": policy["sku"], "marketplace": policy.get("marketplace"), "units": units,
        "unit_landed_cost_eur": cost, "cost_value_eur": round(units * cost, 2),
        "reserve_price_per_unit_eur": round(reserve_pct * cost, 2), "bids": [], "award": None,
        "created_at": now.isoformat(),
    }


def add_bid(lot: Dict[str, Any], bidder: str, price_per_unit: float, channel: str, now: datetime,
            note: Optional[str] = None) -> Dict[str, Any]:
    if lot["status"] != "OPEN":
        raise LotError(f"lot is {lot['status']}")
    if channel not in CHANNELS:
        raise LotError(f"channel must be one of {list(CHANNELS)}")
    if price_per_unit < 0:
        raise LotError("price cannot be negative")
    if channel == "donation" and price_per_unit != 0:
        raise LotError("donation bids are free (price 0)")
    bid = {"id": uuid.uuid4().hex[:8], "bidder": bidder, "price_per_unit_eur": round(price_per_unit, 4),
           "channel": channel, "total_eur": round(price_per_unit * lot["units"], 2), "at": now.isoformat(),
           "note": note}
    lot["bids"].append(bid)
    return bid


def best_bid(lot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Highest price; ties go to the earliest bid. Donations rank last but are valid below the reserve."""
    paid = [b for b in lot["bids"] if b["channel"] != "donation"]
    if paid:
        return sorted(paid, key=lambda b: (-b["price_per_unit_eur"], b["at"]))[0]
    donations = [b for b in lot["bids"] if b["channel"] == "donation"]
    return sorted(donations, key=lambda b: b["at"])[0] if donations else None


def award(lot: Dict[str, Any], awarded_by: str, now: datetime) -> Dict[str, Any]:
    if lot["status"] != "OPEN":
        raise LotError(f"lot is {lot['status']}")
    bid = best_bid(lot)
    if bid is None:
        raise LotError("no bids yet")
    if bid["channel"] != "donation" and bid["price_per_unit_eur"] < lot["reserve_price_per_unit_eur"]:
        raise LotError(f"best bid €{bid['price_per_unit_eur']}/unit is below the reserve "
                       f"€{lot['reserve_price_per_unit_eur']}/unit")
    lot["award"] = {"bid_id": bid["id"], "bidder": bid["bidder"], "channel": bid["channel"],
                    "proceeds_eur": bid["total_eur"],
                    "recovery_rate": round(bid["total_eur"] / lot["cost_value_eur"], 4) if lot["cost_value_eur"] else 0.0,
                    "awarded_by": awarded_by, "awarded_at": now.isoformat()}
    lot["status"] = "SOLD" if bid["channel"] != "donation" else "DONATED"
    return lot["award"]


def observed_rate(lots: List[Dict[str, Any]]) -> Optional[float]:
    done = [lot for lot in lots if lot["status"] in ("SOLD", "DONATED") and lot.get("award")]
    cost = sum(lot["cost_value_eur"] for lot in done)
    return sum(lot["award"]["proceeds_eur"] for lot in done) / cost if cost > 0 else None


def recovery_experience(lots: List[Dict[str, Any]], default_rate: float,
                        pooled: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Credibility-blended recovery rate. The complement is the pooled observed rate of sharing tenants
    when available (pooled = {"rate", "tenants"}), otherwise the default assumption."""
    complement, source = default_rate, "default"
    if pooled and pooled.get("rate") is not None:
        complement, source = float(pooled["rate"]), "pooled"
    done = [lot for lot in lots if lot["status"] in ("SOLD", "DONATED") and lot.get("award")]
    cost = sum(lot["cost_value_eur"] for lot in done)
    proceeds = sum(lot["award"]["proceeds_eur"] for lot in done)
    base = {"lots": len(done), "default_rate": default_rate, "complement": round(complement, 4),
            "complement_source": source, "pooled_tenants": pooled.get("tenants") if pooled else None}
    if not done or cost <= 0:
        return {**base, "observed_rate": None, "credibility": 0.0, "rate": round(complement, 4)}
    observed = proceeds / cost
    z = min(1.0, math.sqrt(len(done) / FULL_CREDIBILITY_LOTS))
    return {**base, "observed_rate": round(observed, 4), "credibility": round(z, 3),
            "rate": round(min(1.0, max(0.0, z * observed + (1 - z) * complement)), 4),
            "proceeds_eur": round(proceeds, 2), "cost_value_eur": round(cost, 2)}


class LotStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._lock = threading.RLock()

    def _dir(self, tenant: str) -> Path:
        if not TENANT_RE.match(tenant or ""):
            raise ValueError("invalid tenant id")
        return self.root / tenant / "lots"

    def save(self, tenant: str, lot: Dict[str, Any]) -> Dict[str, Any]:
        folder = self._dir(tenant)
        with self._lock:
            folder.mkdir(parents=True, exist_ok=True)
            tmp = folder / f"{lot['id']}.json.tmp"
            tmp.write_text(json.dumps(lot, indent=2, default=str), encoding="utf-8")
            tmp.replace(folder / f"{lot['id']}.json")
        return lot

    def get(self, tenant: str, lot_id: str) -> Dict[str, Any]:
        path = self._dir(tenant) / f"{lot_id}.json"
        if not LOT_RE.match(lot_id or "") or not path.exists():
            raise KeyError(f"lot {lot_id} not found")
        return json.loads(path.read_text(encoding="utf-8"))

    def list(self, tenant: str) -> List[Dict[str, Any]]:
        folder = self._dir(tenant)
        if not folder.exists():
            return []
        return sorted((json.loads(p.read_text(encoding="utf-8")) for p in folder.glob("*.json")),
                      key=lambda lot: lot["created_at"], reverse=True)

    def lock(self):
        return self._lock
