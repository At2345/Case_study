"""Channel Hub API: connections (admin), sync, orders, inventory, events, cross-channel actions, webhooks."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.bi.tenancy import DEFAULT_TENANT
from app.channels.base import ChannelError, NotSupported
from app.channels.hub import ChannelHub
from app.channels.vault import VaultError
from app.models.enums import Role


class ConnectionRequest(BaseModel):
    type: str = Field(..., max_length=40)
    name: str = Field("", max_length=80)
    config: Dict[str, Any] = Field(default_factory=dict)
    sync_minutes: int = Field(15, ge=1, le=1440)
    low_stock: Optional[int] = Field(None, ge=0)


class ConnectionUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=80)
    enabled: Optional[bool] = None
    sync_minutes: Optional[int] = Field(None, ge=1, le=1440)
    low_stock: Optional[int] = Field(None, ge=0)
    secrets: Optional[Dict[str, Any]] = None


class InventoryRequest(BaseModel):
    sku: str = Field(..., min_length=1, max_length=80)
    quantity: int = Field(..., ge=0, le=10_000_000)


class PriceRequest(BaseModel):
    sku: str = Field(..., min_length=1, max_length=80)
    price: float = Field(..., gt=0, le=1_000_000)
    currency: Optional[str] = Field(None, max_length=3)


class PoLine(BaseModel):
    sku: str = Field(..., min_length=1, max_length=48)
    quantity: float = Field(..., gt=0, le=10_000_000)
    unit_price: Optional[float] = Field(None, ge=0)
    uom: Optional[str] = Field(None, max_length=2)
    supplier_sku: Optional[str] = Field(None, max_length=48)


class PurchaseOrderRequest(BaseModel):
    po_number: str = Field(..., min_length=1, max_length=22)
    lines: List[PoLine] = Field(..., min_length=1, max_length=500)
    currency: Optional[str] = Field(None, max_length=3)
    ship_to: Optional[Dict[str, str]] = None


class BroadcastRequest(BaseModel):
    sku: str = Field(..., min_length=1, max_length=80)
    quantity: int = Field(..., ge=0, le=10_000_000)
    source_connection: Optional[str] = Field(None, max_length=32)
    buffer: int = Field(0, ge=0, le=1_000_000)


def register(app: FastAPI, ctx: Dict[str, Any]) -> None:
    require, all_roles = ctx["require"], ctx["all_roles"]
    write_roles, admin = (Role.PLANNER.value, Role.ADMIN.value), Role.ADMIN.value
    hub: ChannelHub = ctx["hub"]

    def tenant(user: Dict[str, str]) -> str:
        return user.get("tenant") or DEFAULT_TENANT

    def view(conn: Dict[str, Any]) -> Dict[str, Any]:
        out = {k: v for k, v in conn.items() if k not in ("secrets", "tenant")}
        ctype = hub.types.get(conn["type"])
        out["capabilities"] = list(ctype.capabilities) if ctype else []
        out["secret_fields_set"] = sorted(f.name for f in ctype.fields if f.secret) if ctype else []
        out["webhook_path"] = f"/api/v1/channels/webhooks/{conn['tenant']}/{conn['id']}"
        return out

    def call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc.args[0]) if exc.args else "Not found")
        except NotSupported as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except (ChannelError, VaultError) as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    # ------------------------------------------------------------------ catalogue and connections
    @app.get("/api/v1/channels/types")
    def channel_types(user=Depends(require(*all_roles))):
        return {"types": hub.catalogue()}

    @app.get("/api/v1/channels/connections")
    def list_connections(user=Depends(require(*all_roles))):
        return {"connections": [view(c) for c in hub.store.list_connections(tenant(user))]}

    @app.post("/api/v1/channels/connections", status_code=201)
    def add_connection(body: ConnectionRequest, user=Depends(require(admin))):
        """Credentials are encrypted in the local vault and never returned."""
        return view(call(hub.add, tenant(user), body.type, body.name, body.config, body.sync_minutes, body.low_stock))

    @app.patch("/api/v1/channels/connections/{cid}")
    def update_connection(cid: str, body: ConnectionUpdate, user=Depends(require(admin))):
        t = tenant(user)
        fields = body.model_dump(exclude_none=True, exclude={"secrets"})
        if "enabled" in fields:
            fields["enabled"] = 1 if fields["enabled"] else 0
        conn = call(hub.store.update_connection, t, cid, **fields) if fields else call(hub.store.get_connection, t, cid)
        if body.secrets:
            conn = call(hub.update_secrets, t, cid, body.secrets)
        return view(conn)

    @app.delete("/api/v1/channels/connections/{cid}")
    def delete_connection(cid: str, user=Depends(require(admin))):
        if not hub.store.delete_connection(tenant(user), cid):
            raise HTTPException(status_code=404, detail=f"connection {cid} not found")
        return {"deleted": cid}

    @app.post("/api/v1/channels/connections/{cid}/test")
    async def test_connection(cid: str, user=Depends(require(*write_roles))):
        return await run_in_threadpool(call, hub.test, tenant(user), cid)

    @app.post("/api/v1/channels/connections/{cid}/sync")
    async def sync_connection(cid: str, user=Depends(require(*write_roles))):
        return await run_in_threadpool(call, hub.sync, tenant(user), cid)

    # ------------------------------------------------------------------ data
    @app.get("/api/v1/channels/orders")
    def channel_orders(connection_id: Optional[str] = Query(None, max_length=32), limit: int = Query(100, ge=1, le=1000),
                       user=Depends(require(*all_roles))):
        return {"orders": hub.store.orders(tenant(user), connection_id, limit)}

    @app.get("/api/v1/channels/inventory")
    def channel_inventory(connection_id: Optional[str] = Query(None, max_length=32),
                          sku: Optional[str] = Query(None, max_length=80), user=Depends(require(*all_roles))):
        rows = hub.store.inventory(tenant(user), connection_id, sku)
        by_sku: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            entry = by_sku.setdefault(r["sku"], {"sku": r["sku"], "total": 0.0, "channels": {}})
            entry["channels"][r["connection_id"]] = r["quantity"]
            entry["total"] += r["quantity"]
        return {"items": rows, "by_sku": list(by_sku.values())}

    @app.get("/api/v1/channels/events")
    def channel_events(limit: int = Query(100, ge=1, le=1000), user=Depends(require(*all_roles))):
        return {"events": hub.store.events(tenant(user), limit)}

    # ------------------------------------------------------------------ actions
    @app.post("/api/v1/channels/connections/{cid}/inventory")
    async def set_inventory(cid: str, body: InventoryRequest, user=Depends(require(*write_roles))):
        return await run_in_threadpool(call, hub.set_inventory, tenant(user), cid, body.sku, body.quantity)

    @app.post("/api/v1/channels/connections/{cid}/price")
    async def set_price(cid: str, body: PriceRequest, user=Depends(require(*write_roles))):
        return await run_in_threadpool(call, hub.set_price, tenant(user), cid, body.sku, body.price, body.currency)

    @app.post("/api/v1/channels/connections/{cid}/purchase-orders", status_code=201)
    async def send_po(cid: str, body: PurchaseOrderRequest, user=Depends(require(*write_roles))):
        """Send an EDI 850 purchase order to a supplier connection (file drop)."""
        return await run_in_threadpool(call, hub.send_purchase_order, tenant(user), cid, body.model_dump(exclude_none=True))

    @app.post("/api/v1/channels/inventory/broadcast")
    async def broadcast(body: BroadcastRequest, user=Depends(require(*write_roles))):
        return await run_in_threadpool(call, hub.broadcast_inventory, tenant(user), body.sku, body.quantity,
                                       body.source_connection, body.buffer)

    # ------------------------------------------------------------------ webhooks (signed, no login)
    @app.post("/api/v1/channels/webhooks/{tenant_id}/{cid}")
    async def webhook(tenant_id: str, cid: str, request: Request):
        body = await request.body()
        if len(body) > 2_000_000:
            raise HTTPException(status_code=413, detail="payload too large")
        try:
            return await run_in_threadpool(hub.handle_webhook, tenant_id, cid, dict(request.headers), body)
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown connection")
        except NotSupported as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except ChannelError as exc:
            raise HTTPException(status_code=401, detail=str(exc))
