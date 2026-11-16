# MCP integration (ERP / CRM / Jira)

The app is an **MCP client**: the org runs MCP servers next to each third-party system
(SAP S/4HANA, Dynamics 365, Salesforce, Jira), and the Co-Pilot assistant calls the tools
those servers expose when a question needs external-system data.

## Demo servers (no external systems needed)

Reference MCP servers with simulated feeds derived from the cycle tables:

```bash
python -m app.mcp.demo --kind erp --port 9101   # get_open_purchase_orders, get_stock_levels, ...
python -m app.mcp.demo --kind crm --port 9102   # get_account, get_opportunities, get_cases, ...
python -m app.mcp.demo --kind jira --port 9103  # search_issues, get_issue, create_issue
```

Configure them in the Co-Pilot UI (admin → MCP) with URL `http://127.0.0.1:9101/` etc.,
then "Test connection" to discover the tools.

## Connecting a real server

1. **Transport**: `streamable_http` (single POST JSON-RPC endpoint, the default for SaaS
   MCP servers) or `http_sse`. The built-in client speaks JSON-RPC 2.0 over HTTP and does
   not require the `mcp` Python SDK.
2. **URL**: the server's MCP endpoint, e.g. `https://erp.example.com/mcp`.
3. **Auth headers**: one per line in the UI, e.g. `Authorization: Bearer sk-...` or
   `X-API-Key: ...`. Headers are stored server-side in `store_dir` (`mcp_servers.json` or
   the Mongo `mcp_servers` collection) and **never returned by the API** — `GET` shows
   masked values (`Be****3456`). No secrets reach the frontend bundle.
4. **Allowlist**: restrict which discovered tools the assistant/planner may call. Empty
   allowlist = all discovered tools allowed.
5. **Timeout**: per-server `timeout_s` (1–120, default 10). Responses are capped at 256 KB.

## Security model

- **RBAC**: admin manages servers and calls write tools; planner/admin call read tools;
  all roles can read configs and the `mcp_calls` audit log.
- **Write tools** (`push_forecast` for ERP, `create_issue` for Jira) are admin-only via
  `POST /api/v1/mcp/servers/{id}/call` and are **never** invoked by the assistant.
- **Audit**: every tool call writes a `mcp_calls` row (server, tool, args summary, user,
  ok/error, latency_ms). View it on the MCP page or `GET /api/v1/mcp/calls`.
- **Assistant guardrails**: only allowlisted read tools; tool results are treated as
  Tier-1 evidence (numbers must appear in the evidence before the answer can quote them);
  a failed/unreachable server yields a deterministic "connection is unavailable" answer —
  never a 500 or a fabricated figure.
- **Disabled servers** are never called (409 on direct calls, skipped by the assistant).

## API surface

| Endpoint | Roles | Purpose |
|---|---|---|
| `GET /api/v1/mcp/servers` | all | list servers (secrets masked) + kind templates |
| `POST /api/v1/mcp/servers` | admin | create |
| `PUT /api/v1/mcp/servers/{id}` | admin | update (headers merge) |
| `DELETE /api/v1/mcp/servers/{id}` | admin | delete |
| `POST /api/v1/mcp/servers/{id}/test` | admin | connect + discover tools |
| `GET /api/v1/mcp/servers/{id}/tools` | all | tools from the last test |
| `POST /api/v1/mcp/servers/{id}/call` | planner/admin | call one allowlisted tool (write tools: admin) |
| `GET /api/v1/mcp/calls` | all | audit log |

## Tests

- Backend: `backend/tests/test_mcp.py` (registry, RBAC, test connection, calls, audit,
  assistant evidence + fallback). The assistant tests need the demo ERP server on 9101.
- Frontend: `frontend/src/pages/McpPage.test.jsx`.
