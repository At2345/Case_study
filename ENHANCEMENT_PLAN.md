# Enhancement Plan: Agentic Forecasting, Urgent Co-Pilot, Supply & Operations Completion

Scope: three follow-on enhancements on top of the implemented workbench (see `DEVELOPMENT_PLAN.md` and `memory/PRD.md`).
All steps are incremental, keep the existing audit ledger and RBAC, and are verifiable against the live cycle data.

---

## 1. Agentic Forecasting (Research -> Process -> Forecast -> Inform)

Goal: turn the read-only dynamic forecast into a four-stage agentic workflow for products and supply operations, so the system researches context, processes signals, produces a forecast, and informs the planner with an auditable rationale.

### 1.1 Research (context gathering)
- New `backend/app/forecasting/agent.py` with a `research(item_id)` step that assembles the item's context: history (12-36 months), order-book phasing, promotions overlap, lifecycle phase (NPI/EOL/mature), channel, ABC tier, Amazon signals (glance MoM, p70, OOS rate) when present.
- Reuse `svc.item_detail`, `svc.phasing_detail`, `dynamic.item_forecast` internals; no new data sources.
- Output: a compact `research` JSON (history stats, booked units, promo flags, phase) persisted in the run store for audit.

### 1.2 Process (method selection + reconciliation)
- Extend `_choose_method` in `backend/app/forecasting/dynamic.py` into a scored method selection: run all `stat_methods.METHODS` on the history, score each with a small holdout (last 3 observed months), pick the best by WAPE with the existing phase/intermittency priors as tie-breakers.
- Add a product-level reconciliation pass: when all items of a product are processed, scale item forecasts to the product total (reuse `product_context` in `dynamic.py`).
- Output: `processed` JSON with per-method scores, chosen method, and reconciliation deltas.

### 1.3 Forecast (generate + band)
- Generate the 12-month forecast for the chosen method plus the conformal P10/P90 band (`forecasting/quantiles.py`).
- Reuse the existing `GET /api/v1/forecast/items/{item_id}` response shape so the DetailPanel keeps working; add `agent` fields (`research`, `processed`, `informed`).

### 1.4 Inform (narrative + actions)
- Build a deterministic "forecast brief" (2-4 sentences): why this method, what the band says, what the order book implies, and what the planner should check.
- When a local/remote LLM is configured (`client_from_env()`), generate a richer brief through the existing `Narrator` guardrails; fall back to the deterministic text.
- Emit planner actions (reuse the `suggested_actions` shape from `extended.py`): "Use as decision", "Review phasing", "Compare in scenarios".

### 1.5 API + UI
- `POST /api/v1/forecast/items/{item_id}/agentic` (write roles) returning the full agent trace; `GET` variant for auditors.
- DetailPanel: replace the single "Recompute" button with a 4-step "Run agentic forecast" flow showing Research -> Process -> Forecast -> Inform states (progress + final brief + action chips).
- Keep the existing "Use as decision" button wired to the audit ledger.

### 1.6 Tests
- Unit: method-selection holdout picks the best method on a synthetic series; reconciliation sums to the product total; band covers the holdout at ~80%.
- API: agentic endpoint returns all four stages; auditor can read, planner can write; invalid item -> 404.

---

## 2. Urgent-Information Co-Pilot Chatbot

Goal: a lightweight, always-available chatbot that answers urgent questions with the fastest path to the answer, integrated with the existing Co-Pilot page and its action chips.

### 2.1 Urgency detection
- In `backend/app/api/extended.py`, classify each question as `urgent` when it matches: "urgent", "asap", "immediately", "critical", "now", "today", or references a specific item id / OOS / OTIF penalty.
- Urgent questions bypass the LLM (or use a short deterministic answer) so latency stays low; non-urgent questions keep the current stream path.

### 2.2 Fast-answer endpoints
- `GET /api/v1/assistant/urgent?q=...` (all roles): returns within one request (no SSE):
  - `answer`: 1-2 sentence deterministic answer from the live context (`context()` in `extended.py`).
  - `actions`: 1-3 deep-link chips (reuse `suggested_actions`): review item, mitigate OTIF, raise to order book.
  - `refreshed_at`: context timestamp so the planner knows it is live.
- Cache the context snapshot for 30 s to keep repeated urgent lookups fast.

### 2.3 Chatbot UI
- New `UrgentChat` component embedded at the top of `CopilotPage` (and optionally as a floating button on the Workbench):
  - Input + "Ask now" button; response renders the answer plus action chips (same `ActionChips` component).
  - Shows "live cycle as of <time>" and a "full analysis" link that sends the same question through the normal Co-Pilot stream.
- `api.js`: add `assistantUrgent(q)` client method.

### 2.4 Tests
- Unit: urgency classifier (urgent vs normal), fast-answer returns actions for an OOS question.
- API: urgent endpoint < 500 ms on the live cycle; auditor/planner/admin all allowed.

---

## 3. Fully Functional Supply & Risk and Operations (PRD completion)

Goal: close the remaining PRD gaps so Supply & Risk and Operations are complete, not just present.

### 3.1 Supply & Risk
- **Safety stock drill-down:** item table rows become clickable -> open the Workbench DetailPanel for that item (reuse `navigateAction`).
- **Band calibration history:** add a small trend view of coverage per cutoff (from `svc.extras["coverage"]` + run store) so planners see whether bands are stable.
- **OTIF by customer:** add a customer dimension to `otif.from_plan` output when the order book has customer ids (currently channel-level only); keep the configurable-terms note.
- **Supply risk flags:** surface `ORDER_BOOK_EXCEEDS_PLAN`, `OOS_RISK`, `OVERSTOCK_RISK` counts per channel as summary stats at the top of the page, with deep links to the filtered Workbench queue.

