# Apache Superset integration

Superset charts the S&OP cycle from a **read-only SQLite mart** that the backend builds from the cycle
artifacts (`reports/`) and the audit ledger (`var/ledger.db`).

Superset is **optional**. The workbench **BI dashboards** page renders the same KPIs and nine charts
natively from the mart via `GET /api/v1/bi/dashboard`, so nothing below is required to use the BI page.
Follow this guide only if you want Superset's ad-hoc exploration on top of the mart.

## 1. Build the mart

```powershell
$env:PYTHONPATH="backend"; python -m app.bi.export_mart
```

Writes `var/bi_mart.db` with these tables:

| Table | Rows (current cycle) | Content |
| --- | --- | --- |
| `triage_items` | 9,162 | item-level triage: state, tier, baseline/ML plan, VaR, risk flags |
| `triage_monthly` | 109,944 | monthly plan vs baseline vs ML prediction |
| `quantile_bands` | 109,944 | P10/P50/P90 per item-month |
| `phasing_items` | 1,455 | order-book phasing |
| `safety_stock` | 5,499 | dynamic vs static safety stock and holding cost |
| `overrides` | 22 | audit-ledger decisions (planner, reason, financial impact) |
| `commits` | 0 | committed cycles |
| `fva_by_planner` / `fva_by_reason` | 1 / 4 | FVA scorecard |
| `cycle_summary` | 1 | KPI snapshot (auto-approved, exceptions, planned vs baseline value) |
| `quality_warnings` | 1 | incomplete-actuals warnings |

Admins can rebuild it from the workbench (**BI dashboards → Rebuild mart**) or with
`POST /api/v1/bi/export`.

## 2. Start Superset

```powershell
docker compose -f docker-compose.superset.yml up -d
```

- UI: http://localhost:8088 — `admin` / `admin` (override with `SUPERSET_ADMIN_PASSWORD`)
- The container mounts `var/bi_mart.db` at `/app/bi_mart.db` (read-only) and
  `superset/superset_config.py` at `/app/pythonpath/superset_config.py`
- Metadata lives in the `superset_home` volume (SQLite)

`superset_config.py` disables Talisman so the workbench can embed the dashboard in an iframe, and
enables the `EMBEDDED_SUPERSET` and `DASHBOARD_RBAC` feature flags.

## 3. Bootstrap the dashboard

```powershell
python superset/bootstrap.py
```

Idempotently creates:

- the **SOP Mart** database connection (`sqlite:////app/bi_mart.db`)
- one dataset per mart table
- charts: auto-approved SKUs, exceptions to review, items by triage state, exceptions by channel,
  value at risk by risk type, plan vs baseline by month, top exceptions, overrides by reason code,
  FVA by planner
- the **S&OP Overview** dashboard (`sop-overview`) laid out in rows of three

Re-running the script is safe: existing objects are reused, and the dashboard layout is refreshed.

Then link the charts to the dashboard (Superset's REST API stores the layout but not the
`dashboard_slices` association, so the dashboard would render empty):

```powershell
.\.superset-venv\Scripts\python.exe superset\link_charts.py     # native install
docker exec sop-superset python /app/pythonpath/link_charts.py  # Docker
```

## 3b. Native install (no Docker)

This machine has no Docker, so Superset 4.1.1 runs from an isolated venv:

```powershell
python -m venv .superset-venv
.\.superset-venv\Scripts\python.exe -m pip install --upgrade pip
.\.superset-venv\Scripts\python.exe -m pip install .\superset\stubs\python_geohash   # pure-Python geohash
.\.superset-venv\Scripts\python.exe -m pip install apache-superset==4.1.1
.\.superset-venv\Scripts\python.exe -m pip install "marshmallow==3.26.1"             # Superset 4.1 needs 3.x

$env:SUPERSET_CONFIG_PATH="d:\Case_study_2\superset\superset_config.py"
$env:SUPERSET_METADATA_URI="sqlite:///D:/Case_study_2/var/superset.db"
$env:SUPERSET_SECRET_KEY="sop-superset-dev-secret-change-me"
$env:FLASK_APP="superset"
.\.superset-venv\Scripts\superset.exe db upgrade
.\.superset-venv\Scripts\superset.exe fab create-admin --username admin --firstname Superset --lastname Admin --email admin@sop.local --password admin
.\.superset-venv\Scripts\superset.exe init
```

Start it with `.\superset\run_native.ps1` (sets the env vars and runs on `127.0.0.1:8088`).

Windows-specific notes:

- `python-geohash` has no Windows wheel and there is no C compiler; `superset/stubs/python_geohash`
  provides a pure-Python `encode`/`decode`/`bbox` (Superset only uses it in an example loader).
- `marshmallow` must stay on 3.x: Superset 4.1 passes `minLength` to `fields.List`, which 4.x rejects.
- `superset_config.py` sets `PREVENT_UNSAFE_DB_CONNECTIONS = False` so the read-only SQLite mart can be
  registered as a data source.
- `FLASK_APP=superset` is required when running the CLI outside the Superset source tree.

## 4. Open it

- Workbench → **BI dashboards** (embedded iframe + mart status)
- Direct: http://localhost:8088/superset/dashboard/sop-overview/

## Configuration

| Variable | Default | Used by |
| --- | --- | --- |
| `SUPERSET_URL` | `http://localhost:8088` | backend status endpoint, bootstrap |
| `SUPERSET_DASHBOARD` | `sop-overview` | backend status endpoint, bootstrap |
| `BI_MART_PATH` | `var/bi_mart.db` | backend status/export endpoints |
| `SUPERSET_MART_URI` | `sqlite:////app/bi_mart.db` | bootstrap (path inside the container) |
| `SUPERSET_ADMIN_USER` / `SUPERSET_ADMIN_PASSWORD` | `admin` / `admin` | compose + bootstrap |

## Notes

- The mart is rebuilt from artifacts, not live: rebuild it after a cycle run or a triage refresh.
- Superset never writes to the mart (`allow_dml: false`, file mounted read-only).
- Without Docker, install Superset natively (`pip install apache-superset`, then `superset db upgrade`,
  `superset fab create-admin`, `superset init`) and point `SUPERSET_MART_URI` at the local
  `var/bi_mart.db` path (`sqlite:///D:/Case_study_2/var/bi_mart.db`).
