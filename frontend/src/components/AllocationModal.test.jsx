import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi, describe, it, expect, beforeEach } from "vitest";

vi.mock("../services/api", () => ({ api: { allocate: vi.fn() } }));
import { api } from "../services/api";
import AllocationModal from "./AllocationModal";

const ch = (channel, allocated, demand) => ({ channel, allocated, demand, fill_rate: allocated / demand });
const result = {
  month: "2026-03-01", supply: 1200, total_demand: 2000, shortage: 800,
  assumptions: { chargeback_pct: 0.06, otif_penalty_pct: 0.05 },
  scenarios: [
    { strategy: "margin_optimal", channels: [ch("amazon", 660, 1100), ch("B2B wo Amazon", 240, 600), ch("D2C", 300, 300)], total_profit_gbp: 9000, profit_vs_best_gbp: 0 },
    { strategy: "priority_wholesale", channels: [ch("amazon", 1100, 1100), ch("B2B wo Amazon", 100, 600), ch("D2C", 0, 300)], total_profit_gbp: 6500, profit_vs_best_gbp: -2500 },
    { strategy: "proportional", channels: [ch("amazon", 660, 1100), ch("B2B wo Amazon", 360, 600), ch("D2C", 180, 300)], total_profit_gbp: 7800, profit_vs_best_gbp: -1200 },
  ],
};

describe("AllocationModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.allocate.mockResolvedValue(result);
  });

  it("requires the available units before calling the API", async () => {
    const user = userEvent.setup();
    render(<AllocationModal productId="PROD_1" onClose={() => {}} />);
    await user.click(screen.getByRole("button", { name: /simulate/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/available units/i);
    expect(api.allocate).not.toHaveBeenCalled();
  });

  it("sends the inputs as fractions and shows each strategy with its gap to the best", async () => {
    const user = userEvent.setup();
    render(<AllocationModal productId="PROD_1" onClose={() => {}} />);
    await user.type(screen.getByLabelText(/available units/i), "1200");
    await user.click(screen.getByRole("button", { name: /simulate/i }));
    await waitFor(() => expect(api.allocate).toHaveBeenCalledWith({
      product_id: "PROD_1", available_units: 1200, amazon_floor_pct: 0.6, b2b_floor_pct: 0,
    }));
    const best = await screen.findByTestId("scenario-margin_optimal");
    expect(within(best).getByText("best")).toBeInTheDocument();
    const prio = screen.getByTestId("scenario-priority_wholesale");
    expect(within(prio).getByText(/-\u00a32,500/)).toBeInTheDocument();
    expect(screen.getByText(/shortage/i)).toBeInTheDocument();
  });

  it("shows API errors", async () => {
    api.allocate.mockRejectedValue(new Error("No planned demand for product PROD_1 in 2026-03-01."));
    const user = userEvent.setup();
    render(<AllocationModal productId="PROD_1" onClose={() => {}} />);
    await user.type(screen.getByLabelText(/available units/i), "10");
    await user.click(screen.getByRole("button", { name: /simulate/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("No planned demand");
  });

  it("closes", async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<AllocationModal productId="PROD_1" onClose={onClose} />);
    await user.click(screen.getByRole("button", { name: /close/i }));
    expect(onClose).toHaveBeenCalled();
  });
});
