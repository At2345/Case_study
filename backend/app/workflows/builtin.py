"""First built-in node set for overstock-rescue playbooks.

Action nodes are simulated: they record what they would do and never call Amazon or any other
external system. Real adapters (SP-API pricing, ads, inventory transfer) replace the handlers later.
"""
from __future__ import annotations

import copy
from datetime import timedelta
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.workflows.registry import NodeContext, NodeResult, Policy, Registry

# Note: app.workflows.webhooks (httpx) is imported lazily inside the webhook handler. Importing it at
# module level would pull httpx into Temporal's workflow sandbox, which rejects it.

OPS = {
    "<": lambda a, b: a < b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b, "==": lambda a, b: a == b, "!=": lambda a, b: a != b,
}


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------- params
class ManualTriggerParams(_Params):
    pass


class SellThroughTriggerParams(_Params):
    gap_threshold: float = Field(-0.2, le=0, description="Fire when sell-through is this far below forecast (-0.2 = 20%)")
    min_days_since_receipt: int = Field(60, ge=0)


class IfParams(_Params):
    left: Any
    op: Literal["<", "<=", ">", ">=", "==", "!="]
    right: Any


class WaitParams(_Params):
    days: float = Field(gt=0, le=365)


class ApprovalParams(_Params):
    approver_role: str = "planner"
    message: str = "Please review this step."


class RescueProposalParams(_Params):
    sku: str
    marketplace: str
    units_on_hand: int = Field(ge=0)
    sell_through_gap: float = Field(description="Actual vs forecast sell-through, e.g. -0.25")
    unit_landed_cost_eur: float = Field(gt=0)
    days_to_trigger: int = Field(ge=0, description="Days until the day-180 coverage trigger")


class ChangePriceParams(_Params):
    sku: str
    marketplace: str
    pct_change: float = Field(ge=-50, le=50)
    estimated_cost_eur: float = Field(0, ge=0, description="Expected margin given up; counted against the budget")


class TransferStockParams(_Params):
    sku: str
    from_marketplace: str
    to_marketplace: str
    units: int = Field(gt=0)
    estimated_cost_eur: float = Field(0, ge=0)


class NotifyParams(_Params):
    channel: Literal["in_app", "email", "slack"] = "in_app"
    to: str = "planner"
    message: str


class MeasureOutcomeParams(_Params):
    sku: str
    baseline_units_at_risk: float = Field(ge=0, description="Units expected to stay unsold without action "
                                          "(e.g. $nodes.propose.units_at_risk)")
    units_unsold: float = Field(ge=0, description="Units actually still unsold now (e.g. from a stock fetch)")
    unit_landed_cost_eur: float = Field(gt=0)
    recovery_rate: float = Field(0.0, ge=0, le=1, description="Share of cost recovered on unsold stock anyway "
                                 "(liquidation); 0 = full write-off")
    units_sold: Optional[float] = Field(None, ge=0, description="Units sold since the action (optional)")


class N8nWebhookParams(_Params):
    url: str = Field(pattern=r"^https?://", max_length=2048, description="n8n Webhook node URL (production URL)")
    body: Optional[Dict[str, Any]] = Field(None, description="JSON body; defaults to the run context")
    secret_ref: Optional[str] = Field(None, pattern=r"^[A-Z0-9_]{1,40}$",
                                      description="Sends env WF_SECRET_<REF> in the X-Workflow-Secret header")
    timeout_s: float = Field(10, gt=0, le=30)
    dry_run_response: Optional[Dict[str, Any]] = Field(
        None, description="Response used instead of calling n8n in dry runs, so later steps can be tested")


CHANNEL_EVENTS = ("order_created", "order_updated", "inventory_changed", "stock_low", "edi_ship_notice",
                  "edi_invoice", "edi_po_ack", "edi_functional_ack", "edi_error")


class ChannelEventParams(_Params):
    event: Literal[CHANNEL_EVENTS] = Field("order_created", description="Channel event that starts the workflow")  # type: ignore[valid-type]
    connection_id: Optional[str] = Field(None, description="Only events from this connection (empty = all)")


