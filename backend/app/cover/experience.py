"""Experience rating: every settled policy makes the next quote more accurate.

At bind time a policy stores the paid units its quote expected. At settlement the actual paid units are
known. A/E = sum(actual paid units) / sum(expected paid units) over settled policies.

Limited-fluctuation credibility: Z = min(1, sqrt(n / FULL_CREDIBILITY_POLICIES)).
Factor = Z * A/E + (1 - Z) * 1, clamped to [MIN_FACTOR, MAX_FACTOR]. The factor multiplies the
expected loss in new quotes, so a seller whose batches sell better than forecast pays less over time,
and one that over-orders pays more, without one lucky or unlucky policy swinging the price.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

FULL_CREDIBILITY_POLICIES = 30
MIN_FACTOR, MAX_FACTOR = 0.5, 2.0


def _settled(policies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [p for p in policies if p.get("status") in ("CLAIMED", "CLOSED_NO_CLAIM")
            and p.get("claim") and p.get("expected_paid_units") is not None]


def raw_a_to_e(policies: List[Dict[str, Any]]) -> Optional[float]:
    settled = _settled(policies)
    expected = sum(float(p["expected_paid_units"]) for p in settled)
    return sum(float(p["claim"]["paid_units"]) for p in settled) / expected if expected > 0 else None


def experience(policies: List[Dict[str, Any]], pooled: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Credibility-weighted factor. The complement of credibility is the pooled A/E of sharing tenants
    when available (pooled = {"a_to_e", "tenants"}), otherwise neutral (1.0)."""
    complement, source = 1.0, "neutral"
    if pooled and pooled.get("a_to_e") is not None:
        complement, source = min(MAX_FACTOR, max(MIN_FACTOR, float(pooled["a_to_e"]))), "pooled"
    settled = _settled(policies)
    n = len(settled)
    expected = sum(float(p["expected_paid_units"]) for p in settled)
    actual = sum(float(p["claim"]["paid_units"]) for p in settled)
    base = {"policies": n, "expected_paid_units": round(expected, 1), "actual_paid_units": round(actual, 1),
            "complement": round(complement, 3), "complement_source": source,
            "pooled_tenants": pooled.get("tenants") if pooled else None}
    if n == 0 or expected <= 0:
        return {**base, "a_to_e": None, "credibility": 0.0, "factor": round(complement, 3)}
    a_to_e = actual / expected
    z = min(1.0, math.sqrt(n / FULL_CREDIBILITY_POLICIES))
    factor = min(MAX_FACTOR, max(MIN_FACTOR, z * a_to_e + (1 - z) * complement))
    return {**base, "a_to_e": round(a_to_e, 3), "credibility": round(z, 3), "factor": round(factor, 3)}
