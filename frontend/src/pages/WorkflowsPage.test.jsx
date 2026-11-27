import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi, describe, it, expect, beforeEach } from "vitest";
import catalogue from "../test/fixtures/workflowNodeTypes.json";

// React Flow needs real layout (ResizeObserver, DOMMatrix); replace it with a minimal renderer of our nodes.
vi.mock("@xyflow/react", async () => {
  const R = await import("react");
  const applyChanges = (items, changes) => changes.reduce((acc, c) => (c.type === "remove" ? acc.filter((i) => i.id !== c.id) : acc), items);
  const useListState = (init) => {
    const [items, setItems] = R.useState(init);
    return [items, setItems, (changes) => setItems((cur) => applyChanges(cur, changes))];
  };
  return {
    ReactFlowProvider: ({ children }) => children,
    ReactFlow: ({ nodes, edges, nodeTypes, onNodeClick, onPaneClick, onConnect }) => (
      <div data-testid="rf-canvas" data-edges={edges.map((e) => e.id).join(",")}>
        <button data-testid="rf-pane" onClick={() => onPaneClick && onPaneClick()}>pane</button>
        <button data-testid="rf-connect" onClick={() => onConnect && onConnect(window.__rfConnect)}>connect</button>
        {nodes.map((n) => {
          const C = nodeTypes[n.type];
          return <div key={n.id} onClick={(ev) => onNodeClick && onNodeClick(ev, n)}><C id={n.id} data={n.data} selected={false} /></div>;
        })}
      </div>
    ),
    Handle: () => null, Background: () => null, Controls: () => null, MiniMap: () => null,
    Position: { Top: "top", Bottom: "bottom" },
    useNodesState: useListState, useEdgesState: useListState,
    useReactFlow: () => ({ screenToFlowPosition: (p) => p }),
  };
});

vi.mock("../services/api", () => ({
  api: {
    workflowNodeTypes: vi.fn(), workflows: vi.fn(), workflow: vi.fn(), validateWorkflow: vi.fn(),
    saveWorkflow: vi.fn(), startWorkflowRun: vi.fn(), workflowRuns: vi.fn(), workflowRun: vi.fn(),
    approveWorkflowRun: vi.fn(), workflowRunLedger: vi.fn(), recordWorkflowOutcome: vi.fn(), workflowPerformance: vi.fn(),
    workflowTemplates: vi.fn(), installWorkflowTemplate: vi.fn(), draftWorkflow: vi.fn(),
  },
}));
import { api } from "../services/api";
import WorkflowsPage from "./WorkflowsPage";

const saved = {
  id: "rescue-de", name: "Rescue DE", version: 2, trigger: "start", budget_eur: 2000, approval_threshold_eur: null,
  nodes: [
    { id: "start", type: "trigger.manual", params: {} },
    { id: "move", type: "action.transfer_stock", params: { sku: "MAT", from_marketplace: "DE", to_marketplace: "FR", units: 600, estimated_cost_eur: 900 } },
  ],
  edges: [{ source: "start", target: "move", port: "out" }],
};

const waitingRun = {
  run_id: "abc123", workflow_id: "rescue-de", workflow_version: 2, status: "WAITING_APPROVAL", dry_run: false,
  spent_eur: 0, error: null, pending: { node: "move", kind: "gate", reason: "action requires approval" },
  steps: [{ node: "start", status: "ok", port: "out", cost_eur: 0 }, { node: "move", status: "awaiting_approval", port: null, cost_eur: 0 }],
};

