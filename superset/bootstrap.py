"""Bootstrap Apache Superset for the S&OP workbench.

Creates (idempotently) the SQLite mart connection, one dataset per mart table, a set of charts and the
`sop-overview` dashboard. Run it once Superset is up:

    python superset/bootstrap.py
    python superset/bootstrap.py --url http://localhost:8088 --password admin

Environment: SUPERSET_URL, SUPERSET_ADMIN_USER, SUPERSET_ADMIN_PASSWORD, SUPERSET_MART_URI,
SUPERSET_DASHBOARD. The mart URI is resolved inside the Superset container, where
docker-compose.superset.yml mounts `var/bi_mart.db` at `/app/bi_mart.db`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

import httpx

MART_URI = os.getenv("SUPERSET_MART_URI", "sqlite:////app/bi_mart.db")
DB_NAME = "SOP Mart"
DASHBOARD_TITLE = "SOP Overview"
DASHBOARD_SLUG = os.getenv("SUPERSET_DASHBOARD", "sop-overview")

TABLES = [
    "triage_items", "triage_monthly", "quantile_bands", "phasing_items", "safety_stock",
    "overrides", "commits", "fva_by_planner", "fva_by_reason", "cycle_summary", "quality_warnings",
]


def _metric(column: str, aggregate: str, label: Optional[str] = None) -> Dict[str, Any]:
    return {"expressionType": "SIMPLE", "column": {"column_name": column}, "aggregate": aggregate,
            "label": label or f"{aggregate}({column})"}


def _where(subject: str, comparator: Any) -> Dict[str, Any]:
    return {"expressionType": "SIMPLE", "subject": subject, "operator": "==", "comparator": comparator,
            "clause": "WHERE"}


def _chart_specs(ds: Dict[str, int]) -> List[Dict[str, Any]]:
    """(name, viz_type, dataset, form_data) for the overview dashboard."""
    ti, tm, ov, fva = ds["triage_items"], ds["triage_monthly"], ds["overrides"], ds["fva_by_planner"]
    return [
        {"name": "Auto-approved SKUs", "viz_type": "big_number_total", "dataset": "triage_items", "params": {
            "viz_type": "big_number_total", "datasource": f"{ti}__table",
            "metric": _metric("item_id", "COUNT", "Touchless SKUs"),
            "adhoc_filters": [_where("state", "AUTO_APPROVED")], "time_range": "No filter",
            "subheader": "ML forecast accepted without review"}},
        {"name": "Exceptions to review", "viz_type": "big_number_total", "dataset": "triage_items", "params": {
            "viz_type": "big_number_total", "datasource": f"{ti}__table",
            "metric": _metric("item_id", "COUNT", "Exceptions"),
            "adhoc_filters": [_where("state", "EXCEPTION_REVIEW")], "time_range": "No filter",
            "subheader": "Planner decision required"}},
        {"name": "Items by triage state", "viz_type": "pie", "dataset": "triage_items", "params": {
            "viz_type": "pie", "datasource": f"{ti}__table", "groupby": ["state"],
            "metric": _metric("item_id", "COUNT", "Items"), "adhoc_filters": [],
            "time_range": "No filter", "row_limit": 20, "label_type": "key_value"}},
        {"name": "Exceptions by channel", "viz_type": "bar", "dataset": "triage_items", "params": {
            "viz_type": "bar", "datasource": f"{ti}__table", "groupby": ["channel"],
            "metrics": [_metric("item_id", "COUNT", "Exceptions")],
            "adhoc_filters": [_where("state", "EXCEPTION_REVIEW")], "time_range": "No filter", "row_limit": 20}},
        {"name": "Value at risk by risk type", "viz_type": "bar", "dataset": "triage_items", "params": {
            "viz_type": "bar", "datasource": f"{ti}__table", "groupby": ["risk_type"],
            "metrics": [_metric("var_gbp", "SUM", "Value at risk (GBP)")],
            "adhoc_filters": [_where("state", "EXCEPTION_REVIEW")], "time_range": "No filter", "row_limit": 20}},
        {"name": "Plan vs baseline by month", "viz_type": "line", "dataset": "triage_monthly", "params": {
            "viz_type": "line", "datasource": f"{tm}__table", "granularity_sqla": "date",
            "time_grain_sqla": "P1M",
            "metrics": [_metric("final_qty", "SUM", "Plan"), _metric("baseline_qty", "SUM", "Baseline")],
            "groupby": [], "adhoc_filters": [], "time_range": "No filter", "row_limit": 10000}},
        {"name": "Top exceptions by value at risk", "viz_type": "table", "dataset": "triage_items", "params": {
            "viz_type": "table", "datasource": f"{ti}__table", "query_mode": "raw",
            "all_columns": ["item_id", "product_id", "channel", "risk_type", "var_gbp", "value_delta_gbp"],
            "order_by_cols": ['["var_gbp", false]'],
            "adhoc_filters": [_where("state", "EXCEPTION_REVIEW")], "row_limit": 50, "time_range": "No filter"}},
        {"name": "Overrides by reason code", "viz_type": "pie", "dataset": "overrides", "params": {
            "viz_type": "pie", "datasource": f"{ov}__table", "groupby": ["reason_code"],
            "metric": _metric("id", "COUNT", "Overrides"), "adhoc_filters": [],
            "time_range": "No filter", "row_limit": 20, "label_type": "key_value"}},
        {"name": "FVA by planner", "viz_type": "table", "dataset": "fva_by_planner", "params": {
            "viz_type": "table", "datasource": f"{fva}__table", "query_mode": "raw",
            "all_columns": ["planner_id", "n_overrides", "n_accepted", "volume", "fpe_ml", "fpe_planner",
                            "fpe_baseline", "fva_pts"],
            "order_by_cols": [], "adhoc_filters": [], "row_limit": 50, "time_range": "No filter"}},
    ]


def _position(charts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Superset v2 dashboard layout: rows of up to three charts."""
    pos: Dict[str, Any] = {
        "DASHBOARD_VERSION_KEY": "v2",
        "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
        "GRID_ID": {"type": "GRID", "id": "GRID_ID", "children": [], "parents": ["ROOT_ID"]},
    }
    rows = [charts[i:i + 3] for i in range(0, len(charts), 3)]
    for r_i, row in enumerate(rows, start=1):
        row_id = f"ROW-{r_i}"
        pos["GRID_ID"]["children"].append(row_id)
        pos[row_id] = {"type": "ROW", "id": row_id, "children": [], "parents": ["ROOT_ID", "GRID_ID"],
                       "meta": {"background": "BACKGROUND_TRANSPARENT"}}
        for c_i, chart in enumerate(row, start=1):
            key = f"CHART-{r_i}-{c_i}"
            pos[row_id]["children"].append(key)
            pos[key] = {"type": "CHART", "id": key, "children": [],
                        "parents": ["ROOT_ID", "GRID_ID", row_id],
                        "meta": {"chartId": chart["id"], "width": 4, "height": 50, "sliceName": chart["name"]}}
    return pos


