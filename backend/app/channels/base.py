"""Connector SDK for sales and supply channels.

Every channel (marketplace, shop, supply partner) is a connector type with a config schema and a class
implementing a small common interface. Connectors talk to the channel's own official API with the
trader's own credentials, so no paid aggregator is involved. They map channel data to one canonical shape:

    order     = {"external_id", "status", "created_at", "updated_at", "currency", "total",
                 "country", "lines": [{"sku", "quantity", "price"}]}
    inventory = {"sku", "quantity"}

The HTTP client is injected so connectors are testable without network access.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

KINDS = ("marketplace", "shop", "supply")
DEFAULT_TIMEOUT = 20.0


class ChannelError(RuntimeError):
    """A channel API call failed (bad credentials, rejected request, network)."""


class NotSupported(ChannelError):
    """The channel (or this connector version) does not offer the operation."""


@dataclass(frozen=True)
class ConfigField:
    name: str
    label: str
    secret: bool = False
    required: bool = True
    default: Any = None
    help: str = ""

    def spec(self) -> Dict[str, Any]:
        return {"name": self.name, "label": self.label, "secret": self.secret, "required": self.required,
                "default": self.default, "help": self.help}


@dataclass
class ConnectorType:
    name: str
    label: str
    kind: str
    fields: List[ConfigField]
    factory: Callable[..., "Connector"]
    capabilities: Tuple[str, ...] = ("orders", "inventory")
    docs: str = ""
    notes: str = ""

    def spec(self) -> Dict[str, Any]:
        return {"name": self.name, "label": self.label, "kind": self.kind, "capabilities": list(self.capabilities),
                "fields": [f.spec() for f in self.fields], "docs": self.docs, "notes": self.notes}

    def validate(self, config: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        known = {f.name for f in self.fields}
        unknown = sorted(set(config) - known)
        if unknown:
            raise ValueError(f"unknown settings for {self.name}: {unknown}")
        for f in self.fields:
            value = config.get(f.name, f.default)
            if isinstance(value, str):
                value = value.strip()
            if f.required and value in (None, ""):
                raise ValueError(f"'{f.label}' is required")
            if value not in (None, ""):
                out[f.name] = value
        return out

    def split(self, config: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        secret_names = {f.name for f in self.fields if f.secret}
        public = {k: v for k, v in config.items() if k not in secret_names}
        secrets = {k: v for k, v in config.items() if k in secret_names}
        return public, secrets


class Connector:
    """Base class. Subclasses implement what their channel supports; the rest raise NotSupported."""

    type_name = "base"

    def __init__(self, config: Dict[str, Any], client: Optional[httpx.Client] = None) -> None:
        self.config = config
        self.client = client or httpx.Client(timeout=DEFAULT_TIMEOUT, follow_redirects=False)

    # -- operations --------------------------------------------------------------------------
    def test(self) -> Dict[str, Any]:
        raise NotSupported(f"{self.type_name}: connection test not available")

    def fetch_orders(self, since: Optional[datetime]) -> List[Dict[str, Any]]:
        raise NotSupported(f"{self.type_name}: orders not available")

    def fetch_inventory(self) -> List[Dict[str, Any]]:
        raise NotSupported(f"{self.type_name}: inventory not available")

    def set_inventory(self, sku: str, quantity: int) -> Dict[str, Any]:
        raise NotSupported(f"{self.type_name}: inventory updates not available")

    def set_price(self, sku: str, price: float, currency: Optional[str] = None) -> Dict[str, Any]:
        raise NotSupported(f"{self.type_name}: price updates not available")

    def parse_webhook(self, headers: Dict[str, str], body: bytes) -> List[Dict[str, Any]]:
        """Verify the signature and return canonical events: {"type", "order"?, "sku"?, "quantity"?}."""
        raise NotSupported(f"{self.type_name}: webhooks not available")

    # -- helpers -----------------------------------------------------------------------------
    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            resp = self.client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise ChannelError(f"{self.type_name}: cannot reach the channel ({type(exc).__name__})") from exc
        if resp.status_code in (401, 403):
            raise ChannelError(f"{self.type_name}: the channel rejected the credentials (HTTP {resp.status_code})")
        if resp.status_code == 429:
            raise ChannelError(f"{self.type_name}: rate limited by the channel; try again later")
        if resp.status_code >= 400:
            raise ChannelError(f"{self.type_name}: HTTP {resp.status_code}: {resp.text[:300]}")
        return resp


def iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
