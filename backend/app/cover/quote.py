"""Overstock cover quote engine (indicative, non-binding).

Product: a guaranteed floor value on units of a purchase order still unsold at the end of the cover
window (default 180 days). The platform takes the stock and resells it, so its net cost per paid unit is
floor - expected recovery.

Seller retention (against adverse selection and moral hazard):
- cover only up to the recommended quantity (demand quantile `cap_quantile`, default P80); units above it
  stay with the seller,
- an excess on the first `deductible_pct` of covered units,
- coinsurance: the seller keeps `coinsurance` of each paid unit's floor.

Demand over the window is lognormal, matched to the forecast mean and standard deviation, which gives
closed forms for expected unsold units and claim probability. Premium = expected platform loss x
(1 + loading) + expense fee, with a minimum. Grade E (expected unsold share >= 35%) is declined.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from scipy.stats import norm

METHOD = "lognormal_v1"
GRADES = ((0.05, "A"), (0.10, "B"), (0.20, "C"), (0.35, "D"))


@dataclass(frozen=True)
class CoverTerms:
    horizon_days: int = 180
    floor_pct: float = 0.50          # guaranteed value per unsold unit, share of landed cost
    recovery_rate: float = 0.25      # expected resale value of unsold stock, share of landed cost
    deductible_pct: float = 0.10     # excess: first share of covered units unsold is not paid
    coinsurance: float = 0.20        # seller keeps this share of each paid unit's floor
    cap_quantile: float = 0.80       # cover up to this demand quantile (recommended order quantity)
    loading: float = 0.35            # risk and capital loading on expected loss
    expense_eur: float = 25.0
    min_premium_eur: float = 50.0

    def __post_init__(self) -> None:
        for name in ("floor_pct", "recovery_rate", "deductible_pct", "coinsurance"):
            if not 0 <= getattr(self, name) <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        if not 0.5 <= self.cap_quantile < 1:
            raise ValueError("cap_quantile must be in [0.5, 1)")
        if self.loading < 0 or self.expense_eur < 0 or self.min_premium_eur < 0 or self.horizon_days <= 0:
            raise ValueError("loading, fees and horizon must be positive")


@dataclass
class Demand:
    mean_units: float
    sd_units: float
    source: str = "manual"
    detail: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.mean_units <= 0:
            raise ValueError("forecast demand must be positive to price cover")
        if self.sd_units < 0:
            raise ValueError("demand standard deviation cannot be negative")

    def lognormal(self) -> Dict[str, float]:
        cv2 = (self.sd_units / self.mean_units) ** 2
        s2 = math.log1p(max(cv2, 1e-12))
        return {"mu": math.log(self.mean_units) - s2 / 2, "s": math.sqrt(s2)}


def _cdf(x: float, mu: float, s: float) -> float:
    return 0.0 if x <= 0 else float(norm.cdf((math.log(x) - mu) / s))


def expected_shortfall(q: float, mu: float, s: float) -> float:
    """E[max(q - D, 0)] for lognormal D: expected units left unsold out of q."""
    if q <= 0:
        return 0.0
    mean = math.exp(mu + s * s / 2)
    return q * _cdf(q, mu, s) - mean * float(norm.cdf((math.log(q) - mu - s * s) / s))


def quantile(p: float, mu: float, s: float) -> float:
    return math.exp(mu + s * float(norm.ppf(p)))


def quote(units: float, unit_landed_cost_eur: float, demand: Demand,
          terms: Optional[CoverTerms] = None, experience: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Price cover for one purchase order. Returns figures, retention terms and assumptions.

    `experience` (see app.cover.experience) scales the expected loss by its credibility-weighted factor.
    """
    terms = terms or CoverTerms()
    experience = experience or {"policies": 0, "a_to_e": None, "credibility": 0.0, "factor": 1.0}
    factor = float(experience.get("factor") or 1.0)
    if units <= 0 or unit_landed_cost_eur <= 0:
        raise ValueError("units and unit landed cost must be positive")
    p = demand.lognormal()
    mu, s = p["mu"], p["s"]

    recommended = math.floor(quantile(terms.cap_quantile, mu, s))
    covered = float(min(units, max(recommended, 0)))
    excluded = units - covered
    deductible_units = math.floor(terms.deductible_pct * covered)

    expected_unsold = expected_shortfall(units, mu, s)             # whole PO
    expected_paid_units = expected_shortfall(covered - deductible_units, mu, s)
    claim_probability = _cdf(covered - deductible_units, mu, s)

    payout_per_unit = terms.floor_pct * (1 - terms.coinsurance) * unit_landed_cost_eur
    net_per_unit = max(payout_per_unit - terms.recovery_rate * unit_landed_cost_eur, 0.0)
    expected_payout = expected_paid_units * payout_per_unit
    expected_loss = expected_paid_units * net_per_unit
    rated_loss = expected_loss * factor  # experience-rated
    premium = max(terms.min_premium_eur, rated_loss * (1 + terms.loading) + terms.expense_eur)
    covered_value = covered * unit_landed_cost_eur

    unsold_share = expected_unsold / units
    grade = next((g for limit, g in GRADES if unsold_share < limit), "E")
    offered = grade != "E" and covered > 0
    reasons: List[str] = []
    if grade == "E":
        reasons.append(f"expected unsold share {unsold_share:.0%} is above the 35% limit; order less")
    if covered <= 0:
        reasons.append("recommended quantity is zero")

    return {
        "offered": offered, "decline_reasons": reasons, "risk_grade": grade, "binding": False, "method": METHOD,
        "units": units, "recommended_units": recommended, "covered_units": covered, "excluded_units": excluded,
        "deductible_units": deductible_units,
        "expected_unsold_units": round(expected_unsold, 1), "expected_unsold_share": round(unsold_share, 4),
        "claim_probability": round(claim_probability, 4),
        "expected_paid_units": round(expected_paid_units, 1),
        "payout_per_unit_eur": round(payout_per_unit, 2),
        "expected_payout_eur": round(expected_payout, 2), "expected_loss_eur": round(expected_loss, 2),
        "rated_loss_eur": round(rated_loss, 2), "experience": dict(experience),
        "premium_eur": round(premium, 2) if offered else None,
        "premium_rate": round(premium / covered_value, 4) if offered and covered_value else None,
        "covered_value_eur": round(covered_value, 2),
        "terms": asdict(terms),
        "demand": {"mean_units": round(demand.mean_units, 1), "sd_units": round(demand.sd_units, 1),
                   "source": demand.source, **demand.detail},
    }


