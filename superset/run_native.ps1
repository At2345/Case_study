# Start Superset natively (no Docker) for the S&OP workbench.
# One-time setup is documented in superset/README.md ("Native install").
$root = Split-Path -Parent $PSScriptRoot
$env:SUPERSET_CONFIG_PATH = Join-Path $PSScriptRoot "superset_config.py"
$env:SUPERSET_METADATA_URI = "sqlite:///" + ($root -replace '\\', '/') + "/var/superset.db"
$env:SUPERSET_SECRET_KEY = "sop-superset-dev-secret-change-me"
$env:FLASK_APP = "superset"
& (Join-Path $root ".superset-venv\Scripts\superset.exe") run -h 127.0.0.1 -p 8088 --with-threads
