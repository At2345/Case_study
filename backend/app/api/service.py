"""Read model for the API: serves the triage artifacts and builds the consensus plan."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.data.loaders import load
from app.phasing.lag import expected_deliveries
from app.triage.quality import mask_incomplete

DECISION_KEY = Tuple[int, str]


def clean(value: Any) -> Any:
    """JSON-safe scalar: NaN/NaT -> None, numpy -> python."""
    if value is None:
        return None
    if isinstance(value, (np.floating, float)):
        return None if np.isnan(value) else float(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    return value


def _month(ts: Any) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


class DataService:
    TELEMETRY = ["glance_mom_pct", "p70_gap_pct", "on_hand", "open_po", "oos_rate_28d", "weeks_of_cover"]

    def __init__(
        self,
        triage: pd.DataFrame,
        monthly: pd.DataFrame,
        actuals: pd.DataFrame,
        meta: Dict[str, Any],
        phasing_items: Optional[pd.DataFrame] = None,
        phasing_profile: Optional[pd.DataFrame] = None,
    ) -> None:
        self.triage = triage.set_index("item_id", drop=False)
        self.phasing_items = phasing_items if phasing_items is not None else pd.DataFrame(
            columns=["item_id", "month", "booked_qty", "booked_shifted_qty", "carry_in_qty", "shifted_in_qty"])
        self.phasing_profile = phasing_profile if phasing_profile is not None else pd.DataFrame(
            columns=["channel", "p0", "p1", "p2plus"])
        self._phasing_by_item = {
            k: g.assign(month=g["month"].map(_month)).set_index("month")
            for k, g in self.phasing_items.groupby("item_id")
        }
        self._profile = {r.channel: (float(r.p0), float(r.p1), float(r.p2plus)) for r in self.phasing_profile.itertuples()}
        self.monthly = monthly.copy()
        self.monthly["month"] = self.monthly["date"].map(_month)
        self.actuals = actuals
        self.meta = meta
        self.cutoff: str = meta["cutoff"]
        self.first_month: str = meta["first_month"]
        self._monthly_by_item = {k: g.sort_values("date") for k, g in self.monthly.groupby("item_id")}
        self._actuals_by_item = {k: g.sort_values("date") for k, g in actuals.groupby("item_id")}
        self._build_indexes()

    _STATIC_COLS = ["item_id", "product_id", "channel", "abc_category", "tier", "state", "sales_price", "baseline_h",
                    "final_h", "final_m1", "value_delta_gbp", "risk_type", "risk_flags", "narrative", "source"]

    def _build_indexes(self) -> None:
        """Everything that does not depend on planner decisions is computed once per data load."""
        t = self.triage
        self._static = {}
        for rec in t[self._STATIC_COLS].to_dict("records"):
            rec = {k: clean(v) for k, v in rec.items()}
            rec["risk_flags"] = [f for f in str(rec["risk_flags"]).split(",") if f]
            self._static[int(rec["item_id"])] = rec
        self._filter_index = t[["item_id", "state", "channel", "abc_category", "risk_type", "var_gbp"]].sort_values(
            "var_gbp", ascending=False).reset_index(drop=True)
        self._review_ids = [int(i) for i in t.loc[t["state"] == "EXCEPTION_REVIEW", "item_id"]]
        scope = t[t["in_scope"]]
        review = scope[scope["state"] == "EXCEPTION_REVIEW"]
        price = scope["sales_price"].fillna(0)
        self._summary_static = {
            "total_catalog_skus": int(len(t)), "in_scope_skus": int(len(scope)),
            "autopilot_approved_skus": int((scope["state"] == "AUTO_APPROVED").sum()),
            "exceptions_requiring_review": int(len(review)),
            "planner_led_skus": int((scope["state"] == "PLANNER_LED").sum()),
            "planned_value_gbp": float((scope["final_h"] * price).sum()),
            "baseline_value_gbp": float((scope["baseline_h"] * price).sum()),
            "net_value_variance_gbp": float(((scope["final_h"] - scope["baseline_h"]) * price).sum()),
            "critical_alerts": int(review["risk_type"].isin(["OOS_RISK", "CRITICAL_AMAZON_BUYBOX_ALERT"]).sum()),
        }

    @classmethod
    def from_reports(cls, reports_dir: Path, data_dir=None) -> "DataService":
        reports_dir = Path(reports_dir)
        triage = pd.read_parquet(reports_dir / "triage_live.parquet")
        monthly = pd.read_parquet(reports_dir / "triage_monthly.parquet")
        meta = json.loads((reports_dir / "triage_meta.json").read_text())
        start = pd.Timestamp(meta["cutoff"]) - pd.DateOffset(months=23)
        ts = load("target_time_series", columns=["item_id", "date", "target"], data_dir=data_dir,
                  filters=[("date", ">=", start)])
        warn = pd.DataFrame(meta.get("quality_warnings", []))
        if not warn.empty:
            flagged = pd.DataFrame({"channel": warn["channel"], "date": pd.to_datetime(warn["month"])})
            ts = mask_incomplete(ts, triage[["item_id", "channel"]], flagged)
        items_path, profile_path = reports_dir / "phasing_items.parquet", reports_dir / "phasing_profile.csv"
        phasing_items = pd.read_parquet(items_path) if items_path.exists() else None
        phasing_profile = pd.read_csv(profile_path) if profile_path.exists() else None
        return cls(triage, monthly, ts, meta, phasing_items, phasing_profile)

    # ------------------------------------------------------------------ lookups
    def has_item(self, item_id: int) -> bool:
        return item_id in self.triage.index

    def item_row(self, item_id: int) -> Optional[pd.Series]:
        return self.triage.loc[item_id] if item_id in self.triage.index else None

    def has_month(self, item_id: int, month: str) -> bool:
        m = self._monthly_by_item.get(item_id)
        return m is not None and (m["month"] == month).any()

    def month_values(self, item_id: int, month: str) -> Tuple[float, float]:
        """(baseline quantity, system-recommended quantity) for an item-month."""
        m = self._monthly_by_item[item_id]
        r = m[m["month"] == month].iloc[0]
        return float(r["baseline_qty"]), float(r["final_qty"])

    def product_demand(self, product_id: str, month: str) -> List[Dict[str, Any]]:
        """Planned demand per channel item of a product for a month (all items, in scope or not)."""
        items = self.triage[self.triage["product_id"] == product_id]
        rows = []
        for _, r in items.iterrows():
            m = self._monthly_by_item.get(int(r["item_id"]))
            if m is None or not (m["month"] == month).any():
                continue
            qty = float(m[m["month"] == month].iloc[0]["final_qty"])
            rows.append({"item_id": int(r["item_id"]), "channel": r["channel"], "demand": qty,
                         "sales_price": float(r["sales_price"]), "purchase_price": float(r["purchase_price"])})
        return rows

    # ------------------------------------------------------------------ views
    def _row_to_exception(self, item_id: int, decision: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        s = self._static[item_id]
        final_h = float(s["final_h"])
        current = final_h
        if decision and decision["target_month"] == self.first_month:
            current = final_h + (decision["submitted_quantity"] - float(s["final_m1"]))
        return {
            "item_id": item_id, "product_id": s["product_id"], "channel": s["channel"],
            "abc_category": s["abc_category"], "tier": s["tier"], "state": s["state"],
            "sales_price": s["sales_price"], "baseline_forecast": s["baseline_h"],
            "ml_forecast": final_h, "current_final": current, "has_override": decision is not None,
            "decision_reason": decision["reason_code"] if decision else None,
            "value_delta_gbp": s["value_delta_gbp"], "risk_type": s["risk_type"],
            "risk_flags": list(s["risk_flags"]), "narrative": s["narrative"], "source": s["source"],
        }

    def exceptions(
        self,
        decisions: Dict[DECISION_KEY, Dict[str, Any]],
        state: str = "EXCEPTION_REVIEW",
        channel: Optional[str] = None,
        abc_category: Optional[str] = None,
        risk_type: Optional[str] = None,
        pending: Optional[bool] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        df = self._filter_index
        mask = df["state"] == state
        if channel:
            mask &= df["channel"] == channel
        if abc_category:
            mask &= df["abc_category"] == abc_category
        if risk_type:
            mask &= df["risk_type"] == risk_type
        decided = self._decided_first_month(decisions)
        if pending is not None:
            has = df["item_id"].isin(decided)
            mask &= ~has if pending else has
        ids = df.loc[mask, "item_id"]
        page = ids.iloc[offset: offset + limit]
        rows = [self._row_to_exception(int(i), decided.get(int(i))) for i in page]
        return {"total": int(len(ids)), "items": rows}

    def _decided_first_month(self, decisions: Dict[DECISION_KEY, Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
        return {i: d for (i, m), d in decisions.items() if m == self.first_month}

    def pending_review_ids(self, decisions: Dict[DECISION_KEY, Dict[str, Any]]) -> List[int]:
        decided = {i for (i, _m) in decisions}
        return [i for i in self._review_ids if i not in decided]

    def summary(self, decisions: Dict[DECISION_KEY, Dict[str, Any]], committed: bool, fva: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        pending = self.pending_review_ids(decisions)
        n_review = self._summary_static["exceptions_requiring_review"]
        return {
            "cutoff": self.cutoff, "first_month": self.first_month, "generated_at": self.meta.get("generated_at"),
            **self._summary_static,
            "pending_decisions": len(pending), "decided": n_review - len(pending),
            "quality_warnings": self.meta.get("quality_warnings", []),
            "fva": fva, "committed": committed,
        }

    def item_detail(self, item_id: int, decisions: Dict[DECISION_KEY, Dict[str, Any]], history: int = 18) -> Optional[Dict[str, Any]]:
        r = self.item_row(item_id)
        if r is None:
            return None
        decided = {m: d for (i, m), d in decisions.items() if i == item_id}
        plan = []
        for _, m in self._monthly_by_item.get(item_id, pd.DataFrame()).iterrows():
            d = decided.get(m["month"])
            plan.append({
                "month": m["month"], "baseline": clean(m["baseline_qty"]), "ml": clean(m["ml_prediction"]),
                "final": clean(m["final_qty"]), "source": m["source"],
                "consensus": d["submitted_quantity"] if d else clean(m["final_qty"]),
                "override": d["submitted_quantity"] if d else None,
            })
        act = self._actuals_by_item.get(item_id, self.actuals.iloc[0:0]).tail(history)
        item_keys = ["item_id", "product_id", "channel", "abc_category", "abc_derived", "tier", "state", "risk_type",
                     "narrative", "sales_price", "source", "phase", "months_since_launch", "months_to_eol",
                     "value_delta_gbp", "baseline_h", "final_h"]
        item = {k: clean(r[k]) for k in item_keys if k in r.index}
        item["risk_flags"] = [f for f in str(r["risk_flags"]).split(",") if f]
        first = decided.get(self.first_month)
        return {
            "item": item, "plan": plan,
            "history": [{"month": _month(d), "actual": clean(v)} for d, v in zip(act["date"], act["target"])],
            "telemetry": {k: clean(r[k]) for k in self.TELEMETRY if k in r.index},
            "decision": first,
        }

    # ------------------------------------------------------------------ order-book phasing
    def _probs(self, channel: str) -> Tuple[float, float, float]:
        return self._profile.get(channel, (1.0, 0.0, 0.0))

    def _delivery_for(self, item_id: int, channel: str, demand: pd.Series) -> pd.Series:
        """Expected delivered units per month for one item given its demand series (indexed by month string)."""
        ph = self._phasing_by_item.get(item_id)
        carry = ph["carry_in_qty"].reindex(demand.index).fillna(0.0) if ph is not None else None
        return expected_deliveries(demand, self._probs(channel), carry)

    def phasing_detail(self, item_id: int, decisions: Dict[DECISION_KEY, Dict[str, Any]]) -> List[Dict[str, Any]]:
        r = self.item_row(item_id)
        m = self._monthly_by_item.get(item_id)
        if r is None or m is None:
            return []
        dec = {mo: d["submitted_quantity"] for (i, mo), d in decisions.items() if i == item_id}
        demand = pd.Series([dec.get(mo, q) for mo, q in zip(m["month"], m["final_qty"])], index=list(m["month"]), dtype=float)
        delivery = self._delivery_for(item_id, r["channel"], demand)
        ph = self._phasing_by_item.get(item_id)
        out = []
        for mo, base_qty in zip(m["month"], m["final_qty"]):
            row = ph.loc[mo] if (ph is not None and mo in ph.index) else None
            booked = float(row["booked_qty"]) if row is not None else 0.0
            out.append({
                "month": mo, "demand": float(demand[mo]), "expected_delivery": float(delivery[mo]),
                "booked": booked, "booked_shifted": float(row["booked_shifted_qty"]) if row is not None else 0.0,
                "carry_in": float(row["carry_in_qty"]) if row is not None else 0.0,
                "shifted_in": float(row["shifted_in_qty"]) if row is not None else 0.0,
                "order_book_floor": booked if booked > float(base_qty) else None,
            })
        return out

    def delivery_plan(self, cons: pd.DataFrame) -> pd.DataFrame:
        """Add expected_delivery_qty to a consensus frame (item_id, channel, month, consensus_qty)."""
        piv = cons.pivot(index="item_id", columns="month", values="consensus_qty").sort_index(axis=1).fillna(0.0)
        channel = cons.drop_duplicates("item_id").set_index("item_id")["channel"].reindex(piv.index)
        carry = pd.DataFrame(0.0, index=piv.index, columns=piv.columns)
        if not self.phasing_items.empty:
            p = self.phasing_items.assign(month=self.phasing_items["month"].map(_month))
            c = p.pivot_table(index="item_id", columns="month", values="carry_in_qty", aggfunc="sum")
            carry = c.reindex(index=piv.index, columns=piv.columns).fillna(0.0)
        expected = pd.DataFrame(0.0, index=piv.index, columns=piv.columns)
        for ch in channel.unique():
            ids = channel.index[channel == ch]
            d = piv.loc[ids].to_numpy()
            p0, p1, p2 = self._probs(ch)
            e = p0 * d
            e[:, 1:] += p1 * d[:, :-1]
            e[:, 2:] += p2 * d[:, :-2]
            expected.loc[ids] = e + carry.loc[ids].to_numpy()
        long = expected.stack().rename("expected_delivery_qty").reset_index()
        long.columns = ["item_id", "month", "expected_delivery_qty"]
        return cons.merge(long, on=["item_id", "month"], how="left")

    def phasing_summary(self, decisions: Dict[DECISION_KEY, Dict[str, Any]]) -> Dict[str, Any]:
        cons = self.delivery_plan(self.consensus(decisions))
        booked = self.phasing_items.assign(month=self.phasing_items["month"].map(_month))
        booked = booked.merge(self.triage[["item_id", "channel"]].reset_index(drop=True), on="item_id", how="left")
        agg = cons.groupby(["month", "channel"])[["consensus_qty", "expected_delivery_qty"]].sum()
        bk = booked.groupby(["month", "channel"])[["booked_qty", "booked_shifted_qty", "carry_in_qty", "shifted_in_qty"]].sum()
        j = agg.join(bk, how="left").fillna(0.0).reset_index()
        rows = [{
            "month": r.month, "channel": r.channel, "demand": float(r.consensus_qty),
            "expected_delivery": float(r.expected_delivery_qty), "shift": float(r.expected_delivery_qty - r.consensus_qty),
            "booked": float(r.booked_qty), "booked_shifted": float(r.booked_shifted_qty),
            "carry_in": float(r.carry_in_qty), "shifted_in": float(r.shifted_in_qty),
        } for r in j.itertuples()]
        return {"rows": rows, "profile": {c: {"p0": p[0], "p1": p[1], "p2plus": p[2]} for c, p in self._profile.items()},
                "note": "Demand is booked in the month the material is available; deliveries (and billing) follow the lag profile."}

    # ------------------------------------------------------------------ consensus
    def consensus(self, decisions: Dict[DECISION_KEY, Dict[str, Any]]) -> pd.DataFrame:
        scope_ids = set(self.triage.loc[self.triage["in_scope"], "item_id"])
        m = self.monthly[self.monthly["item_id"].isin(scope_ids)].copy()
        info = self.triage[["item_id", "product_id", "channel"]].reset_index(drop=True)
        m = m.merge(info, on="item_id", how="left")
        keys = list(zip(m["item_id"], m["month"]))
        dec = [decisions.get((int(i), mo)) for i, mo in keys]
        m["consensus_qty"] = [d["submitted_quantity"] if d else q for d, q in zip(dec, m["final_qty"])]
        m["decided_by"] = [d["planner_id"] if d else None for d in dec]
        m["reason_code"] = [d["reason_code"] if d else None for d in dec]
        out = m.rename(columns={"final_qty": "ml_qty"})[
            ["item_id", "product_id", "channel", "month", "baseline_qty", "ml_qty", "consensus_qty", "source", "decided_by", "reason_code"]
        ]
        return out.sort_values(["item_id", "month"]).reset_index(drop=True)

    def consensus_with_deliveries(self, decisions: Dict[DECISION_KEY, Dict[str, Any]]) -> pd.DataFrame:
        out = self.delivery_plan(self.consensus(decisions))
        return out.sort_values(["item_id", "month"]).reset_index(drop=True)
