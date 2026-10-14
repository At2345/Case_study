from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)


class AllocationRequest(BaseModel):
    product_id: str = Field(..., min_length=1, max_length=64)
    available_units: float = Field(..., ge=0, le=100_000_000)
    month: Optional[str] = None
    chargeback_pct: float = Field(0.06, ge=0, le=0.25)
    otif_penalty_pct: float = Field(0.05, ge=0, le=0.5)
    amazon_floor_pct: float = Field(0.60, ge=0, le=1)
    b2b_floor_pct: float = Field(0.0, ge=0, le=1)
    d2c_churn_pct: float = Field(0.0, ge=0, le=0.5)


class OverrideRequest(BaseModel):
    """Planner decision. Identity (planner_id, IP, session) is taken from the token and request, never the body."""

    item_id: int
    override_quantity: float = Field(..., ge=0, description="Planner submitted units")
    reason_code: Optional[str] = Field(None, description="Mandatory audit classification")
    comment: Optional[str] = Field(None, max_length=500)
    cutoff: Optional[str] = None
    target_month: Optional[str] = None
