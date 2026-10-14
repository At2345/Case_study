# Development Plan: ML Demand Forecasting Co-Pilot for UK Consumer Goods S&OP

Scope: turn the Parquet data in `data/data/` into the exception-based S&OP workbench described in the brief (multi-agent core, FastAPI + MongoDB, React workbench, FVA governance).
Basis: the brief plus a profile of all 20 Parquet files (schemas, row counts, backtest recomputation). Numbers marked **[measured]** come from the data; numbers marked **[brief]** come from the brief and are not yet reproduced.

---

## 1. Executive Summary

- **What we build:** a monthly pipeline (ingest, forecast, reconcile, apply rules, triage, explain) that feeds a planner workbench. The planner reviews roughly 25-60 exceptions instead of 9,173 items. Every override is audited and scored with FVA.
- **Biggest finding:** several brief claims do not match the data (Section 3). The 98.09% FPE is reproducible, but the segment table (26.4% FPE on Amazon A-items, 41.2% after MinT) is not. Sprint 1-2 therefore re-derives the evidence base before any adoption claim is made.
- **Second finding:** the live ML forecast covers all 3,278 active items but skips 5,336 discontinued items, which still carry about 8% of last-12-month volume (run-out sales). No legacy baseline exists for the live cycle. Sprint 4 added a statistical fallback and a stand-in baseline for both gaps.
- **Third finding:** the order book already holds the answer for the near months. The target counts demand in the month the material is available, a third of B2B units ship a month later (97% of those available in the last 5 days of a month), and bookings known at the cutoff are a lower bound on demand that ML sometimes misses (D26-D28).
- **Status (Section 12):** the Phase 1 scope is built as a working prototype on the real data (evaluation, fallback, triage, API, audit ledger, workbench, allocation simulator, order-book phasing): 144 backend and 17 frontend tests pass. Open: LLM narratives (WS-F), MinT-based disaggregation of committed plans, production pipeline (Mongo, scheduling), and business sign-off on the open questions.
- **Delivery:** 4 phases / 24 two-week sprints, as in the brief, with a "data truth" gate inserted at the start of Phase 1.

---

## 2. Data Audit (`data/data/*.parquet`)

Total about **33.5M rows** across 20 files **[measured]**. The brief says "over 35M"; NF-04's SLA should be written against about 34M rows.

### 2.1 Table inventory

