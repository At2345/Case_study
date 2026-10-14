from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple


OVERRIDE_FIELDS = [
    "timestamp", "item_id", "cutoff", "target_month", "baseline_quantity", "ml_recommended_quantity",
    "submitted_quantity", "variance_to_ml", "financial_impact_gbp", "reason_code", "planner_comment",
    "planner_id", "ip_address", "session_id", "tier", "risk_type",
]
COMMIT_FIELDS = ["timestamp", "cutoff", "planner_id", "items", "path"]
GENESIS = "0" * 64


def _row_hash(prev_hash: str, payload: Dict[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256((prev_hash + body).encode("utf-8")).hexdigest()


class MongoLedger:
    def __init__(self, uri: str, db_name: str) -> None:
        from pymongo import MongoClient, ASCENDING, DESCENDING
        self._ASC, self._DESC = ASCENDING, DESCENDING
        self.client = MongoClient(uri)
        self.db = self.client[db_name]
        self.overrides = self.db["overrides"]
        self.commits = self.db["commits"]
        self.overrides.create_index([("cutoff", self._ASC), ("target_month", self._ASC), ("item_id", self._ASC), ("_id", self._ASC)])
        self.commits.create_index([( "cutoff", self._ASC)], unique=True)

    def _append(self, coll, fields: List[str], rec: Dict[str, Any]) -> Any:
        payload = {f: rec.get(f) for f in fields}
        last = coll.find_one(sort=[("_id", self._DESC)])
        prev = last.get("row_hash") if last else GENESIS
        digest = _row_hash(prev, payload)
        doc = {**payload, "prev_hash": prev, "row_hash": digest}
        res = coll.insert_one(doc)
        return res.inserted_id

    def append_override(self, rec: Dict[str, Any]) -> int:
        reason = rec.get("reason_code")
        if reason is None or not str(reason).strip():
            raise ValueError("Reason code is required.")
        self._append(self.overrides, OVERRIDE_FIELDS, rec)
        return 1

    def append_commit(self, rec: Dict[str, Any]) -> int:
        try:
            self._append(self.commits, COMMIT_FIELDS, rec)
        except Exception as exc:
            if "E11000" in str(exc):
                raise ValueError(f"cutoff {rec.get('cutoff')} already committed") from exc
            raise
        return 1

    def overrides(
        self, cutoff: Optional[str] = None, item_id: Optional[int] = None, planner_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        filt: Dict[str, Any] = {}
        if cutoff is not None:
            filt["cutoff"] = cutoff
        if item_id is not None:
            filt["item_id"] = item_id
        if planner_id is not None:
            filt["planner_id"] = planner_id
        cur = self.overrides.find(filt, sort=[("_id", self._ASC)])
        if limit:
            cur = cur.limit(int(limit))
        return [{k: v for k, v in d.items() if k != "_id"} for d in cur]

    def latest_by_item(self, cutoff: str, target_month: str) -> Dict[int, Dict[str, Any]]:
        pipeline = [
            {"$match": {"cutoff": cutoff, "target_month": target_month}},
            {"$sort": {"_id": -1}},
            {"$group": {"_id": "$item_id", "doc": {"$first": "$$ROOT"}}},
        ]
        out: Dict[int, Dict[str, Any]] = {}
        for g in self.overrides.aggregate(pipeline):
            d = g["doc"]
            d.pop("_id", None)
            out[int(g["_id"])] = d
        return out

    def latest_decisions(self, cutoff: str) -> Dict[Tuple[int, str], Dict[str, Any]]:
        pipeline = [
            {"$match": {"cutoff": cutoff}},
            {"$sort": {"_id": -1}},
            {"$group": {"_id": {"item_id": "$item_id", "target_month": "$target_month"}, "doc": {"$first": "$$ROOT"}}},
        ]
        out: Dict[Tuple[int, str], Dict[str, Any]] = {}
        for g in self.overrides.aggregate(pipeline):
            d = g["doc"]
            d.pop("_id", None)
            key = (int(g["_id"]["item_id"]), g["_id"]["target_month"])
            out[key] = d
        return out

    def commit_for(self, cutoff: str) -> Optional[Dict[str, Any]]:
        d = self.commits.find_one({"cutoff": cutoff})
        if not d:
            return None
        d.pop("_id", None)
        return d

    def verify_chain(self) -> Tuple[bool, Optional[int]]:
        for coll, fields in ((self.overrides, OVERRIDE_FIELDS), (self.commits, COMMIT_FIELDS)):
            prev = GENESIS
            for d in coll.find({}, sort=[("_id", self._ASC)]):
                payload = {f: d.get(f) for f in fields}
                if d.get("prev_hash") != prev or _row_hash(prev, payload) != d.get("row_hash"):
                    return False, None
                prev = d.get("row_hash")
        return True, None
