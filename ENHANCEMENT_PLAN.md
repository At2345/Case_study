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

## Sequencing

1. **Agentic forecasting** (largest, self-contained) — backend agent + endpoint, then UI.
2. ~~Urgent co-pilot~~ — implemented then fully reverted at the user's request (Oct 2026); do not reintroduce.
3. **Supply & Risk / Operations completion** — incremental panels and the scenario-promotion ledger action.
4. **Hybrid search fallback** — Phase A (project-data retrieval + answerability gate), then Phase B (Gemini knowledge-base fallback behind the `HYBRID_SEARCH` flag).

Each step ends with the backend test suite green and the frontend production build passing.