class ChannelStockParams(_Params):
    connection_id: str
    sku: str
    quantity: int = Field(ge=0)


class ChannelPriceParams(_Params):
    connection_id: str
    sku: str
    price: float = Field(gt=0)
    currency: Optional[str] = None
    estimated_cost_eur: float = Field(0, ge=0, description="Expected margin given up; counted against the budget")


class BroadcastStockParams(_Params):
    sku: str
    quantity: float = Field(ge=0)
    source_connection: Optional[str] = Field(None, description="Connection the change came from (not written back)")
    buffer: int = Field(0, ge=0, description="Units held back on every channel against overselling")


class PoLineParams(_Params):
    sku: str
    quantity: float = Field(gt=0)
    unit_price: Optional[float] = Field(None, ge=0)
    supplier_sku: Optional[str] = None


class EdiPurchaseOrderParams(_Params):
    connection_id: str = Field(description="EDI supplier connection")
    lines: List[PoLineParams] = Field(min_length=1)
    po_number: Optional[str] = Field(None, max_length=22, description="Empty = PO prefix + run id")
    po_prefix: str = Field("PO-", max_length=8)
    currency: Optional[str] = None


class UnderwriteParams(_Params):
    po_id: str
    units: float = Field(gt=0)
    unit_landed_cost_eur: float = Field(gt=0)
    demand_mean_units: float = Field(gt=0)
    demand_sd_units: float = Field(ge=0)


