# n8n-nodes-sop-workbench

n8n community nodes for the S&OP Workbench playbook engine.

| Node | What it does |
|---|---|
| **S&OP Workbench** | Playbook: get, get many. Run: start (or dry run), get, get many, approve or reject a waiting step. |
| **S&OP Workbench Trigger** | Polls for runs that need approval, finished (succeeded, failed, rejected), or changed. |

Typical flow: *Trigger (Run Needs Approval) → Slack "send and wait for approval" → S&OP Workbench (Run: Approve or Reject)*.

## Credentials

1. An admin creates an API key in the workbench: `POST /api/v1/admin/api-keys` with `{"name": "n8n", "role": "planner"}`
   (use `auditor` for read-only). The key (`sopk_…`) is shown once.
2. In n8n, add **S&OP Workbench API** credentials with the workbench base URL and the key.

API keys only reach the workflow endpoints (`/api/v1/workflows…`, `/api/v1/workflow-runs…`) and act in the key's tenant.

## Calling n8n from a playbook

Add an **action.n8n_webhook** node in the workbench editor with the URL of an n8n *Webhook* node. The
workbench sends the run context (or a custom body) with an `Idempotency-Key: <run_id>:<node_id>` header and
continues with the JSON response (`$nodes.<id>.response…`). Optional `secret_ref: N8N` sends the server
environment value `WF_SECRET_N8N` as `X-Workflow-Secret`; check it in n8n with the Webhook node's header auth.
For n8n on localhost or a private network, set `WORKFLOW_ALLOW_PRIVATE_WEBHOOKS=true` on the workbench server.

## Development

```bash
npm install
npm test          # tsc build + node:test unit tests
```

To try it in a local n8n, link the built package into `~/.n8n/custom` (see the n8n docs on running
nodes locally). Before publishing to npm: MIT licence, no runtime dependencies, publish from GitHub
Actions with provenance, and run `npx @n8n/scan-community-package n8n-nodes-sop-workbench`.
