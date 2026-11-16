# Project Overview — ML Demand Forecasting Co-Pilot for UK Consumer Goods S&OP

A complete guide to the system: what it does, how it works, what Danny does with it, and what problems it solves.

---

# 1. What the Project Is

The project is an **exception-based S&OP (Sales & Operations Planning) workbench** for a UK consumer-goods company. It takes ~33.5 million rows of historical data (20 Parquet tables: item masters, monthly demand, Amazon daily traffic/sell-out/inventory, the order book, promotions, lifecycle) and turns them into a **monthly planning pipeline** that a single planner can run.

Instead of reviewing 9,173 product-channel items every month, the planner reviews **25–100 exceptions** — the items where the machine forecast disagrees with the baseline by a material amount of money. Every decision is **audited** (who, what, when, why) and **scored with FVA** (Forecast Value Added) so the planner's judgement can be measured, not just trusted.

The system has grown beyond the core workbench into a full **daily task hub and co-pilot**: a chatbot that answers questions from the live data, 12 one-click automation tasks, what-if scenario simulations, an executive briefing generator, and MCP integrations that let the assistant pull live data from external ERP, CRM and Jira systems.

---

# 2. Technical Terms Explained

| Term | Meaning |
|---|---|
| **S&OP** | Sales & Operations Planning — the monthly cross-functional process that agrees one demand plan (the "consensus") for the next 12–24 months. |
| **S&OE** | Sales & Operations Execution — the daily/weekly layer that executes the plan (Amazon PO confirmations, Buy Box watch, OTIF). |
| **Item vs product** | A *product* is a physical SKU (3,560). An *item* is a product × channel combination (9,173), e.g. "Stand Mixer on Amazon". |
| **Cutoff** | The date the monthly forecast cycle is frozen. Everything known up to the cutoff is input; everything after is forecast. |
| **Horizon (h1, h2…)** | Forecast months ahead. h1 = next month, h12 = a year out. |
| **FPE / WAPE** | Forecast Percentage Error / Weighted Absolute Percentage Error — accuracy metrics. 98% FPE means the forecast is off by ~98% of actual demand on average (typical for long-tail intermittent items). |
| **Bias** | Systematic over- or under-forecasting. The legacy baseline over-forecasts by +28.9%; the ML forecast by only +3.1%. |
| **FVA** | Forecast Value Added — measures whether a planner's override actually improved the forecast vs the machine. Positive FVA = the planner added value. |
| **Baseline** | Danny's legacy statistical forecast (seasonal naive + 12-month mean proxy in the live cycle). |
| **ML forecast** | The machine-learning forecast (pre-computed per item, 12 months ahead). |
| **Reconciliation** | Making item-level forecasts add up exactly to product-level totals (hierarchy summing matrix). |
| **Triage / tiers** | Automatic routing of every item into: **Autopilot** (touchless), **Collaborative** (machine + planner), **Planner-led** (Danny decides). |
| **Exception queue** | The short list of items (25–100) where ML and baseline disagree by more than a £ threshold — the only items Danny must review. |
| **Value at risk** | `|ML − baseline| × sales price` — the £ impact of the disagreement. |
| **Override** | A planner's audited change to a forecast, with a mandatory reason code. |
| **Consensus** | The final agreed plan after overrides — the number that goes to supply, finance and the ERP. |
| **OTIF** | On-Time In-Full — the B2B service metric (delivered on time and complete). |
| **OOS / Buy Box** | Out-of-stock; the Amazon Buy Box is the "Add to Cart" slot — losing it collapses sales. |
| **Chargeback** | Amazon's penalty (3–9% of COGS) for missing the 24h PO-confirmation window or OTIF fines. |
| **Conformal band (P10/P90)** | A statistically calibrated uncertainty range around each forecast (80% target coverage). |
| **Safety stock** | Buffer stock; the system computes it dynamically from forecast uncertainty + lead time. |
| **NPI / EOL** | New Product Introduction / End of Life — lifecycle phases with special rules. |
| **FVA scorecard** | The page showing whether overrides improved accuracy, plus pending overrides awaiting actuals. |
| **Cycle run** | One execution of the monthly pipeline (ingest → forecast → rules → triage → explain), tracked node-by-node. |
| **MCP** | Model Context Protocol — a standard for connecting the assistant to external systems (ERP, CRM, Jira) so it can call their tools. |
| **SSE** | Server-Sent Events — the streaming channel the co-pilot uses to stream answers token-by-token. |
| **JWT / RBAC** | JSON Web Tokens for login; Role-Based Access Control (planner / admin / auditor). |
| **Ledger** | The insert-only, hash-chained audit store for overrides — edits are detectable. |

