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

Phase 2-3 artifacts (after the triage): `python -m app.pipeline.daily_amazon`, `python -m app.forecasting.quantiles`,
`python -m app.inventory.safety_stock`. Or run the whole LangGraph cycle from the Operations tab (admin).

### Preview environment (Emergent)

`backend/server.py` loads `backend/.env` (MONGO_URL, DB_NAME, EMERGENT_LLM_KEY, JWT_SECRET, CORS_ORIGINS, SEED_* passwords),
seeds the users `danny` / `admin` / `auditor` and serves the API on :8001; the Vite workbench runs on :3000 (`yarn start`).

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

### Workflow automation (playbooks)

The **Workflows** tab is a drag-and-drop editor for rescue playbooks (engine in `backend/app/workflows`,
API under `/api/v1/workflows` and `/api/v1/workflow-runs`). By default runs execute in-process and the
API's scheduler resumes expired waits. For durable runs (waits of days, approvals that survive restarts)
point the API at Temporal and start a worker with the same `STORE_DIR` / `MONGO_URL`:

```powershell
temporal server start-dev                                   # Temporal CLI dev server on localhost:7233
$env:TEMPORAL_ADDRESS="localhost:7233"
cd backend; python -m app.workflows.temporal_worker         # worker (task queue "playbooks")
# then start the API with the same TEMPORAL_ADDRESS
```

Settings: `TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`, `TEMPORAL_TASK_QUEUE`, `WORKFLOW_FOUR_EYES`.
`GET /api/v1/workflows-executor` reports which executor the API uses.

**Machine access (n8n, scripts, AI agents).** An admin creates an API key with
`POST /api/v1/admin/api-keys` (`{"name": "n8n", "role": "planner"}`; `auditor` = read-only). Keys only reach the
workflow endpoints. The n8n nodes live in `integrations/n8n-nodes-sop-workbench`.

**Template library and benchmark.** `GET /api/v1/workflows-templates` lists ready-made playbooks
(`rescue-basic`, `rescue-measured`, `rescue-escalation`) with your results; install one with
`POST /api/v1/workflows-templates/{id}/install`. Results of real runs are written to an insert-only,
hash-chained decision-outcome ledger (`decisions.db`; `agent.measure_outcome` records loss avoided
automatically). An admin can opt the tenant into the anonymised cross-company benchmark with
`PUT /api/v1/admin/workflows-settings {"share_benchmarks": true}`: give-to-get (only sharing tenants see
pooled figures) and shown only when at least `WORKFLOW_BENCHMARK_MIN_TENANTS` (default 3) companies contribute.