class Superset:
    def __init__(self, url: str, username: str, password: str, mart_uri: str = MART_URI) -> None:
        self.url = url.rstrip("/")
        self.client = httpx.Client(base_url=self.url, timeout=60, follow_redirects=True)
        self.token: Optional[str] = None
        self.csrf: Optional[str] = None
        self.username, self.password = username, password
        self.mart_uri = mart_uri

    def login(self, attempts: int = 30) -> None:
        for i in range(attempts):
            try:
                r = self.client.post("/api/v1/security/login", json={
                    "username": self.username, "password": self.password, "provider": "db", "refresh": True})
                if r.status_code == 200:
                    self.token = r.json()["access_token"]
                    self.client.headers["Authorization"] = f"Bearer {self.token}"
                    self.csrf = self.client.get("/api/v1/security/csrf_token/").json()["result"]
                    self.client.headers["X-CSRFToken"] = self.csrf
                    return
                if r.status_code in (400, 401):
                    raise SystemExit(f"Superset login failed ({r.status_code}): {r.text[:300]}")
            except httpx.HTTPError as exc:
                if i == attempts - 1:
                    raise SystemExit(f"Cannot reach Superset at {self.url}: {exc}")
            time.sleep(2)

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        r = self.client.request(method, path, **kwargs)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:500]}")
        return r

    def find(self, path: str, column: str, value: str) -> Optional[Dict[str, Any]]:
        q = {"filters": [{"col": column, "opr": "eq", "value": value}]}
        r = self._request("GET", path, params={"q": json.dumps(q)})
        result = r.json().get("result") or []
        return result[0] if result else None

    def database(self) -> int:
        existing = self.find("/api/v1/database/", "database_name", DB_NAME)
        if existing:
            return int(existing["id"])
        r = self._request("POST", "/api/v1/database/", json={
            "database_name": DB_NAME, "sqlalchemy_uri": self.mart_uri, "expose_in_sqllab": True,
            "allow_ctas": False, "allow_cvas": False, "allow_dml": False,
            "extra": json.dumps({"metadata_params": {}, "engine_params": {}})})
        return int(r.json()["id"])

    def dataset(self, db_id: int, table: str) -> int:
        existing = self.find("/api/v1/dataset/", "table_name", table)
        if existing:
            return int(existing["id"])
        r = self._request("POST", "/api/v1/dataset/", json={
            "database": db_id, "schema": None, "table_name": table})
        return int(r.json()["id"])

    def chart(self, spec: Dict[str, Any]) -> int:
        existing = self.find("/api/v1/chart/", "slice_name", spec["name"])
        payload = {"slice_name": spec["name"], "viz_type": spec["viz_type"],
                   "datasource_id": spec["dataset_id"], "datasource_type": "table",
                   "params": json.dumps(spec["params"])}
        if existing:
            self._request("PUT", f"/api/v1/chart/{existing['id']}", json=payload)
            return int(existing["id"])
        return int(self._request("POST", "/api/v1/chart/", json=payload).json()["id"])

    def dashboard(self, charts: List[Dict[str, Any]]) -> int:
        existing = self.find("/api/v1/dashboard/", "dashboard_title", DASHBOARD_TITLE)
        positions = _position(charts)
        if existing:
            dash_id = int(existing["id"])
            # The update schema takes `positions` (dict) and syncs the dashboard<->chart links.
            self._request("PUT", f"/api/v1/dashboard/{dash_id}",
                          json={"positions": positions, "published": True})
            return dash_id
        r = self._request("POST", "/api/v1/dashboard/", json={
            "dashboard_title": DASHBOARD_TITLE, "slug": DASHBOARD_SLUG,
            "position_json": json.dumps(positions), "published": True})
        return int(r.json()["id"])