---

# 3. Use Cases and User Stories

## Danny — the planner (main user)

- **"As Danny, I want to see only the items that matter, so I don't spend a week reviewing 9,173 rows."**
  The workbench shows the exception queue (value-at-risk sorted), filters by channel/ABC/risk, and one-click detail panels.
- **"As Danny, I want to know why the machine thinks what it thinks, so I can trust or challenge it."**
  Every item has a telemetry card (history, Amazon signals, promo overlap, lifecycle phase) and a deterministic narrative; the co-pilot explains in plain language.
- **"As Danny, I want my changes to be recorded, so nobody can dispute the plan later."**
  Every override requires a reason code and is written to the hash-chained ledger; the audit badge shows the count.
- **"As Danny, I want to know if my judgement actually helps, so I can improve."**
  The FVA scorecard shows per-planner, per-category accuracy deltas — and pending overrides while actuals are still arriving.
- **"As Danny, I want my daily checks done for me, so I can focus on decisions."**
  The co-pilot task hub runs 12 automations: Amazon PO confirmations, OTIF exposure, Buy Box watch, promo sanity, safety stock, value at risk, and more — each returns a plain-language summary in under 2 seconds.
- **"As Danny, I want to ask questions in plain English, so I don't need to dig through tables."**
  "What open purchase orders are in SAP?", "Which items are at risk of OOS?", "Explain the exception queue" — answered from live data with source chips and follow-up suggestions.
- **"As Danny, I want to test 'what if' before committing, so I don't commit a bad plan."**
  Scenario simulation: shift a promo, constrain a component, change a price — see revenue, fill rate and safety stock impact instantly.
- **"As Danny, I want to hand the plan to the business without rebuilding spreadsheets."**
  One-click consensus commit, executive briefing PDF, and (via MCP) pushing the plan to ERP.

## Admin

- **"As admin, I want to manage who can do what, so the plan can't be changed by accident."**
  RBAC: planner can review/override, admin can manage users and MCP servers and re-run failed cycle nodes, auditor can only read.
- **"As admin, I want to configure external system connections, so the co-pilot can use live ERP/CRM/Jira data."**
  The MCP page: add servers, test connections, discover tools, mask secrets, and watch the audit log of every tool call.

## Auditor / Finance

- **"As auditor, I want to verify the plan is defensible, so the numbers survive scrutiny."**
  Read-only access to the ledger, the audit log, cycle runs, and the FVA scorecard; `/api/v1/audit/verify` detects any tampering with the hash chain.

---

# 4. How the Project Facilitates Danny's Workload — Detailed

The system attacks the **60–70% of a planner's hours lost to data hunting, reconciliation and spreadsheet prep**. Concretely:

1. **From 9,173 rows to ~67 exceptions.** The triage engine routes 84% of items to autopilot (touchless), leaving a queue of ~67–74 items (about 21% of volume) that genuinely need a human. Danny reviews exceptions, not the catalogue.
2. **Daily checks run themselves.** Amazon PO confirmations (24h chargeback window), Buy Box/glance-view watch, OTIF exposure — each is a scheduled automation that lands as an in-app notification with a deep link. Danny reacts to alerts instead of polling dashboards.
3. **Answers in seconds, not queries.** The co-pilot answers from live context (queue, OTIF, safety stock, FVA, cycle status, external MCP systems) with source chips so Danny can verify. Suggested follow-up chips keep the conversation going.
4. **One-click task runs.** Each of the 12 fast tasks executes in ≤1.4 s (after performance work) and returns a plain-language summary plus navigation actions — e.g. "Exception queue review" opens the filtered workbench queue.
5. **Decisions are audited and scored.** Every override needs a reason code and lands in the hash-chained ledger; FVA tells Danny whether his overrides beat the machine (and by how much). This turns judgement into a measurable, improvable skill.
6. **What-if without Excel.** Scenario simulation and promotion of a scenario into audited ledger decisions before commit — days of Excel rebuilding compressed into minutes.
7. **The plan hands itself over.** Consensus commit, executive briefing PDF, and ERP push (via MCP) replace the end-of-month spreadsheet marathon.
8. **External systems are reachable.** MCP connects the co-pilot to ERP (open POs, stock levels, material master), CRM (accounts, opportunities, cases) and Jira (issues) — with every tool call audited and write tools admin-gated.

