"""Link the charts referenced by the dashboard layout to the dashboard itself.

Superset's REST API stores `position_json` but does not populate the `dashboard_slices` association
table, so `/api/v1/dashboard/<id>/charts` stays empty and the dashboard renders without charts.
Run this once inside the Superset environment after `bootstrap.py`:

    .superset-venv\\Scripts\\python superset\\link_charts.py
    docker exec sop-superset python /app/pythonpath/link_charts.py

Environment: SUPERSET_CONFIG_PATH, SUPERSET_METADATA_URI, SUPERSET_SECRET_KEY, SUPERSET_DASHBOARD.
"""
import json
import os

from superset.app import create_app

app = create_app()
with app.app_context():
    from superset import db
    from superset.models.dashboard import Dashboard
    from superset.models.slice import Slice

    slug = os.getenv("SUPERSET_DASHBOARD", "sop-overview")
    dash = db.session.query(Dashboard).filter_by(slug=slug).one()
    positions = json.loads(dash.position_json or "{}")
    chart_ids = [
        value["meta"]["chartId"]
        for value in positions.values()
        if isinstance(value, dict) and value.get("type") == "CHART"
    ]
    dash.slices = db.session.query(Slice).filter(Slice.id.in_(chart_ids)).all()
    db.session.commit()
    print(f"linked {len(dash.slices)} charts to dashboard '{dash.slug}'")
