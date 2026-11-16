"""Superset configuration for the S&OP workbench integration (mounted at /app/pythonpath/superset_config.py).

Single-container local setup: SQLite metadata DB, the S&OP mart mounted read-only at /app/bi_mart.db,
and Talisman disabled so the workbench can embed the dashboard in an iframe.
"""
import os

SECRET_KEY = os.environ.get("SUPERSET_SECRET_KEY", "sop-superset-dev-secret-change-me")
SQLALCHEMY_DATABASE_URI = os.environ.get(
    "SUPERSET_METADATA_URI", "sqlite:////app/superset_home/superset.db")

# Local dev: allow the workbench (Vite on :5173) to embed dashboards in an iframe.
# CORS is intentionally not enabled: the iframe loads Superset's own pages same-origin.
TALISMAN_ENABLED = False
WTF_CSRF_ENABLED = True

# Local dev: allow the read-only SQLite mart as a data source (Superset blocks SQLite by default).
PREVENT_UNSAFE_DB_CONNECTIONS = False

FEATURE_FLAGS = {
    "EMBEDDED_SUPERSET": True,
    "DASHBOARD_RBAC": True,
    "ENABLE_TEMPLATE_PROCESSING": True,
}

# Keep the SQLite metadata DB responsive in a single container.
SUPERSET_WEBSERVER_TIMEOUT = 120