---

# 5. Which Tasks the Project Runs and What They Return

## The monthly cycle (orchestrated pipeline, node-by-node)

| Node | What it does | Output |
|---|---|---|
| Ingest & validate | Loads the 20 tables, schema checks, data-quality gates (e.g. missing Amazon Feb feed) | Validation report in `cycle_runs` |
| Forecast | Loads ML predictions; statistical fallback for uncovered/discontinued items | Forecast per item, 12 months, tagged `source` |
| Reconcile | Sums item forecasts to product level (hierarchy matrix) | Coherent product + item plan |
| Rules | EOL run-out decay, NPI run-rate, ghost-promo, Amazon guard, order-book floor | Risk flags per item |
| Triage | Value-at-risk scoring, tier routing (autopilot/collaborative/planner-led) | Exception queue + tiers |
| Explain | Deterministic narratives (+ LLM when configured) | Item narratives, cached |
| Bands & safety stock | Conformal P10/P90 bands, dynamic safety stock | Band coverage, SS units, holding-cost saving |
| Commit | Freezes the consensus after Danny's approvals | Consensus export, ledger entries |

## The 12 co-pilot fast tasks (task hub)

| Task | Rhythm | Returns |
|---|---|---|
| Amazon PO confirmations | S&OE daily | Open POs inside the 24h window, ATP deficits, chargeback £ at risk |
| OTIF exposure | S&OE daily | B2B months the plan under-covers, penalty £ at risk |
| Buy Box & glance-view watch | S&OE daily | A-item ASINs with >25% glance drop / OOS suppression |
| Exception queue review | S&OP weeks 1–2 | Largest value-at-risk exceptions needing a decision |
| Data-quality gate | S&OP weeks 1–2 | Schema/completeness warnings that could invalidate the baseline |
| Promo sanity check | S&OP weeks 1–2 | Promotions with uplift >3× historical average |
| Safety stock & bands | S&OP week 3 | Dynamic vs static SS, holding-cost reduction, band coverage |
| Value at risk | S&OP week 3 | Planned vs baseline £ value; P&L-moving exceptions |
| Scenario what-if | S&OP week 3 | Simulated promo/constraint/price changes → revenue, fill rate, SS deltas |
| Consensus commit readiness | S&OP week 4 | Pending vs decided items; is the plan ready to commit? |
| Executive briefing | S&OP week 4 | Monthly executive S&OP brief (PDF download) |
| FVA & autopilot | S&OP week 4 | FVA scorecard + low-risk ML-acceptance recommendations |

## The co-pilot assistant

- Answers from live context with **source chips** (project data, documents, MCP external systems, general knowledge — each labeled).
- Runs tasks on request ("run the promo sanity check"), explains tasks, and suggests follow-up questions.
- Falls back deterministically when the LLM is down — never a 500, never a fabricated number (every quoted number must exist in the evidence).

## MCP external-system calls (admin-configured)

- ERP: open purchase orders, stock levels, material master, supplier lead times.
- CRM: accounts, opportunities, cases, forecast commit.
- Jira: search issues, get issue.
- Every call is audited (who, tool, args, latency, ok/error); write tools are admin-only.

---

# 6. Business Scheme — From Data Points to Final Summary (with Danny's Approvals)

