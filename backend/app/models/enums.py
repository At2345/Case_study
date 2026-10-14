from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    PLANNER = "planner"
    ADMIN = "admin"
    AUDITOR = "auditor"


class ReasonCode(str, Enum):
    ML_ACCEPTED = "ML Output Formally Accepted"
    COMMERCIAL_INTELLIGENCE = "Commercial Intelligence (Account Feedback)"
    RETAILER_PROMO_TIMING = "Retailer Promotion Timing Shift"
    LISTING_AUTH_DELAY = "Listing Authorization Delay"
    LISTING_DATE_ADJUSTMENT = "Retailer Listing Date Adjustment"
    COMPETITOR_STOCKOUT = "Competitor Stockout Capture"
    MARKETING_BUDGET = "Marketing Budget Adjustment"
    AMAZON_PROMO_SHIFT = "Amazon Promotion Shift / Cancellation"
    SUPPLY_CONSTRAINED = "Supply Chain Constrained Run"
    NO_PROMO_AGREEMENT = "No retailer promotional agreement confirmed"
    ORDER_BOOK_FLOOR = "Order Book Floor Applied"


class RiskType(str, Enum):
    OOS_RISK = "OOS_RISK"
    OVERSTOCK_RISK = "OVERSTOCK_RISK"
    BUYBOX = "CRITICAL_AMAZON_BUYBOX_ALERT"
    TERMINAL = "TERMINAL_OBSOLESCENCE_RISK"
    NPI_GAP = "NPI_ANALOGY_GAP"
