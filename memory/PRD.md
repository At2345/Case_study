# PRD: ML Demand Forecasting Co-Pilot (UK consumer goods S&OP)

## Original problem statement
"Read the DEVELOPMENT_PLAN.md in my repository and existing codes and implement the rest."
User choices: full scope including Phase 2-4; Gemini 3 Flash via the Emergent Universal Key; preview adapter (backend/server.py + Vite on :3000); MongoDB audit ledger.

## Architecture
FastAPI (backend/app, entry backend/server.py) over Parquet data (data/data) and pipeline artifacts (reports/); MongoDB for the ledger, cycle_runs and assistant_messages; React 18 + Vite + Tailwind workbench (frontend/); LangGraph orchestration; DuckDB daily pipeline; reportlab briefing; emergentintegrations LLM.

## Personas
Demand planner (Danny), admin (runs the cycle), auditor (read-only, verifies the ledger).

## Implemented (2026-06)
- Existing Phase 1 (pre-existing): evaluation, fallback, triage, API/JWT/RBAC, workbench, allocation, phasing.
- WS-F Gemini narratives with guardrails and cache; co-pilot chat (SSE, persisted per user)
- MongoDB insert-only hash-chained ledger (bug fix: collection attribute shadowed overrides()), run store
- F-B2B-03 OTIF check (configurable terms), F-SOP-01 disaggregation (MinT diag / proportional / OLS) + modal
- Phase 2 DuckDB daily Amazon pipeline (100% regression vs past_covariates, ~3 s)
- Phase 3 conformal P10/P90 bands (94% out-of-sample coverage), dynamic safety stock with a service back-test
- Phase 4 LangGraph monthly cycle with guards + human-review interrupt, autopilot negative-FVA rule, exec briefing PDF, Prometheus metrics
- UI tabs: Workbench, FVA & audit, Supply & risk, Operations, Co-pilot
- Tests: 185 backend + 20 frontend pass; testing agent iteration_1 all flows pass

## Backlog
- P1: production scheduling and sources (S3/MinIO drops, TPM promo sync, EDI 852), OpenTelemetry spans, drift alerts
- P1: censoring-aware (exposure-weighted) training/evaluation; more rolling-origin cutoffs for MinT-shrink
- P2: PPT export, per-customer OTIF clauses once data exists (Q4), business sign-off on open questions Q1-Q18