```
DATA POINTS (20 Parquet tables, ~33.5M rows)
  items · baseline_items · target_time_series · backtest/live_predictions
  orderbook · amazon_traffic/sell_out/inventory/forecast · promotions
  lifecycle · predecessors · covariates · error metrics
        │
        ▼
INGEST & VALIDATE ──► quality gates (missing feeds, imputed share)
        │
        ▼
FORECAST (ML + statistical fallback) ──► RECONCILE to product level
        │
        ▼
BUSINESS RULES (EOL decay · NPI · ghost-promo · Amazon guard · order-book floor)
        │
        ▼
TRIAGE (value-at-risk scoring)
        ├── Autopilot tier  (~84% of items) ──► auto-approved, touchless
        ├── Collaborative tier ──► machine + planner
        └── Planner-led tier + EXCEPTION QUEUE (67–74 items)
                    │
                    ▼
        DANNY REVIEWS THE QUEUE
        ├── Accept ML forecast ──────────────┐
        ├── Override with reason code ───────┤ (hash-chained ledger, audited)
        └── "Raise plan to order book" ──────┤ (one-click, audited action)
                    │                        │
                    ▼                        ▼
        FVA SCORING (did Danny's judgement add value?)
                    │
                    ▼
        CONSENSUS COMMIT (Danny's approval freezes the plan)
                    │
                    ├──► Consensus export (CSV) + ERP push (MCP)
                    ├──► Executive briefing PDF (waterfall: AOP vs baseline vs commercial vs consensus)
                    └──► Scenario promotion (what-if → audited ledger decisions)
                    │
                    ▼
        SUPPLY & FINANCE EXECUTE (safety stock, OTIF, Amazon POs)
```

**Danny's approval points (all audited):**
1. **Exception review** — accept or override each queued item (reason code mandatory).
2. **Order-book floor** — one-click "Raise plan to order book" when bookings exceed the plan.
3. **Scenario promotion** — promote a what-if scenario into ledger decisions before commit.
4. **Consensus commit** — the final approval that freezes the cycle and triggers exports/briefing/ERP push.
5. **Autopilot acceptance** — FVA & autopilot recommendations Danny can accept for low-risk items.

---

# 7. Which Problems the Project Solves in Supply & Operations

## Supply-side problems

- **Stockouts (OOS) and Buy Box loss** — daily Amazon monitoring alerts on >25% glance-view drops and OOS suppression before revenue is lost.
- **Chargebacks (3–9% of COGS)** — the 24h Amazon PO-confirmation reconciler flags deficits and recommends partial confirmations before the window closes.
- **Excess safety stock** — dynamic safety stock (band sigma × lead time) cuts A-item holding cost by ~69% vs the static rule while keeping ~97% service.
- **OTIF penalties** — B2B order-book months the plan under-covers are surfaced with the penalty £ at risk.
- **Order-book blind spots** — bookings known at the cutoff are a lower bound on demand; the plan is raised to the order book when bookings exceed it (worth ~£90k/month in the measured cycle).
- **EOL run-out** — discontinued items still carry ~8% of volume; calibrated run-out decay replaces the old "hard zero" that under-forecast real sales.

## Operations / planning problems

- **Review overload** — 9,173 items compressed to a ~67-item exception queue (84% touchless).
- **Systematic over-forecasting** — legacy baseline bias +28.9% vs ML +3.1%; the queue ranks disagreements, not errors.
- **Missing/incomplete data** — data-quality gates block cycles on missing feeds (e.g. Amazon Feb actuals) instead of silently corrupting the baseline.
- **Unaccountable judgement** — every override is reason-coded, hash-chained, and FVA-scored; auditors can verify the chain.
- **Spreadsheet hand-offs** — consensus export, executive briefing PDF, and ERP push replace manual reformatting.
- **External-system blindness** — MCP connects the co-pilot to ERP/CRM/Jira so "what's the stock level?" is answered from the live system, not a stale export.

---

# 8. Tech Stack Summary