def demand_from_forecast(plan: List[Dict[str, Any]], sigmas: List[Optional[float]], months: int) -> Demand:
    """Sum the next `months` monthly forecasts. Uses the consensus (planner overrides applied) as the mean
    and the model's sigma per month; months are treated as independent (sd = sqrt(sum sigma^2)).
    A month without sigma uses half the P10-P90 width / 2.563 (normal approximation), else 30% of the mean.
    """
    rows = plan[:months]
    if not rows:
        raise ValueError("no forecast months available for this item")
    mean, var, fallback = 0.0, 0.0, 0
    monthly: List[float] = []
    for row, sigma in zip(rows, sigmas[:months]):
        m = float(row.get("consensus") if row.get("consensus") is not None else row.get("final") or 0)
        mean += max(m, 0.0)
        monthly.append(round(max(m, 0.0), 2))
        if sigma is None and row.get("p10") is not None and row.get("p90") is not None:
            sigma = (float(row["p90"]) - float(row["p10"])) / 2.563
        if sigma is None:
            sigma, fallback = 0.3 * max(m, 0.0), fallback + 1
        var += float(sigma) ** 2
    return Demand(mean, math.sqrt(var), source="forecast",
                  detail={"months": [r["month"] for r in rows], "months_without_sigma": fallback,
                          "monthly_means": monthly})