| Table | Rows | Grain / key | Role in the system |
|---|---|---|---|
| `items` | 9,173 | `item_id` (product x channel); 3,560 `product_id`s | Item master: channel, ABC, lifecycle flags, `purchase_price`, `sales_price` |
| `baseline_items` | 3,560 | `level_id` | Product-level master (Danny's planning grain) |
| `baseline_mapping` | 9,173 | `level_id` to `item_id` | Hierarchy bridge for the summing matrix **S** |
| `target_time_series` | 381,173 | `item_id` x month (2018-09 to 2026-02) | Actuals + `imputed` + `exposure` |
| `baseline_target_time_series` | 163,618 | `level_id` x month | Product-level actuals + exposure |
| `baseline_forecast` | 179,639 | `level_id` x `cutoff` x month | Danny's legacy statistical baseline (2,070 of 3,560 levels) |
| `backtest_predictions` | 270,324 | `item_id` x `cutoff` x month; model `ml_model` | ML backtest, 5 cutoffs (2024-11, 2025-01, 04, 07, 11), horizons 1-12 |
| `live_predictions` | 46,044 | `item_id` x month (2026-03 to 2027-02) | Current ML forecast (3,837 items) |
| `overall_error_metrics` / `item_error_metrics` | 1 / 6,042 | portfolio / item | FPE 98.09%, bias +6.45%, n_rows 152,499 |
| `past_covariates` / `future_covariates` | 381,173 / 110,076 | `item_id` x month | Monthly Amazon aggregates (past); deal days, p70, `months_since_launch`, `months_to_eol` (future) |
| `amazon_traffic` | 11.9M | `item_id` x day | `glance_views` |
| `amazon_sell_out` | 8.4M | `item_id` x day | `ordered_units` |
| `amazon_inventory` | 8.5M | `item_id` x day | OOS rate, open PO units, vendor confirmation rate, on-hand |
| `amazon_forecast` | 514k | `item_id` x week x generation date | Amazon p70 (2,960 items) |
| `orderbook` | 2.75M | `order_id` line | Dates, quantities, cancellation fields (25 distinct customers) |
| `promotions` | 7,052 | `promotion_id` x `item_id` | 6,673 `marketplace_deal` (Amazon), 379 `retailer_campaign` (B2B) |
| `predecessors` | 889 | `item_id` to `predecessor_item_id` | NPI analogies (`split_weight` is 100% null) |
| `lifecycle` | 9,135 | `item_id` x start/end | Active windows (3,349 null `end_date`) |

### 2.2 Structural facts that shape the design **[measured]**

- **Channels:** B2B wo Amazon 3,491 items, Amazon 3,081, D2C 2,601. Most products exist in 3 channels (2,427), 759 in 2, 374 in 1.
- **ABC class:** 3,815 items (41.6%) have no class; the rest are A 1,439, B 1,936, C 1,727 and **N 256** (the brief does not mention `N`; it likely means new).
- **Intermittency:** 50.3% of target rows are zero and 44.4% are `imputed`. Amazon items are worst (55% imputed, 64% zero).
- **Censoring:** 43.8% of target rows have `exposure < 1`, so the stockout-censoring issue is large.
- **Amazon signals are not Amazon-only:** traffic, inventory and sell-out exist for B2B and D2C item IDs too (about 3,000 each), but `amazon_forecast` and `has_amazon_future` are Amazon-channel only. Cross-channel use of these signals needs an explicit rule.
- **Promotions:** there are no D2C promotions and only 379 retailer campaigns. B2B campaign logic rests on a thin dataset.
- **Lifecycle:** `months_to_eol` ranges from -90 to +1, so negative means "EOL already passed". 5,820 items have `product_status = 'eol'`. The brief's rule `months_to_eol <= 6` would match nearly every item and must be redefined (Section 3, D5).
- **Order book:** 17% of lines are cancelled completely; 19% have no `requested_delivery_at`; 41% have `material_availability_at` and `requested_delivery_at` in different months, so month-end re-phasing is a real effect. Measured effect in D26: B2B delivery lag is 34% next month overall and 97% for material available in the last 5 days of a month.

---

## 3. Brief vs. Data: Findings and Decisions

| # | Brief claim | What the data shows | Decision |
|---|---|---|---|
| D1 | Segment FPE table: Amazon A 26.4% ML vs 44.1% Danny | Item-level ML FPE for Amazon A is **103.4%**, B2B A **90.2%**, D2C A **70.0%**; whole backtest 98.09% (reproduced exactly) **[measured, Sprint 1]**. The segment table cannot be reproduced. | Recompute the full stratified table in Sprint 1-2 (`evaluation` module). Do not show the brief's table to stakeholders until reproduced. |
| D2 | MinT lowers FPE from 98.09% to 41.2% at product level | Simple sum-aggregation to `level_id`, like-for-like on 48,374 level-months (1,830 levels): ML **77.4%** vs baseline **83.1%**, FVA **+5.7 pts**. ML wins at h4-12 (+7.0 / +10.6) but loses at h2-3 (-4.1) and on the latest cutoff 2025-11 (93.6% vs 83.9%, -9.7) **[measured, Sprint 1]**. Reconciling ML items with the legacy product forecast (structural WLS, the closest MinT variant with no estimated covariance) gives product FPE **74.6%** (+2.8 pts vs ML) but raises bias from +3.1% to +18.5% and worsens item FPE from 94.1% to 99.2%; OLS weights are worse than plain ML (78.1%). MinT-shrink is not run: 5 cutoffs cannot support a covariance estimate. (An earlier 80.1% vs 98.4% estimate used an `exposure >= 1` filter and is superseded.) | The honest story is "ML beats the baseline by about 6 pts at product level, mostly at longer horizons, and recent-cutoff performance is a concern", not 41%. Implement MinT and measure. Report the result as is. |
| D3 | Danny's baseline FPE 36-72% | Baseline FPE at level_id is **83.1%** with **+28.9% bias** (systematic over-forecast, growing with horizon: +16% at h1, +36% at h7-12); ML bias is +3.1% **[measured, Sprint 1]**. | Use measured values. The bias finding is a strong adoption argument in its own right. |
| D4 | Volume shares (Amazon A+B = 58% + 22%, D2C = 0.8%) | Backtest volume: B2B 58.8%, Amazon 35.5%, **D2C 5.8%**; Amazon A-items 18.4%; ABC `N` 16.3%; C 6.9%; unclassified 0.7% **[measured, Sprint 1]**. | Recompute tier boundaries (80/15/5% split) from data. Touchless-tier sizing is an output, not an input. |
| D5 | EOL rule `months_to_eol <= 6`; forecasts after `end_of_life_month` set to exactly 0 (F-SOP-05) | The field is negative once EOL has passed. Worse for the zero rule: **42% of last-12-month volume of items with an EOL date was sold after that EOL month** (1,621 items; median 3 months after, 75th percentile 13 months) **[measured, Sprint 4]**. A hard zero would under-forecast real run-out demand. | Define: *approaching EOL* = `0 <= months_to_eol <= 6`; *post-EOL* = `< 0`. Replace the hard zero with a calibrated run-out decay (D15) and flag `TERMINAL_OBSOLESCENCE_RISK` when the baseline stays above the decayed run-out. Confirm with the business what `end_of_life_month` means (last order date vs last sale, Q8). |
| D6 | Live cycle covers 9,173 items | `live_predictions` covers 3,837 items: **100% of the 3,278 active items**, 522 of 5,820 EOL items (85% of their rows are NaN) and the 37 future launches. The 5,298 uncovered EOL items are not idle: 1,796 sold units in the last 12 months (**8.0% of volume, 10.1% of value, about £4.0M**) and 854 still sold in the last 3 months **[measured, Sprint 4]**. In the backtest, 17.7% of rows with an actual have no prediction (3.5% of volume), mostly EOL. | The gap is discontinued-item run-out, not missing active items. Fill it with the statistical fallback (D16) and tag provenance (`source`). Earlier "only 3,837 of 9,173" wording overstated the problem for active items. |
| D7 | Danny's baseline is available for the live cycle | `baseline_forecast` ends at cutoff **2025-11**. No baseline for the 2026-03 cycle; only 2,070 of 3,560 levels. | Build a reproducible statistical baseline (seasonal naive/ETS at level_id) to stand in for the legacy tool, and disaggregate to items. Confirm with Danny whether the legacy export can be supplied. |
| D8 | Summary KPI: 2,018 exceptions, 34 critical OOS alerts; "40-50 lines" | Proxy calculation on live data: **26** items have `|ML - 3-month avg| x price > £5,000`; 55 at £2,500; total live ML value for Mar-26 is about **£1.5M** (3,837 items) vs the brief's £14.9M **[measured]** | Keep the £5,000 default but make it configurable per tier. Remove hard-coded mock numbers from the API. |
| D9 | D2C margin £25.49 vs wholesale £8.30 | Mean `sales_price - purchase_price`: D2C **£10.54**, Amazon **£3.77**, B2B **£3.45** **[measured]**. The direction holds (about 3x), the figures do not. | Compute margins from `items`. Treat the brief's figures as illustrative. |
| D10 | Scenario item IDs (102752 Stand Mixer, 105417 B2B chopper...) | 102752 is an Amazon B-item; 105417 is an Amazon A-item (not B2B). | Treat scenarios as templates. Select real demo items per scenario by query (Section 8). |
| D11 | Amazon chargebacks 3-9% COGS, OTIF fines, 8-week promo lock | Not present in any table. | Configurable business parameters (`config.py`), flagged as assumptions to confirm with the business. |
| D12 | Predecessor `split_weight` for NPI | 100% null (889 rows). | Use equal split; add open question for the business. |
| D13 | The 98% is driven by long-tail C-items and lifecycle phases worth < 5% of revenue | Item-level FPE by segment **[measured, Sprint 1]**: smooth-history items (69.7% of volume) **89.0%**; mature phase (82.2% of volume) **94.4%**; NPI (<= 6 months since launch at cutoff, **16.8%** of volume, 28% of rows) **114.1%**, bias +24%; intermittent / no-history items (11.6% of volume) **156-162%**, bias +55%; EOL-approaching **123.0%** (1.0% of volume). Excluding these segments does not bring the mature core anywhere near the brief's 26-38%. | Do not claim the headline is an artefact of the tail. Treat mature-core accuracy (about 90% item FPE, 77% at product level) as the real baseline to improve, and prioritise NPI (large volume share, over-forecast) and intermittent-item handling (F-D2C-02) in WS-B/WS-D. |
| D15 | n/a (not in brief) | Sales decay after the EOL month at **0.894 per month** on average (ratio to pre-EOL level: 0.32 in the EOL month, 0.20 at +1, 0.12 at +5, 0.04 at +12; items with EOL <= 2024-12) **[measured, Sprint 4]**. | Use `ma3_decay` (last-3-month mean x 0.894^h) as the post-EOL run-out forecast. Parameter fitted on all history, so slightly optimistic in backtest. |
| D16 | ML clearly beats simple methods | On rows ML scored, ML FPE is 92.8% vs 96.2% for the best simple method (seasonal naive + 12-month mean) and 100% for a zero forecast: **ML's edge over trivial methods is 3-7 pts**. By segment ML wins on smooth items (89.0% vs 91.2%) but **loses on intermittent items (159% vs 122% for a decayed mean)**; at the out-of-cutoff check, routing intermittent and no-history items to a median-of-6-months forecast cuts total FPE from 95.7% to **93.8%** (intermittent 158 to 105, no-history 130 to 101). Bias calibration alone gives 94.9%. | Route `intermittent` and `no_history` segments to the median fallback in the live cycle (done in the artifact); keep ML for smooth and mixed items. This is the data-driven version of F-D2C-02. |
| D17 | Stand-in for Danny's legacy baseline | Closest simple method to the legacy forecast is seasonal naive + 12-month mean (`combo_sn_ma12`): it differs from legacy by 59% of legacy volume, scores **84.2% FPE vs legacy 84.9%**, but bias +13.5% vs legacy +27.7% **[measured, Sprint 4]**. | Use `combo_sn_ma12` as the live-cycle baseline proxy for triage (artifact `live_baseline_proxy.parquet`). It is a proxy, not a reproduction: confirm with Danny before the numbers are shown as his baseline (Q6). |
| D18 | Month-end actuals arrive complete (Day 1 of S&OP) | **Amazon actuals for Feb-2026 are missing**: all 3,080 Amazon items have an imputed zero (100% imputed; the order book has no Amazon orders for the month), while Amazon's own sell-out shows 62k units. Amazon target was already 87% imputed in Jan-2026 **[measured, Sprint 5]**. Left unchecked, every statistical forecast and the baseline proxy are dragged down for Amazon. | Ingestion quality gate (`triage/quality.py`): channel-months in the last 3 months with imputed share >= 95% are masked as missing (not zero) in fallback history, baseline proxy and triage. The gate must also run in the production pipeline and block the cycle. Ask the data owner whether the Amazon feed is delayed (Q14). |
| D19 | Vendor confirmation rate < 95% drives the Buy Box alert (F-AMZ-03) | `vendor_confirmation_rate` is unusable: 95% of 8.5M rows are zero, median 0, maximum 13 **[measured, Sprint 5]**. Amazon-side `open_purchase_order_units` is mostly 0 and `sellable_on_hand_inventory_units` is 12% null. | F-AMZ-03 is implemented on a proxy: A-item, 28-day procurable out-of-stock rate >= 5% and month-1 forecast above Amazon on-hand plus open POs. Business to confirm the definition of the confirmation field (Q15). |
| D20 | Amazon p70 can be compared with the baseline forecast (F-AMZ-01: p70 > baseline by 25%) | `amazon_forecast_p70` is on Amazon sell-out scale: 1.21x last month's sell-out (median) but about 3x the plan for the same items (p70 sum 84.8k vs plan 20.1k units) **[measured, Sprint 5]**. | Compare p70 with the trailing 3-month Amazon sell-out (same units), not with the vendor-order baseline. Item-level scaling between sell-out and vendor orders needs confirming (Q16). |
| D21 | Promo calendar drives the exception queue | The forward calendar is almost empty for the first months: 17 items with an Amazon deal in Mar-May 2026, **0 retailer campaigns overlapping Mar-May**; promotions start mostly from July (585), Oct-Nov (about 1,000) **[measured, Sprint 5]**. | Promo-based review will mostly fire from Q3. Ghost-promo detection is valid for Mar-Apr: with an 8-week lock, no campaign on file means none is coming. 44 B2B items are flagged as ghost-promo candidates. |
| D22 | Triage splits items 78% touchless / 15% collaborative / 7% planner-led (80/15/5% of volume) | With the calibrated defaults (value-at-risk = month 1, review floor £2,000 for flagged items, £5,000 always) **[measured, Sprint 5]**: 5,499 in-scope items; **touchless 84.1% of items / 69.9% of volume; exception queue 67 items (1.2% / 21.6% of volume); planner-led 808 items (14.7% / 8.5% of volume)**, mostly new products and EOL-soon items. 42 items exceed £5,000 (the brief's 40-50 lines holds); the floor for flagged items is what drives queue size (204 items at £500, 67 at £2,000; summing 3 months triples it). After D28 the queue is **74 items** (7 added by the order-book flag). | Keep these defaults, expose them in `TriageConfig`, and review after the first shadow cycle. Planner-led volume is higher than the brief's 5%: check whether the NPI window (<= 3 months since launch) should require a minimum volume. |
| D23 | Queue contents are an unbiased view of ML vs baseline | **65 of 67 queued items have ML below the baseline proxy**; the proxy exceeds ML by 60% for B2B (170k vs 106k units for March). The proxy's 12-month-mean component is not seasonal and over-forecasts in low-season months (the legacy tool also showed +16% to +36% bias). | Treat the queue as a ranking of disagreements, not of ML errors, until the legacy baseline (Q6) is available; ML March B2B plan (106k) is close to March-2025 actual (101k). |
| D24 | Mongo `overrides` collection immutable via role permissions (NF-03); NF-01 P95 < 400 ms | Local build uses an insert-only SQLite ledger: UPDATE/DELETE blocked by triggers, a SHA-256 hash chain makes edits made around the triggers detectable (`/api/v1/audit/verify`, tested). Measured on the real 5,499-item cycle, single user, in-process: `/exceptions` filtered p95 **45 ms**, `/items/{id}` p95 **34 ms**, `/summary` p95 **117 ms** **[measured, Sprint 6]**. **10 concurrent users over HTTP against a real uvicorn process** (`python -m app.api.load_test`, 600 requests, no think time, no errors): first run P95 388 ms (a thin pass); after precomputing the static exception rows and indexes at load time, **`/exceptions` P95 219 ms, `/items/{id}` P95 242 ms, `/summary` P95 281 ms, 55 req/s**. NF-01 passes at 10 users. At 25 users with zero think time the single process saturates (`/exceptions` P95 566 ms), so scale with `--workers` if more concurrency is needed **[measured, Sprint 6]**. | Keep the `Ledger` interface so a MongoDB implementation (insert/find-only role) can replace SQLite. Re-run the load test after any change to the read path; use multiple workers for production. |
| D25 | Allocation simulator: D2C ringfencing protects the highest-margin sales (F-D2C-01) | Prices are complete (no missing, none with sales < purchase). Of the 120 products with >= 200 units planned for March, only **16 have a D2C share >= 10%** (6 >= 25%) and 83 sell in all three channels, so on most products the strategies differ by a few pounds (largest-demand product PROD_1320: margin-optimal £3,230 vs wholesale-first £3,218, because D2C is 2 of 3,158 units) **[measured, Sprint 6]**. The brief's £25.49 vs £8.30 margins are not reproduced (D9). | The simulator (`allocation/optimiser.py`, `POST /api/v1/allocation/simulate`, workbench modal) is exact for one linear constraint with minimum fills and is read-only. Use it for the minority of products where D2C or Amazon penalties matter. Chargeback (6% of COGS), OTIF fine (5% of sales value) and the 60% Amazon floor are assumptions to confirm (Q4, D11). |
| D26 | Orders created in March for April delivery inflate the March plan (F-B2B-02, about £44k in the brief); 85% of orders placed in the last 5 days of the month ship the following month | The `target` series books an order line in the month of `material_availability_at` (monthly correlation 0.993 with the plan-relevant quantity; order date and requested date correlate only 0.85-0.87) **[measured, Sprint 7]**. Delivery lag (quantity-weighted, 1.03M B2B lines): **B2B 64.8% same month, 34.0% next month, 1.2% later**; Amazon 71.1% / 28.9%; D2C 98.5% / 1.5%. For B2B lines available in the **last 5 days of the month, 97.1% are delivered the next month** (brief: 85%); other days 21%. Month-boundary effects are large for B2B (33% of quantity has a requested delivery month after its material month, and 99.8% of those ship in the requested month). Applied to the live plan: B2B March demand 105.5k units becomes about **113.5k units expected deliveries** (45.1k already scheduled from February orders plus 65% of March demand), a 7.5% (about 8k units, roughly £90k at average price) gap between the demand view and the delivery/revenue view. The profile predicts the book well: February demand x 34% = 41.5k vs 45.1k actually scheduled. | The plan keeps two views: demand month (what ML forecasts and what is scored) and expected delivery month (what supply and finance need). `phasing/lag.py`, `GET /api/v1/phasing/summary`, item-level phasing table in the workbench and an `expected_delivery_qty` column in the consensus export. Business to confirm that material availability date is the intended demand-month definition (Q17). |
| D27 | Re-phasing improves forecast accuracy | Probabilistic phasing of the *plan* gains little at item-month grain: WAPE B2B 46.1% to 44.0%, Amazon 51.7% to 47.1%, D2C unchanged; at channel-month grain B2B 6.9% to 4.8%. Item-specific lag profiles (shrunk to the channel) do not beat the channel profile **[measured, Sprint 7]**. The value is in what the order book *already knows*, not in a better prior. | Use the channel profile only for the unbooked part of the plan and rely on booked lines for the near months (D28). Do not spend effort on item or customer lag models. |
| D28 | n/a (not in brief) | **Order-book floor:** bookings known at the cutoff are a lower bound on a month's demand. On the backtest (horizon 1-2, 34,454 rows) raising ML to the bookings known at the cutoff cuts FPE from **94.0% to 84.0%** (B2B 88.3% to 80.1%, Amazon 105.6% to 90.7%). It affects only **610 rows (1.8%)**, on which FPE falls from 80.0% to 29.5% **[measured, Sprint 7]**. Live: 39 B2B items have March bookings above the ML plan; the largest, item 103125, has 5,633 units booked vs a plan of 885 (about £44.8k of unplanned demand). Amazon has no bookings for the live months because its February feed is missing (D18). | Flag `ORDER_BOOK_EXCEEDS_PLAN` (valued at the unplanned units, always reaches the queue above the threshold; queue grows from 67 to 74 items) and a one-click audited action "Raise plan to order book" with reason code `Order Book Floor Applied`. Not applied automatically; after sign-off the floor is a candidate for the autopilot tier. |
| D14 | Planner beats ML on retailer campaigns | Only 316 scored B2B rows are promo-active: FPE 85.7% but bias **-77.7%** (ML under-forecasts campaign volume). Amazon promo-active rows score better (96.9%) than non-promo (114.8%). No planner-adjusted forecast exists in the data, so planner-vs-ML FVA by segment cannot be measured. | Collect Danny's historical consensus numbers (Q13) to measure real FVA; until then FVA is limited to ML vs the legacy baseline. |

---

## 4. Target Architecture (Adjusted)

Keep the four layers from the brief. Adjustments for what we found:

| Layer | Choice | Adjustment |
|---|---|---|
| Data | Polars + DuckDB over Parquet | Neither is installed locally (pandas 2.3 + pyarrow 21 only). Add to `requirements.txt`. DuckDB handles the 30M-row Amazon daily tables via SQL pushdown. |
| Analytics core | Python 3.9.13 (decision: stay on the local interpreter), LangGraph 0.2-0.4 | Agents wrap deterministic functions; the graph is the orchestration, not the math. |
| Persistence | MongoDB 7 + Redis | 9,173 items x 12 months x 5 cutoffs is small. Compound indexes are enough for NF-01. |
| API | FastAPI + Pydantic v2 | JWT/RBAC from day 1 (NF-02), not Phase 4. |
| UI | React 18 + Vite + Tailwind | Node 22 is available locally. |
| Containers | Docker Compose | **Docker is not installed on this machine.** Either install it or provide a no-Docker dev path (local `mongod`/`redis`, or `mongomock` for unit tests). |
| LLM | Gemini via `google-genai` | API key through environment variable only; the LLM only ever receives the telemetry JSON, with a deterministic template fallback if the call fails. |

### 4.1 Repository layout

Follow the layout in brief Section 10 (`backend/`, `frontend/`, `tests/`, `docker-compose.yml`). Two deviations:

- Data stays at `data/data/` (the existing location). Configure it via `DATA_DIR`; do not move it.
- Add `backend/app/evaluation/` (metrics, FVA, segmentation) and `backend/app/rules/` (promo, lifecycle, censoring) so that business logic lives outside the agents and is unit-testable without LangGraph.

### 4.2 Hierarchy and reconciliation

- Levels: `level_id` (3,560 products) -> `item_id` (9,173 product-channel items). Optional upper levels from `baseline_items` (`category`, `brand`, `segment`).
- Build the summing matrix **S** from `baseline_mapping` (sparse, `scipy.sparse`).
- Reconciliation methods to implement and compare: bottom-up, OLS, WLS (structural), MinT-shrink. Covariance **W** estimated from backtest residuals (5 cutoffs x 12 horizons is thin, so use shrinkage and a diagonal fallback).
- Reconciled forecasts must stay non-negative (apply non-negative projection or clip and re-aggregate).

---

## 5. Data Model (MongoDB)

| Collection | Content | Key / index |
|---|---|---|
| `items` | Item master + `level_id`, ABC (with derived class for nulls), tier, lifecycle flags, prices, margin | `item_id` unique; `(channel, abc_category)` |
| `forecast_cycles` | One doc per `(cutoff, item_id, target_month)`: baseline, ML raw, ML reconciled, final, tier, state, `value_delta_gbp`, `risk_type`, `requires_review`, narrative | `(cutoff, requires_review, value_delta_gbp desc)`; `(cutoff, channel, risk_type)` |
| `overrides` | Insert-only audit ledger (schema in brief Section 9) | Role without update/delete rights |
| `fva_analytics` | Rolling FVA per planner / category / cycle | `(planner_id, cutoff)` |
| `cycle_runs` | Pipeline run metadata, validation results, state-machine position | `cutoff` |
| `signals_cache` | Pre-aggregated per-item Amazon telemetry for the detail panel | `item_id` |

---

## 6. Workstreams

### WS-A Data ingestion and validation (`ingestion_agent`)
- Schema contracts for all 20 files (column names/types from Section 2.1) with a validation report written to `cycle_runs`.
- Exposure and censoring features: expose `exposure < 1` rows to the model and evaluation, and add an `is_censored` flag.
- Daily-to-monthly Amazon aggregation (DuckDB) that reproduces `past_covariates` as a regression test, so later daily pipelines are verifiable.
- Order-book re-phasing (F-B2B-02): **built** (`phasing/`, D26-D28). Done at channel level and by end-of-month flag from `material_availability_at` and `delivered_at`; per-customer or per-item profiles were tested and add nothing (D27), so the per-customer model is dropped. `requested_delivery_at` is not needed for the profile.
- Data-quality checks: duplicates, NaN predictions, negative values, items without actuals (11 items missing from `target_time_series`).

### WS-B Forecasting and reconciliation (`forecast_agent`)
- Load existing `live_predictions`/`backtest_predictions` as the production-of-record for now (no model retraining is in the dataset). Training code is out of scope until a training spec is agreed (Open Question Q1).
- Coverage fallback (D6): Croston/SBA for intermittent items, seasonal naive for the rest, so each active item has a forecast. Tag provenance (`source = ml | fallback`).
- Intermittent handling (F-D2C-02): items with `imputed` share > 40% use discrete count models. Note 44% of all rows are imputed, so the rule applies to a large share of the catalog; confirm the threshold with a sensitivity check.
- MinT reconciliation as in 4.2, with a coherence unit test (`S @ b == y`).

### WS-C Evaluation, FVA and stratification (`evaluation/`)
- Reproduce `overall_error_metrics` exactly (FPE 98.09, bias +6.45, n_rows 152,499). Identify the row filter that yields 152,499 (our first attempt gives 147,433 with `exposure >= 1`; 185,236 with any target) before proceeding.
- Metrics: FPE/WAPE, bias, MAE, RMSE, MASE, plus a volume-weighted view and an item-count view.
- Stratification dimensions: channel, ABC (incl. null and N), horizon, cutoff, lifecycle phase (NPI/EOL/mature), promo-active, intermittency bucket.
- Product-level comparison against `baseline_forecast` on the matched sample (47,156 rows today), reported alongside the matched-coverage caveat.
- Output: the corrected version of the brief's Section 2 table. This is the artefact that drives tier boundaries.

### WS-D Business rules (`rules/`, `lifecycle_agent`, promo logic)
- **Promo/ghost-promo (F-B2B-01):** for B2B items, detect promo-month spikes in M-12 with no matching `retailer_campaign` for month M; strip the spike and flag `OVERSTOCK_RISK`. Only 379 campaigns exist, so calibrate the spike detector on order-book data.
- **Amazon deals (F-AMZ-02):** `amazon_deal_days`, `amazon_deal_count`, `amazon_max_relative_discount_vs_rrp` from `future_covariates` feed a deal uplift estimate computed from the 6,673 historical `marketplace_deal` events.
- **Amazon guard (F-AMZ-01/03):** thresholds `glance_views_mom_pct > +20%`, `p70 > baseline + 25%` and `vendor_confirmation_rate < 0.95` for A-items. Compute traffic MoM from `amazon_traffic`; use `sellable_on_hand_inventory_units` for stock cover.
- **NPI (F-SOP-04):** 527 items have `months_since_launch <= 3` in the live window; 889 have predecessors. Run-rate transfer from `predecessors`; `NPI_OPTIMISM_WARNING` needs a commercial target source that is **not in the data** (Q3).
- **EOL (F-SOP-05):** per D5; **no hard zero**. Post-EOL items get a calibrated run-out decay (D15) and `TERMINAL_OBSOLESCENCE_RISK` when the baseline stays above it.
- **OTIF (F-B2B-03):** customer minimum-order and penalty data are not in the data (Q4); implement the check against a configurable table.

### WS-E Triage and tiers (`triage_agent`)
- Value-at-risk = `|ML - baseline| x sales_price` per item-month; sum over the planning horizon or use month 1 (decision needed; default month 1 plus 3-month cumulative).
- Tiers: Autopilot / Collaborative / Planner-Led. Start with the brief's rules, then set the thresholds from WS-C results.
- Item states: `AUTO_APPROVED`, `EXCEPTION_REVIEW`, `PLANNER_LED`, `OVERRIDDEN`, `COMMITTED`.
- Suppression rule: ignore changes < 5% on slow movers; stable low-run-rate D2C items stay out of the queue.
- Acceptance check against real data: queue size is within 25-100 items at £5,000 for the live cycle (about 26 on the proxy measure).

### WS-F Reasoning agent (Gemini)
- Prompt from brief Section 8, verbatim, with the telemetry schema built from `items`, `amazon_*`, `future_covariates`, `promotions`.
- Guardrails: JSON-schema-validated input; output length check (2-3 sentences); regex check that every number in the output exists in the input telemetry; fallback to a deterministic template narrative on failure or violation.
- Cache narratives in Mongo keyed by hash of telemetry (no repeated LLM calls on refresh).

### WS-G Allocation agent
- Inputs: available stock (user-entered scenario), channel demand, unit margin per channel (from `items`), vendor confirmation floor (default 60%, configurable), chargeback % (default 3-9%).
- Output: allocation matrix plus profit trade-off. Greedy-by-margin with constraints first; LP (`scipy.optimize.linprog`) if constraints interact.
- Important: with D2C margin ~3x wholesale on average, the heuristic will always favour D2C, so validate that the result also respects Amazon and B2B penalty costs.

### WS-H API (`backend/app/api/v1`)
- Endpoints: `/health`, `/summary`, `/exceptions`, `/item-signals/{item_id}`, `/override`, `/allocation/simulate`, `/fva`, `/cycles/{cutoff}/commit`, `/admin/retrain` (admin only).
- Server-side validation: reason code required (F-SOP-03, message `"Reason code is required."`); reason codes as enum in `models/enums.py`.
- Replace the hard-coded fallbacks in the brief's prototype (mock summary, mock items, mock signals) with real queries. The API returns 404/empty states, never silent fake data.
- Fix prototype issues: `allow_origins=["*"]` with credentials, `payload.dict()` (Pydantic v2: `model_dump`), `datetime.utcnow()` deprecations, `planner_id` taken from the JWT instead of the body, and `ml_revenue` computed from `ml_forecast` rather than `current_final`.

### WS-I Frontend (React workbench)
- Components per brief layout. Split the prototype `DemandWorkbench.jsx` into `Header`, `SummaryCards`, `ExceptionTable`, `DetailPanel`, `AllocationModal`, `TelemetryCard`, with `services/api.js`.
- Additions: filters (channel/ABC/risk), reason-code blocking validation, audit badge, forecast chart with P10/P90 bands (Phase 3), glance-view curve, re-phasing audit view, FVA scorecard page.
- No mock fallbacks in production builds; use explicit error and empty states.

### WS-J Security and observability
- JWT + roles (`planner`, `admin`, `auditor`): 401/403 per NF-02.
- Mongo: `overrides` insert-only role (NF-03); TLS in non-dev; customer IDs hashed before they reach the analytics layer.
- OpenTelemetry spans per agent node; Prometheus metrics (latency histograms, queue size); drift alert when FVA or bias moves > 5 pts.

---

## 7. Roadmap

Sprints are two weeks. Phases follow the brief; the data-truth gate is added to Phase 1.

### Phase 1 (Months 1-3, Sprints 1-6): Foundation and shadow-mode workbench

| Sprint | Deliverables |
|---|---|
| 1 | Repo scaffold, `requirements.txt`, config, DuckDB/Polars loaders for all 20 tables, schema contracts and validation report. Reproduce `overall_error_metrics` (resolve 152,499 row filter). |
| 2 | **Gate G0 (data truth):** stratified FPE/bias/FVA report (corrected Section 2 table), product-level ML vs baseline comparison, coverage-gap analysis (D6), baseline strategy for the live cycle (D7). Business sign-off on corrected numbers. |
| 3 | Hierarchy matrix **S**, reconciliation (bottom-up/OLS/WLS/MinT-shrink) with coherence tests; fallback forecasts for uncovered items; Mongo collections and loaders. |
| 4 | Rules engine: EOL, NPI, ghost promo, Amazon guard; triage engine with tiers and states; FastAPI endpoints `/summary`, `/exceptions`, `/item-signals`, `/override`, JWT/RBAC. |
| 5 | React workbench (table, detail panel, override form, reason codes, audit badge), insert-only ledger, FVA v1. |
| 6 | Shadow mode: run on the 2026-03 cycle next to Danny's spreadsheets; deterministic narratives; performance test for NF-01. |

**Exit gate:** Danny completes the Month 2 and Month 3 reviews in the workbench; no downtime in the 72h window; reproducible numbers in `DEVELOPMENT_PLAN` Section 3 replaced with measured values.

### Phase 2 (Months 4-6, Sprints 7-12): Closed-loop signal automation
- Daily Amazon feature pipeline (replace manual Parquet drops); S3/MinIO ingestion trigger; scheduling of the order-book phasing step in production (the logic is built, see D26-D28); automated OOS and Buy Box alerts (F-AMZ-03, proxy already in the triage).
- Promo calendar sync (TPM) and EDI 852 feed **if** the business provides sources (Q5); until then, `promotions.parquet` stays the source.
- Pipeline SLA test: ingestion plus features under 300s on 8 cores / 32 GB (NF-04), measured on about 34M rows.
- **Exit:** daily glance-view ingestion recalculates weekly risk without manual steps.

### Phase 3 (Months 7-9, Sprints 13-18): Probabilistic S&OP and inventory coupling
- Conformalized quantile bands P10/P50/P90, calibrated on the 5 backtest cutoffs (coverage check: target 80% inside P10-P90). Five cutoffs are thin; add more rolling-origin cutoffs if the model team can produce them.
- Dynamic safety stock from the quantile spread plus lead-time (order book `ordered_at` to `delivered_at`).
- Censoring-aware training/evaluation option (exposure weighting), the first step toward the Tobit approach in brief Section 14.
- **Exit:** safety stock simulation on A-items shows the targeted 12% holding-cost reduction (to be demonstrated, not assumed).

### Phase 4 (Months 10-12, Sprints 19-24): Agentic orchestration and rollout
- Full LangGraph `StateGraph` with checkpoints for human review, conversational query agent, executive briefing and PDF/PPT export, allocation simulator in the S&OP review.
- Autopilot promotion rule: categories with negative FVA over consecutive cycles move to autopilot automatically.
- **Exit:** tier shares verified against measured data (target about 78% autopilot, subject to WS-C), review time under 8 hours.

### Orchestration state machine (LangGraph)
`TRIGGER -> INGEST/VALIDATE -> BASE_INFERENCE -> RECONCILE -> PROMO_LIFECYCLE_RULES -> TRIAGE -> NARRATIVE -> HUMAN_REVIEW (interrupt) -> COMMIT`. Each edge has a guard: schema valid, no NaN, non-negative, coherence residual < tolerance, queue size sanity bounds. Failed guards halt the run and alert, and never publish partial results.

---

## 8. Traceability: User Stories to Work

| Story | Workstream | Phase | Data dependency / note |
|---|---|---|---|
| F-AMZ-01 PO spike sensing | WS-D, WS-E | 1 (basic), 2 (daily) | `amazon_traffic`, `amazon_forecast`, `future_covariates` |
| F-AMZ-02 Deal uplift | WS-D | 1 | `promotions` (marketplace_deal), `future_covariates.amazon_deal_*` |
| F-AMZ-03 Confirmation / Buy Box alert | WS-D | 2 | `amazon_inventory.vendor_confirmation_rate`, on-hand |
| F-B2B-01 Ghost promo | WS-D | 1 | Only 379 campaigns; validate on order book |
| F-B2B-02 Order re-phasing | WS-A | 1 (built, D26-D28) | `orderbook` dates; order-book floor and expected-delivery view |
| F-B2B-03 OTIF | WS-D | 2 | Needs OTIF clause data (Q4) |
| F-D2C-01 Ringfencing | WS-G | 4 (basic in 3) | Margin from `items` |
| F-D2C-02 Intermittent | WS-B | 1 | `imputed` share (44% overall) |
| F-SOP-01 Disaggregation | WS-B | 1 | `baseline_mapping` |
| F-SOP-02 Triage queue | WS-E | 1 | Threshold calibrated per D8 |
| F-SOP-03 Override + FVA | WS-H, WS-I | 1 | Insert-only ledger |
| F-SOP-04 NPI | WS-D | 2 | `predecessors` (no weights), targets missing (Q3) |
| F-SOP-05 EOL | WS-D | 1 | Redefined per D5 |
| NF-01 Latency | WS-H | 1 / 6 | P95 < 400 ms at 10 users |
| NF-02 RBAC | WS-J | 1 | |
| NF-03 Immutable audit | WS-J | 1 | |
| NF-04 Ingestion SLA | WS-A | 2 | About 34M rows, not 35M |

**Demo-item selection (D10):** pick one real item per scenario by query, for example the Amazon A-item with the largest 14-day glance-view increase for AMZ-1, the Amazon item with rising `sellable_on_hand_inventory_units` and falling views for AMZ-2, a B2B item with a spike in M-12 and no `retailer_campaign` for B2B-1, and a month-end order cluster for B2B-2.

---

## 9. Test Strategy

Tests are written before the corresponding implementation.

- **`tests/test_metrics.py`:** reproduces FPE 98.09 / bias +6.45 / n_rows 152,499 from `backtest_predictions`; FVA sign conventions.
- **`tests/test_reconciliation.py`:** coherence (`S @ b == y`), non-negativity, MinT beats or equals bottom-up on the backtest, shrinkage stable with few cutoffs.
- **`tests/test_agents.py`:** state transitions and guards; failure halts the graph; idempotent re-runs per cutoff.
- **`tests/test_rules.py`:** one test per story acceptance criterion (F-AMZ-01 through F-SOP-05), using small fixture slices of the real Parquet files.
- **`tests/test_api.py`:** 401/403, reason-code-required error text, override writes, queue sort order and filters, no mock data when DB is empty.
- **`tests/test_security.py`:** `overrides` update/delete rejected for the app role.
- **Load test (k6/Locust):** NF-01 at 10 concurrent users; **pipeline benchmark** for NF-04.
- **LLM guardrail tests:** numbers in the narrative must be traceable to input telemetry; fallback path covered with a mocked Gemini client.
- **Data contract tests:** run on every new monthly drop.

Frontend: Vitest and React Testing Library for the override form; Playwright for the end-to-end triage, override, ledger path.

---

## 10. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Brief's performance claims are not reproducible (D1-D4) | Loss of credibility with Danny and leadership | Gate G0: present measured numbers only; bias and coverage findings are the story |
| Live ML covers 3,837 / 9,173 items (all 3,278 active; EOL items are the gap) | Cannot claim 9,173-item automation | Mitigated: fallback forecasts with provenance tags (D6, D16); keep the coverage KPI |
| No live baseline for triage | Triage numbers are not Danny's | Mitigated with a proxy (D17); the queue is a ranking of disagreements until the legacy export arrives (D23, Q6) |
| Amazon actuals missing for Feb-2026 and thin for Jan-2026 (D18) | Amazon forecasts, floor and triage are blind for the channel | Quality gate masks the months; Q14 to the data owner; block the cycle when the gate fires in production |
| Thin history for covariance and conformal calibration (5 cutoffs) | Unstable MinT / bands | Shrinkage, diagonal fallback, request more cutoffs |
| 41.6% of items without ABC class | Segmentation and rules blind spots | Derive class from trailing volume, flag as derived |
| Business data not in dataset (OTIF clauses, chargeback %, commercial targets, lock dates) | Stories F-B2B-03, F-SOP-04 incomplete | Configurable placeholders and tracked questions |
| LLM hallucination | Trust loss | Telemetry-only prompt, numeric traceability check, template fallback |
| Docker not installed locally | Delayed environment | Install Docker Desktop or provide a local no-container path |
| Customer data sensitivity (orderbook) | Compliance | Hash IDs at ingest, RBAC, TLS, UK GDPR logging policy |

---

## 11. Open Questions for the Business / Data Owners

1. **Q1:** Is a training pipeline for the ML model available, or is the forecast delivered as Parquet only?
2. **Q2 (resolved in Sprint 1):** `n_rows = 152,499` is exactly the rows with both `target` and `prediction` non-null. No exposure filter is applied, so stockout-censored months are inside the 98.09%. FPE, bias, MAE and RMSE all reproduce to 6 decimals (`tests/test_metrics.py`). Remaining question for the business: should the headline exclude censored rows? (With `exposure >= 1` it would be 100.4%, i.e. worse.)
3. **Q3:** Where do commercial sales targets for NPIs come from?
4. **Q4:** Where are the OTIF penalty clauses and customer minimum order quantities?
5. **Q5:** Which sources will feed TPM promo sync and EDI 852 (Phase 2)?
6. **Q6:** Can the legacy baseline be exported for the live cycle? What is its method?
7. **Q7:** What does ABC class `N` mean, and why are 41.6% of items unclassified?
8. **Q8:** What is the sign convention of `months_to_eol` and its source?
9. **Q9:** Why do B2B and D2C item IDs have Amazon traffic and inventory data? Is it product-level data copied across channels?
10. **Q10:** Is `sales_price` the channel selling price and `purchase_price` the full COGS (for margin and chargeback maths)?
11. **Q11:** Is the 8-week promo lock enforced in the data, and how often do dates shift?
12. **Q12:** Should the £5,000 threshold be applied to month 1 only or the full 12-month horizon?
13. **Q13:** Can we obtain Danny's historical submitted consensus plans (not just the statistical baseline)? Without them the planner FVA scorecard cannot be back-tested.
14. **Q14:** Why are there no Amazon order-book lines for Feb-2026 (and only 17.5k units in Jan-2026 vs 133k in Nov-2025)? Feed delay or genuine stop of 1P ordering?
15. **Q15:** What does `vendor_confirmation_rate` contain (95% zeros, max 13)? Is a different field available for PO confirmation?
16. **Q16:** How do Amazon sell-out units (`amazon_total_ordered_units`, `amazon_forecast_p70`) relate to vendor order quantities at item level?
17. **Q17:** Is `material_availability_at` the intended definition of the demand month? The target follows it, but supply and revenue follow `delivered_at`; which month should S&OP and finance use for the consensus?
18. **Q18:** `delivered_at` extends to Dec-2026 for lines not yet shipped: is it the scheduled delivery date, and how often does it move?

---

## 12. Build Status and Next Steps

**Progress against the roadmap (prototype on the real data; no production infrastructure yet):**

| Roadmap item | Status | Where |
|---|---|---|
| Sprint 1: loaders, contracts, metrics reproduced | Done | `data/`, `evaluation/metrics.py` |
| Sprint 2: Gate G0 evidence (stratified error, product level, coverage, baseline) | Done; business sign-off pending | D1-D7, D13-D17 |
| Sprint 3: reconciliation, fallback forecasts | Done (MinT-shrink not run: too few cutoffs) | `evaluation/reconcile.py`, `forecasting/` |
| Sprint 4: rules engine, triage, API, RBAC | Done | `triage/`, `api/` |
| Sprint 5: workbench, audit ledger, FVA v1 | Done (SQLite ledger instead of MongoDB) | `frontend/`, `api/ledger.py` |
| Sprint 6: shadow mode, narratives, NF-01 | Load test passes (D24); shadow run pending a real cycle; narratives are deterministic only | `api/load_test.py` |
| Order-book re-phasing (planned Phase 2) | Pulled forward and done | `phasing/`, D26-D28 |
| Allocation simulator (planned Phase 3-4) | Pulled forward and done | `allocation/`, D25 |
| LLM narratives (WS-F) | Next | see below |
| Phases 2-4: daily pipelines, quantile bands, safety stock, LangGraph | Not started | Section 7 |

**Done (144 backend + 17 frontend tests passing):**
1. Scaffold: `backend/app/{config,data,evaluation}`, `backend/requirements.txt`, `pytest.ini`, `.gitignore`.
2. `backend/app/data/contracts.py` + `loaders.py`: schema contracts for all 20 tables, validated against the real files, with `load()`, `validate_all()` and `row_counts()`.
3. `backend/app/evaluation/metrics.py`: reproduces `overall_error_metrics` and per-item metrics exactly (Q2 resolved).
4. `backend/app/evaluation/stratify.py` + `report.py`: stratified backtest metrics (channel, ABC, horizon, cutoff, status) and the like-for-like product-level ML vs baseline comparison. Run: `$env:PYTHONPATH="backend"; python -m app.evaluation.report` (CSVs go to `reports/`).
5. `backend/app/evaluation/segments.py`: lifecycle phase (NPI / EOL_APPROACHING / POST_EOL / MATURE as known at the cutoff), promo-active and intermittency buckets; all in the report (findings D13, D14).
6. `backend/app/evaluation/reconcile.py`: summing matrix, matrix MinT formula, closed-form two-level reconciliation (OLS and structural WLS) verified against the matrix formula, and a backtest evaluation (D2).
7. `backend/app/forecasting/`: vectorised statistical methods (seasonal naive, moving average/median, SES, Croston-SBA, combo), EOL decay fit, leave-one-cutoff-out evaluation of routing and calibration, and `apply_fallback` with provenance tags. Run: `$env:PYTHONPATH="backend"; python -m app.forecasting.report`. It writes `reports/live_forecast_complete.parquet` (every item has a forecast, with `source` = `ml` / `fallback:*` / `routed:*`) and `reports/live_baseline_proxy.parquet`. 49 tests passing.

8. `backend/app/triage/`: `quality.py` (incomplete-month gate, D18), `features.py` (Amazon telemetry, p70 gap, seasonal baseline disaggregation, ghost-promo candidates, promo activity, NPI analogy, derived ABC), `rules.py` (risk flags, tiers, states, deterministic narratives, `TriageConfig`) and `run.py`. Run: `$env:PYTHONPATH="backend"; python -m app.triage.run` (after the forecasting report). Writes `reports/triage_live.parquet` (all items with tier, state, `risk_type`, `risk_flags`, value delta, narrative) and `reports/triage_queue.csv`. 79 tests passing.

9. **API and ledger** (`backend/app/api/`, `store/`, `models/`): FastAPI app with JWT login (PBKDF2 passwords, lockout after 5 failures, HS256 with a minimum 32-character secret), roles `planner` / `admin` / `auditor`, restricted CORS, endpoints `summary`, `exceptions` (filters, paging), `items/{id}`, `override` (mandatory reason code with the exact message "Reason code is required.", identity/IP/session taken from the token and request, 5% suppression on touchless items, accept-ML rules), `overrides`, `fva`, `reason-codes`, `export/consensus`, `cycles/{cutoff}/commit` (blocked while exceptions are undecided, once per cycle, writes `consensus_<cutoff>.csv`), `admin/reload`, `audit/verify`. SQLite ledger is insert-only with a hash chain. `evaluation/fva.py` scores overrides against actuals (empty until actuals exist) and detects negative-FVA streaks for the autopilot rule. Smoke test: `python -m app.api.smoke`; black-box check of a running server: `python -m app.api.live_check`.
10. **React workbench** (`frontend/`): Vite + React 18 + Tailwind, login, summary cards, data-quality banner, filterable exception table, detail panel (narrative, telemetry, plan/actuals chart, audited override form, accept-ML), draft export and commit. Frontend tests cover reason code blocking, payloads, error handling, read-only auditors and the allocation modal; the production build passes.
11. **Allocation simulator and load test:** `allocation/optimiser.py` (margin-optimal, wholesale-first and pro-rata strategies with Amazon/B2B minimum fills), `POST /api/v1/allocation/simulate`, `AllocationModal` in the workbench, `purchase_price` carried in the triage artifact. `python -m app.api.load_test` runs N concurrent users against a live server and fails when `/exceptions` P95 is >= 400 ms (D24). The API read path precomputes static rows at load (about 2x faster under load).

12. **Order-book phasing (F-B2B-02)** (`backend/app/phasing/`): delivery lag profile by channel and end-of-month flag, channel/item backtests (D27), expected-delivery plan (carry-in from earlier months plus lag-spread demand), booked/shifted/carry-in audit per item-month, and the order-book floor evaluation (D28). Run `python -m app.phasing.run` before the triage; it writes `phasing_profile.csv`, `phasing_items.parquet` and the backtests. Triage flag `ORDER_BOOK_EXCEEDS_PLAN`, API `phasing` block on item detail, `GET /api/v1/phasing/summary`, `expected_delivery_qty` in the consensus, reason code `Order Book Floor Applied`, workbench `PhasingTable` and "Raise plan to order book" button.

**Assumption to confirm:** the backtest cutoff is the last observed month (first forecast month = cutoff + 1). The live cutoff is therefore 2026-02-01, not the brief's 2026-03-01 (which is the first forecast month).

**Environment decision:** the project stays on the local Python 3.9.13 (the brief specifies 3.11; this is a deliberate deviation). Consequences: use `from __future__ import annotations`, no `match` statements and no `X | Y` runtime type unions; pin `langgraph>=0.2,<0.5` and `langchain-core<0.4` (later releases require 3.10+); FastAPI and Pydantic v2 run on 3.9 unchanged.

**Still open from the Sprint 1-3 scope:**
1. Confirm with Danny that `combo_sn_ma12` is an acceptable stand-in for the legacy baseline (D7, D17, Q6).
2. Decide Docker vs local services for the dev environment.
3. Business sign-off on corrected numbers (Gate G0), including D13 (the headline error is not just a long-tail artefact) and D5 (no hard zero after EOL).
4. Estimated-covariance MinT once more rolling-origin cutoffs are available.
5. Next build step: shadow-mode run of the workbench next to Danny's spreadsheet for the first real cycle; MongoDB `Ledger` implementation (needs a MongoDB instance). Remaining rules not yet built: OTIF check (F-B2B-03, data missing), MinT-based disaggregation of committed plans (F-SOP-01), Gemini narratives (WS-F; deterministic narratives are the telemetry input). Candidate after sign-off: apply the order-book floor automatically (D28).
6. Business questions blocking sign-off: Q6 (legacy baseline), Q14 (missing Amazon Feb actuals), Q15/Q16 (Amazon fields).