| Layer | Technology |
|---|---|
| **Data** | Parquet (~33.5M rows), pandas + pyarrow, DuckDB (30M-row Amazon daily tables via SQL pushdown), Polars |
| **Backend** | Python 3.9, FastAPI, Pydantic v2, LangGraph (cycle orchestration), scipy (reconciliation/optimisation) |
| **Persistence** | MongoDB 7 (production) with local JSON/SQLite fallbacks; insert-only hash-chained override ledger; SQLite FTS5 for doc search |
| **LLM** | Gemini (primary), GLM (fallback), local flan-t5 (offline) via a failover client; deterministic template fallback always |
| **Auth** | JWT + RBAC (planner / admin / auditor) |
| **Frontend** | React 18, Vite, Tailwind CSS, Vitest + React Testing Library |
| **MCP** | JSON-RPC 2.0 over streamable HTTP/SSE; demo ERP/CRM/Jira servers; masked secrets; full call audit |
| **Ops** | Scheduler (daily/monthly UK time), in-app notifications, admin re-run of failed nodes, load-tested API (P95 < 400 ms at 10 users) |
| **Testing** | 194+ backend tests, 25 frontend tests; production build green |

---

# 9. Danny's Guided Walkthrough — Working with the Automation

This is the concrete "what do I click, in what order" tour of the website, aligned with the S&OP rhythm. The automation does the heavy lifting; Danny's job is **review, decide, approve** — never data hunting.

## 9.1 Every morning — the S&OE daily loop (10 minutes)

1. **Sign in as `danny`** (planner role) on the login screen.
2. **Check the notification bell** (top-right of the header). A red badge with a number means the automations found something overnight (Amazon PO window closing, Buy Box alert, OTIF exposure, a cycle node finished). Click a notification — it deep-links you straight to the relevant page (Operations run detail, Workbench item, etc.).
3. **Open the Co-pilot page** (nav menu). This is the daily task hub.
4. **Filter the fast tasks**: set rhythm = *S&OE daily*, channel = *Amazon* (or B2B). The grid re-renders to exactly the tasks for that rhythm.
5. **Run the daily tasks** — click **Run** on:
   - *Amazon PO confirmations* — the reconciler checks every open PO against available-to-promise; if a line can't be fully confirmed it recommends a partial confirmation **before the 24h chargeback window closes**.
   - *Buy Box & glance-view watch* — alerts on A-item ASINs with a >25% glance-view drop over 3 days or OOS suppression.
   - *OTIF exposure* — B2B order-book months the consensus plan under-covers, with the penalty £ at risk.
6. **Read the result**: each run posts a plain-language summary into the chat as an assistant message, with **action chips** underneath (e.g. "Review item 103125", "Open OTIF queue"). Click a chip to jump to the filtered view.
7. **Act only on what's urgent**: the task cards show a pulsing "N urgent" badge when the automation found something that needs Danny today. Everything else is already handled.

**What Danny does NOT do:** poll dashboards, reconcile spreadsheets, or manually check Amazon feeds. The automations run on schedule and *push* the findings to him.

## 9.2 Weeks 1–2 — S&OP preparation (the exception review)

1. **Co-pilot → run *Data-quality gate*** first. If the cycle has warnings (e.g. an imputed Amazon feed), the QualityBanner at the top of the Workbench shows them; a blocked cycle is better than a silently corrupted baseline.
2. **Co-pilot → run *Exception queue review***. The result chip opens the **Workbench** with the queue pre-filtered to the items that still need a decision.
3. **Workbench — the decision loop** (repeat per exception):
   - The **exception table** shows value-at-risk (£), baseline vs ML vs current final, sorted by impact. Use the filters (channel / ABC / risk type) to focus.
   - Click an item → the **DetailPanel** opens on the right:
     - Read the **narrative** (why this item is flagged).
     - Click **Explain with AI** for a plain-language walkthrough of the signals.
     - Check the telemetry: history chart with P10/P90 band, Amazon signals (glance MoM, p70, OOS), order-book phasing, lifecycle phase.
     - Decide:
       - **Accept the ML forecast** — no action needed; the item stays as-is.
       - **Override** — enter the final quantity, pick a **reason code** (mandatory), submit. This writes an audited, hash-chained ledger entry with your name and the £ impact.
       - **Raise plan to order book** — one click when bookings at the cutoff exceed the plan (the automation flags `ORDER_BOOK_EXCEEDS_PLAN`); audited with reason "Order Book Floor Applied".
   - The **audit badge** on the panel shows your decision is recorded.
4. **Co-pilot → run *Promo sanity check*** — promotions with uplift >3× the historical average get flagged for KAM justification before they enter the baseline.

