"""Connectors against simulated channel APIs (httpx.MockTransport): requests, mapping, paging, writes, webhooks."""
import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone

import httpx
import pytest

from app.channels.base import ChannelError, NotSupported
from app.channels.connectors.marketplaces import AmazonConnector, EbayConnector, MiraklConnector
from app.channels.connectors.shops import ShopifyConnector, WooCommerceConnector

SINCE = datetime(2026, 11, 1, tzinfo=timezone.utc)


def client(handler):
    calls = []

    def wrapped(request):
        calls.append(request)
        return handler(request)
    c = httpx.Client(transport=httpx.MockTransport(wrapped))
    c.calls = calls
    return c


def sign(secret, body):
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


# ---------------------------------------------------------------- Shopify
SHOP_CFG = {"shop_domain": "acme.myshopify.com", "access_token": "shpat_x", "location_id": "77",
            "webhook_secret": "whsec"}
PRODUCTS = {"products": [{"id": 1, "variants": [{"id": 11, "sku": "MAT", "inventory_item_id": 111, "inventory_quantity": 40},
                                                {"id": 12, "sku": "", "inventory_item_id": 112}]}]}


def shopify_api(request):
    assert request.headers["X-Shopify-Access-Token"] == "shpat_x"
    path = request.url.path
    if path.endswith("/shop.json"):
        return httpx.Response(200, json={"shop": {"name": "Acme", "currency": "EUR"}})
    if path.endswith("/orders.json"):
        if "page_info" in str(request.url):
            return httpx.Response(200, json={"orders": [{"id": 2, "financial_status": "paid", "line_items": []}]})
        assert request.url.params["updated_at_min"] == "2026-11-01T00:00:00Z"
        nxt = '<https://acme.myshopify.com/admin/api/2025-01/orders.json?page_info=abc>; rel="next"'
        return httpx.Response(200, headers={"link": nxt}, json={"orders": [{
            "id": 1, "fulfillment_status": None, "financial_status": "paid", "created_at": "2026-11-02T10:00:00Z",
            "updated_at": "2026-11-02T11:00:00Z", "currency": "EUR", "total_price": "29.90",
            "shipping_address": {"country_code": "DE"}, "line_items": [{"sku": "MAT", "quantity": 2, "price": "14.95"}]}]})
    if path.endswith("/products.json"):
        return httpx.Response(200, json=PRODUCTS)
    if path.endswith("/inventory_levels/set.json"):
        return httpx.Response(200, json={"inventory_level": {}})
    if path.endswith("/variants/11.json"):
        return httpx.Response(200, json={"variant": {}})
    return httpx.Response(404)


def test_shopify_orders_inventory_and_writes():
    c = client(shopify_api)
    shop = ShopifyConnector(SHOP_CFG, c)
    assert shop.test() == {"account": "Acme", "currency": "EUR"}
    orders = shop.fetch_orders(SINCE)
    assert [o["external_id"] for o in orders] == ["1", "2"]  # followed the Link header
    assert orders[0] == {"external_id": "1", "status": "paid", "created_at": "2026-11-02T10:00:00Z",
                         "updated_at": "2026-11-02T11:00:00Z", "currency": "EUR", "total": 29.9, "country": "DE",
                         "lines": [{"sku": "MAT", "quantity": 2, "price": 14.95}]}
    assert shop.fetch_inventory() == [{"sku": "MAT", "quantity": 40}]
    shop.set_inventory("MAT", 12)
    assert json.loads(c.calls[-1].content) == {"location_id": 77, "inventory_item_id": 111, "available": 12}
    shop.set_price("MAT", 19.5)
    assert json.loads(c.calls[-1].content) == {"variant": {"id": 11, "price": "19.50"}}
    with pytest.raises(ChannelError, match="no variant"):
        shop.set_inventory("NOPE", 1)


