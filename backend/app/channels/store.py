"""Canonical channel data store (SQLite): connections, orders, inventory and events, per tenant."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS connections (
    tenant TEXT NOT NULL, id TEXT NOT NULL, type TEXT NOT NULL, name TEXT NOT NULL,
    config TEXT NOT NULL, secrets TEXT, cursor TEXT, enabled INTEGER NOT NULL DEFAULT 1,
    sync_minutes INTEGER NOT NULL DEFAULT 15, low_stock INTEGER,
    created_at TEXT NOT NULL, last_sync_at TEXT, last_error TEXT,
    PRIMARY KEY (tenant, id)
);
CREATE TABLE IF NOT EXISTS orders (
    tenant TEXT NOT NULL, connection_id TEXT NOT NULL, external_id TEXT NOT NULL,
    status TEXT, created_at TEXT, updated_at TEXT, currency TEXT, total REAL, country TEXT, lines TEXT,
    seen_at TEXT NOT NULL,
    PRIMARY KEY (tenant, connection_id, external_id)
);
CREATE TABLE IF NOT EXISTS inventory (
    tenant TEXT NOT NULL, connection_id TEXT NOT NULL, sku TEXT NOT NULL, quantity REAL NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (tenant, connection_id, sku)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, tenant TEXT NOT NULL, connection_id TEXT NOT NULL,
    type TEXT NOT NULL, payload TEXT NOT NULL, at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_tenant ON events (tenant, id);
"""
PUBLIC_COLUMNS = ("id", "type", "name", "config", "enabled", "sync_minutes", "low_stock", "created_at",
                  "last_sync_at", "last_error", "cursor")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChannelStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with closing(self._connect()) as con:
            con.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        con.row_factory = sqlite3.Row
        return con

    # ------------------------------------------------------------------ connections
    def add_connection(self, tenant: str, type_name: str, name: str, config: Dict[str, Any], secrets: Optional[str],
                       sync_minutes: int = 15, low_stock: Optional[int] = None) -> Dict[str, Any]:
        cid = uuid.uuid4().hex[:12]
        with self._lock, closing(self._connect()) as con:
            con.execute("INSERT INTO connections (tenant, id, type, name, config, secrets, sync_minutes, low_stock, "
                        "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                        (tenant, cid, type_name, name, json.dumps(config), secrets, sync_minutes, low_stock, now_iso()))
        return self.get_connection(tenant, cid)

    def get_connection(self, tenant: str, cid: str, with_secrets: bool = False) -> Dict[str, Any]:
        with closing(self._connect()) as con:
            row = con.execute("SELECT * FROM connections WHERE tenant = ? AND id = ?", (tenant, cid)).fetchone()
        if row is None:
            raise KeyError(f"connection {cid} not found")
        return self._row(row, with_secrets)

    def list_connections(self, tenant: Optional[str] = None) -> List[Dict[str, Any]]:
        sql, args = "SELECT * FROM connections", []
        if tenant is not None:
            sql, args = sql + " WHERE tenant = ?", [tenant]
        with closing(self._connect()) as con:
            return [self._row(r, False) for r in con.execute(sql + " ORDER BY created_at", args)]

    def update_connection(self, tenant: str, cid: str, **fields: Any) -> Dict[str, Any]:
        allowed = {"name", "enabled", "sync_minutes", "low_stock", "cursor", "last_sync_at", "last_error",
                   "config", "secrets"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"cannot update {sorted(bad)}")
        for k in ("cursor", "config"):
            if k in fields and not isinstance(fields[k], str) and fields[k] is not None:
                fields[k] = json.dumps(fields[k])
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self._lock, closing(self._connect()) as con:
            cur = con.execute(f"UPDATE connections SET {sets} WHERE tenant = ? AND id = ?",
                              list(fields.values()) + [tenant, cid])
            if cur.rowcount == 0:
                raise KeyError(f"connection {cid} not found")
        return self.get_connection(tenant, cid)

    def delete_connection(self, tenant: str, cid: str) -> bool:
        with self._lock, closing(self._connect()) as con:
            n = con.execute("DELETE FROM connections WHERE tenant = ? AND id = ?", (tenant, cid)).rowcount
            con.execute("DELETE FROM inventory WHERE tenant = ? AND connection_id = ?", (tenant, cid))
        return n > 0

    def secrets_token(self, tenant: str, cid: str) -> Optional[str]:
        return self.get_connection(tenant, cid, with_secrets=True).get("secrets")

    @staticmethod
    def _row(row: sqlite3.Row, with_secrets: bool) -> Dict[str, Any]:
        out = {k: row[k] for k in PUBLIC_COLUMNS}
        out["config"] = json.loads(out["config"] or "{}")
        out["cursor"] = json.loads(out["cursor"]) if out["cursor"] else None
        out["enabled"] = bool(out["enabled"])
        out["tenant"] = row["tenant"]
        if with_secrets:
            out["secrets"] = row["secrets"]
        return out

    # ------------------------------------------------------------------ orders
    def upsert_order(self, tenant: str, cid: str, order: Dict[str, Any]) -> str:
        """Insert or update a canonical order. Returns "created", "updated" or "unchanged"."""
        lines = json.dumps(order.get("lines") or [], sort_keys=True)
        with self._lock, closing(self._connect()) as con:
            old = con.execute("SELECT status, updated_at, total, lines FROM orders WHERE tenant = ? AND connection_id = ? "
                              "AND external_id = ?", (tenant, cid, str(order["external_id"]))).fetchone()
            values = (order.get("status"), order.get("created_at"), order.get("updated_at"), order.get("currency"),
                      order.get("total"), order.get("country"), lines)
            if old is None:
                con.execute("INSERT INTO orders (tenant, connection_id, external_id, status, created_at, updated_at, "
                            "currency, total, country, lines, seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (tenant, cid, str(order["external_id"]), *values, now_iso()))
                return "created"
            if (old["status"], old["updated_at"], old["total"], old["lines"]) == (values[0], values[2], values[4], lines):
                return "unchanged"
            con.execute("UPDATE orders SET status=?, created_at=?, updated_at=?, currency=?, total=?, country=?, lines=? "
                        "WHERE tenant = ? AND connection_id = ? AND external_id = ?",
                        (*values, tenant, cid, str(order["external_id"])))
            return "updated"

    def orders(self, tenant: str, cid: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        sql, args = "SELECT * FROM orders WHERE tenant = ?", [tenant]
        if cid:
            sql, args = sql + " AND connection_id = ?", args + [cid]
        with closing(self._connect()) as con:
            rows = con.execute(sql + " ORDER BY COALESCE(updated_at, created_at) DESC LIMIT ?", args + [limit]).fetchall()
        return [{**dict(r), "lines": json.loads(r["lines"] or "[]")} for r in rows]

    # ------------------------------------------------------------------ inventory
    def set_inventory(self, tenant: str, cid: str, sku: str, quantity: float) -> Optional[float]:
        """Store a quantity. Returns the previous quantity (None if the SKU was new)."""
        with self._lock, closing(self._connect()) as con:
            old = con.execute("SELECT quantity FROM inventory WHERE tenant = ? AND connection_id = ? AND sku = ?",
                              (tenant, cid, sku)).fetchone()
            con.execute("INSERT INTO inventory (tenant, connection_id, sku, quantity, updated_at) VALUES (?,?,?,?,?) "
                        "ON CONFLICT (tenant, connection_id, sku) DO UPDATE SET quantity = excluded.quantity, "
                        "updated_at = excluded.updated_at", (tenant, cid, sku, float(quantity), now_iso()))
        return None if old is None else float(old["quantity"])

    def inventory(self, tenant: str, cid: Optional[str] = None, sku: Optional[str] = None) -> List[Dict[str, Any]]:
        sql, args = "SELECT * FROM inventory WHERE tenant = ?", [tenant]
        if cid:
            sql, args = sql + " AND connection_id = ?", args + [cid]
        if sku:
            sql, args = sql + " AND sku = ?", args + [sku]
        with closing(self._connect()) as con:
            return [dict(r) for r in con.execute(sql + " ORDER BY sku, connection_id", args)]

    # ------------------------------------------------------------------ events
    def add_event(self, tenant: str, cid: str, type_: str, payload: Dict[str, Any]) -> int:
        with self._lock, closing(self._connect()) as con:
            cur = con.execute("INSERT INTO events (tenant, connection_id, type, payload, at) VALUES (?,?,?,?,?)",
                              (tenant, cid, type_, json.dumps(payload, default=str), now_iso()))
            return int(cur.lastrowid)

    def events(self, tenant: str, limit: int = 100) -> List[Dict[str, Any]]:
        with closing(self._connect()) as con:
            rows = con.execute("SELECT * FROM events WHERE tenant = ? ORDER BY id DESC LIMIT ?", (tenant, limit)).fetchall()
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]
