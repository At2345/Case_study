"""Curated demand-forecasting concept knowledge base.

Answers general-knowledge questions ("What is Croston's method?") deterministically when the
LLM chain is unavailable, with proper references. No external calls; entries are static text.
"""
from __future__ import annotations

import re
from typing import Dict, Optional

CONCEPTS: Dict[str, Dict[str, str]] = {
    "croston": {
        "keywords": ("croston", "croston's", "croston method"),
        "title": "Croston's method",
        "text": (
            "Croston's method (Croston, 1972) forecasts intermittent demand by applying exponential "
            "smoothing separately to the non-zero demand sizes and to the intervals between consecutive "
            "demands, then forecasting demand as the smoothed size divided by the smoothed interval. "
            "It avoids the positive bias of plain exponential smoothing on sporadic series; the "
            "Syntetos-Boylan Approximation (SBA) corrects a remaining bias. "
            "Reference: Croston, J.D. (1972), 'Forecasting and stock control for intermittent demands', "
            "Operational Research Quarterly 23(3), 289-303; Hyndman & Athanasopoulos, Forecasting: "
            "Principles and Practice (3rd ed.), section 13.4."
        ),
    },
    "exponential_smoothing": {
        "keywords": ("exponential smoothing", "holt-winters", "holt winters", "ets model", "triple exponential"),
        "title": "Exponential smoothing",
        "text": (
            "Exponential smoothing forecasts a series as a weighted average of past observations with "
            "weights decaying exponentially, controlled by a smoothing parameter alpha in (0, 1). "
            "Holt's linear trend adds a trend component and Holt-Winters (triple exponential smoothing) "
            "adds a seasonal component; the family is formalised as ETS (Error, Trend, Seasonal) models. "
            "Reference: Holt (1957) and Winters (1960); Hyndman & Athanasopoulos, Forecasting: Principles "
            "and Practice (3rd ed.), chapters 7-8."
        ),
    },
    "intermittent_demand": {
        "keywords": ("intermittent demand", "sporadic demand", "lumpy demand"),
        "title": "Intermittent demand",
        "text": (
            "Intermittent (sporadic) demand series contain many zero periods, so standard methods such as "
            "moving averages or simple exponential smoothing are biased. Common approaches are Croston's "
            "method, the Syntetos-Boylan Approximation (SBA), and count/state-space models; evaluation "
            "usually uses scaled errors such as MASE rather than MAPE. "
            "Reference: Syntetos & Boylan (2005), 'The accuracy of intermittent demand estimates', "
            "International Journal of Forecasting 21(2), 303-314."
        ),
    },
    "forecast_error_metrics": {
        "keywords": ("wape", "mape", "mase", "rmse", "forecast error metric"),
        "title": "Forecast error metrics",
        "text": (
            "MAPE is the mean absolute percentage error: scale-free but undefined or inflated when actuals "
            "are zero or near zero. WAPE (weighted absolute percentage error) divides total absolute error "
            "by total actual demand, avoiding the zero-actual problem. MASE compares the forecast to a "
            "naive benchmark and is robust for intermittent series. "
            "Reference: Hyndman & Koehler (2006), 'Another look at measures of forecast accuracy', "
            "International Journal of Forecasting 22(4), 679-688."
        ),
    },
    "safety_stock": {
        "keywords": ("safety stock", "safety inventory", "buffer stock"),
        "title": "Safety stock",
        "text": (
            "Safety stock buffers against demand and lead-time variability; a common formula is Z times the "
            "standard deviation of demand over lead time, where Z is the z-score of the target service level "
            "(e.g. 95% cycle service level). It can be computed per item or per aggregate and is a key "
            "inventory trade-off against holding cost. "
            "Reference: Silver, Pyke & Thomas, Inventory and Production Management in Supply Chains (4th ed.), "
            "chapter 7."
        ),
    },
    "moving_average": {
        "keywords": ("moving average", "moving-average"),
        "title": "Moving average",
        "text": (
            "A moving average forecasts the next period as the mean of the last k observations, smoothing "
            "out noise at the cost of lagging trends and level shifts. It is a special case of exponential "
            "smoothing with equal weights; the window k trades smoothness against responsiveness. "
            "Reference: Hyndman & Athanasopoulos, Forecasting: Principles and Practice (3rd ed.), section 3.2."
        ),
    },
    "arima": {
        "keywords": ("arima", "box-jenkins", "box jenkins"),
        "title": "ARIMA (Box-Jenkins)",
        "text": (
            "ARIMA models a series with autoregressive (AR) and moving-average (MA) terms applied to the "
            "differenced series; ARIMA(p, d, q) denotes the orders, and seasonal series use SARIMA with "
            "seasonal orders. ARIMA handles trends and autocorrelation well but needs sufficient history and "
            "is less suited to highly intermittent demand. "
            "Reference: Box, Jenkins & Reinsel, Time Series Analysis: Forecasting and Control (4th ed.)."
        ),
    },
    "seasonality": {
        "keywords": ("seasonality", "seasonal pattern", "seasonal decomposition"),
        "title": "Seasonality",
        "text": (
            "Seasonality is a repeating pattern within a fixed period, such as a monthly pattern within a "
            "year. It can be handled by seasonal decomposition (additive or multiplicative), seasonal "
            "dummies, seasonal exponential smoothing (Holt-Winters), or SARIMA. "
            "Reference: Hyndman & Athanasopoulos, Forecasting: Principles and Practice (3rd ed.), chapter 3."
        ),
    },
    "otif": {
        "keywords": ("otif", "on-time in-full", "on time in full"),
        "title": "OTIF (On-Time In-Full)",
        "text": (
            "OTIF measures whether orders are delivered on time and in full; it is the product of the "
            "on-time rate and the in-full rate. It is a supply-chain service KPI, often contractual with "
            "penalty clauses for missed targets. "
            "Reference: APICS/ASCM supply-chain KPI dictionary."
        ),
    },
    "fva": {
        "keywords": ("forecast value added", "fva"),
        "title": "Forecast Value Added (FVA)",
        "text": (
            "Forecast Value Added (FVA) measures whether each forecasting step (statistical baseline, "
            "planner override, consensus) improves accuracy over a naive benchmark. Positive FVA means the "
            "step added value; negative FVA means it degraded the forecast and should be questioned. "
            "Reference: Gilliland (2010), The Business Value of Forecasting."
        ),
    },
    "consensus_sop": {
        "keywords": ("s&op", "sales and operations planning", "consensus forecast", "consensus planning"),
        "title": "S&OP and consensus forecasting",
        "text": (
            "S&OP (Sales and Operations Planning) is a monthly cross-functional process that reconciles "
            "demand, supply, inventory and finance into one agreed plan. Consensus forecasting combines "
            "statistical forecasts with commercial judgment (account feedback, promotions) before the plan "
            "is committed. "
            "Reference: APICS/ASCM S&OP framework; Lapide (2004), 'Sales and Operations Planning'."
        ),
    },
    "forecast_bias": {
        "keywords": ("forecast bias", "bias"),
        "title": "Forecast bias",
        "text": (
            "Forecast bias is the systematic tendency to over- or under-forecast, measured as the mean error "
            "or the ratio of total forecast to total actual. Persistent bias signals process issues such as "
            "incentive misalignment or stale baselines and is often tracked per planner or per channel. "
            "Reference: Gilliland, Sglavo & Sashkin, Business Forecasting: The Emerging Role of Data Science (2016)."
        ),
    },
}


def lookup_concept(question: str) -> Optional[Dict[str, str]]:
    """First concept whose keyword appears in the question, or None."""
    q = question.lower()
    for entry in CONCEPTS.values():
        for kw in entry["keywords"]:
            if re.search(rf"\b{re.escape(kw)}\b", q):
                return entry
    return None


def concept_answer(question: str) -> Optional[str]:
    """Formatted deterministic answer for a concept question, or None if no concept matches."""
    entry = lookup_concept(question)
    if entry is None:
        return None
    return f"{entry['title']}: {entry['text']}"
