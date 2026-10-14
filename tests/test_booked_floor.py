import numpy as np
import pandas as pd
import pytest

from app.phasing.floor import booked_by_cutoff, evaluate_floor
from app.phasing.lag import prepare_lines

T = pd.Timestamp


def lines(rows):
    cols = ["order_id", "item_id", "channel", "customer_id", "ordered_at", "material_availability_at", "delivered_at",
            "ordered_quantity_plan_relevant", "cancellation_status", "cancellation_date"]
    df = pd.DataFrame(rows, columns=cols)
    for c in ("ordered_at", "material_availability_at", "delivered_at", "cancellation_date"):
        df[c] = pd.to_datetime(df[c])
    out = prepare_lines(df.drop(columns=["cancellation_date"]))
    return out.merge(df[["order_id", "cancellation_date"]], on="order_id")


def row(i, ordered, mat, qty, item=1, status="no_cancellation", cancel=None):
    return (f"o{i}", item, "B2B wo Amazon", "C1", ordered, mat, mat, qty, status, cancel)


def test_booked_by_cutoff_counts_only_lines_known_and_alive_at_the_cutoff():
    ls = lines([
        row(1, "2025-01-10", "2025-02-12", 100.0),                                   # known before cutoff: counts
        row(2, "2025-02-02", "2025-02-20", 50.0),                                    # ordered after the cutoff: unknown then
        row(3, "2025-01-15", "2025-02-18", 70.0, status="cancelled_completely", cancel="2025-01-25"),  # cancelled before cutoff
        row(4, "2025-01-16", "2025-02-19", 30.0, status="cancelled_completely", cancel="2025-02-05"),  # alive at cutoff
        row(5, "2025-01-17", "2025-03-05", 999.0),                                   # other target month
    ])
    b = booked_by_cutoff(ls, cutoff=T("2025-01-01"), target_month=T("2025-02-01"))
    assert b.to_dict() == {1: 130.0}


def test_floor_improves_error_when_bookings_exceed_the_forecast_and_never_when_below():
    ls = lines([
        row(1, "2025-01-10", "2025-02-12", 500.0, item=1),
        row(2, "2025-01-10", "2025-02-12", 20.0, item=2),
    ])
    bt = pd.DataFrame({
        "item_id": [1, 2], "cutoff": [T("2025-01-01")] * 2, "date": [T("2025-02-01")] * 2,
        "prediction": [100.0, 100.0], "target": [520.0, 90.0], "horizon": [1, 1],
        "channel": ["B2B wo Amazon"] * 2,
    })
    out = evaluate_floor(ls, bt).set_index("scope").loc["B2B wo Amazon"]
    assert out["rows_floored"] == 1  # only item 1 has bookings above the forecast
    assert out["fpe_floored"] < out["fpe_ml"]
    assert out["fpe_ml"] == pytest.approx(100 * (420 + 10) / 610)
    assert out["fpe_floored"] == pytest.approx(100 * (20 + 10) / 610)
