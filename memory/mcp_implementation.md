# MCP integration — implementation status

Plan: connect the Co-Pilot to external systems (ERP/CRM/Jira) via MCP servers.

## Status

- **Phase A — registry + client (DONE)**: `backend/app/mcp/` (templates, registry, client),
  `backend/app/store/mcp.py` (Mongo + JSON stores), `backend/app/api/mcp.py` endpoints,
  wired in `backend/app/api/main.py`. Tests: `backend/tests/test_mcp.py` (registry/RBAC/test/call/audit).
- **Phase B — demo servers (DONE)**: `backend/app/mcp/demo/` — ERP/CRM/Jira MCP servers over
  streamable HTTP, fed from the cycle tables. Run:
  `python -m app.mcp.demo --kind erp --port 9101` (crm 9102, jira 9103).
- **Phase C — assistant integration (DONE)**: `backend/app/mcp/intent.py` maps questions to
  tools; `assistant_stream` in `backend/app/api/extended.py` appends MCP evidence to the prompt,
  emits an `mcp` source chip, and answers deterministically when the server is unreachable.
  MCP evidence numbers are exempt from the unverifiable-number check.
- **Phase D — Co-Pilot UI (DONE)**: `frontend/src/pages/McpPage.jsx` (admin config page:
  list/add/edit/delete, test connection, discovered tools, audit log), nav item in
  `Header.jsx` (admin only), routing in `App.jsx`, `mcp` source chips (emerald + plug)
  in `CopilotPage.jsx`, API methods in `services/api.js`. Tests: `McpPage.test.jsx` (5).
- **Phase E — audit/security hardening (DONE)**: write tools (`push_forecast`, `create_issue`)
  admin-gated in `api/mcp.py` and never mapped by the assistant (`intent.py`); audit rows now
  carry `args` summary + `latency_ms` (shown in the McpPage audit table); real-credential seams
  documented in `backend/app/mcp/README.md`. Tests: `TestWriteToolGating` (planner 403 / admin
  attempts, intent never maps to write tools).

## Notes

- Demo ERP server config `erp_demo` may exist in the store pointing at `http://127.0.0.1:9101/`.
- `test_mcp.py::TestAssistantMCP` requires the demo ERP server on 9101.
- **Planner password (local .env)**: `D4nny-Plan-2026!XH2` — NOT the documented default.
  `server.py::seed_users()` overwrites danny on every startup from `SEED_PLANNER_PASSWORD`,
  so run tests with `TEST_PLANNER_PASSWORD=D4nny-Plan-2026!XH2`.
- Verified 2026-10-16: `test_mcp.py` 16/16 green (incl. write-tool gating). Frontend 25/25 +
  production build green. Regression: `test_sop_api.py`+`test_tasks.py`
  53/56 (3 pre-existing: task-hub intent routing expectations, agent_work notifications need a
  cycle run), `test_ops_scenarios.py` 28/29 (queue_ready notification needs a cycle run).
