"""Shop connectors: Shopify (custom app, Admin REST API) and WooCommerce (REST API v3). Both are free with
the trader's own store: Shopify custom apps and WooCommerce's built-in API need no extra subscription."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.channels.base import ChannelError, ConfigField, Connector, ConnectorType, iso, num

LINK_NEXT = re.compile(r'<([^>]+)>;\s*rel="next"')


def _hmac_b64(secret: str, body: bytes) -> str:
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


def _verify(secret: Optional[str], header: Optional[str], body: bytes, channel: str) -> None:
    if not secret:
        raise ChannelError(f"{channel}: set the webhook secret on the connection to accept webhooks")
    if not header or not hmac.compare_digest(_hmac_b64(secret, body), header):
        raise ChannelError(f"{channel}: webhook signature does not match")


# ---------------------------------------------------------------------------------------------- Shopify
class ShopifyConnector(Connector):
    type_name = "shopify"

    @property
    def base(self) -> str:
        shop = str(self.config["shop_domain"]).replace("https://", "").strip("/")
        return f"https://{shop}/admin/api/{self.config.get('api_version') or '2025-01'}"

    def _headers(self) -> Dict[str, str]:
        return {"X-Shopify-Access-Token": self.config["access_token"], "Accept": "application/json"}

    def _pages(self, path: str, params: Dict[str, Any], key: str) -> List[Dict[str, Any]]:
        url: Optional[str] = f"{self.base}{path}"
        out: List[Dict[str, Any]] = []
        first = True
        while url:
            resp = self._request("GET", url, headers=self._headers(), params=params if first else None)
            out.extend(resp.json().get(key, []))
            match = LINK_NEXT.search(resp.headers.get("link", ""))
            url, first = (match.group(1) if match else None), False
        return out

    def test(self) -> Dict[str, Any]:
        shop = self._request("GET", f"{self.base}/shop.json", headers=self._headers()).json()["shop"]
        return {"account": shop.get("name"), "currency": shop.get("currency")}

    def fetch_orders(self, since: Optional[datetime]) -> List[Dict[str, Any]]:
        params = {"status": "any", "limit": 250}
        if since:
            params["updated_at_min"] = iso(since)
        return [self._order(o) for o in self._pages("/orders.json", params, "orders")]

    @staticmethod
    def _order(o: Dict[str, Any]) -> Dict[str, Any]:
        ship = o.get("shipping_address") or {}
        return {
            "external_id": str(o["id"]), "status": o.get("fulfillment_status") or o.get("financial_status") or "open",
            "created_at": o.get("created_at"), "updated_at": o.get("updated_at"), "currency": o.get("currency"),
            "total": num(o.get("total_price")), "country": ship.get("country_code"),
            "lines": [{"sku": li.get("sku"), "quantity": li.get("quantity", 0), "price": num(li.get("price"))}
                      for li in o.get("line_items", [])],
        }

    def _variants(self) -> List[Dict[str, Any]]:
        products = self._pages("/products.json", {"limit": 250, "fields": "id,variants"}, "products")
        return [v for p in products for v in p.get("variants", []) if v.get("sku")]

    def _variant(self, sku: str) -> Dict[str, Any]:
        for v in self._variants():
            if v["sku"] == sku:
                return v
        raise ChannelError(f"shopify: no variant with SKU {sku}")

    def fetch_inventory(self) -> List[Dict[str, Any]]:
        return [{"sku": v["sku"], "quantity": v.get("inventory_quantity", 0)} for v in self._variants()]

    def set_inventory(self, sku: str, quantity: int) -> Dict[str, Any]:
        location = self.config.get("location_id")
        if not location:
            raise ChannelError("shopify: set the location id on the connection to update stock")
        variant = self._variant(sku)
        self._request("POST", f"{self.base}/inventory_levels/set.json", headers=self._headers(),
                      json={"location_id": int(location), "inventory_item_id": variant["inventory_item_id"],
                            "available": int(quantity)})
        return {"variant_id": variant["id"]}

    def set_price(self, sku: str, price: float, currency: Optional[str] = None) -> Dict[str, Any]:
        variant = self._variant(sku)
        self._request("PUT", f"{self.base}/variants/{variant['id']}.json", headers=self._headers(),
                      json={"variant": {"id": variant["id"], "price": f"{price:.2f}"}})
        return {"variant_id": variant["id"]}

    def parse_webhook(self, headers: Dict[str, str], body: bytes) -> List[Dict[str, Any]]:
        _verify(self.config.get("webhook_secret"), headers.get("x-shopify-hmac-sha256"), body, "shopify")
        topic = headers.get("x-shopify-topic", "")
        data = json.loads(body or b"{}")
        if topic.startswith("orders/"):
            return [{"type": "order", "order": self._order(data)}]
        return []  # other topics are acknowledged but not mapped


SHOPIFY = ConnectorType(
    name="shopify", label="Shopify", kind="shop", factory=ShopifyConnector,
    capabilities=("orders", "inventory", "inventory_write", "price_write", "webhooks"),
    fields=[
        ConfigField("shop_domain", "Shop domain", help="your-store.myshopify.com"),
        ConfigField("access_token", "Admin API access token", secret=True,
                    help="From a custom app in your Shopify admin (free)"),
        ConfigField("location_id", "Location id for stock updates", required=False),
        ConfigField("webhook_secret", "Webhook signing secret", secret=True, required=False,
                    help="The app's API secret key; needed to accept webhooks"),
        ConfigField("api_version", "API version", required=False, default="2025-01"),
    ],
    docs="https://shopify.dev/docs/api/admin-rest",
    notes="Uses the Admin REST API, which Shopify calls legacy; custom apps can still use it.",
)


# ---------------------------------------------------------------------------------------------- WooCommerce
class WooCommerceConnector(Connector):
    type_name = "woocommerce"

    @property
    def base(self) -> str:
        return f"{str(self.config['site_url']).rstrip('/')}/wp-json/wc/v3"

    def _auth(self):
        return (self.config["consumer_key"], self.config["consumer_secret"])

    def _pages(self, path: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        out, page = [], 1
        while True:
            resp = self._request("GET", f"{self.base}{path}", auth=self._auth(), params={**params, "per_page": 100, "page": page})
            batch = resp.json()
            out.extend(batch)
            total_pages = int(resp.headers.get("x-wp-totalpages", "1") or 1)
            if page >= total_pages or not batch:
                return out
            page += 1

    def test(self) -> Dict[str, Any]:
        resp = self._request("GET", f"{self.base}/products", auth=self._auth(), params={"per_page": 1})
        return {"account": self.config["site_url"], "products": int(resp.headers.get("x-wp-total", "0") or 0)}

    def fetch_orders(self, since: Optional[datetime]) -> List[Dict[str, Any]]:
        params = {"modified_after": iso(since).replace("Z", "")} if since else {}
        return [self._order(o) for o in self._pages("/orders", params)]

    @staticmethod
    def _order(o: Dict[str, Any]) -> Dict[str, Any]:
        def gmt(v: Optional[str]) -> Optional[str]:
            return f"{v}Z" if v and not v.endswith("Z") else v
        return {
            "external_id": str(o["id"]), "status": o.get("status"),
            "created_at": gmt(o.get("date_created_gmt")), "updated_at": gmt(o.get("date_modified_gmt")),
            "currency": o.get("currency"), "total": num(o.get("total")), "country": (o.get("shipping") or {}).get("country"),
            "lines": [{"sku": li.get("sku"), "quantity": li.get("quantity", 0), "price": num(li.get("price"))}
                      for li in o.get("line_items", [])],
        }

    def fetch_inventory(self) -> List[Dict[str, Any]]:
        return [{"sku": p["sku"], "quantity": p.get("stock_quantity") or 0} for p in self._pages("/products", {})
                if p.get("sku") and p.get("manage_stock")]

    def _product_id(self, sku: str) -> int:
        found = self._request("GET", f"{self.base}/products", auth=self._auth(), params={"sku": sku}).json()
        if not found:
            raise ChannelError(f"woocommerce: no product with SKU {sku}")
        return found[0]["id"]

    def set_inventory(self, sku: str, quantity: int) -> Dict[str, Any]:
        pid = self._product_id(sku)
        self._request("PUT", f"{self.base}/products/{pid}", auth=self._auth(),
                      json={"manage_stock": True, "stock_quantity": int(quantity)})
        return {"product_id": pid}

    def set_price(self, sku: str, price: float, currency: Optional[str] = None) -> Dict[str, Any]:
        pid = self._product_id(sku)
        self._request("PUT", f"{self.base}/products/{pid}", auth=self._auth(), json={"regular_price": f"{price:.2f}"})
        return {"product_id": pid}

    def parse_webhook(self, headers: Dict[str, str], body: bytes) -> List[Dict[str, Any]]:
        _verify(self.config.get("webhook_secret"), headers.get("x-wc-webhook-signature"), body, "woocommerce")
        topic = headers.get("x-wc-webhook-topic", "")
        data = json.loads(body or b"{}")
        if topic.startswith("order."):
            return [{"type": "order", "order": self._order(data)}]
        if topic.startswith("product.") and data.get("sku") and data.get("manage_stock"):
            return [{"type": "inventory", "sku": data["sku"], "quantity": data.get("stock_quantity") or 0}]
        return []


WOOCOMMERCE = ConnectorType(
    name="woocommerce", label="WooCommerce / own website", kind="shop", factory=WooCommerceConnector,
    capabilities=("orders", "inventory", "inventory_write", "price_write", "webhooks"),
    fields=[
        ConfigField("site_url", "Site URL", help="https://shop.example.com"),
        ConfigField("consumer_key", "Consumer key", help="WooCommerce > Settings > Advanced > REST API"),
        ConfigField("consumer_secret", "Consumer secret", secret=True),
        ConfigField("webhook_secret", "Webhook secret", secret=True, required=False),
    ],
    docs="https://woocommerce.github.io/woocommerce-rest-api-docs/",
    notes="Simple products with managed stock; variations are not mapped yet.",
)