**What Danny does NOT do:** review 9,173 items. The triage already routed ~84% of items to autopilot; only the ~67–74 exceptions reach this screen.

## 9.3 Week 3 — financialization, safety stock and what-if

1. **Co-pilot → run *Value at risk*** — the £ view of planned vs baseline; the items that move the P&L.
2. **Co-pilot → run *Safety stock & bands*** — dynamic vs static safety stock, holding-cost reduction (~69% on A-items) and band coverage. Drill into **Supply & Risk** for the full tables (band calibration history, lead times, OTIF by customer).
3. **Scenarios page** (nav menu) — the what-if sandbox:
   - **Generate** a scenario from a prompt (e.g. "15% B2B upside for high-margin products next month").
   - **Compare up to 3 versions** side by side (revenue, margin, OTIF exposure, safety stock deltas).
   - When one wins, click **Promote to ledger** — the scenario's month-1 quantities become audited overrides (reason "Commercial Intelligence (Account Feedback)") **before** commit. Nothing is promoted silently.

## 9.4 Week 4 — commit and hand-off

1. **Co-pilot → run *Consensus commit readiness*** — pending vs decided items; the automation tells you if the plan is ready.
2. **Co-pilot → run *FVA & autopilot*** — the scorecard plus low-risk ML-acceptance recommendations. Accept the ones you agree with; every acceptance is audited.
3. **Commit the consensus** — the **Commit consensus** button in the header (enabled only when every exception is decided). This freezes the cycle and triggers the hand-offs.
4. **Hand off**:
   - **Export draft plan** (header) — the consensus CSV.
   - **Executive briefing** (header) — the monthly PDF for leadership.
   - **MCP ERP push** (admin) — the approved plan pushed to the ERP adapter; safety stock and reorder points recomputed from the P10–P90 spread.
5. **FVA & audit page** — after actuals arrive, the scorecard shows whether Danny's overrides added value (per planner, per category). This is the feedback loop that makes his judgement measurable.

## 9.5 Asking the co-pilot (any time)

- Type a question: *"What open purchase orders are in SAP?"*, *"Which three exceptions carry the most value at risk?"*, *"How big is the OTIF exposure?"*, *"Explain the promo sanity check"*.
- The answer streams in with a **source chip** (project data / document / MCP external system / general knowledge) so you know what it's grounded in, plus **follow-up suggestion chips** to continue.
- Every quoted number is checked against the evidence; if the LLM is down, a deterministic answer takes over — the system never invents figures.

**The division of labour in one line:** the automation *finds, computes, alerts and drafts*; Danny *reviews, decides and approves* — and every decision is recorded and scored.

---

# 10. Presenting the Demo to Danny — Presenter's Guide

## 10.1 Setup checklist (before Danny arrives)

