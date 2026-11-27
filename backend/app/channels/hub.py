"""Channel Hub: connections, sync, webhooks, cross-channel actions and events that start workflows.

Events (stored and fanned out to workflows whose trigger is `trigger.channel_event` with a matching
`event` param): order_created, order_updated, inventory_changed, stock_low.

Rules that keep automations safe:
- The first sync of a connection imports history silently (no events), so connecting a shop does not
  fire hundreds of "order_created" workflows.
- When the hub writes stock to a channel it stores the new quantity at once, so the next sync does not see
  its own write as a change (no ping-pong between channels in stock-sync automations).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

import httpx

from app.channels.base import ChannelError, Connector, ConnectorType, NotSupported
from app.channels.store import ChannelStore
from app.channels.vault import Vault

log = logging.getLogger("sop.channels")
EVENTS = ("order_created", "order_updated", "inventory_changed", "stock_low")
INITIAL_LOOKBACK_DAYS = 30
_CURRENT: Dict[str, "ChannelHub"] = {}


def set_current(hub: Optional["ChannelHub"]) -> None:
    """Make the hub available to workflow node handlers (they have no direct service access)."""
    if hub is None:
        _CURRENT.pop("hub", None)
    else:
        _CURRENT["hub"] = hub


def current() -> "ChannelHub":
    hub = _CURRENT.get("hub")
    if hub is None:
        raise ChannelError("channel hub is not configured in this process")
    return hub


def _dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    d = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


class ChannelHub:
    def __init__(self, store: ChannelStore, vault: Vault, types: Dict[str, ConnectorType],
                 workflows: Any = None, runner: Any = None,
                 client_factory: Optional[Callable[[str], httpx.Client]] = None) -> None:
        self.store, self.vault, self.types = store, vault, types
        self.workflows, self.runner = workflows, runner
        self.client_factory = client_factory  # tests inject mock transports per connector type

    # ------------------------------------------------------------------ connections
    def catalogue(self) -> List[Dict[str, Any]]:
        return [t.spec() for t in self.types.values()]

    def add(self, tenant: str, type_name: str, name: str, config: Dict[str, Any], sync_minutes: int = 15,
            low_stock: Optional[int] = None) -> Dict[str, Any]:
        ctype = self._type(type_name)
        clean = ctype.validate(config)
        public, secrets = ctype.split(clean)
        return self.store.add_connection(tenant, type_name, name.strip() or ctype.label, public,
                                         self.vault.seal(secrets) if secrets else None, sync_minutes, low_stock)

    def update_secrets(self, tenant: str, cid: str, secrets: Dict[str, Any]) -> Dict[str, Any]:
        conn = self.store.get_connection(tenant, cid)
        ctype = self._type(conn["type"])
        merged = {**self.vault.open(self.store.secrets_token(tenant, cid)), **secrets}
        clean = ctype.validate({**conn["config"], **merged})
        return self.store.update_connection(tenant, cid, secrets=self.vault.seal(ctype.split(clean)[1]))

    def connector(self, tenant: str, cid: str) -> Connector:
        conn = self.store.get_connection(tenant, cid, with_secrets=True)
        ctype = self._type(conn["type"])
        config = {**conn["config"], **self.vault.open(conn.get("secrets"))}
        # Private working folder (EDI file drop); never a user-supplied path.
        config["_data_dir"] = str(self.store.path.parent / "data" / tenant / cid)
        client = self.client_factory(conn["type"]) if self.client_factory else None
        return ctype.factory(config, client)

    def test(self, tenant: str, cid: str) -> Dict[str, Any]:
        try:
            result = self.connector(tenant, cid).test()
            self.store.update_connection(tenant, cid, last_error=None)
            return {"ok": True, **result}
        except ChannelError as exc:
            self.store.update_connection(tenant, cid, last_error=str(exc))
            return {"ok": False, "error": str(exc)}

    # ------------------------------------------------------------------ sync
    def sync(self, tenant: str, cid: str, now: Optional[datetime] = None) -> Dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        conn = self.store.get_connection(tenant, cid)
        ctype = self._type(conn["type"])
        cursor = conn.get("cursor") or {}
        initial = not cursor.get("synced")
        summary: Dict[str, Any] = {"connection_id": cid, "initial": initial, "orders_created": 0, "orders_updated": 0,
                                   "inventory_items": 0, "inventory_changed": 0, "events": 0, "errors": []}
        connector = self.connector(tenant, cid)
        new_cursor = dict(cursor)

        if "orders" in ctype.capabilities:
            since = _dt(cursor.get("orders_since")) or now - timedelta(days=INITIAL_LOOKBACK_DAYS)
            try:
                latest = since
                for order in connector.fetch_orders(since):
                    outcome = self.store.upsert_order(tenant, cid, order)
                    if outcome == "created":
                        summary["orders_created"] += 1
                    elif outcome == "updated":
                        summary["orders_updated"] += 1
                    if outcome != "unchanged" and not initial:
                        summary["events"] += self._emit(tenant, cid, f"order_{outcome}", {"order": order})
                    seen = _dt(order.get("updated_at")) or _dt(order.get("created_at"))
                    if seen and seen > latest:
                        latest = seen
                # Re-read a small overlap next time so late-arriving updates are not missed.
                new_cursor["orders_since"] = (min(latest, now) - timedelta(minutes=5)).isoformat()
            except NotSupported:
                pass
            except ChannelError as exc:
                summary["errors"].append(str(exc))

        if "inventory" in ctype.capabilities:
            try:
                for item in connector.fetch_inventory():
                    summary["inventory_items"] += 1
                    summary["events"] += self._apply_inventory(tenant, conn, item["sku"], float(item["quantity"]),
                                                               emit=not initial, counter=summary)
            except NotSupported:
                pass
            except ChannelError as exc:
                summary["errors"].append(str(exc))

        if "edi" in ctype.capabilities:
            summary["documents"] = 0
            try:
                for doc in connector.poll_inbound():  # partner documents are always new: emit even on first sync
                    summary["documents"] += 1
                    summary["events"] += self._emit(tenant, cid, doc["event"], {"document": doc})
            except ChannelError as exc:
                summary["errors"].append(str(exc))

        new_cursor["synced"] = True if not summary["errors"] or cursor.get("synced") else cursor.get("synced", False)
        self.store.update_connection(tenant, cid, cursor=new_cursor, last_sync_at=now.isoformat(),
                                     last_error="; ".join(summary["errors"]) or None)
        return summary

    def sync_due(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Scheduler hook: sync every enabled connection whose interval has passed."""
        now = now or datetime.now(timezone.utc)
        results = []
        for conn in self.store.list_connections():
            if not conn["enabled"]:
                continue
            last = _dt(conn.get("last_sync_at"))
            if last and now - last < timedelta(minutes=conn["sync_minutes"] or 15):
                continue
            try:
                results.append(self.sync(conn["tenant"], conn["id"], now))
            except Exception:  # one broken connection must not stop the others
                log.exception("channel sync failed for %s", conn["id"])
        return results

    # ------------------------------------------------------------------ webhooks
    def handle_webhook(self, tenant: str, cid: str, headers: Dict[str, str], body: bytes) -> Dict[str, Any]:
        conn = self.store.get_connection(tenant, cid)
        if not conn["enabled"]:
            raise ChannelError("connection is disabled")
        events = self.connector(tenant, cid).parse_webhook({k.lower(): v for k, v in headers.items()}, body)
        fired = 0
        for ev in events:
            if ev.get("order"):
                outcome = self.store.upsert_order(tenant, cid, ev["order"])
                if outcome != "unchanged":
                    fired += self._emit(tenant, cid, f"order_{outcome}", {"order": ev["order"]})
            if ev.get("sku") is not None and ev.get("quantity") is not None:
                fired += self._apply_inventory(tenant, conn, ev["sku"], float(ev["quantity"]), emit=True)
        return {"received": len(events), "events": fired}

    # ------------------------------------------------------------------ actions
    def set_inventory(self, tenant: str, cid: str, sku: str, quantity: int) -> Dict[str, Any]:
        result = self.connector(tenant, cid).set_inventory(sku, int(quantity))
        self.store.set_inventory(tenant, cid, sku, float(quantity))  # our own write is not a "change" later
        self.store.add_event(tenant, cid, "inventory_set", {"sku": sku, "quantity": int(quantity)})
        return {"connection_id": cid, "sku": sku, "quantity": int(quantity), **(result or {})}

    def set_price(self, tenant: str, cid: str, sku: str, price: float, currency: Optional[str] = None) -> Dict[str, Any]:
        result = self.connector(tenant, cid).set_price(sku, float(price), currency)
        self.store.add_event(tenant, cid, "price_set", {"sku": sku, "price": float(price), "currency": currency})
        return {"connection_id": cid, "sku": sku, "price": float(price), **(result or {})}

    def send_purchase_order(self, tenant: str, cid: str, po: Dict[str, Any]) -> Dict[str, Any]:
        connector = self.connector(tenant, cid)
        if not hasattr(connector, "send_purchase_order"):
            raise NotSupported(f"{connector.type_name}: purchase orders are not available")
        result = connector.send_purchase_order(po)
        self.store.add_event(tenant, cid, "edi_po_sent", {"po_number": po.get("po_number"), **result})
        return {"connection_id": cid, "po_number": po.get("po_number"), **result}

    def broadcast_inventory(self, tenant: str, sku: str, quantity: float, source_connection: Optional[str] = None,
                            buffer: int = 0) -> Dict[str, Any]:
        """Write one available quantity to every other enabled channel that accepts stock updates."""
        target = max(int(quantity) - int(buffer), 0)
        results, errors = [], []
        for conn in self.store.list_connections(tenant):
            if not conn["enabled"] or conn["id"] == source_connection:
                continue
            if "inventory_write" not in self._type(conn["type"]).capabilities:
                continue
            try:
                results.append(self.set_inventory(tenant, conn["id"], sku, target))
            except ChannelError as exc:
                errors.append({"connection_id": conn["id"], "error": str(exc)})
        return {"sku": sku, "quantity": target, "updated": results, "errors": errors}

    # ------------------------------------------------------------------ internals
    def _apply_inventory(self, tenant: str, conn: Dict[str, Any], sku: str, quantity: float, emit: bool,
                         counter: Optional[Dict[str, Any]] = None) -> int:
        cid = conn["id"]
        previous = self.store.set_inventory(tenant, cid, sku, quantity)
        fired = 0
        if previous is not None and previous != quantity:
            if counter is not None:
                counter["inventory_changed"] += 1
            if emit:
                fired += self._emit(tenant, cid, "inventory_changed",
                                    {"sku": sku, "quantity": quantity, "previous_quantity": previous})
        low = conn.get("low_stock")
        crossed = low is not None and quantity <= low and (previous is None or previous > low)
        if emit and crossed:
            fired += self._emit(tenant, cid, "stock_low",
                                {"sku": sku, "quantity": quantity, "previous_quantity": previous, "threshold": low})
        return fired

    def _emit(self, tenant: str, cid: str, type_: str, payload: Dict[str, Any]) -> int:
        conn = self.store.get_connection(tenant, cid)
        full = {"event": type_, "connection_id": cid, "channel_type": conn["type"], "channel_name": conn["name"],
                **payload}
        self.store.add_event(tenant, cid, type_, full)
        return 1 + self._start_workflows(tenant, cid, type_, full)

    def _start_workflows(self, tenant: str, cid: str, type_: str, payload: Dict[str, Any]) -> int:
        if self.workflows is None or self.runner is None:
            return 0
        started = 0
        try:
            summaries = self.workflows.list(tenant)
        except ValueError:
            return 0
        for summary in summaries:
            wf = self.workflows.get(tenant, summary["id"])
            trigger = wf.node(wf.trigger)
            if trigger.type != "trigger.channel_event":
                continue
            if trigger.params.get("event", "order_created") != type_:
                continue
            only = trigger.params.get("connection_id")
            if only and only != cid:
                continue
            try:
                self.runner.start(tenant, wf.id, payload, f"channel:{cid}")
                started += 1
            except Exception:  # a broken playbook must not break syncing
                log.exception("starting workflow %s for channel event %s failed", wf.id, type_)
        return started

    def _type(self, name: str) -> ConnectorType:
        if name not in self.types:
            raise KeyError(f"unknown connector type {name}")
        return self.types[name]