def test_shopify_webhook_signature():
    shop = ShopifyConnector(SHOP_CFG, client(shopify_api))
    body = json.dumps({"id": 5, "financial_status": "paid", "line_items": []}).encode()
    events = shop.parse_webhook({"x-shopify-hmac-sha256": sign("whsec", body), "x-shopify-topic": "orders/create"}, body)
    assert events[0]["order"]["external_id"] == "5"
    with pytest.raises(ChannelError, match="signature"):
        shop.parse_webhook({"x-shopify-hmac-sha256": sign("wrong", body), "x-shopify-topic": "orders/create"}, body)
    no_secret = ShopifyConnector({**SHOP_CFG, "webhook_secret": None}, client(shopify_api))
    with pytest.raises(ChannelError, match="webhook secret"):
        no_secret.parse_webhook({}, body)


def test_errors_are_mapped():
    shop = ShopifyConnector(SHOP_CFG, client(lambda r: httpx.Response(401)))
    with pytest.raises(ChannelError, match="rejected the credentials"):
        shop.test()
    limited = ShopifyConnector(SHOP_CFG, client(lambda r: httpx.Response(429)))
    with pytest.raises(ChannelError, match="rate limited"):
        limited.test()
    with pytest.raises(ChannelError, match="location id"):
        ShopifyConnector({**SHOP_CFG, "location_id": None}, client(shopify_api)).set_inventory("MAT", 1)


# ---------------------------------------------------------------- WooCommerce
WOO_CFG = {"site_url": "https://shop.example.com/", "consumer_key": "ck_1", "consumer_secret": "cs_1",
           "webhook_secret": "woosec"}


def woo_api(request):
    assert request.headers["authorization"].startswith("Basic ")
    path, params = request.url.path, request.url.params
    if path == "/wp-json/wc/v3/orders":
        assert params["modified_after"] == "2026-11-01T00:00:00"
        page = int(params["page"])
        rows = [{"id": 100 + page, "status": "processing", "date_created_gmt": "2026-11-03T08:00:00",
                 "date_modified_gmt": "2026-11-03T09:00:00", "currency": "EUR", "total": "50.00",
                 "shipping": {"country": "FR"}, "line_items": [{"sku": "MUG", "quantity": 5, "price": 10}]}]
        return httpx.Response(200, json=rows, headers={"x-wp-totalpages": "2"})
    if path == "/wp-json/wc/v3/products":
        if "sku" in params:
            return httpx.Response(200, json=[{"id": 9}] if params["sku"] == "MUG" else [])
        return httpx.Response(200, json=[{"sku": "MUG", "manage_stock": True, "stock_quantity": 7},
                                         {"sku": "GIFT", "manage_stock": False, "stock_quantity": None}],
                              headers={"x-wp-totalpages": "1", "x-wp-total": "2"})
    if path == "/wp-json/wc/v3/products/9":
        return httpx.Response(200, json={"id": 9})
    return httpx.Response(404)


def test_woocommerce():
    c = client(woo_api)
    woo = WooCommerceConnector(WOO_CFG, c)
    assert woo.test()["products"] == 2
    orders = woo.fetch_orders(SINCE)
    assert [o["external_id"] for o in orders] == ["101", "102"]  # two pages
    assert orders[0]["created_at"] == "2026-11-03T08:00:00Z" and orders[0]["country"] == "FR" and orders[0]["total"] == 50.0
    assert woo.fetch_inventory() == [{"sku": "MUG", "quantity": 7}]  # unmanaged stock skipped
    woo.set_inventory("MUG", 3)
    assert json.loads(c.calls[-1].content) == {"manage_stock": True, "stock_quantity": 3}
    woo.set_price("MUG", 12)
    assert json.loads(c.calls[-1].content) == {"regular_price": "12.00"}
    with pytest.raises(ChannelError, match="no product"):
        woo.set_price("NOPE", 1)
    body = json.dumps({"sku": "MUG", "manage_stock": True, "stock_quantity": 2}).encode()
    events = woo.parse_webhook({"x-wc-webhook-signature": sign("woosec", body), "x-wc-webhook-topic": "product.updated"}, body)
    assert events == [{"type": "inventory", "sku": "MUG", "quantity": 2}]


# ---------------------------------------------------------------- Amazon
AMZ_CFG = {"region": "eu", "marketplace_ids": "A1PA6795UKMFR9", "client_id": "amzn1.app", "client_secret": "s",
           "refresh_token": "Atzr|x"}