### 3.2 Operations
- **Cycle run detail:** clicking a run row opens a panel with per-node logs/errors (already stored in `cycle_runs`), duration, and the validation report from `ingest_validate`.
- **Manual node re-run:** admin can re-run a failed node (`POST /api/v1/admin/cycles/rerun-node`) instead of restarting the whole cycle; guard against concurrent runs (reuse `JobRunner` claim).
- **Data-quality gate visibility:** show the `ingest_validate` warnings (missing Amazon Feb feed, imputed share) on the Operations page, not only in the QualityBanner.
- **Scenario promotion to ledger:** implement PRD backlog item "promote a scenario into ledger decisions (audited) before commit" — a scenario comparison column gets a "Promote" action that writes the scenario's month-1 quantities as audited overrides (reason code `Commercial Intelligence (Account Feedback)`, comment referencing the scenario id).

### 3.3 Tests
- API: rerun-node only for failed nodes and admins; promote-scenario writes audited overrides and rejects after commit; OTIF customer dimension does not break the existing channel view.
- Frontend: Supply & Risk deep links navigate to the Workbench with the item selected; Operations run-detail panel renders node errors.

---

## 4. Hybrid Search Fallback (Project Data -> Gemini Knowledge Base)

**Status: Phase A (Tier 1) and Phase B (Tier 2) implemented Oct 2026.** `backend/app/retrieval/hybrid.py` (structured router + lexical doc/ledger index + answerability gate), `HYBRID_SEARCH` flag in `config.py` (`off|tier1|tier2`, default `tier1`), tier/sources SSE events + Gemini knowledge-base fallback in `assistant_stream`, tier badge + sources in `CopilotPage.jsx`. Phase C (evaluation metrics) pending.

Goal: when the Co-Pilot cannot answer from the project's own data, fall back to the configured Gemini model's own knowledge base — with an explicit answerability gate, provenance labeling, and safety controls. No vector database and no external search provider are required.

### 4.1 Tier 1 — project data sources (authoritative, always first)
- **Structured live-cycle data**: `DataService` (triage, monthly, actuals, meta), the audit ledger (SQLite, hash-chained overrides + planner comments), run store (cycle runs, node details), scenarios, notes/schedules.
- The existing assistant context snapshot (`context()` in `backend/app/api/extended.py`) stays the primary structured source.
- **Project documents**: `README.md`, `DEVELOPMENT_PLAN.md`, `ENHANCEMENT_PLAN.md`, `memory/PRD.md` — for "how does the system work" questions.
- **Retrieval without a vector DB**:
  - *Structured query router*: extract entities/metrics (item id, product, channel, month, metric names) and answer directly from `DataService`/ledger (exact, deterministic).
  - *Lexical full-text search*: SQLite FTS5 index over project docs + run store + ledger comments (simple keyword scoring).
  - *Optional later*: local embeddings (sentence-transformers) + reranking only if lexical recall proves insufficient — not required for Phase A/B.

### 4.2 Answerability gate (when to fall back)
- Tier 1 is "sufficient" when: a structured lookup returns the requested fields, OR lexical search relevance is above a threshold, OR the LLM (given retrieved evidence plus an explicit "insufficient evidence" instruction) says it can answer.
- Otherwise -> Tier 2. The gate result is returned to the UI (`tier: project|general_knowledge|none`) so the planner sees why the answer may be general.

### 4.3 Tier 2 — Gemini knowledge base (fallback, non-authoritative)
- When the answerability gate says Tier 1 is insufficient, ask the configured Gemini model directly to answer from its own knowledge base — no external search provider, no web fetch, no new network dependency beyond the Gemini API already in use.
- A dedicated fallback system prompt instructs the model to:
  - answer from general knowledge only, and clearly label the answer as "general knowledge — not from the project's data";
  - never invent project-specific figures (item ids, quantities, cutoffs, FVA/OTIF numbers) — those must come from Tier 1;
  - state uncertainty when the knowledge is dated or uncertain (e.g., "as of my knowledge cutoff").
- The UI shows a tier badge (`project` vs `general knowledge`) so the planner can tell grounded answers from general ones.

