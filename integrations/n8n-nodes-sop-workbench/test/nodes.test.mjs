// Unit tests for the compiled nodes (node --test). No n8n runtime needed: n8n's context is faked.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { SopWorkbenchTrigger, matchesEvent } = require("../dist/nodes/SopWorkbenchTrigger/SopWorkbenchTrigger.node.js");
const { SopWorkbench } = require("../dist/nodes/SopWorkbench/SopWorkbench.node.js");
const { SopWorkbenchApi } = require("../dist/credentials/SopWorkbenchApi.credentials.js");

function fakePoll({ event = "needsApproval", playbookId = "", mode = "trigger", staticData = {}, runs = [] } = {}) {
  const calls = [];
  const ctx = {
    getNodeParameter: (name, fallback) => ({ event, playbookId }[name] ?? fallback),
    getMode: () => mode,
    getWorkflowStaticData: () => staticData,
    getCredentials: async () => ({ baseUrl: "https://wb.example.com/", apiKey: "sopk_x" }),
    helpers: {
      httpRequestWithAuthentication: async function (credName, options) {
        calls.push({ credName, ...options });
        return { runs };
      },
      returnJsonArray: (items) => items.map((json) => ({ json })),
    },
  };
  return { ctx, calls, staticData };
}

const run = (id, status, updated) => ({ run_id: id, status, updated_at: updated });

test("first activation only sets the cursor and replays nothing", async () => {
  const { ctx, calls, staticData } = fakePoll({ runs: [run("a", "WAITING_APPROVAL", "2026-11-26T10:00:00+00:00")] });
  const out = await new SopWorkbenchTrigger().poll.call(ctx);
  assert.equal(out, null);
  assert.equal(calls.length, 0);
  assert.match(staticData.lastUpdatedAt, /^\d{4}-\d{2}-\d{2}T/);
});

test("polls changes since the cursor, filters server-side and advances the cursor", async () => {
  const staticData = { lastUpdatedAt: "2026-11-26T09:00:00+00:00" };
  const runs = [run("a", "WAITING_APPROVAL", "2026-11-26T09:30:00+00:00"), run("b", "WAITING_APPROVAL", "2026-11-26T09:45:00+00:00")];
  const { ctx, calls } = fakePoll({ staticData, runs, playbookId: " rescue-de " });
  const out = await new SopWorkbenchTrigger().poll.call(ctx);
  assert.deepEqual(out[0].map((i) => i.json.run_id), ["a", "b"]);
  assert.equal(calls[0].credName, "sopWorkbenchApi");
  assert.equal(calls[0].url, "https://wb.example.com/api/v1/workflow-runs");
  assert.deepEqual(calls[0].qs, { limit: 500, updated_after: "2026-11-26T09:00:00+00:00", status: "WAITING_APPROVAL", workflow_id: "rescue-de" });
  assert.equal(staticData.lastUpdatedAt, "2026-11-26T09:45:00+00:00");
});

test("finished event filters client-side but still advances past non-matching changes", async () => {
  const staticData = { lastUpdatedAt: "2026-11-26T09:00:00+00:00" };
  const runs = [run("a", "WAITING", "2026-11-26T09:10:00+00:00"), run("b", "SUCCEEDED", "2026-11-26T09:20:00+00:00"),
    run("c", "WAITING_APPROVAL", "2026-11-26T09:30:00+00:00")];
  const { ctx, calls } = fakePoll({ event: "finished", staticData, runs });
  const out = await new SopWorkbenchTrigger().poll.call(ctx);
  assert.deepEqual(out[0].map((i) => i.json.run_id), ["b"]);
  assert.equal(calls[0].qs.status, undefined);
  assert.equal(staticData.lastUpdatedAt, "2026-11-26T09:30:00+00:00");

  const none = fakePoll({ event: "finished", staticData, runs: [run("d", "WAITING", "2026-11-26T09:40:00+00:00")] });
  assert.equal(await new SopWorkbenchTrigger().poll.call(none.ctx), null);
  assert.equal(staticData.lastUpdatedAt, "2026-11-26T09:40:00+00:00");
});

test("manual mode returns recent runs without touching the cursor", async () => {
  const staticData = {};
  const { ctx, calls } = fakePoll({ mode: "manual", event: "anyChange", staticData, runs: [run("z", "RUNNING", "2026-11-26T09:00:00+00:00")] });
  const out = await new SopWorkbenchTrigger().poll.call(ctx);
  assert.equal(out[0][0].json.run_id, "z");
  assert.deepEqual(calls[0].qs, { limit: 5 });
  assert.deepEqual(staticData, {});
});

test("event matching", () => {
  assert.equal(matchesEvent("needsApproval", run("a", "WAITING_APPROVAL", "")), true);
  assert.equal(matchesEvent("finished", run("a", "REJECTED", "")), true);
  assert.equal(matchesEvent("finished", run("a", "WAITING", "")), false);
  assert.equal(matchesEvent("anyChange", run("a", "RUNNING", "")), true);
});

test("action node routes map to the workbench API", () => {
  const d = new SopWorkbench().description;
  assert.equal(d.requestDefaults.baseURL, '={{$credentials.baseUrl.replace(/\\/+$/, "")}}');
  const ops = Object.fromEntries(
    d.properties.filter((p) => p.name === "operation").flatMap((p) =>
      p.options.map((o) => [`${p.displayOptions.show.resource[0]}.${o.value}`, o.routing.request])),
  );
  assert.deepEqual(ops["run.start"], { method: "POST", url: "=/api/v1/workflows/{{encodeURIComponent($parameter.playbookId)}}/runs" });
  assert.deepEqual(ops["run.approve"], { method: "POST", url: "=/api/v1/workflow-runs/{{encodeURIComponent($parameter.runId)}}/approve" });
  assert.equal(ops["run.getAll"].url, "/api/v1/workflow-runs");
  assert.equal(ops["playbook.getAll"].url, "/api/v1/workflows");
  const bodyProps = d.properties.filter((p) => p.routing && p.routing.send && p.routing.send.type === "body").map((p) => p.routing.send.property);
  assert.deepEqual(bodyProps.sort(), ["approved", "dry_run", "metric", "node", "note", "note", "payload", "sku", "value"]);
  assert.deepEqual(ops["run.recordOutcome"], { method: "POST", url: "=/api/v1/workflow-runs/{{encodeURIComponent($parameter.runId)}}/outcomes" });
});

test("credential sends a bearer API key and tests against the executor endpoint", () => {
  const c = new SopWorkbenchApi();
  assert.equal(c.authenticate.properties.headers.Authorization, "=Bearer {{$credentials.apiKey}}");
  assert.equal(c.test.request.url, "/api/v1/workflows-executor");
  assert.equal(c.properties.find((p) => p.name === "apiKey").typeOptions.password, true);
});