def amazon_api(request):
    url = str(request.url)
    if url == "https://api.amazon.com/auth/o2/token":
        form = dict(x.split("=") for x in request.content.decode().split("&"))
        assert form["grant_type"] == "refresh_token" and form["client_id"] == "amzn1.app"
        return httpx.Response(200, json={"access_token": "Atza|tok", "expires_in": 3600})
    assert request.url.host == "sellingpartnerapi-eu.amazon.com"
    assert request.headers["x-amz-access-token"] == "Atza|tok"
    path, params = request.url.path, request.url.params
    if path == "/orders/v0/orders":
        if "NextToken" in params:
            return httpx.Response(200, json={"payload": {"Orders": [{"AmazonOrderId": "302-2", "OrderStatus": "Shipped"}]}})
        assert params["MarketplaceIds"] == "A1PA6795UKMFR9" and params["LastUpdatedAfter"] == "2026-11-01T00:00:00Z"
        return httpx.Response(200, json={"payload": {"NextToken": "n1", "Orders": [{
            "AmazonOrderId": "302-1", "OrderStatus": "Unshipped", "PurchaseDate": "2026-11-04T10:00:00Z",
            "LastUpdateDate": "2026-11-04T10:05:00Z", "OrderTotal": {"CurrencyCode": "EUR", "Amount": "39.99"}}]}})
    if path == "/fba/inventory/v1/summaries":
        assert params["granularityId"] == "A1PA6795UKMFR9"
        if "nextToken" in params:
            return httpx.Response(200, json={"payload": {"inventorySummaries": [{"sellerSku": "MUG", "totalQuantity": 3}]}})
        return httpx.Response(200, json={"pagination": {"nextToken": "t2"}, "payload": {"inventorySummaries": [
            {"sellerSku": "MAT", "totalQuantity": 60, "inventoryDetails": {"fulfillableQuantity": 55}}]}})
    if path == "/sellers/v1/marketplaceParticipations":
        return httpx.Response(200, json={"payload": [{"marketplace": {"name": "Amazon.de"}}]})
    return httpx.Response(404)


def test_amazon_read_only():
    c = client(amazon_api)
    amz = AmazonConnector(AMZ_CFG, c)
    assert amz.test()["marketplaces"] == ["Amazon.de"]
    orders = amz.fetch_orders(SINCE)
    assert [o["external_id"] for o in orders] == ["302-1", "302-2"] and orders[0]["total"] == 39.99
    assert amz.fetch_inventory() == [{"sku": "MAT", "quantity": 55}, {"sku": "MUG", "quantity": 3}]
    token_calls = [r for r in c.calls if r.url.host == "api.amazon.com"]
    assert len(token_calls) == 1  # access token cached
    with pytest.raises(NotSupported):
        amz.set_price("MAT", 10)
    with pytest.raises(ChannelError, match="region"):
        AmazonConnector({**AMZ_CFG, "region": "mars"}, c).test()


# ---------------------------------------------------------------- eBay
EBAY_CFG = {"environment": "sandbox", "marketplace_id": "EBAY_DE", "client_id": "app", "client_secret": "cert",
            "refresh_token": "v^1"}


def ebay_api(request):
    path = request.url.path
    assert request.url.host == "api.sandbox.ebay.com"
    if path == "/identity/v1/oauth2/token":
        assert request.headers["authorization"].startswith("Basic ")
        return httpx.Response(200, json={"access_token": "v^tok", "expires_in": 7200})
    assert request.headers["authorization"] == "Bearer v^tok" and request.headers["x-ebay-c-marketplace-id"] == "EBAY_DE"
    if path == "/sell/fulfillment/v1/order":
        if request.url.params.get("offset") == "200":
            return httpx.Response(200, json={"orders": []})
        assert request.url.params["filter"] == "lastmodifieddate:[2026-11-01T00:00:00Z..]"
        return httpx.Response(200, json={"next": "https://api.sandbox.ebay.com/sell/fulfillment/v1/order?offset=200", "orders": [{
            "orderId": "12-1", "orderFulfillmentStatus": "NOT_STARTED", "creationDate": "2026-11-05T08:00:00Z",
            "lastModifiedDate": "2026-11-05T08:00:00Z", "pricingSummary": {"total": {"value": "20.00", "currency": "EUR"}},
            "fulfillmentStartInstructions": [{"shippingStep": {"shipTo": {"contactAddress": {"countryCode": "AT"}}}}],
            "lineItems": [{"sku": "MAT", "quantity": 1, "lineItemCost": {"value": "20.00"}}]}]})
    if path == "/sell/inventory/v1/inventory_item":
        return httpx.Response(200, json={"inventoryItems": [
            {"sku": "MAT", "availability": {"shipToLocationAvailability": {"quantity": 8}}}]})
    if path == "/sell/inventory/v1/offer":
        return httpx.Response(200, json={"offers": [{"offerId": "o-1", "pricingSummary": {"price": {"currency": "EUR"}}}]})
    if path == "/sell/inventory/v1/bulk_update_price_quantity":
        req = json.loads(request.content)["requests"][0]
        status = 400 if req["sku"] == "BAD" else 200
        return httpx.Response(200, json={"responses": [{"statusCode": status, "errors": ["nope"] if status == 400 else []}]})
    return httpx.Response(404)