# ---------------------------------------------------------------------------- handlers
def default_registry() -> Registry:
    reg = Registry()

    @reg.node("trigger.manual", "trigger", "Start the run by hand or from the API.", ManualTriggerParams)
    def manual(ctx: NodeContext, p: ManualTriggerParams) -> NodeResult:
        return NodeResult(output=dict(ctx.payload))

    @reg.node("trigger.sell_through_gap", "trigger",
              "Start when an insured batch sells slower than forecast.", SellThroughTriggerParams)
    def sell_through(ctx: NodeContext, p: SellThroughTriggerParams) -> NodeResult:
        return NodeResult(output={**ctx.payload, "gap_threshold": p.gap_threshold})

    @reg.node("logic.if", "logic", "Branch on a comparison.", IfParams, ports=("true", "false"))
    def if_(ctx: NodeContext, p: IfParams) -> NodeResult:
        result = bool(OPS[p.op](p.left, p.right))
        return NodeResult(output={"result": result}, port="true" if result else "false")

    @reg.node("logic.wait", "logic", "Pause the run for a number of days.", WaitParams)
    def wait(ctx: NodeContext, p: WaitParams) -> NodeResult:
        if ctx.resumed is not None:
            return NodeResult(output={"waited_days": p.days, **ctx.resumed})
        return NodeResult(status="wait", resume_at=ctx.now + timedelta(days=p.days))

    @reg.node("control.approval", "control", "Ask a person to approve before continuing.", ApprovalParams,
              ports=("approved", "rejected"))
    def approval(ctx: NodeContext, p: ApprovalParams) -> NodeResult:
        if ctx.resumed is None:
            return NodeResult(status="approval", reason=p.message)
        port = "approved" if ctx.resumed.get("approved") else "rejected"
        return NodeResult(output=dict(ctx.resumed), port=port)

    @reg.node("agent.rescue_proposal", "agent",
              "Propose a rescue action for a slow batch. Heuristic placeholder for the rescue agent.",
              RescueProposalParams)
    def rescue(ctx: NodeContext, p: RescueProposalParams) -> NodeResult:
        return NodeResult(output=_rescue_heuristic(p))

    @reg.node("action.change_price", "action", "Change the selling price of a SKU (simulated).",
              ChangePriceParams, policy=Policy(reversible=True, cost_param="estimated_cost_eur"))
    def change_price(ctx: NodeContext, p: ChangePriceParams) -> NodeResult:
        return NodeResult(output={"simulated": True, "applied": False, **p.model_dump()},
                          cost_eur=0.0 if ctx.dry_run else p.estimated_cost_eur)

    @reg.node("action.transfer_stock", "action", "Move units to another marketplace (simulated).",
              TransferStockParams,
              policy=Policy(reversible=False, needs_approval=True, cost_param="estimated_cost_eur"))
    def transfer(ctx: NodeContext, p: TransferStockParams) -> NodeResult:
        return NodeResult(output={"simulated": True, "applied": False, **p.model_dump()},
                          cost_eur=0.0 if ctx.dry_run else p.estimated_cost_eur)

    @reg.node("action.notify", "action", "Send a message (recorded only in this version).", NotifyParams)
    def notify(ctx: NodeContext, p: NotifyParams) -> NodeResult:
        return NodeResult(output={"sent": False, "recorded": True, **p.model_dump()})

    @reg.node("agent.measure_outcome", "agent",
              "Measure what the rescue achieved: unsold units vs the baseline at decision time. "
              "Writes loss avoided to the decision ledger automatically (real runs only).",
              MeasureOutcomeParams)
    def measure(ctx: NodeContext, p: MeasureOutcomeParams) -> NodeResult:
        return NodeResult(output=measure_outcome(p))

    @reg.node("action.n8n_webhook", "action",
              "Call an n8n workflow through its Webhook node and continue with its JSON response.",
              N8nWebhookParams, policy=Policy(reversible=False))
    def n8n_webhook(ctx: NodeContext, p: N8nWebhookParams) -> NodeResult:
        # Snapshot the run context: storing the live outputs dict inside this node's output would be circular.
        body = p.body if p.body is not None else {"run_id": ctx.run_id, "node_id": ctx.node_id,
                                                  "payload": copy.deepcopy(ctx.payload),
                                                  "outputs": copy.deepcopy(ctx.outputs)}
        if ctx.dry_run:
            return NodeResult(output={"simulated": True, "url": p.url, "response": p.dry_run_response})
        from app.workflows import webhooks
        return NodeResult(output=webhooks.post_json(p.url, body, idempotency_key=f"{ctx.run_id}:{ctx.node_id}",
                                                    secret_ref=p.secret_ref, timeout_s=p.timeout_s))

    # ------------------------------------------------------------------ channels (marketplaces, shops, EDI)
    # The hub is imported lazily: Temporal's workflow sandbox must never load it.
    def hub():
        from app.channels.hub import current
        return current()

    @reg.node("trigger.channel_event", "trigger",
              "Start when a channel reports an order, a stock change, low stock or an EDI document.",
              ChannelEventParams)
    def channel_event(ctx: NodeContext, p: ChannelEventParams) -> NodeResult:
        return NodeResult(output=dict(ctx.payload))

    @reg.node("action.channel_set_stock", "action", "Set the available stock of a SKU on one channel.",
              ChannelStockParams, policy=Policy(reversible=True))
    def channel_set_stock(ctx: NodeContext, p: ChannelStockParams) -> NodeResult:
        if ctx.dry_run:
            return NodeResult(output={"simulated": True, **p.model_dump()})
        return NodeResult(output=hub().set_inventory(ctx.tenant, p.connection_id, p.sku, p.quantity))

    @reg.node("action.channel_set_price", "action", "Set the price of a SKU on one channel.",
              ChannelPriceParams, policy=Policy(reversible=True, cost_param="estimated_cost_eur"))
    def channel_set_price(ctx: NodeContext, p: ChannelPriceParams) -> NodeResult:
        if ctx.dry_run:
            return NodeResult(output={"simulated": True, **p.model_dump()})
        out = hub().set_price(ctx.tenant, p.connection_id, p.sku, p.price, p.currency)
        return NodeResult(output=out, cost_eur=p.estimated_cost_eur)

    @reg.node("action.channel_broadcast_stock", "action",
              "Write one available quantity to every other channel that accepts stock updates.",
              BroadcastStockParams, policy=Policy(reversible=True))
    def channel_broadcast_stock(ctx: NodeContext, p: BroadcastStockParams) -> NodeResult:
        if ctx.dry_run:
            return NodeResult(output={"simulated": True, **p.model_dump()})
        return NodeResult(output=hub().broadcast_inventory(ctx.tenant, p.sku, p.quantity, p.source_connection, p.buffer))

    @reg.node("action.edi_purchase_order", "action",
              "Send a purchase order (EDI 850) to a supplier. Commits money, so it always needs approval.",
              EdiPurchaseOrderParams, policy=Policy(reversible=False, needs_approval=True))
    def edi_purchase_order(ctx: NodeContext, p: EdiPurchaseOrderParams) -> NodeResult:
        po = {"po_number": p.po_number or f"{p.po_prefix}{ctx.run_id}"[:22], "currency": p.currency,
              "lines": [line.model_dump(exclude_none=True) for line in p.lines]}
        if ctx.dry_run:
            return NodeResult(output={"simulated": True, **po})
        return NodeResult(output=hub().send_purchase_order(ctx.tenant, p.connection_id, po))

    @reg.node("platform.underwrite", "platform", "Price overstock cover for a PO. Platform-only.",
              UnderwriteParams, policy=Policy(locked=True))
    def underwrite(ctx: NodeContext, p: UnderwriteParams) -> NodeResult:
        from app.cover.quote import Demand, quote  # lazy: scipy must stay out of Temporal's workflow sandbox
        result = quote(p.units, p.unit_landed_cost_eur, Demand(p.demand_mean_units, p.demand_sd_units))
        return NodeResult(output={"po_id": p.po_id, **result})

    return reg


