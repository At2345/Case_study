import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi, describe, it, expect, beforeEach } from "vitest";

vi.mock("../services/api", () => ({
  api: { item: vi.fn(), override: vi.fn(), narrative: vi.fn() },
}));
import { api } from "../services/api";
import DetailPanel, { REASON_REQUIRED } from "./DetailPanel";

const detail = (decision = null) => ({
  item: {
    item_id: 1, product_id: "PROD_1", channel: "amazon", sales_price: 49.99, narrative: "Traffic surge.",
    risk_flags: ["OOS_SURGE"], value_delta_gbp: 12997, source: "ml",
  },
  plan: [{ month: "2026-03-01", baseline: 620, ml: 880, final: 880, consensus: 880, override: null, source: "ml" }],
  history: [{ month: "2026-01-01", actual: 500 }],
  telemetry: { glance_mom_pct: 0.34, on_hand: 182 },
  decision,
});
const withPhasing = (floor) => ({
  ...detail(),
  phasing: [
    { month: "2026-03-01", demand: 880, expected_delivery: 656, booked: 1200, booked_shifted: 100, carry_in: 40, shifted_in: 0, order_book_floor: floor },
    { month: "2026-04-01", demand: 700, expected_delivery: 720, booked: 80, booked_shifted: 0, carry_in: 0, shifted_in: 100, order_book_floor: null },
  ],
});
const codes = { accept: "ML Output Formally Accepted", codes: ["Supply Chain Constrained Run", "Retailer Promotion Timing Shift"] };

describe("DetailPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.item.mockResolvedValue(detail());
    api.override.mockResolvedValue({ id: 1 });
  });

  it("blocks submission without a reason code and does not call the API", async () => {
    const user = userEvent.setup();
    render(<DetailPanel itemId={1} reasonCodes={codes} role="planner" onChanged={() => {}} />);
    await screen.findByText("Traffic surge.");
    await user.click(screen.getByRole("button", { name: /commit decision/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(REASON_REQUIRED);
    expect(api.override).not.toHaveBeenCalled();
  });

  it("submits quantity, reason and comment once a reason is chosen", async () => {
    const onChanged = vi.fn();
    const user = userEvent.setup();
    render(<DetailPanel itemId={1} reasonCodes={codes} role="planner" onChanged={onChanged} />);
    await screen.findByText("Traffic surge.");
    const qty = screen.getByLabelText(/final quantity/i);
    await user.clear(qty);
    await user.type(qty, "850");
    await user.selectOptions(screen.getByLabelText(/reason code/i), "Supply Chain Constrained Run");
    await user.type(screen.getByLabelText(/operational rationale/i), "buyer delay");
    await user.click(screen.getByRole("button", { name: /commit decision/i }));
    await waitFor(() => expect(api.override).toHaveBeenCalled());
    expect(api.override).toHaveBeenCalledWith({
      item_id: 1, override_quantity: 850, reason_code: "Supply Chain Constrained Run", comment: "buyer delay",
    });
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("accepts the ML plan with the dedicated reason code", async () => {
    const user = userEvent.setup();
    render(<DetailPanel itemId={1} reasonCodes={codes} role="planner" onChanged={() => {}} />);
    await screen.findByText("Traffic surge.");
    await user.click(screen.getByRole("button", { name: /accept ml plan/i }));
    await waitFor(() => expect(api.override).toHaveBeenCalledWith({
      item_id: 1, override_quantity: 880, reason_code: "ML Output Formally Accepted",
    }));
  });

  it("shows the server error message", async () => {
    api.override.mockRejectedValue(new Error("Adjustments below 5% on touchless items are suppressed."));
    const user = userEvent.setup();
    render(<DetailPanel itemId={1} reasonCodes={codes} role="planner" onChanged={() => {}} />);
    await screen.findByText("Traffic surge.");
    await user.selectOptions(screen.getByLabelText(/reason code/i), "Retailer Promotion Timing Shift");
    await user.click(screen.getByRole("button", { name: /commit decision/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("below 5%");
  });

  it("shows an audit badge for an existing decision", async () => {
    api.item.mockResolvedValue(detail({
      submitted_quantity: 850, planner_id: "danny", reason_code: "Supply Chain Constrained Run", financial_impact_gbp: -1500,
    }));
    render(<DetailPanel itemId={1} reasonCodes={codes} role="planner" onChanged={() => {}} />);
    expect(await screen.findByTestId("audit-badge")).toHaveTextContent("danny");
  });

  it("shows the phasing table and offers to raise the plan to the order book", async () => {
    api.item.mockResolvedValue(withPhasing(1200));
    const user = userEvent.setup();
    render(<DetailPanel itemId={1} reasonCodes={codes} role="planner" onChanged={() => {}} />);
    const table = await screen.findByTestId("phasing-table");
    expect(within(table).getByText("656")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /raise plan to order book/i }));
    await waitFor(() => expect(api.override).toHaveBeenCalledWith({
      item_id: 1, override_quantity: 1200, reason_code: "Order Book Floor Applied",
      comment: "Plan raised to the units already booked in the order book.",
    }));
  });

  it("does not offer the floor when bookings do not exceed the plan", async () => {
    api.item.mockResolvedValue(withPhasing(null));
    render(<DetailPanel itemId={1} reasonCodes={codes} role="planner" onChanged={() => {}} />);
    await screen.findByTestId("phasing-table");
    expect(screen.queryByRole("button", { name: /raise plan to order book/i })).not.toBeInTheDocument();
  });

  it("shows an AI explanation with its source when the planner asks for one", async () => {
    api.narrative.mockResolvedValue({ text: "Views are up 34% while 182 units are on hand.", source: "llm", cached: false, reasons: [], enabled: true });
    const user = userEvent.setup();
    render(<DetailPanel itemId={1} reasonCodes={codes} role="auditor" onChanged={() => {}} />);
    await user.click(await screen.findByRole("button", { name: /explain with ai/i }));
    const box = await screen.findByTestId("ai-explanation");
    expect(box).toHaveTextContent("Views are up 34% while 182 units are on hand.");
    expect(box).toHaveTextContent(/ai-generated/i);
    expect(api.narrative).toHaveBeenCalledWith(1);
  });

  it("labels the rule-based text when the model is unavailable or rejected", async () => {
    api.narrative.mockResolvedValue({ text: "Rule text.", source: "template", cached: false, reasons: ["llm_not_configured"], enabled: false });
    const user = userEvent.setup();
    render(<DetailPanel itemId={1} reasonCodes={codes} role="planner" onChanged={() => {}} />);
    await user.click(await screen.findByRole("button", { name: /explain with ai/i }));
    const box = await screen.findByTestId("ai-explanation");
    expect(box).toHaveTextContent(/rule-based/i);
    expect(box).toHaveTextContent("The language model is not configured.");
  });

  it("reports a failed explanation request", async () => {
    api.narrative.mockRejectedValue(new Error("Cannot reach the API. Is the backend running?"));
    const user = userEvent.setup();
    render(<DetailPanel itemId={1} reasonCodes={codes} role="planner" onChanged={() => {}} />);
    await user.click(await screen.findByRole("button", { name: /explain with ai/i }));
    expect(await screen.findByText(/cannot reach the api/i)).toBeInTheDocument();
  });

  it("is read-only for auditors", async () => {
    render(<DetailPanel itemId={1} reasonCodes={codes} role="auditor" onChanged={() => {}} />);
    await screen.findByText("Traffic surge.");
    expect(screen.queryByRole("button", { name: /commit decision/i })).not.toBeInTheDocument();
    expect(screen.getByText(/read-only/i)).toBeInTheDocument();
  });
});
