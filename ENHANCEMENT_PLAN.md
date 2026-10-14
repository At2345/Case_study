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

## Sequencing

1. **Agentic forecasting** (largest, self-contained) — backend agent + endpoint, then UI.
2. **Urgent co-pilot** (small, high value) — classifier + fast endpoint + UI.
3. **Supply & Risk / Operations completion** — incremental panels and the scenario-promotion ledger action.

Each step ends with the backend test suite green and the frontend production build passing.
