from __future__ import annotations

from typing import Dict

# Column kinds: int, float, str, datetime, bool
Contract = Dict[str, str]

CONTRACTS: Dict[str, Contract] = {
    "items": {
        "item_id": "int", "product_id": "str", "channel": "str", "category": "str",
        "subcategory": "str", "segment": "str", "subsegment": "str", "product_group": "str",
        "brand": "str", "product_brand": "str", "product_family": "str",
        "itemcategorygroup": "str", "license": "str", "customerspecific": "str",
        "embargoflag": "str", "launch_month": "str", "end_of_life_month": "str",
        "local_eol_month": "str", "global_eol_month": "str", "product_status": "str",
        "abc_category": "str", "has_eol": "bool", "has_predecessor": "bool",
        "has_successor": "bool", "purchase_price": "float", "sales_price": "float",
    },
    "baseline_items": {
        "level_id": "int", "product_id": "str", "category": "str", "subcategory": "str",
        "segment": "str", "subsegment": "str", "brand": "str", "itemcategorygroup": "str",
        "launch_month": "str", "end_of_life_month": "str", "product_status": "str",
        "abc_category": "str",
    },
    "baseline_mapping": {"level_id": "int", "item_id": "int"},
    "target_time_series": {
        "item_id": "int", "date": "datetime", "target": "float", "imputed": "bool",
        "exposure": "float",
    },
    "baseline_target_time_series": {
        "level_id": "int", "date": "datetime", "target": "float", "exposure": "float",
    },
    "baseline_forecast": {
        "level_id": "int", "cutoff": "datetime", "date": "datetime", "prediction": "float",
    },
    "backtest_predictions": {
        "model": "str", "cutoff": "datetime", "item_id": "int", "date": "datetime",
        "target": "float", "prediction": "float", "exposure": "float",
    },
    "live_predictions": {"item_id": "int", "date": "datetime", "prediction": "float"},
    "overall_error_metrics": {
        "metric_fpe": "float", "metric_bias_pct": "float", "metric_mae": "float",
        "metric_rmse": "float", "n_rows": "int",
    },
    "item_error_metrics": {
        "item_id": "int", "metric_fpe": "float", "metric_bias_pct": "float",
        "metric_mae": "float", "metric_rmse": "float", "n_rows": "int",
    },
    "past_covariates": {
        "item_id": "int", "date": "datetime", "amazon_avg_oos_rate": "float",
        "amazon_max_oos_rate": "float", "amazon_avg_inventory_units": "float",
        "amazon_min_inventory_units": "float", "amazon_avg_open_po_units": "float",
        "amazon_avg_vendor_confirmation_rate": "float", "amazon_total_ordered_units": "float",
        "amazon_avg_daily_ordered_units": "float", "amazon_max_daily_ordered_units": "float",
        "amazon_zero_sales_day_rate": "float", "amazon_total_glance_views": "float",
        "amazon_avg_daily_glance_views": "float", "amazon_max_daily_glance_views": "float",
        "amazon_zero_traffic_day_rate": "float", "inventory_min_week_start_stock": "float",
        "inventory_weeks_zero_week_start_stock": "float",
        "inventory_weeks_week_start_stock_below_threshold": "float",
        "ordered_quantity": "float", "ordered_quantity_plan_relevant": "float",
        "delivered_quantity": "float", "has_amazon": "bool", "has_internal_inventory": "bool",
    },
    "future_covariates": {
        "item_id": "int", "date": "datetime", "amazon_forecast_p70": "float",
        "amazon_deal_days": "float", "amazon_deal_count": "float",
        "amazon_max_relative_discount_vs_rrp": "float", "months_since_launch": "float",
        "months_to_eol": "float", "has_amazon_future": "bool",
    },
    "amazon_traffic": {"item_id": "int", "date": "datetime", "glance_views": "int"},
    "amazon_sell_out": {"item_id": "int", "date": "datetime", "ordered_units": "int"},
    "amazon_inventory": {
        "item_id": "int", "date": "datetime", "procurable_product_out_of_stock_rate": "float",
        "open_purchase_order_units": "float", "vendor_confirmation_rate": "float",
        "sellable_on_hand_inventory_units": "float",
    },
    "amazon_forecast": {
        "item_id": "int", "start_date": "datetime", "end_date": "datetime",
        "forecast_generation_date": "datetime", "p70": "float",
    },
    "orderbook": {
        "order_id": "str", "item_id": "int", "channel": "str", "product_id": "str",
        "customer_id": "str", "ordered_at": "datetime", "decision_at": "datetime",
        "material_availability_at": "datetime", "requested_delivery_at": "datetime",
        "planned_delivery_at": "datetime", "delivered_at": "datetime",
        "last_updated": "datetime", "ordered_quantity": "int",
        "ordered_quantity_plan_relevant": "float", "delivered_quantity": "int",
        "cancelled_quantity": "int", "cancelled_quantity_by_delivery_doc": "int",
        "cancellation_status": "str", "cancellation_date": "datetime",
        "cancellation_date_source": "str", "cancellation_lag_days": "float",
        "cancellation_mechanism": "str", "cancellation_sales_document": "str",
        "rejection_reasons": "str",
    },
    "promotions": {
        "promotion_id": "int", "item_id": "int", "promotion_name": "str",
        "date_from": "datetime", "date_to": "datetime", "discount_abs": "float",
        "discount_rel": "float", "forecast_units": "float", "promotion_type": "str",
    },
    "predecessors": {"item_id": "int", "predecessor_item_id": "int", "split_weight": "float"},
    "lifecycle": {"item_id": "int", "start_date": "datetime", "end_date": "datetime"},
}
