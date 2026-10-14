# Demand Forecast Co-Pilot (UK Consumer Goods S&OP)

See `DEVELOPMENT_PLAN.md` for scope, findings and roadmap.

## Quickstart

Python 3.9+ and Node 18+ (Docker is not required).

```powershell
pip install -r backend/requirements.txt
python -m pytest                                         # 144 backend tests
cd frontend; npm install; npm test; cd ..                # 17 frontend tests
```

### 1. Build the cycle artifacts (writes to `reports/`)

```powershell
$env:PYTHONPATH="backend"
python -m app.evaluation.report      # Gate G0 evaluation: stratified error, MinT/product-level comparison
python -m app.forecasting.report     # fallback + baseline proxy -> live_forecast_complete / live_baseline_proxy
python -m app.phasing.run            # delivery-lag profile, order-book bookings, floor backtest -> phasing_*
python -m app.triage.run             # tiers, risk flags, narratives -> triage_live / triage_monthly / triage_meta
```

### 2. Run the API (FastAPI, SQLite audit ledger in `var/`)

```powershell
$env:PYTHONPATH="backend"
$env:JWT_SECRET="<at least 32 random characters>"   # required for tokens that survive restarts
python -m app.api.users add danny planner            # prompts for a password (min 10 characters)
python -m app.api.users add auditor1 auditor
python -m uvicorn app.api.main:create_default_app --factory --port 8000
```

Roles: `planner` and `admin` can record decisions and commit; `auditor` is read-only and can verify the ledger;
only `admin` can reload data. Interactive docs: http://localhost:8000/docs.
Check a running server: `$env:LIVE_USER="danny"; $env:LIVE_PASSWORD="..."; python -m app.api.live_check`.
Load test (NF-01, 10 concurrent users): `python -m app.api.load_test http://127.0.0.1:8000 10 60`.
After changing the triage artifacts, restart the server or call `POST /api/v1/admin/reload` as admin.

### 3. Run the workbench

```powershell
cd frontend
npm run dev        # http://localhost:5173 (set VITE_API_URL if the API is not on :8000)
```

Settings (environment variables): `DATA_DIR`, `REPORTS_DIR`, `STORE_DIR`, `JWT_SECRET`, `TOKEN_MINUTES`, `CORS_ORIGINS`.
Data is read from `data/data/` by default.