def test_ebay():
    c = client(ebay_api)
    ebay = EbayConnector(EBAY_CFG, c)
    orders = ebay.fetch_orders(SINCE)
    assert orders == [{"external_id": "12-1", "status": "NOT_STARTED", "created_at": "2026-11-05T08:00:00Z",
                       "updated_at": "2026-11-05T08:00:00Z", "currency": "EUR", "total": 20.0, "country": "AT",
                       "lines": [{"sku": "MAT", "quantity": 1, "price": 20.0}]}]
    assert ebay.fetch_inventory() == [{"sku": "MAT", "quantity": 8}]
    ebay.set_inventory("MAT", 4)
    assert json.loads(c.calls[-1].content) == {"requests": [{"sku": "MAT", "shipToLocationAvailability": {"quantity": 4}}]}
    ebay.set_price("MAT", 22)
    assert json.loads(c.calls[-1].content)["requests"][0]["offers"] == [
        {"offerId": "o-1", "price": {"value": "22.00", "currency": "EUR"}}]
    with pytest.raises(ChannelError, match="rejected"):
        ebay.set_inventory("BAD", 1)


# ---------------------------------------------------------------- Mirakl
MIRAKL_CFG = {"base_url": "https://marketplace.example.com/", "api_key": "mk-123"}


def mirakl_api(request):
    assert request.headers["authorization"] == "mk-123"
    path, params = request.url.path, request.url.params
    if path == "/api/account":
        return httpx.Response(200, json={"shop_name": "Acme FR"})
    if path == "/api/orders":
        offset = int(params["offset"])
        rows = [{"order_id": f"M-{offset}", "order_state": "SHIPPING", "created_date": "2026-11-06T08:00:00Z",
                 "last_updated_date": "2026-11-06T09:00:00Z", "currency_iso_code": "EUR", "total_price": 15,
                 "customer": {"shipping_address": {"country_iso_code": "FRA"}},
                 "order_lines": [{"offer_sku": "MAT", "quantity": 1, "price": 15}]}] if offset < 2 else []
        return httpx.Response(200, json={"orders": rows, "total_count": 2})
    if path == "/api/offers" and request.method == "GET":
        return httpx.Response(200, json={"offers": [{"shop_sku": "MAT", "quantity": 6}], "total_count": 1})
    if path == "/api/offers" and request.method == "POST":
        return httpx.Response(201, json={"import_id": 4242})
    return httpx.Response(404)


def test_mirakl():
    c = client(mirakl_api)
    mk = MiraklConnector(MIRAKL_CFG, c)
    assert mk.test() == {"account": "Acme FR"}
    orders = mk.fetch_orders(SINCE)
    assert [o["external_id"] for o in orders] == ["M-0", "M-1"] and orders[0]["country"] == "FRA"
    assert mk.fetch_inventory() == [{"sku": "MAT", "quantity": 6}]
    assert mk.set_inventory("MAT", 2) == {"import_id": 4242}
    assert json.loads(c.calls[-1].content) == {"offers": [{"shop_sku": "MAT", "quantity": 2, "update_delete": "update"}]}
    mk.set_price("MAT", 13.456)
    assert json.loads(c.calls[-1].content)["offers"][0]["price"] == 13.46