def main() -> None:
    ap = argparse.ArgumentParser(description="Bootstrap Superset for the S&OP workbench")
    ap.add_argument("--url", default=os.getenv("SUPERSET_URL", "http://localhost:8088"))
    ap.add_argument("--user", default=os.getenv("SUPERSET_ADMIN_USER", "admin"))
    ap.add_argument("--password", default=os.getenv("SUPERSET_ADMIN_PASSWORD", "admin"))
    ap.add_argument("--mart-uri", default=MART_URI,
                    help="SQLAlchemy URI of the mart as seen by Superset (default: %(default)s)")
    args = ap.parse_args()

    ss = Superset(args.url, args.user, args.password, args.mart_uri)
    print(f"Connecting to {ss.url} ...")
    ss.login()
    print("Authenticated.")

    db_id = ss.database()
    print(f"Database '{DB_NAME}' -> id {db_id} ({ss.mart_uri})")

    ds_ids: Dict[str, int] = {}
    for table in TABLES:
        ds_ids[table] = ss.dataset(db_id, table)
        print(f"  dataset {table:<18} id {ds_ids[table]}")

    charts: List[Dict[str, Any]] = []
    for spec in _chart_specs(ds_ids):
        spec["dataset_id"] = ds_ids[spec["dataset"]]
        chart_id = ss.chart(spec)
        charts.append({"id": chart_id, "name": spec["name"]})
        print(f"  chart {spec['name']:<32} id {chart_id}")

    dash_id = ss.dashboard(charts)
    print(f"Dashboard '{DASHBOARD_TITLE}' -> id {dash_id}")
    print(f"Open {ss.url}/superset/dashboard/{DASHBOARD_SLUG}/")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