def measure_outcome(p: MeasureOutcomeParams) -> Dict[str, Any]:
    """Loss avoided = (baseline unsold units - actual unsold units) x landed cost x (1 - recovery rate).

    The baseline is what the rescue proposal expected to stay unsold at decision time, so the figure is
    "what the action changed". It can be negative when the batch did worse than the baseline.
    """
    avoided_units = p.baseline_units_at_risk - p.units_unsold
    loss_avoided = round(avoided_units * p.unit_landed_cost_eur * (1 - p.recovery_rate), 2)
    outcomes = [{"metric": "loss_avoided_eur", "value": loss_avoided, "sku": p.sku}]
    if p.units_sold is not None:
        outcomes.append({"metric": "units_sold", "value": p.units_sold, "sku": p.sku})
    return {
        "sku": p.sku, "baseline_units_at_risk": p.baseline_units_at_risk, "units_unsold": p.units_unsold,
        "avoided_units": avoided_units, "loss_avoided_eur": loss_avoided, "method": "baseline_vs_actual_v1",
        "outcomes": outcomes,
    }


def _rescue_heuristic(p: RescueProposalParams) -> Dict[str, Any]:
    """Rule of thumb until the learned rescue agent exists: discount for mild gaps, transfer for deep ones."""
    excess_share = min(1.0, max(0.0, -p.sell_through_gap))
    units_at_risk = int(round(p.units_on_hand * excess_share))
    exposure = round(units_at_risk * p.unit_landed_cost_eur, 2)
    action: Optional[str]
    if units_at_risk == 0:
        action, pct, cost = None, 0.0, 0.0
    elif excess_share <= 0.3 and p.days_to_trigger >= 45:
        action, pct = "change_price", -10.0
        cost = round(units_at_risk * p.unit_landed_cost_eur * 0.10, 2)
    else:
        action, pct = "transfer_stock", 0.0
        cost = round(units_at_risk * 1.5, 2)  # placeholder handling cost per unit
    return {
        "sku": p.sku,
        "marketplace": p.marketplace,
        "units_at_risk": units_at_risk,
        "exposure_eur": exposure,
        "action": action,
        "pct_change": pct,
        "estimated_cost_eur": cost,
        "method": "heuristic_v0",
    }
