"""Marketplace connectors on free official seller APIs: Amazon SP-API (private developer), eBay Sell APIs and
the Mirakl seller API (one connector for every Mirakl-run marketplace, each with its own seller key)."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.channels.base import ChannelError, ConfigField, Connector, ConnectorType, iso, num

AMAZON_HOSTS = {"na": "https://sellingpartnerapi-na.amazon.com", "eu": "https://sellingpartnerapi-eu.amazon.com",
                "fe": "https://sellingpartnerapi-fe.amazon.com"}
AMAZON_TOKEN_URL = "https://api.amazon.com/auth/o2/token"
EBAY_HOSTS = {"production": "https://api.ebay.com", "sandbox": "https://api.sandbox.ebay.com"}


class _OAuthToken:
    """Caches a short-lived access token obtained from a refresh token."""

    def __init__(self) -> None:
        self.value: Optional[str] = None
        self.expires_at = 0.0

    def valid(self) -> bool:
        return bool(self.value) and time.monotonic() < self.expires_at - 60


# ---------------------------------------------------------------------------------------------- Amazon SP-API
class AmazonConnector(Connector):
    """Read-only for now: orders and FBA inventory. Writes (prices via the Listings API) need a live check."""

    type_name = "amazon"

    def __init__(self, config: Dict[str, Any], client=None) -> None:
        super().__init__(config, client)
        self._token = _OAuthToken()

    @property
    def base(self) -> str:
        region = str(self.config.get("region") or "eu").lower()
        if region not in AMAZON_HOSTS:
            raise ChannelError("amazon: region must be na, eu or fe")
        return AMAZON_HOSTS[region]

    @property
    def marketplaces(self) -> List[str]:
        return [m.strip() for m in str(self.config["marketplace_ids"]).split(",") if m.strip()]

    def _headers(self) -> Dict[str, str]:
        if not self._token.valid():
            resp = self._request("POST", AMAZON_TOKEN_URL, data={
                "grant_type": "refresh_token", "refresh_token": self.config["refresh_token"],
                "client_id": self.config["client_id"], "client_secret": self.config["client_secret"]})
            data = resp.json()
            self._token.value = data["access_token"]
            self._token.expires_at = time.monotonic() + float(data.get("expires_in", 3600))
        return {"x-amz-access-token": self._token.value or "", "Accept": "application/json"}

    def test(self) -> Dict[str, Any]:
        data = self._request("GET", f"{self.base}/sellers/v1/marketplaceParticipations", headers=self._headers()).json()
        names = [p.get("marketplace", {}).get("name") for p in data.get("payload", [])]
        return {"account": "Amazon seller", "marketplaces": [n for n in names if n]}

    def fetch_orders(self, since: Optional[datetime]) -> List[Dict[str, Any]]:
        latest_allowed = datetime.now(timezone.utc) - timedelta(minutes=2)  # API requires >= 2 minutes ago
        since = min(since or latest_allowed - timedelta(days=30), latest_allowed)
        params: Dict[str, Any] = {"MarketplaceIds": ",".join(self.marketplaces), "LastUpdatedAfter": iso(since)}
        out: List[Dict[str, Any]] = []
        while True:
            payload = self._request("GET", f"{self.base}/orders/v0/orders", headers=self._headers(),
                                    params=params).json().get("payload", {})
            for o in payload.get("Orders", []):
                total = o.get("OrderTotal") or {}
                out.append({"external_id": o["AmazonOrderId"], "status": o.get("OrderStatus"),
                            "created_at": o.get("PurchaseDate"), "updated_at": o.get("LastUpdateDate"),
                            "currency": total.get("CurrencyCode"), "total": num(total.get("Amount")),
                            "country": None, "lines": []})  # order items need a separate, rate-limited call
            token = payload.get("NextToken")
            if not token:
                return out
            params = {"MarketplaceIds": ",".join(self.marketplaces), "NextToken": token}

    def fetch_inventory(self) -> List[Dict[str, Any]]:
        mp = self.marketplaces[0]
        params: Dict[str, Any] = {"details": "true", "granularityType": "Marketplace", "granularityId": mp,
                                  "marketplaceIds": mp}
        out: List[Dict[str, Any]] = []
        while True:
            data = self._request("GET", f"{self.base}/fba/inventory/v1/summaries", headers=self._headers(),
                                 params=params).json()
            for s in data.get("payload", {}).get("inventorySummaries", []):
                details = s.get("inventoryDetails") or {}
                qty = details.get("fulfillableQuantity", s.get("totalQuantity", 0))
                out.append({"sku": s["sellerSku"], "quantity": qty or 0})
            token = (data.get("pagination") or {}).get("nextToken")
            if not token:
                return out
            params = {**params, "nextToken": token}


AMAZON = ConnectorType(
    name="amazon", label="Amazon (SP-API, all regions)", kind="marketplace", factory=AmazonConnector,
    capabilities=("orders", "inventory"),
    fields=[
        ConfigField("region", "Region", default="eu", help="na, eu or fe"),
        ConfigField("marketplace_ids", "Marketplace ids", help="Comma separated, e.g. A1PA6795UKMFR9 for amazon.de"),
        ConfigField("client_id", "LWA client id", help="From your private SP-API app (free for your own business)"),
        ConfigField("client_secret", "LWA client secret", secret=True),
        ConfigField("refresh_token", "Refresh token", secret=True, help="From self-authorizing your app"),
    ],
    docs="https://developer-docs.amazon.com/sp-api/",
    notes="Read-only: orders (without items) and FBA inventory. Price and listing updates come after a live check.",
)


# ---------------------------------------------------------------------------------------------- eBay
class EbayConnector(Connector):
    type_name = "ebay"

    def __init__(self, config: Dict[str, Any], client=None) -> None:
        super().__init__(config, client)
        self._token = _OAuthToken()

    @property
    def base(self) -> str:
        return EBAY_HOSTS.get(str(self.config.get("environment") or "production"), EBAY_HOSTS["production"])

    def _headers(self) -> Dict[str, str]:
        if not self._token.valid():
            resp = self._request("POST", f"{self.base}/identity/v1/oauth2/token",
                                 auth=(self.config["client_id"], self.config["client_secret"]),
                                 data={"grant_type": "refresh_token", "refresh_token": self.config["refresh_token"]})
            data = resp.json()
            self._token.value = data["access_token"]
            self._token.expires_at = time.monotonic() + float(data.get("expires_in", 7200))
        return {"Authorization": f"Bearer {self._token.value}", "Accept": "application/json",
                "Content-Type": "application/json",
                "X-EBAY-C-MARKETPLACE-ID": self.config.get("marketplace_id") or "EBAY_DE"}

    def _pages(self, url: str, params: Dict[str, Any], key: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        next_url: Optional[str] = url
        first = True
        while next_url:
            data = self._request("GET", next_url, headers=self._headers(), params=params if first else None).json()
            out.extend(data.get(key, []))
            next_url, first = data.get("next"), False
        return out

    def test(self) -> Dict[str, Any]:
        self._request("GET", f"{self.base}/sell/inventory/v1/inventory_item", headers=self._headers(), params={"limit": 1})
        return {"account": f"eBay {self.config.get('marketplace_id') or 'EBAY_DE'}"}

    def fetch_orders(self, since: Optional[datetime]) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"limit": 200}
        if since:
            params["filter"] = f"lastmodifieddate:[{iso(since)}..]"
        out = []
        for o in self._pages(f"{self.base}/sell/fulfillment/v1/order", params, "orders"):
            total = (o.get("pricingSummary") or {}).get("total") or {}
            ship_to = ((o.get("fulfillmentStartInstructions") or [{}])[0].get("shippingStep") or {}).get("shipTo") or {}
            out.append({
                "external_id": o["orderId"], "status": o.get("orderFulfillmentStatus"),
                "created_at": o.get("creationDate"), "updated_at": o.get("lastModifiedDate"),
                "currency": total.get("currency"), "total": num(total.get("value")),
                "country": (ship_to.get("contactAddress") or {}).get("countryCode"),
                "lines": [{"sku": li.get("sku"), "quantity": li.get("quantity", 0),
                           "price": num((li.get("lineItemCost") or {}).get("value"))} for li in o.get("lineItems", [])],
            })
        return out

    def fetch_inventory(self) -> List[Dict[str, Any]]:
        items = self._pages(f"{self.base}/sell/inventory/v1/inventory_item", {"limit": 100}, "inventoryItems")
        return [{"sku": i["sku"], "quantity": ((i.get("availability") or {}).get("shipToLocationAvailability") or {})
                 .get("quantity", 0)} for i in items if i.get("sku")]

    def _bulk(self, request: Dict[str, Any]) -> Dict[str, Any]:
        data = self._request("POST", f"{self.base}/sell/inventory/v1/bulk_update_price_quantity", headers=self._headers(),
                             json={"requests": [request]}).json()
        response = (data.get("responses") or [{}])[0]
        if response.get("statusCode", 200) >= 400:
            raise ChannelError(f"ebay: update rejected: {response.get('errors')}")
        return response

    def set_inventory(self, sku: str, quantity: int) -> Dict[str, Any]:
        self._bulk({"sku": sku, "shipToLocationAvailability": {"quantity": int(quantity)}})
        return {}

    def set_price(self, sku: str, price: float, currency: Optional[str] = None) -> Dict[str, Any]:
        offers = self._request("GET", f"{self.base}/sell/inventory/v1/offer", headers=self._headers(),
                               params={"sku": sku}).json().get("offers", [])
        if not offers:
            raise ChannelError(f"ebay: no offer for SKU {sku}")
        offer = offers[0]
        cur = currency or (offer.get("pricingSummary", {}).get("price") or {}).get("currency") or "EUR"
        self._bulk({"sku": sku, "offers": [{"offerId": offer["offerId"],
                                            "price": {"value": f"{price:.2f}", "currency": cur}}]})
        return {"offer_id": offer["offerId"]}


EBAY = ConnectorType(
    name="ebay", label="eBay (all sites)", kind="marketplace", factory=EbayConnector,
    capabilities=("orders", "inventory", "inventory_write", "price_write"),
    fields=[
        ConfigField("environment", "Environment", default="production", help="production or sandbox"),
        ConfigField("marketplace_id", "eBay site", default="EBAY_DE", help="e.g. EBAY_DE, EBAY_GB, EBAY_US"),
        ConfigField("client_id", "App id (client id)", help="Free eBay Developers Program keys"),
        ConfigField("client_secret", "Cert id (client secret)", secret=True),
        ConfigField("refresh_token", "User refresh token", secret=True),
    ],
    docs="https://developer.ebay.com/api-docs/sell/static/sell-landing.html",
    notes="Uses Sell Fulfillment and Sell Inventory APIs; listings must be inventory-API managed.",
)


# ---------------------------------------------------------------------------------------------- Mirakl
class MiraklConnector(Connector):
    """Seller API shared by every Mirakl-run marketplace (each gives the seller a free API key)."""

    type_name = "mirakl"

    @property
    def base(self) -> str:
        return f"{str(self.config['base_url']).rstrip('/')}/api"

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": self.config["api_key"], "Accept": "application/json"}

    def _params(self, extra: Dict[str, Any]) -> Dict[str, Any]:
        shop = self.config.get("shop_id")
        return {**extra, **({"shop_id": shop} if shop else {})}

    def _pages(self, path: str, params: Dict[str, Any], key: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        offset = 0
        while True:
            data = self._request("GET", f"{self.base}{path}", headers=self._headers(),
                                 params=self._params({**params, "max": 100, "offset": offset})).json()
            batch = data.get(key, [])
            out.extend(batch)
            offset += len(batch)
            if not batch or offset >= int(data.get("total_count", offset)):
                return out

    def test(self) -> Dict[str, Any]:
        data = self._request("GET", f"{self.base}/account", headers=self._headers(), params=self._params({})).json()
        return {"account": data.get("shop_name") or data.get("shop_id")}

    def fetch_orders(self, since: Optional[datetime]) -> List[Dict[str, Any]]:
        params = {"start_update_date": iso(since)} if since else {}
        out = []
        for o in self._pages("/orders", params, "orders"):
            address = ((o.get("customer") or {}).get("shipping_address") or {})
            out.append({
                "external_id": o["order_id"], "status": o.get("order_state"), "created_at": o.get("created_date"),
                "updated_at": o.get("last_updated_date"), "currency": o.get("currency_iso_code"),
                "total": num(o.get("total_price")), "country": address.get("country_iso_code") or address.get("country"),
                "lines": [{"sku": li.get("offer_sku"), "quantity": li.get("quantity", 0), "price": num(li.get("price"))}
                          for li in o.get("order_lines", [])],
            })
        return out

    def fetch_inventory(self) -> List[Dict[str, Any]]:
        return [{"sku": o["shop_sku"], "quantity": o.get("quantity", 0)} for o in self._pages("/offers", {}, "offers")
                if o.get("shop_sku")]

    def _offer_update(self, offer: Dict[str, Any]) -> Dict[str, Any]:
        data = self._request("POST", f"{self.base}/offers", headers=self._headers(), params=self._params({}),
                             json={"offers": [{**offer, "update_delete": "update"}]}).json()
        return {"import_id": data.get("import_id")}

    def set_inventory(self, sku: str, quantity: int) -> Dict[str, Any]:
        return self._offer_update({"shop_sku": sku, "quantity": int(quantity)})

    def set_price(self, sku: str, price: float, currency: Optional[str] = None) -> Dict[str, Any]:
        return self._offer_update({"shop_sku": sku, "price": round(float(price), 2)})


MIRAKL = ConnectorType(
    name="mirakl", label="Mirakl marketplace (one per marketplace)", kind="marketplace", factory=MiraklConnector,
    capabilities=("orders", "inventory", "inventory_write", "price_write"),
    fields=[
        ConfigField("base_url", "Marketplace API URL", help="The Mirakl URL your marketplace gives sellers"),
        ConfigField("api_key", "Seller API key", secret=True, help="Free, from the marketplace's seller back office"),
        ConfigField("shop_id", "Shop id", required=False, help="Only if your key covers several shops"),
    ],
    docs="https://developer.mirakl.com/content/product/mmp/rest/seller/openapi3",
    notes="Offer updates are asynchronous imports; some marketplaces require more offer fields, so check the "
          "import report the first time.",
)
