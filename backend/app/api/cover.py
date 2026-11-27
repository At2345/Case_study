"""Overstock cover quotes: price a purchase order from the platform's own demand forecast."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from app.bi.tenancy import DEFAULT_TENANT
from app.cover.quote import CoverTerms, Demand, demand_from_forecast, quote
from app.cover.policies import (CLOSED, PolicyError, PolicyStore, assess, effective_status, new_policy, portfolio,
                                rescue_due, settle)
from app.cover.experience import experience, raw_a_to_e
from app.cover.policies import TENANT_RE
from app.cover.monitor import check_policies
from app.cover.recovery import (LotError, LotStore, add_bid, award, best_bid, new_lot, observed_rate,
                                recovery_experience)
from app.cover.store import QuoteStore
from app.models.enums import Role


class TermsOverride(BaseModel):
    horizon_days: Optional[int] = Field(None, ge=30, le=365)
    floor_pct: Optional[float] = Field(None, ge=0, le=1)
    recovery_rate: Optional[float] = Field(None, ge=0, le=1)
    deductible_pct: Optional[float] = Field(None, ge=0, le=1)
    coinsurance: Optional[float] = Field(None, ge=0, le=1)
    cap_quantile: Optional[float] = Field(None, ge=0.5, lt=1)
    loading: Optional[float] = Field(None, ge=0, le=5)


class QuoteRequest(BaseModel):
    po_id: str = Field(..., min_length=1, max_length=64)
    sku: Optional[str] = Field(None, max_length=64)
    units: float = Field(..., gt=0, le=10_000_000)
    unit_landed_cost_eur: float = Field(..., gt=0, le=1_000_000)
    item_id: Optional[int] = Field(None, description="Price from the platform forecast for this item")
    demand_mean_units: Optional[float] = Field(None, gt=0, description="Manual demand over the cover window")
    demand_sd_units: Optional[float] = Field(None, ge=0)
    terms: Optional[TermsOverride] = None

    @model_validator(mode="after")
    def _one_demand_source(self) -> "QuoteRequest":
        if (self.item_id is None) == (self.demand_mean_units is None):
            raise ValueError("give either item_id (forecast) or demand_mean_units (manual), not both")
        return self


class BindRequest(BaseModel):
    marketplace: Optional[str] = Field(None, max_length=8)
    rescue_playbook_id: Optional[str] = Field(None, max_length=64)
    rescue_gap_threshold: float = Field(-0.20, ge=-0.9, lt=0)


class ObservationRequest(BaseModel):
    as_of: Optional[datetime] = None
    units_sold_to_date: float = Field(..., ge=0)
    units_on_hand: float = Field(..., ge=0)


class SettleRequest(BaseModel):
    units_unsold: float = Field(..., ge=0)


class BidRequest(BaseModel):
    price_per_unit_eur: float = Field(..., ge=0, le=1_000_000)
    channel: str = Field("b2b", pattern="^(b2b|outlet|donation)$")
    bidder: Optional[str] = Field(None, max_length=80, description="Buyer name; defaults to the caller")
    note: Optional[str] = Field(None, max_length=300)


def register(app: FastAPI, ctx: Dict[str, Any]) -> None:
    require, all_roles, state, decisions = ctx["require"], ctx["all_roles"], ctx["state"], ctx["decisions"]
    write_roles, admin = (Role.PLANNER.value, Role.ADMIN.value), Role.ADMIN.value
    store = QuoteStore(ctx["store_dir"] / "cover")

    def tenant(user: Dict[str, str]) -> str:
        return user.get("tenant") or DEFAULT_TENANT

    def demand_for(body: QuoteRequest, horizon_days: int) -> Demand:
        if body.item_id is None:
            sd = body.demand_sd_units if body.demand_sd_units is not None else 0.3 * body.demand_mean_units
            return Demand(body.demand_mean_units, sd, source="manual")
        svc = state["svc"]
        detail = svc.item_detail(body.item_id, decisions())
        if detail is None:
            raise HTTPException(status_code=404, detail=f"item {body.item_id} not found")
        plan = [r for r in detail["plan"] if r["month"] >= svc.first_month]
        months = max(1, math.ceil(horizon_days / 30))
        sigmas = [svc.sigma(body.item_id, r["month"]) for r in plan[:months]]
        return demand_from_forecast(plan, sigmas, months)

    @app.post("/api/v1/cover/quotes", status_code=201)
    def create_quote(body: QuoteRequest, user=Depends(require(*write_roles))):
        """Indicative cover quote for one purchase order (non-binding, valid 7 days)."""
        overrides = body.terms.model_dump(exclude_none=True) if body.terms else {}
        if overrides and user["role"] != admin:
            raise HTTPException(status_code=403, detail="only admins can change cover terms")
        try:
            t = tenant(user)
            pool = pooled_experience(t)
            # Recovery rate learned from this tenant's resold lots, unless an admin sets it explicitly.
            recovery = recovery_experience(lots.list(t), CoverTerms().recovery_rate, pool["recovery"])
            if "recovery_rate" not in overrides:
                overrides["recovery_rate"] = recovery["rate"]
            terms = CoverTerms(**overrides)
            rating = experience(policies.list(t), pool["loss"])  # own settled policies, pooled complement
            result = quote(body.units, body.unit_landed_cost_eur, demand_for(body, terms.horizon_days), terms,
                           experience=rating)
            result["recovery"] = recovery
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        record = {"po_id": body.po_id, "sku": body.sku, "item_id": body.item_id,
                  "unit_landed_cost_eur": body.unit_landed_cost_eur, **result}
        return store.add(tenant(user), record, user["username"])

    @app.get("/api/v1/cover/quotes")
    def list_quotes(limit: int = Query(50, ge=1, le=500), user=Depends(require(*all_roles))):
        return {"quotes": store.list(tenant(user), limit)}

    @app.get("/api/v1/cover/quotes/{quote_id}")
    def get_quote(quote_id: str, user=Depends(require(*all_roles))):
        try:
            return store.get(tenant(user), quote_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc.args[0]))

    # ------------------------------------------------------------------ policies (pilot)
    policies = PolicyStore(ctx["store_dir"] / "cover")
    lots = LotStore(ctx["store_dir"] / "cover")
    workflows, runner = ctx.get("workflows"), ctx.get("runner")
    notes = ctx.get("notes")
    min_tenants = int(ctx.get("benchmark_min_tenants") or 3)

    def sharing(t: str) -> bool:
        try:
            return bool(workflows and workflows.get_settings(t)["share_benchmarks"])
        except ValueError:
            return False

    def pooled_experience(t: str) -> Dict[str, Optional[Dict[str, Any]]]:
        """Pooled loss A/E and recovery rate across tenants that share (give-to-get, minimum group size)."""
        empty = {"loss": None, "recovery": None}
        root = ctx["store_dir"] / "cover"
        if not sharing(t) or not root.exists():
            return empty
        all_policies, all_lots, loss_tenants, lot_tenants = [], [], 0, 0
        for d in root.iterdir():
            if not d.is_dir() or not TENANT_RE.match(d.name) or not sharing(d.name):
                continue
            ps, ls = policies.list(d.name), lots.list(d.name)
            if raw_a_to_e(ps) is not None:
                loss_tenants += 1
                all_policies += ps
            if observed_rate(ls) is not None:
                lot_tenants += 1
                all_lots += ls
        return {
            "loss": {"a_to_e": raw_a_to_e(all_policies), "tenants": loss_tenants} if loss_tenants >= min_tenants else None,
            "recovery": {"rate": observed_rate(all_lots), "tenants": lot_tenants} if lot_tenants >= min_tenants else None,
        }
    # Scheduler hook (see server.py): settlement and sales-snapshot reminders.
    app.state.cover_monitor = lambda now=None: check_policies(policies, notes, now)

    def now() -> datetime:
        return datetime.now(timezone.utc)

    def view(policy: Dict[str, Any]) -> Dict[str, Any]:
        last = policy["observations"][-1] if policy["observations"] else None
        return {**policy, "effective_status": effective_status(policy, now()), "last_observation": last}

    def load(user, policy_id: str) -> Dict[str, Any]:
        try:
            return policies.get(tenant(user), policy_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc.args[0]))

    @app.post("/api/v1/cover/quotes/{quote_id}/bind", status_code=201)
    def bind_quote(quote_id: str, body: BindRequest, user=Depends(require(*write_roles))):
        """Turn an offered, unexpired quote into a pilot cover policy (one policy per quote)."""
        t = tenant(user)
        try:
            q = store.get(t, quote_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc.args[0]))
        if body.rescue_playbook_id:
            try:
                workflows.get(t, body.rescue_playbook_id)
            except KeyError:
                raise HTTPException(status_code=422, detail=f"playbook {body.rescue_playbook_id} not found")
        with policies.lock():
            if any(p["quote_id"] == quote_id and p["status"] != "CANCELLED" for p in policies.list(t)):
                raise HTTPException(status_code=409, detail="this quote is already bound")
            try:
                policy = new_policy(q, user["username"], now(), body.marketplace, body.rescue_playbook_id,
                                    body.rescue_gap_threshold)
            except PolicyError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            return view(policies.save(t, policy))

    @app.get("/api/v1/cover/policies")
    def list_policies(user=Depends(require(*all_roles))):
        return {"policies": [view(p) for p in policies.list(tenant(user))]}

    @app.get("/api/v1/cover/policies/{policy_id}")
    def get_policy(policy_id: str, user=Depends(require(*all_roles))):
        return view(load(user, policy_id))

    @app.post("/api/v1/cover/policies/{policy_id}/observations", status_code=201)
    def observe(policy_id: str, body: ObservationRequest, user=Depends(require(*write_roles))):
        """Record a sales snapshot (from ERP, n8n or a planner). Starts the linked rescue playbook when
        sell-through is behind the expected curve."""
        t = tenant(user)
        as_of = body.as_of or now()
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=timezone.utc)
        with policies.lock():
            policy = load(user, policy_id)
            if policy["status"] in CLOSED:
                raise HTTPException(status_code=409, detail=f"policy is {policy['status']}")
            assessment = assess(policy, body.units_sold_to_date, as_of)
            observation = {"at": as_of.isoformat(), "recorded_by": user["username"],
                           "units_sold_to_date": body.units_sold_to_date, "units_on_hand": body.units_on_hand,
                           **assessment}
            reason = rescue_due(policy, assessment, as_of)
            rescue: Dict[str, Any] = {"started": False, "reason": reason}
            if reason is None:
                payload = {"sku": policy["sku"], "marketplace": policy.get("marketplace") or "DE",
                           "units_on_hand": int(body.units_on_hand), "gap": assessment["gap"],
                           "unit_cost": policy["unit_landed_cost_eur"],
                           "days_to_trigger": int(assessment["days_to_end"]), "policy_id": policy["id"]}
                try:
                    run = runner.start(t, policy["rescue_playbook_id"], payload, f"cover-monitor:{policy['id']}")
                    policy["rescue_runs"].append({"run_id": run["run_id"], "at": as_of.isoformat(),
                                                  "gap": assessment["gap"], "status": run["status"]})
                    rescue = {"started": True, "run_id": run["run_id"], "status": run["status"]}
                except Exception as exc:  # a broken playbook must not lose the sales snapshot
                    rescue = {"started": False, "reason": f"rescue playbook failed to start: {exc}"}
            observation["rescue"] = rescue
            policy["observations"].append(observation)
            policies.save(t, policy)
        return {"policy": view(policy), "assessment": assessment, "rescue": rescue}

    @app.post("/api/v1/cover/policies/{policy_id}/settle")
    def settle_policy(policy_id: str, body: SettleRequest, user=Depends(require(admin))):
        """Settle after the cover window with the final unsold count (admin only: it fixes the payout)."""
        with policies.lock():
            policy = load(user, policy_id)
            try:
                claim = settle(policy, body.units_unsold, user["username"], now())
            except PolicyError as exc:
                raise HTTPException(status_code=409, detail=str(exc))
            if claim["paid_units"] > 0:  # the platform now owns the paid units: open a recovery lot
                claim["lot_id"] = lots.save(tenant(user), new_lot(policy, now()))["id"]
            return view(policies.save(tenant(user), policy))

    @app.get("/api/v1/cover/portfolio")
    def cover_portfolio(user=Depends(require(*all_roles))):
        t = tenant(user)
        pool = pooled_experience(t)
        book = portfolio(policies.list(t), now())
        book["experience"] = experience(policies.list(t), pool["loss"])  # same factor pricing uses
        recovery = recovery_experience(lots.list(t), CoverTerms().recovery_rate, pool["recovery"])
        recovered = recovery.get("proceeds_eur", 0.0)
        premium_settled = sum(p["premium_eur"] for p in policies.list(t) if p["status"] in ("CLAIMED", "CLOSED_NO_CLAIM"))
        book.update(recovered_eur=round(recovered, 2), net_claims_eur=round(book["claims_paid_eur"] - recovered, 2),
                    net_loss_ratio=round((book["claims_paid_eur"] - recovered) / premium_settled, 3) if premium_settled else None,
                    recovery=recovery)
        return book

    # ------------------------------------------------------------------ recovery marketplace
    def load_lot(user, lot_id: str) -> Dict[str, Any]:
        try:
            return lots.get(tenant(user), lot_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc.args[0]))

    def lot_view(lot: Dict[str, Any]) -> Dict[str, Any]:
        return {**lot, "best_bid": best_bid(lot)}

    @app.get("/api/v1/cover/lots")
    def list_lots(user=Depends(require(*all_roles))):
        return {"lots": [lot_view(lot) for lot in lots.list(tenant(user))]}

    @app.get("/api/v1/cover/lots/{lot_id}")
    def get_lot(lot_id: str, user=Depends(require(*all_roles))):
        return lot_view(load_lot(user, lot_id))

    @app.post("/api/v1/cover/lots/{lot_id}/bids", status_code=201)
    def place_bid(lot_id: str, body: BidRequest, user=Depends(require(*write_roles))):
        """Bid for a whole lot (planners, or buyer systems with an API key)."""
        with lots.lock():
            lot = load_lot(user, lot_id)
            # Buyer systems bid as themselves; planners may enter a bid on a named buyer's behalf.
            bidder = user["username"] if user.get("via") == "api_key" else (body.bidder or user["username"])
            try:
                bid = add_bid(lot, bidder, body.price_per_unit_eur, body.channel, now(), body.note)
            except LotError as exc:
                raise HTTPException(status_code=409 if "lot is" in str(exc) else 422, detail=str(exc))
            lots.save(tenant(user), lot)
        return {"bid": bid, "lot": lot_view(lot)}

    @app.post("/api/v1/cover/lots/{lot_id}/award")
    def award_lot(lot_id: str, user=Depends(require(admin))):
        """Award the lot to the best bid at or above the reserve (donations are always acceptable)."""
        with lots.lock():
            lot = load_lot(user, lot_id)
            try:
                award(lot, user["username"], now())
            except LotError as exc:
                raise HTTPException(status_code=409, detail=str(exc))
            return lot_view(lots.save(tenant(user), lot))

    @app.get("/api/v1/cover/terms")
    def default_terms(user=Depends(require(*all_roles))):
        from dataclasses import asdict
        return asdict(CoverTerms())
