"""Channel Hub: free connectors to marketplaces, shops and supply partners (official APIs, own credentials)."""
from __future__ import annotations

from typing import Dict

from app.channels.base import ConnectorType


def connector_types() -> Dict[str, ConnectorType]:
    from app.channels.connectors.marketplaces import AMAZON, EBAY, MIRAKL
    from app.channels.connectors.shops import SHOPIFY, WOOCOMMERCE
    from app.channels.edi import EDI
    return {t.name: t for t in (SHOPIFY, WOOCOMMERCE, AMAZON, EBAY, MIRAKL, EDI)}