describe("WorkflowsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.workflowNodeTypes.mockResolvedValue(catalogue);
    api.workflows.mockResolvedValue({ workflows: [{ id: "rescue-de", name: "Rescue DE", version: 2 }] });
    api.workflow.mockResolvedValue(saved);
    api.workflowRuns.mockResolvedValue({ runs: [] });
    api.workflowPerformance.mockResolvedValue({ playbooks: [] });
    api.workflowRunLedger.mockResolvedValue({ decisions: [], outcomes: [] });
    api.workflowTemplates.mockResolvedValue({ templates: [] });
  });

  it("shows template benchmarks and installs a template into the editor", async () => {
    const measured = {
      id: "rescue-measured", version: 1, name: "Rescue with measured outcome", description: "Price cut, wait, measure.",
      category: "overstock rescue", example_payload: { sku: "MAT-BLUE", gap: -0.2 },
      params: [
        { name: "budget_eur", type: "number", default: 2000, description: "Max spend", required: false },
        { name: "stock_webhook_url", type: "url", default: null, description: "n8n Webhook", required: true },
      ],
    };
    const basic = { ...measured, id: "rescue-basic", name: "Rescue: discount or transfer", params: [] };
    api.workflowTemplates.mockResolvedValue({ templates: [
      { template: measured, yours: { runs: 2, value_per_eur: 5.62 },
        pooled: { tenants: 4, runs: 9, value_per_eur: 4.1 }, pooled_status: "available" },
      { template: basic, yours: null, pooled: null, pooled_status: "share_to_see" },
    ] });
    api.installWorkflowTemplate.mockResolvedValue({ ...saved, id: "de-measured", name: measured.name,
      template: { id: "rescue-measured", version: 1 } });
    api.workflow.mockResolvedValue({ ...saved, id: "de-measured", name: measured.name, version: 1,
      template: { id: "rescue-measured", version: 1 } });

    const user = userEvent.setup();
    render(<WorkflowsPage role="planner" />);
    expect(await screen.findByTestId("wf-template-pooled-rescue-measured")).toHaveTextContent("4 companies · 9 runs · €4.1 / €1");
    expect(screen.getByTestId("wf-template-rescue-measured")).toHaveTextContent("Yours: 2 runs · €5.62 / €1");
    expect(screen.getByTestId("wf-template-pooled-rescue-basic")).toHaveTextContent("Share your results");

    await user.click(screen.getByTestId("wf-template-install-rescue-measured"));
    expect(screen.getByTestId("wf-template-field-budget_eur")).toHaveValue("2000");
    await user.clear(screen.getByTestId("wf-template-field-workflow_id"));
    await user.type(screen.getByTestId("wf-template-field-workflow_id"), "de-measured");
    await user.type(screen.getByTestId("wf-template-field-stock_webhook_url"), "https://n8n.example.com/webhook/stock");
    await user.click(screen.getByTestId("wf-template-confirm-rescue-measured"));

    await waitFor(() => expect(api.installWorkflowTemplate).toHaveBeenCalledWith("rescue-measured", {
      workflow_id: "de-measured", params: { budget_eur: 2000, stock_webhook_url: "https://n8n.example.com/webhook/stock" } }));
    expect(await screen.findByText(/Installed Rescue with measured outcome as de-measured/)).toBeInTheDocument();
    expect(api.workflow).toHaveBeenCalledWith("de-measured");
    expect(screen.getByTestId("wf-payload")).toHaveValue(JSON.stringify(measured.example_payload, null, 2));

    // Saving a new version keeps the template link, so its results stay in the benchmark.
    api.saveWorkflow.mockResolvedValue({ ...saved, id: "de-measured", version: 2 });
    await user.click(screen.getByTestId("wf-save"));
    await waitFor(() => expect(api.saveWorkflow).toHaveBeenCalled());
    expect(api.saveWorkflow.mock.calls[0][0].template).toEqual({ id: "rescue-measured", version: 1 });
  });

  it("drafts a playbook from text into the editor without saving", async () => {
    api.draftWorkflow.mockResolvedValue({
      method: "template", errors: ["cut.pct_change: Input should be less than or equal to 50"],
      notes: ["Matched template 'rescue-basic' from keywords (no LLM used)."],
      spec: { ...saved, id: "discount-slow-de-stock", name: "Discount slow DE stock", version: 1 },
    });
    const user = userEvent.setup();
    render(<WorkflowsPage role="planner" />);
    const box = await screen.findByTestId("wf-describe");
    expect(screen.getByTestId("wf-draft")).toBeDisabled();
    await user.type(box, "Discount slow DE stock");
    await user.click(screen.getByTestId("wf-draft"));
    await waitFor(() => expect(api.draftWorkflow).toHaveBeenCalledWith("Discount slow DE stock"));
    expect(await screen.findByTestId("wf-node-move")).toBeInTheDocument();
    expect(screen.getByText("Not saved yet")).toBeInTheDocument(); // draft stays unsaved
    expect(screen.getByTestId("wf-errors")).toHaveTextContent("pct_change");
    expect(screen.getByText(/Drafted from the closest template\. Review every step, then save\. Matched template/)).toBeInTheDocument();
    expect(screen.getByTestId("wf-run")).toBeDisabled(); // cannot run an unsaved draft
    expect(api.saveWorkflow).not.toHaveBeenCalled();
  });

  it("auditors see templates without install buttons", async () => {
    api.workflowTemplates.mockResolvedValue({ templates: [{ template: { id: "rescue-basic", name: "Rescue", description: "",
      params: [], example_payload: {} }, yours: null, pooled: null, pooled_status: "insufficient_data" }] });
    render(<WorkflowsPage role="auditor" />);
    expect(await screen.findByTestId("wf-template-pooled-rescue-basic")).toHaveTextContent("Not enough companies");
    expect(screen.queryByTestId("wf-template-install-rescue-basic")).not.toBeInTheDocument();
  });

  it("ranks playbooks by measured results and records an outcome", async () => {
    api.workflowPerformance.mockResolvedValue({ playbooks: [
      { workflow_id: "rescue-fr", runs: 3, spent_eur: 160, value_eur: 400, value_per_eur: 2.5, approval_rate: null },
      { workflow_id: "rescue-de", runs: 1, spent_eur: 900, value_eur: 0, value_per_eur: null, approval_rate: 1 },
    ] });
    const finished = { ...waitingRun, status: "SUCCEEDED", pending: null, spent_eur: 900,
      steps: [...waitingRun.steps, { node: "move", status: "ok", port: "out", cost_eur: 900 }] };
    api.workflowRun.mockResolvedValue(finished);
    api.workflowRuns.mockResolvedValue({ runs: [{ ...finished, id: finished.run_id }] });
    const before = { decisions: [
      { id: 1, kind: "approval_request", node_id: "move", status: "awaiting_approval", cost_eur: 0 },
      { id: 2, kind: "human_decision", node_id: "move", status: "approved", cost_eur: 0, actor: "root" },
      { id: 3, kind: "action", node_id: "move", status: "ok", cost_eur: 900 },
    ], outcomes: [] };
    api.workflowRunLedger.mockResolvedValueOnce(before).mockResolvedValue({
      ...before, outcomes: [{ id: 9, metric: "loss_avoided_eur", value: 1500, recorded_by: "danny" }] });
    api.recordWorkflowOutcome.mockResolvedValue({ id: 9 });

    const user = userEvent.setup();
    render(<WorkflowsPage role="planner" />);
    expect(await screen.findByTestId("wf-perf-rescue-fr")).toHaveTextContent("€2.5 / €1");
    expect(screen.getByTestId("wf-perf-rescue-de")).toHaveTextContent("not measured");
    expect(screen.getByTestId("wf-perf-rescue-de")).toHaveTextContent("100% approved");

    await user.click(await screen.findByTestId("wf-open-rescue-de"));
    await user.click(await screen.findByText("abc123"));
    expect(await screen.findByTestId("wf-ledger")).toHaveTextContent("human decision move · approved · by root");
    expect(screen.getByTestId("wf-ledger")).toHaveTextContent("action move · ok · €900");

    await user.click(screen.getByTestId("wf-outcome-save"));
    expect(await screen.findByRole("alert")).toHaveTextContent("numeric outcome value");
    await user.type(screen.getByTestId("wf-outcome-value"), "1500");
    await user.click(screen.getByTestId("wf-outcome-save"));
    await waitFor(() => expect(api.recordWorkflowOutcome).toHaveBeenCalledWith("abc123",
      { metric: "loss_avoided_eur", value: 1500, note: null }));
    expect(await screen.findByTestId("wf-outcomes")).toHaveTextContent("loss avoided eur: 1500");
    expect(api.workflowPerformance).toHaveBeenCalledTimes(2); // refreshed after recording
  });

  it("hides outcome recording for dry runs and auditors", async () => {
    api.workflowRun.mockResolvedValue({ ...waitingRun, dry_run: true });
    api.workflowRuns.mockResolvedValue({ runs: [{ ...waitingRun, dry_run: true }] });
    const user = userEvent.setup();
    render(<WorkflowsPage role="planner" />);
    await user.click(await screen.findByTestId("wf-open-rescue-de"));
    await user.click(await screen.findByText("abc123"));
    await screen.findByTestId("wf-ledger");
    expect(screen.queryByTestId("wf-outcome-save")).not.toBeInTheDocument();
  });

  it("builds a new workflow from the palette and saves it as the DSL", async () => {
    api.saveWorkflow.mockImplementation(async (spec) => ({ ...spec, version: 1 }));
    const user = userEvent.setup();
    render(<WorkflowsPage role="planner" />);
    await user.click(await screen.findByTestId("wf-palette-trigger.manual"));
    await user.click(screen.getByTestId("wf-palette-action.notify"));
    expect(screen.getByTestId("wf-node-manual_1")).toBeInTheDocument();
    expect(screen.getByTestId("wf-inspector")).toBeInTheDocument(); // newly added node is selected

    await user.type(screen.getByTestId("wf-param-message"), "Check sell-through");
    fireEvent.blur(screen.getByTestId("wf-param-message"));

    await user.click(screen.getByTestId("wf-palette-trigger.sell_through_gap"));
    expect(screen.getByRole("alert")).toHaveTextContent("exactly one trigger");

    window.__rfConnect = { source: "manual_1", target: "notify_1", sourceHandle: "out" };
    await user.click(screen.getByTestId("rf-connect"));
    await user.click(screen.getByTestId("rf-connect")); // duplicate edge is ignored
    expect(screen.getByTestId("rf-canvas").dataset.edges).toBe("manual_1:out->notify_1");

    await user.click(screen.getByTestId("rf-pane"));
    expect(screen.getByTestId("wf-settings")).toBeInTheDocument();
    expect(screen.getByTestId("wf-save")).toBeDisabled(); // needs an id first
    await user.type(screen.getByTestId("wf-meta-id"), "notify-me");
    await user.type(screen.getByTestId("wf-meta-name"), "Notify me");
    await user.click(screen.getByTestId("wf-save"));

    await waitFor(() => expect(api.saveWorkflow).toHaveBeenCalled());
    const sent = api.saveWorkflow.mock.calls[0][0];
    expect(sent).toMatchObject({
      id: "notify-me", name: "Notify me", trigger: "manual_1", budget_eur: 0, approval_threshold_eur: null,
      edges: [{ source: "manual_1", target: "notify_1", port: "out" }],
    });
    expect(sent.nodes.map((n) => [n.id, n.type, n.params])).toEqual([
      ["manual_1", "trigger.manual", {}],
      ["notify_1", "action.notify", { channel: "in_app", to: "planner", message: "Check sell-through" }],
    ]);
    expect(await screen.findByText(/Saved notify-me as version 1/)).toBeInTheDocument();
    expect(screen.getByTestId("wf-meta-id")).toBeDisabled(); // id is fixed once saved

    await user.click(screen.getByTestId("wf-node-notify_1"));
    await user.click(screen.getByTestId("wf-delete-node"));
    expect(screen.queryByTestId("wf-node-notify_1")).not.toBeInTheDocument();
    expect(screen.getByTestId("rf-canvas").dataset.edges).toBe("");
  });

  it("loads, validates and saves a workflow", async () => {
    api.validateWorkflow.mockResolvedValue({ valid: false, errors: ["move.units: Input should be greater than 0"] });
    api.saveWorkflow.mockResolvedValue({ ...saved, version: 3 });
    const user = userEvent.setup();
    render(<WorkflowsPage role="planner" />);
    await user.click(await screen.findByTestId("wf-open-rescue-de"));
    expect(await screen.findByTestId("wf-node-move")).toHaveTextContent("needs approval");

    await user.click(screen.getByTestId("wf-validate"));
    expect(await screen.findByTestId("wf-errors")).toHaveTextContent("move.units");
    const sent = api.validateWorkflow.mock.calls[0][0];
    expect(sent).toMatchObject({ id: "rescue-de", trigger: "start", budget_eur: 2000, edges: saved.edges });
    expect(sent.nodes[1].params.units).toBe(600);

    await user.click(screen.getByTestId("wf-save"));
    expect(await screen.findByText(/Saved rescue-de as version 3/)).toBeInTheDocument();
  });

  it("runs a workflow and approves the guarded step", async () => {
    api.startWorkflowRun.mockResolvedValue(waitingRun);
    api.approveWorkflowRun.mockResolvedValue({
      ...waitingRun, status: "SUCCEEDED", pending: null, spent_eur: 900,
      steps: [...waitingRun.steps, { node: "move", status: "approved" }, { node: "move", status: "ok", port: "out", cost_eur: 900 }],
    });
    const user = userEvent.setup();
    render(<WorkflowsPage role="admin" />);
    await user.click(await screen.findByTestId("wf-open-rescue-de"));
    await screen.findByTestId("wf-node-move");

    fireEvent.change(screen.getByTestId("wf-payload"), { target: { value: '{"sku": "MAT"}' } });
    await user.click(screen.getByTestId("wf-run"));
    await waitFor(() => expect(api.startWorkflowRun).toHaveBeenCalledWith("rescue-de", { payload: { sku: "MAT" }, dry_run: false }));
    expect(await screen.findByTestId("wf-run-status")).toHaveTextContent("WAITING_APPROVAL");
    expect(screen.getByTestId("wf-node-move")).toHaveTextContent("awaiting_approval"); // canvas shows run state

    await user.click(screen.getByTestId("wf-approve"));
    await waitFor(() => expect(api.approveWorkflowRun).toHaveBeenCalledWith("abc123", { node: "move", approved: true }));
    expect(await screen.findByTestId("wf-run-status")).toHaveTextContent("SUCCEEDED");
  });

  it("rejects an invalid payload before calling the API", async () => {
    const user = userEvent.setup();
    render(<WorkflowsPage role="planner" />);
    await user.click(await screen.findByTestId("wf-open-rescue-de"));
    await screen.findByTestId("wf-node-move");
    fireEvent.change(screen.getByTestId("wf-payload"), { target: { value: "{oops" } });
    await user.click(screen.getByTestId("wf-dry-run"));
    expect(await screen.findByRole("alert")).toHaveTextContent("not valid JSON");
    expect(api.startWorkflowRun).not.toHaveBeenCalled();
  });

  it("auditors can view but not edit or run", async () => {
    const user = userEvent.setup();
    render(<WorkflowsPage role="auditor" />);
    await user.click(await screen.findByTestId("wf-open-rescue-de"));
    await screen.findByTestId("wf-node-move");
    expect(screen.queryByTestId("wf-palette-action.notify")).not.toBeInTheDocument();
    expect(screen.queryByTestId("wf-save")).not.toBeInTheDocument();
    expect(screen.queryByTestId("wf-run")).not.toBeInTheDocument();
    expect(screen.getByTestId("wf-validate")).toBeInTheDocument();
  });

  it("shows API errors", async () => {
    api.workflowNodeTypes.mockRejectedValue(new Error("Cannot reach the API. Is the backend running?"));
    render(<WorkflowsPage role="planner" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Cannot reach the API");
  });
});