### 4.4 Integration with the assistant stream
- New `retrieve(question)` step in `assistant_stream` (`backend/app/api/extended.py`): Tier 1 first; if the gate says insufficient, Tier 2.
- Tier 1 evidence is appended to the prompt with provenance tags; context size is bounded (top-k snippets, truncation).
- Tier 2 reuses the same stream with a different system prompt (general-knowledge mode) and no project context.
- Fact-check: the existing number check stays for Tier 1; Tier 2 answers are exempt from the telemetry check but must carry the "general knowledge" label.
- Deterministic fallback preserved when both tiers fail.
- **Local concept knowledge base** (`app/narrative/concepts.py`): general-knowledge concept questions (Croston's method, exponential smoothing, WAPE/MAPE, safety stock, S&OP, ...) are routed to the `general_knowledge` tier with a `concept` source (never the project docs) and answered deterministically with references when the LLM chain is down; the reference text also grounds the LLM prompt when it is up.

### 4.5 Safety, privacy, ops
- Feature flag `HYBRID_SEARCH` (default: Tier 1 on, Tier 2 off) — the knowledge-base fallback is opt-in.
- No new external calls: Tier 2 reuses the existing Gemini API; nothing else leaves the system.
- The Tier 2 prompt forbids fabricating project-specific figures; the label plus the existing number guardrail enforce it.
- Logging: tier used, latency — no query content beyond the question.

### 4.6 Tests
- Project-first precedence: Tier 2 is not invoked when Tier 1 answers.
- Fallback: Tier 2 is invoked only when the gate says insufficient.
- Labeling: Tier 2 answers are labeled "general knowledge" and never present project figures as facts.
- Resilience: Gemini timeout/error -> deterministic fallback, no 500.
- RBAC unchanged (all roles can read; planner/admin write flows untouched).

### 4.7 Sequencing
- **Phase A (Tier 1 only)**: query router + FTS5 index + answerability gate; no fallback. Highest value, zero external risk.
- **Phase B (Tier 2 behind flag)**: Gemini knowledge-base fallback with the general-knowledge label.
- **Phase C (evaluation)**: answerability rate, fallback rate, label accuracy, latency; add embeddings/reranking only if lexical recall is insufficient.

Each phase ends with the backend test suite green and the frontend production build passing.

---

## 5. Answer Accuracy (Co-Pilot)

**Trigger:** "Can you research the best selling products in Amazon?" returned the first-month phasing summary instead of an answer about best-selling products.

**Status: implemented Oct 2026** (see 5.2/5.3; DeepSeek replaced by GLM `glm-5.2` via `GLM_API_KEY`).

### 5.1 Root causes (verified Oct 2026)
- **Intent misclassification (primary)**: `_structured_intent()` in `backend/app/retrieval/hybrid.py` matched the single word "amazon" to the `phasing` intent, so the answerability gate locked the question to the project tier with the wrong intent. Channel words ("amazon", "b2b", "d2c") are qualifiers, not phasing triggers.
- **LLM chain unavailable (compounding)**: server log shows `GeminiClient failed (ClientError)` [429 free-tier quota] -> `DeepSeekClient failed (HTTPStatusError)` [402 Payment Required, no credit] -> deterministic fallback. The keyword-based `deterministic_answer` (`backend/app/api/extended.py:126-130`) then produced the phasing summary because "amazon" is also a trigger word there.
- **No "top products" intent**: the project data can answer this (monthly demand has a channel dimension -> top items by Amazon demand), but neither the router nor `deterministic_answer` supports a ranking-by-channel intent.
- **Misleading verification note**: the phasing figures (107,647 / 7,229 / 16,926) were flagged "could not be verified against the cycle data" even though they come from `phasing_first_month`; `_allowed_numbers(_round(ctx))` does not include those fields, so the note is a false negative.
- **Ambiguity**: "research ... in Amazon" can mean general research (external/general knowledge) or top items by Amazon channel demand (project data); the gate cannot distinguish and defaulted to neither.

### 5.2 Fixes
- **Precision intent matching** (`hybrid.py`):
  - Phasing requires >= 2 phasing-specific tokens ("phasing", "delivery", "deliveries", "expected delivery", "b2b demand", "shift") or a channel plus one phasing word; channel words alone never trigger phasing.
  - New `top_products` intent: ranking words ("best", "top", "highest", "most", "worst", "selling") + product words + optional channel -> answered from monthly demand via `DataService.top_items_by_demand()` (top-N items by planned demand for the channel).
  - `research`/general-knowledge questions need no explicit intent: without a project entity they fall through the lexical gate to the `general_knowledge` tier.
- **Honest fallback**: `_det_branch()` (`extended.py`) reuses the router's precision rules; when the LLM chain fails and no branch matches, the answer is "The project data does not contain this answer..." instead of an off-topic keyword answer. `deterministic_answer` is gated by intent match.
- **LLM chain health**: DeepSeek replaced by GLM (`GlmClient`, `glm-5.2`, `GLM_API_KEY`/`GLM_MODEL`/`GLM_BASE_URL`). `FailoverClient` records per-provider status; `GET /api/v1/assistant/providers` exposes configured providers + last error (e.g. Gemini 429 quota, GLM 429 no balance).
- **Number-note fix**: the "could not be verified" note is skipped when the answer came from the deterministic path (deterministic answers are by construction from cycle data).

### 5.3 Tests
- Intent precision: "best selling products in Amazon" -> `top_products` or `general_knowledge`, never `phasing`.
- Honest fallback: off-topic question with the LLM chain down -> honest message, not a phasing summary.
- Number note: deterministic phasing answer -> no "could not be verified" note.
- Live: assistant stream returns a relevant tier and answer for the trigger question.

---

## 6. Co-Pilot summaries, Operations routing, Dynamic Safety Stock, agent notifications, FVA visibility (Oct 2026)

**Status: implemented Oct 2026.** User request: Co-Pilot summary + details button; Operations "Open" -> Workbench summary; Dynamic Safety Stock functional; rename "Supply & risk" -> "Supply & Risk"; agent work as clickable notifications; FVA scorecard not empty.

### 6.1 Co-Pilot summary + details button
- `suggested_actions()` (`backend/app/api/extended.py`) now emits a `view_operation` action (latest cycle run id + status/steps) for operation/cycle/run/pipeline questions, and a `review_exception` item action for product/item questions. The assistant context gained an `operations` block (latest run summary from the runs store).
- `CopilotPage.jsx` renders a **View details** button under assistant messages that carry an item or run action; clicking expands an inline `DetailView` (item: channel/phase/risk/value delta/M1 plan; run: status, started-at, per-node chips, error) via `api.item` / `api.runDetail`. Existing action chips still navigate to the Workbench/Operations pages.

### 6.2 Operations -> Workbench routing
- `App.jsx` `navigateAction` handles `view_operation` (sets `operationsRunId`, opens Operations) and shows the action title+detail as the notice banner. Item actions now also show their title/detail summary on the Workbench.
- `OperationsPage.jsx` accepts `initialRunId` and opens that run's detail panel on mount.

### 6.3 Dynamic Safety Stock functional
- Root cause: the `quantile_bands.parquet` / `quantile_calibration.csv` / `safety_stock.parquet` artifacts were missing from `reports/` (the `bands_safety` node had never run through the orchestration runner), so `load_extras()` returned `None` and the API answered `available: false`.
- Fix: ran the `quantiles` and `safety_stock` pipelines (they are part of the monthly cycle) and then the full monthly cycle; the endpoint now returns `available: true` with 25 top-saving items, A-item dynamic SS 169,750 units, holding reduction 69%.

### 6.4 Rename
- Header nav label and all user-facing notices changed from "Supply & risk" to "Supply & Risk".

### 6.5 Agent work as notifications
- `backend/app/orchestration/jobs.py` collects each node step during a cycle run and emits one `agent_work` notification per step (`Agent: <label>` + one-line summary of the node's detail + seconds), linked to `operations` with the `run_id`. Clicking a notification navigates to Operations and opens that run's summary (`NotificationBell` now passes the full record; `App.handleNotification` sets `operationsRunId`).

### 6.6 FVA scorecard visibility
- Root cause: not a bug — actuals end at the cutoff (2026-02-01) while the 7 current overrides target 2026-03-01, so no override has matured; monthly FVA is legitimately empty until target-month actuals arrive. Daily calculation is not possible (actuals are monthly).
- Fix: `GET /api/v1/fva` now also returns `pending_overrides` + `pending` (overrides awaiting actuals, grouped by planner/reason, with volume). `ScorecardPage.jsx` renders a "Pending — awaiting actuals" table when nothing has matured, so the scorecard is never blank.

### 6.7 Tests
- `test_fva_pending`, `test_assistant_view_operation`, `test_agent_work_notifications` added; `test_scenario_promote` hardened to base its edit on the consensus value (an existing override on item 106205 exactly cancelled the old `final + 100` edit). Full suite green; frontend production build passes.

### 6.8 Run-detail readability
- The Run Detail panel showed each step's raw `detail` JSON (e.g. `{"tables":20,"rows":33624816}`). Both run endpoints (`GET /api/v1/cycles/runs` and `/runs/{id}`) now attach a plain-language `summary` per step via `_step_summaries()` (`extended.py`), reusing the `_node_summary` formatter from `jobs.py` (same wording as the agent notifications). `OperationsPage.jsx` renders `s.summary` instead of `JSON.stringify(s.detail)`; the node-track hover tooltip uses it too.

---

## 7. Co-Pilot daily-task hub + S&OE/S&OP automation suite (planned Oct 2026)

**Goal.** Turn the Co-Pilot page into Danny's daily task hub: filters and fast tasks under the "S&OP co-pilot" section that run the automated workflows below and land the results (queue, alerts, exports) where Danny already works. The automations target the 60-70% of planner hours lost to data hunting, reconciliations and spreadsheet prep.

**Design principles.**
- Every automation is a small, deterministic service behind a REST endpoint + a scheduled job; the Co-Pilot page is the launcher and results viewer, not a separate app.
- External systems (Amazon EDI/Vendor Central, Slack/Teams, SAP ERP, supplier EDI 830) do not exist in this environment: build **adapters with simulated feeds and export templates**, with a documented seam where real credentials/webhooks plug in.
- Alerts reuse the existing notification store (`app/store/ops.py`, `GET /api/v1/notifications`); schedules reuse the existing scheduler (`app/orchestration/scheduler.py`, daily/monthly, UK time — currently only `monthly_cycle`).
- Each phase ends with backend tests green and the frontend production build passing.

### 7.1 Phase 0 — Co-Pilot task hub UX (the integration layer) — IMPLEMENTED (Oct 2026)
- **Filters** in the left rail of `CopilotPage.jsx`: rhythm (S&OE daily / S&OP weeks 1-2 / week 3 / week 4), channel (Amazon/B2B/D2C), priority (high/medium/low). **Every filter has its own set of fast tasks** — the grid under "S&OP co-pilot" re-renders to the tasks Danny needs to analyse and prove for that rhythm/channel/priority.
- **Fast tasks** under the "S&OP co-pilot" heading: a grid of one-click buttons (one per automation below) that call the new endpoints and open the result — a filtered workbench queue (reusing `action.filters` navigation in `App.jsx`), a chat answer, or a download.
- **Task registry endpoint** `GET /api/v1/tasks` (name, rhythm, channel, priority, status, live attention count, urgent flag) so the rail is data-driven; `POST /api/v1/tasks/{id}/run` executes one task and returns a plain-language summary + navigation actions.
- **Implemented:** `backend/app/api/tasks.py` (12 tasks: Amazon PO confirmations, OTIF exposure, Buy Box watch, exception queue, data-quality gate, promo sanity, safety stock, value at risk, scenario what-if, consensus commit, executive briefing, FVA & autopilot), registered in `main.py`; `CopilotPage.jsx` filter selects + fast-task grid + run-to-chat; `tests/test_tasks.py` (8 tests, green).
- **Performance fix (Oct 2026):** `load()` in `app/data/loaders.py` re-read parquet from disk on every call — the task hub loaded ~100 MB per request (first `/tasks` call took 303 s). Added an `lru_cache` on full-table reads (`clear_cache()` on `/admin/reload`), a 60 s TTL on the attention computation, and a background warm-up at startup. `/api/v1/tasks` now responds in ~50-110 ms.
- **Task-run performance fix (Oct 2026):** `run()` computed `otif.from_plan()` (~13-20 s) and the exception queue for **every** task before branching by task id, so even `consensus_commit` took 55 s. Made the heavy steps lazy (OTIF only for `otif_exposure`, queue only for `exception_queue`/`value_at_risk`), stopped the urgent-notification branch from re-running the full attention computation, and raised the attention TTL to 300 s. Also filtered the big tables to the rows each analysis needs: `amazon_traffic` (11.9 M rows) to the last 10 days, `amazon_inventory` (8.5 M rows) to the last 7 days, and the `orderbook` (2.7 M rows) to the at-risk item ids in `otif._by_customer`. Measured live: every task run now returns in ≤1.4 s (was 14-79 s); cold attention dropped from 46 s to ~3.6 s.

### 7.2 Phase 1 — Daily & weekly S&OE automations (Amazon execution) — chatbot reachability + urgency pings IMPLEMENTED
All three are feasible from existing tables — no new data needed for v1.

**Chatbot reachability (implemented).** Every task detail is reachable over the chatbot: the assistant context (`extended.py` `context()`) now includes the urgent-task list, `suggested_actions()` emits `run_task` chips for "what should I do / urgent / daily" questions, and running a task appends its full summary + actions as an assistant chat message. **Urgency pings (implemented):** running a task with urgent findings emits an in-app notification (`kind: "task"`, planner+admin) via the existing `NotificationStore` + bell; the task card shows a pulsing "N urgent" badge.
- **Task-explanation fix (Oct 2026):** "explain <task>" questions (e.g. "Can you explain Promo sanity check?") previously fell through to lexical doc search, so the LLM answered "the context does not contain an explanation" and cited `DEVELOPMENT_PLAN.md`/`ENHANCEMENT_PLAN.md`/`memory/PRD.md`. The retrieval router now maps explanation questions to the task registry (`task_for_question` in `tasks.py`), the assistant context carries a full `task_catalog` (id/title/description/rhythm/channel/priority), the deterministic fallback has a `task` branch, and `suggested_actions()` adds a "Run: <task>" chip. Verified live: the assistant answers from the registry description with no doc-file references.
- **Suggested follow-ups (Oct 2026):** every assistant response now ends with 2-3 clickable follow-up question chips. The backend emits a `suggestions` SSE event (`suggested_questions()` in `extended.py`) derived deterministically from the question's intent/task (task-specific for "explain <task>", per-intent pools for OTIF/exceptions/quality/safety/FVA/cycle/phasing/top-products/Amazon, generic pool otherwise); task-run messages get client-side suggestions. `api.js` exposes an `onSuggestions` callback; `CopilotPage.jsx` renders the chips under each assistant message and clicking one asks it directly.

**A. Amazon PO ingestion & 24-hour confirmation reconciler**
- Bottleneck: missed 24h EDI 850 acceptance window or over-confirmation → 3-9% COGS chargebacks.
- What exists: `orderbook` (ordered/delivered/cancelled, decision_at), `amazon_inventory` (sellable_on_hand_inventory_units, open_purchase_order_units, vendor_confirmation_rate), `amazon_sell_out`.
- Build: `app/soe/po_reconciler.py` — ATP = sellable on-hand + open PO − committed; confirm lines with coverage ≥100%, flag deficits with recommended partial confirmation before `decision_at + 24h`; daily schedule `amazon_po_reconcile` (daily 07:00 UK); results as `agent_work`-style notifications + a `GET /api/v1/soe/po-confirmations` queue panel.
- Acceptance: every open order line gets a status (confirm / partial / deficit); deficit lines carry a recommended qty; chargeback exposure £ summed.

**B. Amazon shortage deduction & chargeback dispute bot**
- Bottleneck: unmonitored co-op/shortage/receiving deductions erode margin (up to 74% of 1P vendors).
- What exists: `orderbook` delivered vs ordered + cancellation fields; `amazon_inventory.vendor_confirmation_rate`.
- Build: `app/soe/disputes.py` — weekly reconciliation of delivered qty vs confirmed qty per order; where the gap is Amazon-side (delivered < confirmed but no internal cancellation), auto-assemble a dispute packet (order id, ASN/PO refs, evidence rows) and stage it for Vendor Central submission; `GET /api/v1/soe/disputes` + "submit" stub adapter; weekly schedule `amazon_dispute_review` (Monday 08:00 UK).
- Acceptance: disputes list with £ at risk, evidence bundle download, submission log.

**C. Daily detail page & Buy Box monitoring alert**
- Bottleneck: Buy Box loss / glance-view collapse discovered weeks late.
- What exists: `amazon_traffic` (glance_views), `amazon_inventory` (out-of-stock rate), `amazon_sell_out` (ordered_units), `past_covariates` (amazon_avg_daily_glance_views, zero-traffic rate).
- Build: `app/soe/buybox.py` — per-ASIN daily poller (adapter over `amazon_traffic`), alert when an A-item loses Buy Box proxy (vendor_confirmation_rate → 0 or OOS) or glance views drop >25% for 3 consecutive days; Slack/Teams adapter stub + in-app notification; daily schedule `buybox_monitor` (09:00 UK).
- Acceptance: alert fires on the 3-day rule; notification links to the item; suppression list viewable.

### 7.3 Phase 2 — Pre-S&OP commercial automations (weeks 1-2)
**A. Commercial input & promotional sanity checker**
- Bottleneck: late/fragmented KAM promo submissions with unrealistic uplifts.
- What exists: `promotions` (date_from/to, discount, forecast_units, type), `items` (launch/eol, abc), orderbook lead times (`inventory/safety_stock.lead_times`).
- Build: promo intake endpoint `POST /api/v1/promos/validate` + portal form; validation rules: lead time vs required factory date, uplift vs historical campaign lift per customer (flag >300%), packaging/barcode readiness (launch/eol + item master checks); results feed the S&OP baseline.
- Acceptance: each rule returns pass/warn/fail with the evidence; a "needs justification" queue for KAM follow-up.

**B. Retail POS (EDI 852) data cleansing & outlier scrubbing**
- Bottleneck: delivery spikes, bulk transfers, phantom zero-sales distort baselines.
- What exists: `target_time_series` (`imputed`, `exposure`), `triage/quality.py` masking (`mask_incomplete`, `mask_level_series`), `forecasting/quantiles` residual ratios.
- Build: `app/soe/pos_cleanse.py` — month-end pipeline that flags anomalies (single-day returns, test orders, OOS censorship via zero-traffic days), scrubs the series, writes an audit-ready parquet + audit log (what changed, why); `GET /api/v1/soe/pos-cleanse` report.
- Acceptance: anomaly counts by type, before/after series diff, audit log rows; baseline metrics recompute cleanly.

### 7.4 Phase 3 — Financialization & scenario planning (week 3)
**A. Automated volume-to-value financial translation**
- Bottleneck: Danny spends a day converting units to £ for exec/finance.
- What exists: `items.sales_price`, `items.purchase_price`, consensus plan, `allocation/optimiser` margin/penalty config, AOP gap concept in briefing.
- Build: `app/finance/financialize.py` — join consensus units × price/cost/margin/co-op discount → gross revenue, gross margin, working-capital (safety stock £ via `inventory/safety_stock`), gap vs AOP; `GET /api/v1/finance/plan` + a Financial view on the Co-Pilot task hub; CSV/PDF export.
- Acceptance: revenue/margin reconcile to units × price within rounding; AOP gap per channel/month.

**B. One-click scenario simulation engine**
- Bottleneck: exec "what-if" questions take days of Excel rebuilding.
- What exists: scenario store + `POST /api/v1/scenarios`, `generate`, `compare`, `promote` (`ops_routes.py`), `engine.apply_edits`.
- Build: extend the scenario engine with **parameter sliders** (promo date shift, component constraint %, price change) that map to edit templates; instant re-run of financialization + OTIF + safety-stock impact; `GET /api/v1/scenarios/simulate?delta=...` returning downstream deltas; UI sliders on `ScenariosPage`/Co-Pilot fast task.
- Acceptance: a promo-shift slider changes revenue, fill rate and SS in one response; results are explainable (which items moved, why).

### 7.5 Phase 4 — Supply-chain hand-off & executive reporting (week 4)
**A. Automated ERP/MRP parameter & forecast push**
- Bottleneck: hours reformatting CSVs to upload forecasts; stale safety stock in ERP.
- What exists: consensus export (`export/consensus` CSV), safety stock + P10-P90 bands, scheduler.
- Build: `app/handoff/erp_push.py` — approved consensus triggers a webhook adapter (stub endpoint + signed payload) pushing the reconciled plan; concurrently recompute SS/reorder points from the P90−P10 spread and emit an ERP parameter file; `GET /api/v1/handoff/erp` status + retry.
- Acceptance: payload schema documented; push logged with status; SS params match `inventory/safety-stock` output.

**B. Automated supplier forecast sharing (BOM explosion + EDI 830)**
- Bottleneck: manual component requirement calcs and emailed spreadsheets.
- What exists: consensus plan, `baseline_mapping` (product→item), no BOM table yet.
- Build: add a minimal `bom` contract (finished-good → component, qty per unit) + `app/handoff/edi830.py` — explode consensus into component requirements, emit rolling 12-month EDI 830-style CSV per supplier; `GET /api/v1/handoff/edi830` download.
- Acceptance: component totals = finished-goods × BOM ratio; 830 file validates against the template.

**C. Automated executive S&OP deck generation**
- Bottleneck: 1-2 days of slide/waterfall/commentary drafting.
- What exists: `export/briefing.pdf` (executive brief), Gemini/GLM via `narrative/client.py` + `FailoverClient`, waterfall data in consensus vs baseline vs commercial.
- Build: extend the briefing generator into a **deck mode**: month-over-month waterfall (AOP vs statistical baseline vs commercial vs consensus), top-5 exception risks (Amazon chargeback exposure, OTIF), LLM-generated bullet commentary with guardrails (`narrative/guardrails.py`); `GET /api/v1/export/deck.pdf`; deterministic fallback when the LLM is down.
- Acceptance: deck renders without the LLM; with the LLM, commentary cites only numbers present in the data (guardrail-validated).

### 7.6 Cross-cutting
- **Scheduler defaults** (`scheduler.py` `DEFAULTS`): add `amazon_po_reconcile` (daily 07:00), `amazon_dispute_review` (weekly Mon 08:00), `buybox_monitor` (daily 09:00), `pos_cleanse` (monthly day 1), `erp_push` (monthly day 3 after commit). All admin-tunable via the existing `PUT /api/v1/admin/schedules/{job}`.
- **Notifications**: every automation emits `agent_work`-style notifications with a link (existing `NotificationStore` + bell).
- **Adapters**: `app/integrations/` with `AmazonEdiAdapter`, `VendorCentralAdapter`, `MessagingAdapter`, `ErpAdapter` — simulated in this environment, each with a documented credential/webhook seam and a deterministic test double.
- **Tests**: one test module per phase (`tests/test_soe_automations.py`, `tests/test_finance.py`, `tests/test_handoff.py`) + Co-Pilot task-hub component tests; keep the existing suite green.
- **Sequencing**: Phase 0 first (UX shell + task registry), then 1 → 2 → 3 → 4; each phase is independently shippable and demoable.

---

## 8. MCP Configuration for Third-Party Applications (ERP, CRM, Jira) (planned Oct 2026)

**Goal.** Let the user configure Model Context Protocol (MCP) servers for third-party applications — ERP (e.g. SAP S/4HANA, Dynamics 365 F&O), CRM (e.g. Salesforce), Jira — directly from the Co-Pilot page, and let the Co-Pilot assistant call the tools those servers expose when a question needs external-system data. The app is an **MCP client**: the org runs MCP servers next to each third-party system; we store the connection config, discover tools, and invoke them with audit and guardrails.

**Design principles.**
- Same pattern as section 7: a small deterministic service behind REST endpoints + a config store; the Co-Pilot page is the configuration surface and the assistant is the consumer.
- No real ERP/CRM/Jira exists in this environment: ship **reference MCP servers with simulated feeds** (`backend/app/mcp/demo/`) plus documented credential seams, consistent with the section 7 adapter principle.
- MCP transport: **streamable HTTP / SSE** for remote servers (the standard for SaaS MCP servers); stdio support later for local servers. Use the official `mcp` Python SDK client; a minimal JSON-RPC fallback keeps the assistant working if the SDK is unavailable.
- Every tool call is **audited** (who, which server, which tool, args, latency, ok/error) and **time-bounded** (default 10 s) so a hanging external system never blocks the assistant stream.
- Secrets never leave the backend: API keys/tokens are stored server-side (env or `store_dir` with restricted perms) and returned masked (`sk-****last4`) by the API.

### 8.1 Data model — MCP server config

| Field | Type | Notes |
|---|---|---|
| `id` | str | slug, e.g. `erp_sap` |
| `name` | str | display name, e.g. "SAP ERP" |
| `kind` | enum `erp \| crm \| jira \| generic` | drives the template + default tool allowlist |
| `transport` | enum `http_sse \| streamable_http` | default `streamable_http` |
| `url` | str | MCP server endpoint |
| `headers` | dict (masked) | auth headers; `Authorization`/`X-API-Key` stored server-side |
| `enabled` | bool | disabled servers are never called |
| `tools` | list[str] | allowlist of tool names; empty = all discovered tools |
| `timeout_s` | int | default 10 |
| `created_by` / `updated_at` | str | audit fields |

Persisted in a new `McpStore` following the existing store pattern (`app/store/ops.py` Mongo collection `mcp_servers`, or `JsonMcpStore` at `store_dir / "mcp_servers.json"` for the local build).

### 8.2 Backend module `backend/app/mcp/`

- **`client.py`** — thin MCP client wrapper: `connect(config)` (lazy, per-call), `list_tools()`, `call_tool(name, args)` with timeout, result-size cap (e.g. 256 KB), and error mapping (`timeout | auth | not_found | server_error`). Uses the `mcp` SDK when installed; otherwise a minimal JSON-RPC `POST` over the configured transport.
- **`registry.py`** — CRUD over `McpStore` with secret masking, kind templates, and the tool allowlist filter.
- **`templates.py`** — per-kind reference tool schemas so the UI can show what a server should expose:
  - **ERP (SAP/D365):** `get_open_purchase_orders`, `get_stock_levels`, `get_material_master`, `get_supplier_lead_times`, `push_forecast` (write, admin-gated).
  - **CRM (Salesforce):** `get_account`, `get_opportunities`, `get_cases`, `get_forecast_commit`.
  - **Jira:** `search_issues`, `get_issue`, `create_issue` (write, admin-gated).
- **`demo/`** — reference MCP servers (`erp_server.py`, `crm_server.py`, `jira_server.py`) implementing the MCP protocol over the same transport with simulated data derived from the existing tables (order book → open POs, items → material master, OTIF rows → Jira issues), so the feature is demoable end-to-end without external systems.
- **`audit.py`** — one `mcp_call` log row per invocation (server, tool, args summary, ok/error, latency, user) in the run store / ledger-adjacent store.

### 8.3 API `backend/app/api/mcp.py` (registered in `main.py` like `tasks.py`)

| Endpoint | Roles | Purpose |
|---|---|---|
| `GET /api/v1/mcp/servers` | all | list servers; secrets masked; `status` (last test result) |
| `POST /api/v1/mcp/servers` | admin | create (validates URL + kind) |
| `PUT /api/v1/mcp/servers/{id}` | admin | update (headers re-sent fields only) |
| `DELETE /api/v1/mcp/servers/{id}` | admin | delete |
| `POST /api/v1/mcp/servers/{id}/test` | admin | connect + `list_tools()`; stores `status` + discovered tools |
| `GET /api/v1/mcp/servers/{id}/tools` | all | discovered tools (from last test) |
| `POST /api/v1/mcp/servers/{id}/call` | planner/admin | call one allowlisted tool; audited; disabled server → 409 |

### 8.4 Assistant integration (`assistant_stream` in `backend/app/api/extended.py`)

- New `_mcp_intent(question)` step alongside `_structured_intent`: matches external-system words ("erp", "sap", "crm", "salesforce", "jira", "open po", "stock level", "ticket", "account", "opportunity") to the configured server of that kind.
- When an intent matches an **enabled** server: call the mapped tool(s) (e.g. `get_open_purchase_orders` for "open POs in SAP"), append the JSON result to the prompt as `MCP EVIDENCE (<server>): ...`, and emit a `sources` SSE event entry `{title: "mcp:<server>", snippet: tool + args}` so the UI shows an `mcp` source chip (reuse the existing tier/sources rendering in `CopilotPage.jsx`).
- Guardrails: only allowlisted tools; tool results are treated like Tier-1 evidence (numbers must appear in the evidence before the answer can quote them — reuse `_unverifiable_numbers`); MCP failure → deterministic fallback answer ("the ERP connection is unavailable"), never a 500 or a fabricated figure.
- The existing `context()` snapshot and task hub are untouched; MCP is additive.

### 8.5 Co-Pilot UI (`frontend/src/pages/CopilotPage.jsx` + `frontend/src/services/api.js`)

- **Connections panel**: a "Connections (MCP)" collapsible section in the left rail (between Fast tasks and suggestions) listing configured servers with kind badge (ERP/CRM/Jira), enabled toggle, and last-test status (connected / failed / never tested).
- **Add/Edit modal**: name, kind (selecting a kind pre-fills the template tool list and a sample URL), transport, URL, auth headers (token input, stored server-side), tool allowlist checkboxes (from the last test), timeout. "Test connection" button calls `POST .../test` and shows the discovered tools inline.
- **Source chips**: assistant answers that used an MCP tool render an `mcp:<server>` chip (same style as the existing `copilot-source-*` chips); clicking it shows the tool call details.
- **`api.js`**: add `mcpServers()`, `createMcpServer(body)`, `updateMcpServer(id, body)`, `deleteMcpServer(id)`, `testMcpServer(id)`, `mcpTools(id)`, `callMcpTool(id, name, args)`.

### 8.6 Security and ops

- RBAC: admin manages servers; planner/admin can call tools; auditor can read configs and the `mcp_call` audit log.
- Secrets: never returned by `GET` (masked); stored in env/`store_dir`; no secrets in the frontend bundle.
- Timeouts + result caps on every call; a slow/hung server cannot stall the assistant stream (call happens before streaming or in a bounded task).
- Write tools (`push_forecast`, `create_issue`) are admin-gated and always audited; the default allowlist for planner use is read-only.

### 8.7 Tests

- Unit: registry CRUD + secret masking; allowlist filtering; timeout → `timeout` error; template tool lists per kind.
- API: admin-only create/update/delete (403 for planner); test-connection returns tools; call on a disabled server → 409; every call writes an `mcp_call` audit row.
- Assistant: "open POs in SAP" triggers the ERP tool and the answer carries an `mcp:erp` source; MCP server down → deterministic fallback, no 500; numbers in the answer are verifiable against the tool evidence.
- Frontend: connections panel renders servers + status; test button shows discovered tools; assistant message shows the `mcp` source chip.
- Demo: `demo/` servers answer `list_tools` and `call_tool` for the seeded data; full backend suite + frontend production build stay green.

### 8.8 Sequencing

- **Phase A — Registry + API + store**: `McpStore`, `registry.py`, `mcp.py` endpoints, RBAC, secret masking. No assistant changes.
- **Phase B — Demo servers + test connection**: `demo/` ERP/CRM/Jira MCP servers, `test` endpoint, tools listing.
- **Phase C — Assistant integration**: `_mcp_intent`, tool invocation in `assistant_stream`, `mcp` source events, deterministic fallback.
- **Phase D — Co-Pilot UI**: connections panel, add/edit modal, test button, source chips, `api.js` methods.
- **Phase E — Hardening**: audit log UI, write-tool gating, timeout/result-cap tuning, real-credential seams documented.

Each phase ends with the backend test suite green and the frontend production build passing.

---

## Sequencing

1. **Agentic forecasting** (largest, self-contained) — backend agent + endpoint, then UI.
2. ~~Urgent co-pilot~~ — implemented then fully reverted at the user's request (Oct 2026); do not reintroduce.
3. **Supply & Risk / Operations completion** — incremental panels and the scenario-promotion ledger action.
4. **Hybrid search fallback** — Phase A (project-data retrieval + answerability gate), then Phase B (Gemini knowledge-base fallback behind the `HYBRID_SEARCH` flag).
5. **Co-Pilot daily-task hub + S&OE/S&OP automations** (section 7) — Phase 0 task-hub UX first, then Amazon S&OE (1), pre-S&OP (2), financialization (3), hand-off & deck (4).
6. **MCP configuration for third-party apps** (section 8) — Phase A registry/API first, then demo servers, assistant integration, Co-Pilot UI, hardening.

Each step ends with the backend test suite green and the frontend production build passing.