**MCP server for AI agents.** `POST /api/v1/workflows/mcp` is a streamable-HTTP MCP endpoint (JSON responses)
with tools `list_playbooks`, `get_playbook`, `list_runs`, `get_run`, `get_playbook_performance`,
`list_templates`, `draft_playbook`, `record_outcome`, `start_run` and, only when
`WORKFLOW_MCP_ALLOW_APPROVALS=true`, `decide_step`. `POST /api/v1/workflows/draft` turns a plain-language
description into a draft playbook (LLM when configured, template fallback otherwise); drafts are never saved
or run until a planner saves them. Point an MCP client (e.g. n8n's MCP Client Tool) at
`<api>/api/v1/workflows/mcp` with header `Authorization: Bearer <api key>`. `start_run` is a dry run unless the
agent passes `dry_run: false`; real runs keep their budget and approval gates. Browser `Origin`s must be in
`CORS_ORIGINS`. Outbound: the `action.n8n_webhook` node calls an n8n Webhook (set
`WORKFLOW_ALLOW_PRIVATE_WEBHOOKS=true` for n8n on localhost or a LAN; secrets via `WF_SECRET_<REF>`).

### Channels, automations and AI agents (no licence cost)

The **Channels** tab connects marketplaces, shops and suppliers through their own official APIs with your own
credentials; no paid aggregator, EDI network or AI subscription is involved.

| Connector | Covers | Notes |
|---|---|---|
| `shopify` | Shopify store (custom app) | orders, stock, prices, signed webhooks |
| `woocommerce` | WooCommerce or your own website | orders, stock, prices, signed webhooks |
| `amazon` | Amazon SP-API, all regions (private developer) | read-only for now: orders, FBA stock |
| `ebay` | all eBay sites (free developer keys) | orders, stock, prices |
| `mirakl` | any Mirakl-run marketplace (one connection each) | orders, stock, prices via offer imports |
| `edi` | suppliers and retailers over X12 | 850 out; 855/856/810/997 in; file drop (`outbox/`, `inbox/`) |

- Credentials are encrypted with a local key (`CHANNEL_VAULT_KEY`, or `var/channels/vault.key` is generated;
  back it up) and are never returned by the API. Admins add connections; planners sync, view and act.
- Sync runs on the scheduler per connection (`sync_minutes`). The first sync imports history silently; later
  changes raise events (`order_created`, `order_updated`, `inventory_changed`, `stock_low`, `edi_*`) that
  start workflows with a `trigger.channel_event` trigger. Shops can push events to the webhook URL shown in the tab.
- Prebuilt automations in the template library: **stock sync across all channels** (with a safety buffer and no
  ping-pong), **low-stock reorder from an EDI supplier** (purchase orders always need approval), and **new
  order to your warehouse** through an n8n webhook. Describe-a-playbook drafting knows all channel nodes.
- EDI files are moved to and from partners with any AS2/SFTP tool, for example the open-source Bots EDI
  translator run as a separate program.
- **Free local AI:** install [Ollama](https://ollama.com), run `ollama pull qwen2.5:14b`, then set
  `OLLAMA_MODEL=qwen2.5:14b` (and `OLLAMA_URL` if it is not on `localhost:11434`). The operations agent in the
  Channels tab, drafting and narratives then use the local model; prompts stay on your machine. The agent reads
  through the same tools as the MCP server (`list_channel_connections`, `get_channel_inventory`,
  `get_channel_orders`, ...) and only **proposes** changes, which a planner approves or rejects.

Remaining costs are the trader's own seller and store accounts and the machine this runs on.

### Overstock cover (pilot)

The **Overstock cover** tab prices and runs a guaranteed floor value on unsold stock of a purchase order.
Everything here is a pilot: quotes are indicative and non-binding, policies are internal commitments, and the
default terms (`GET /api/v1/cover/terms`) are placeholders that need actuarial calibration before real use.

1. **Quote** `POST /api/v1/cover/quotes`: demand from the platform forecast (`item_id`; planner consensus and
   model sigma) or manual input. Lognormal demand gives expected unsold units, claim probability and expected
   loss; cover is capped at the P80 recommended quantity, with an excess and coinsurance. Grades A-E; E is
   declined. The expected loss is scaled by the tenant's **experience factor** and the **recovery rate** is
   learned from resold stock, both credibility-weighted (pooled across sharing companies as the complement).
2. **Bind** `POST /api/v1/cover/quotes/{id}/bind`, optionally linking a rescue playbook.
3. **Monitor** `POST /api/v1/cover/policies/{id}/observations` (planner, ERP or n8n via API key): sell-through
   is compared with the seasonal forecast curve; a gap beyond the threshold starts the rescue playbook (not in
   the first 14 days, at most once per 30 days). The scheduler reminds planners when snapshots stop and admins
   when a policy is ready to settle.
4. **Settle** `POST /api/v1/cover/policies/{id}/settle` (admin, after the window): cap, excess and payout per
   unit; paid units open a **recovery lot**.
5. **Recover** `POST /api/v1/cover/lots/{id}/bids` (b2b, outlet or donation; buyers can use API keys) and
   `POST /api/v1/cover/lots/{id}/award` (admin). `GET /api/v1/cover/portfolio` reports premium, claims,
   recovered value, net loss ratio, A/E and open exposure.

Admins manage API keys and the benchmark sharing switch on the **Integrations** tab.

### 4. BI dashboards

The **BI dashboards** page renders the cycle KPIs and nine charts natively from a read-only SQLite mart
(`var/bi_mart.db`), so it works without any extra service:

```powershell
$env:PYTHONPATH="backend"; python -m app.bi.export_mart   # writes var/bi_mart.db
```

`GET /api/v1/bi/dashboard` returns every chart dataset in one call, `GET /api/v1/bi/status` reports the
mart and whether Superset is reachable, `GET /api/v1/bi/contracts` returns the declared mart schema
plus a live conformance report, and `POST /api/v1/bi/export` (admin) rebuilds the mart.
Settings: `SUPERSET_URL`, `SUPERSET_DASHBOARD`, `BI_MART_PATH`.

The mart schema is declared in `backend/app/bi/contracts.py` and is authoritative: the exporter creates
each table from the declared types, so an empty table keeps its real column types instead of collapsing
to `TEXT`. A build that cannot satisfy the contract fails before the file is written.

#### Tenant isolation

Every mart table carries a `tenant_id`, and a BI request is served through a connection whose temp views
shadow each table with a filter on that column (`backend/app/bi/tenancy.py`). The tenant is taken from the
verified session — the user record, then the JWT — never from a request parameter, so a caller cannot ask
to be served as someone else. A table with no `tenant_id` column is refused rather than served unscoped.

```powershell
$env:PYTHONPATH="backend"
python -m app.api.users add danny planner acme     # tenant defaults to "default"
python -m app.bi.export_mart                       # rebuilds the mart for the default tenant
```

**Upgrading an existing checkout:** the mart written before tenant isolation has no `tenant_id` column, so
every BI endpoint returns 403 until `python -m app.bi.export_mart` (or `POST /api/v1/bi/export` as admin)
has run. The rebuild drops and recreates each table whose schema has moved, so it is a migration, not a
routine build. `build_mart(tenant_id=...)` replaces only that tenant's rows, so a second tenant's build
leaves the first tenant's data intact.

#### Visual regression

`frontend/e2e/bi-visual.spec.js` captures 18 panel-level baselines with Playwright. Every API call is
stubbed and the clock is frozen, so the suite needs no backend and no mart:

```powershell
cd frontend
npm run test:visual            # compare against e2e/__screenshots__
npm run test:visual:update     # re-baseline after an intentional UI change
npm run test:visual:docker     # same run inside the pinned Playwright image
```

Review the diff images before accepting `--update-snapshots`; the suite proves a panel did not move, not
that the numbers in it are right.

#### AI privacy

Every prompt is redacted before it leaves the process (`backend/app/ai/privacy.py`). `client_from_env()`
wraps the whole provider stack in a `PrivacyClient`, so the narrative, grounded-ask and assistant paths are
covered at one choke point rather than at each call site. Email addresses, IBANs, payment card numbers, NI
numbers, UK phone numbers, UK postcodes and IPv4 addresses are replaced with a stable placeholder
(`[[EMAIL_1]]`) for the duration of one request and put back in the response, so the model never receives
the value while the planner still reads their own data. Provider error messages are redacted too, because
`/api/v1/assistant/providers` serves them to the browser.

`GET /api/v1/ai/privacy` reports the active rules, the provider declaration and the counters.

```powershell
$env:AI_ZDR_PROVIDERS="glmclient"   # providers whose zero-retention terms you have verified
$env:AI_REQUIRE_ZDR="true"          # refuse any provider not named above
```

ZDR is a declaration, not an attestation: nothing is declared by default, `zdr.attested` is `false`, and
with `AI_REQUIRE_ZDR` set and nothing declared no external call is made at all — the deterministic text is
used instead. Redaction is pattern-based, so person names in free-text comments are **not** detected.

#### FinOps and sprawl

Every governed metric query carries a cost tag — team, pipeline, environment and workload — and the tag is
a required argument of `semantics.evaluate`, so a query cannot be issued without one. Tags are appended to
`var/finops.db`; `GET /api/v1/bi/finops` reports attribution by team, pipeline and workload, the mart's
storage per table, the declared spend tiers, and the metrics and surfaces nobody used in the window. The
Analyst persona renders it as a FinOps panel.

```powershell
$env:AI_ASK_QUOTA="60"        # cap POST /api/v1/bi/ask per tenant per window (0 = off, the default)
$env:AI_ASK_WINDOW_S="3600"
$env:APP_ENV="staging"        # the environment dimension of the tag
```

There is no cloud warehouse here, so no dollar figure is computed: the $15k/month, $250k/year and
$30k/month tiers are declared policy, `active` and `measured_usd` are `null`, and the report says why.
Attribution covers the semantic layer and the BI read surfaces, not every statement the process issues.

#### Serving SLAs and columnar transport

Each mart table declares a freshness tier with an SLA (`realtime` 60 s, `hourly` 1 h, `daily` 1 d,
`weekly` 1 w) and each BI workload declares a latency target from the persona SLA matrix. `GET
/api/v1/bi/serving` compares the mart's actual age and the p95 measured in the FinOps ledger against
those targets, and reports which of the declared mart indexes are present. The mart is indexed on the
columns the governed queries filter by, leading with `tenant_id`, and `export_mart` creates them.

`GET /api/v1/bi/transport` encodes the same tenant-scoped tables three ways — JSON rows, Arrow IPC and
Parquet — and reports the bytes and encode time for each. `GET /api/v1/bi/export/columnar?table=…&format=arrow|parquet`
returns the bytes. Both read through the tenant-scoped connection, so an export can only contain the
caller's own rows. The Analyst persona renders the report and the comparison, and measures the
transport on demand rather than on page load.

```powershell
$env:BI_MART_PATH="var/bi_mart.db"   # the mart the serving report and the export read
```

There is no lakehouse, streaming ingestion, OLAP engine or Flight SQL server here, so the freshness
tier is a declared SLA rather than a delivery guarantee, the latency targets are measured against
SQLite, and the transport comparison is a same-process encode benchmark rather than a wire-protocol
speedup. Arrow is larger than JSON on a two-row table because of its schema message; the ratio is
reported per table for that reason.

#### Apache Superset (optional)

Superset can read the same mart for ad-hoc analysis; the workbench does not depend on it. See
`superset/README.md` for the full walkthrough.

```powershell
docker compose -f docker-compose.superset.yml up -d       # Superset on http://localhost:8088
python superset/bootstrap.py                              # DB connection, datasets, charts, dashboard
```

Without Docker, Superset runs from `.superset-venv` (`superset/README.md` → "Native install") and is
started with `.\superset\run_native.ps1`. After the bootstrap, link the charts to the dashboard with
`superset/link_charts.py` (the REST API does not populate `dashboard_slices`).

Settings (environment variables): `DATA_DIR`, `REPORTS_DIR`, `STORE_DIR`, `JWT_SECRET`, `TOKEN_MINUTES`, `CORS_ORIGINS`.
Data is read from `data/data/` by default.