- [ ] Backend running: `python -m uvicorn server:app --host 127.0.0.1 --port 8000` (from `backend/`)
- [ ] Frontend running: `npm run dev` (from `frontend/`)
- [ ] Demo MCP servers running: `python -m app.mcp.demo --kind erp --port 9101` (and crm 9102, jira 9103 if you'll show them)
- [ ] MCP server configured in the UI (admin) pointing at `http://127.0.0.1:9101/` — test the connection so it shows discovered tools
- [ ] A monthly cycle has been run (Operations page shows a run with all nodes green) so the Workbench, Supply & Risk and FVA pages have real data
- [ ] FVA demo rows seeded automatically at backend startup (`app/seed_demo_fva.py`): historical overrides for months with actuals, so the FVA scorecard shows matured, scored rows. Reset/refresh with `python -m app.seed_demo_fva --reset` (from `backend/`)
- [ ] Demo accounts ready: `danny` (planner), `admin`, `auditor` — passwords from the local `.env` (`SEED_PLANNER_PASSWORD` etc.)
- [ ] Browser open on the login page; close any other tabs

## 10.2 The 25-minute script

**Scene 1 — The problem (2 min).** Open the Workbench. Show the summary cards: 9,173 items in the catalogue vs ~67 exceptions in the queue. Say: *"Today you review 9,173 rows. With this system you review the ~67 items where the machine and the baseline disagree by real money — and everything you decide is audited."*

**Scene 2 — The daily loop (5 min).** Go to Co-pilot. Show the notification bell (a real alert if one exists), then the task hub: set rhythm = S&OE daily, run *Amazon PO confirmations*. The answer lands in the chat with action chips. Say: *"This is your 10-minute morning: the automation checked every open PO against the 24h chargeback window and tells you exactly which lines need a decision — with the £ at risk."* Click a chip to show the deep link.

**Scene 3 — The exception review (6 min).** Run *Exception queue review* from the task hub → it opens the Workbench queue. Open one item in the DetailPanel: read the narrative, click **Explain with AI**, show the band chart and the Amazon signals. Then **override** with a reason code and show the audit badge appear. Say: *"Every override is hash-chained — an auditor can verify nobody changed it afterwards. And in a month, FVA tells you whether your judgement beat the machine."*

**Scene 4 — The what-if (4 min).** Open Scenarios. Generate one from a prompt, compare 2–3 versions, then **Promote to ledger**. Say: *"What used to take days of Excel rebuilding is now minutes — and the promoted scenario lands as audited decisions, not a side spreadsheet."*

**Scene 5 — The hand-off (4 min).** Show **Commit consensus** (header), then **Export draft plan** and **Executive briefing**. Say: *"The plan hands itself over: CSV for supply, PDF for leadership, and the ERP push via MCP."*

**Scene 6 — External systems (4 min).** As `admin`, open **MCP**. Show the configured ERP server, test the connection, list the discovered tools, and show the audit log of real calls. Then back to Co-pilot as `danny`: ask *"What open purchase orders are in SAP?"* — the answer carries the emerald `mcp:erp_...` source chip. Say: *"The co-pilot reads your live ERP, CRM and Jira — and every call is audited; write tools are admin-only."*

## 10.3 Key messages to land

- **Exceptions, not rows** — 9,173 items → ~67 decisions; 84% of items run touchless.
- **The machine is honest** — the ML forecast beats the legacy baseline on bias (+3.1% vs +28.9%) and accuracy at product level; the queue ranks disagreements, not errors.
- **Judgement becomes measurable** — reason-coded overrides, hash-chained ledger, FVA scorecard. Danny's experience is now an asset the system *scores*, not a black box.
- **The daily grind is automated** — PO confirmations, Buy Box, OTIF run themselves and push alerts to him.
- **Nothing is silent** — data-quality gates block bad cycles; the LLM never invents numbers; every external call is audited.

## 10.4 Pitfalls to avoid

- **Don't quote the brief's headline numbers** (98% → 41% MinT, £25.49 D2C margin) — the measured values differ (see `DEVELOPMENT_PLAN.md` Section 3). Use the measured story: ~6 pts product-level FVA, bias +3.1% vs +28.9%, ~69% holding-cost reduction.
- **Don't demo LLM-dependent features without network/quota** — if Gemini/GLM are down, the deterministic fallback still answers; say so explicitly ("the system degrades gracefully") rather than hiding it.
- **Don't skip the demo ERP server** — the MCP scene fails without it. Verify `9101` is up and the server config exists before Danny arrives.
- **Don't show the auditor role for Danny-facing flows** — use `danny` for the workbench and `admin` only for MCP/user management.
- **Don't run a fresh monthly cycle during the demo** — it takes minutes; use the pre-run cycle so every page is populated.
- **Don't claim FVA is scored daily** — actuals are monthly, so a March override can only be scored when March actuals arrive. The demo seed solves this by adding *historical* overrides (tagged `demo-seed` in the ledger) for months that already have actuals: the scorecard shows real scored rows (e.g. FVA ≈ +2.9 pts, FPE planner 3.1% vs ML 6.0% vs baseline 24.0%) plus the pending table for current-cycle decisions. Frame it as *"the engine scores every override the moment its month closes — here are last quarter's scored decisions, and these are the ones awaiting March actuals"*.

---

*Companion documents: `DEVELOPMENT_PLAN.md` (architecture, data audit, findings), `ENHANCEMENT_PLAN.md` (co-pilot, task hub, automations, MCP), `backend/app/mcp/README.md` (MCP integration guide).*
